"""
🧩 Plugin meneceri — yalnız creator.

  • Bota .py faylı göndər → yoxlanılır (sintaksis, setup funksiyası) → təsdiqlə → plugins/-a yazılır
    və BOTU YENİDƏN BAŞLATMADAN dərhal işə düşür. Eyni adlı plugin varsa köhnəsi plugins/.backup/-a köçürülür.
  • /plugins — bütün plugin-lər: 🔄 yenidən yüklə · ⏸ söndür · ▶️ aktivləşdir · 🗑 sil

Plugin-in köhnə handler-ləri yenidən yükləmədə avtomatik çıxarılır. Plugin-də istəyə görə
`teardown(context)` funksiyası olsa, söndürüləndə çağırılır (fon işlərini dayandırmaq üçün).

Söndürülən plugin plugins/disabled/-a köçür — bot yenidən başlayanda da yüklənmir.
"""
import ast
import asyncio
import importlib
import importlib.util
import inspect
import logging
import os
import re
import shutil
import sys
import time
from html import escape
from pathlib import Path

from aiogram import F, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from core.utilities import BASE_DIR

logger = logging.getLogger(__name__)

PLUGINS_DIR = Path(BASE_DIR) / "plugins"
DISABLED_DIR = PLUGINS_DIR / "disabled"
BACKUP_DIR = PLUGINS_DIR / ".backup"
SELF_NAME = Path(__file__).stem
MAX_SIZE = 1024 * 1024


def btn(text, data):
    return InlineKeyboardButton(text=text, callback_data=data)


def safe_name(file_name: str) -> str:
    stem = Path(file_name or "").stem.lower()
    stem = re.sub(r"[^a-z0-9_]", "_", stem).strip("_")
    return stem


def check_source(source: str):
    """(ok, problem, info) — sintaksis və setup() yoxlanışı."""
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return False, f"SyntaxError: {e.msg} (sətir {e.lineno})", {}
    funcs = {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    if "setup" not in funcs:
        return False, "Faylda setup(context) funksiyası yoxdur — plugin deyil.", {}
    priority = None
    for n in tree.body:
        if isinstance(n, ast.Assign) and any(getattr(t, "id", None) == "PRIORITY" for t in n.targets):
            if isinstance(n.value, ast.Constant):
                priority = n.value.value
    imports = sorted({
        (a.name if isinstance(n, ast.Import) else (n.module or "")).split(".")[0]
        for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))
        for a in (n.names if isinstance(n, ast.Import) else [n])
    } - {""})
    return True, "", {"priority": priority, "teardown": "teardown" in funcs, "imports": imports,
                      "lines": source.count("\n") + 1}


def setup(context):
    dp = context.dp
    bot = context.bot
    pending = {}              # creator-un göndərdiyi, təsdiq gözləyən fayl
    status = getattr(context, "plugin_status", None)
    if status is None:
        status = context.plugin_status = {}

    def is_creator(uid) -> bool:
        return bool(context.creator_id) and uid == context.creator_id

    # ───────────── aiogram daxilində handler-lərin idarəsi ─────────────
    def all_routers(router=None):
        router = router or dp
        yield router
        for sub in list(getattr(router, "sub_routers", [])):
            yield from all_routers(sub)

    def count_handlers(modname: str) -> int:
        n = 0
        for r in all_routers():
            for obs in r.observers.values():
                n += sum(1 for h in obs.handlers if getattr(h.callback, "__module__", None) == modname)
        return n

    def detach(modname: str) -> int:
        """Plugin-in handler, middleware və router-lərini dispetçerdən çıxarır."""
        removed = 0
        old_mod = sys.modules.get(modname)
        mod_routers = set()
        if old_mod:
            for v in vars(old_mod).values():
                if v.__class__.__name__ == "Router" and v is not dp:
                    mod_routers.add(id(v))
        for r in list(all_routers()):
            for obs in r.observers.values():
                before = len(obs.handlers)
                obs.handlers[:] = [h for h in obs.handlers
                                   if getattr(h.callback, "__module__", None) != modname]
                removed += before - len(obs.handlers)
                for mm_name in ("outer_middleware", "middleware"):
                    mm = getattr(obs, mm_name, None)
                    lst = getattr(mm, "_middlewares", None)
                    if isinstance(lst, list):
                        lst[:] = [m for m in lst if getattr(type(m), "__module__", None) != modname
                                  and getattr(m, "__module__", None) != modname]
            subs = getattr(r, "sub_routers", None)
            if isinstance(subs, list):
                for sub in [s for s in subs if id(s) in mod_routers]:
                    subs.remove(sub)
                    if hasattr(sub, "_parent_router"):
                        sub._parent_router = None
        return removed

    async def call_teardown(modname: str):
        mod = sys.modules.get(modname)
        td = getattr(mod, "teardown", None) if mod else None
        if td:
            try:
                res = td(context)
                if inspect.isawaitable(res):
                    await res
            except Exception as e:
                logger.warning(f"teardown xətası ({modname}): {e}")

    async def load_plugin(name: str) -> str:
        """Yükləyir (və ya yenidən yükləyir). Nəticə mətni qaytarır, xəta olsa exception atır."""
        modname = f"plugins.{name}"
        removed = 0
        if modname in sys.modules:
            await call_teardown(modname)
            removed = detach(modname)
            importlib.invalidate_caches()
            module = importlib.reload(sys.modules[modname])
        else:
            importlib.invalidate_caches()
            module = importlib.import_module(modname)

        # setup-dan əvvəl hər observer-dəki handler sayı → yeniləri tapmaq üçün
        before = {(id(r), et): len(obs.handlers) for r in all_routers() for et, obs in r.observers.items()}
        res = module.setup(context)
        if inspect.isawaitable(res):
            await res

        # PRIORITY < 50 olan plugin-in handler-ləri öndə olsun (startdakı sıralama kimi)
        priority = getattr(module, "PRIORITY", 50)
        added = 0
        for r in all_routers():
            for et, obs in r.observers.items():
                n0 = before.get((id(r), et), 0)
                new = obs.handlers[n0:]
                added += len(new)
                if new and priority < 50:
                    obs.handlers[:] = new + obs.handlers[:n0]
        status[f"{name}.py"] = "ok"
        logger.info(f"🧩 Plugin yükləndi: {name} (+{added} handler, köhnə {removed} çıxarıldı)")
        return f"+{added} handler" + (f", köhnə {removed} çıxarıldı" if removed else "")

    async def unload_plugin(name: str) -> int:
        modname = f"plugins.{name}"
        await call_teardown(modname)
        removed = detach(modname)
        sys.modules.pop(modname, None)
        status.pop(f"{name}.py", None)
        return removed

    # ───────────── fayl yükləmə ─────────────
    async def is_creator_py(message: types.Message) -> bool:
        d = message.document
        return bool(d and message.from_user and is_creator(message.from_user.id)
                    and (d.file_name or "").lower().endswith(".py"))

    @dp.message(F.chat.type == "private", F.document, is_creator_py)
    async def on_py_file(message: types.Message):
        doc = message.document
        if doc.file_size and doc.file_size > MAX_SIZE:
            await message.reply("❌ Fayl çox böyükdür (maks. 1 MB).")
            return
        name = safe_name(doc.file_name)
        if not name or name == SELF_NAME:
            await message.reply("❌ Bu adla plugin quraşdırmaq olmaz.")
            return
        tmp = Path("download") / f"plugin_upload_{int(time.time())}.py"
        tmp.parent.mkdir(exist_ok=True)
        await bot.download(doc.file_id, destination=str(tmp))
        source = tmp.read_text(encoding="utf-8", errors="replace")
        ok, problem, info = check_source(source)
        if not ok:
            tmp.unlink(missing_ok=True)
            await message.reply(f"❌ <b>Plugin qəbul olunmadı</b>\n<code>{escape(problem)}</code>", parse_mode="HTML")
            return

        exists = (PLUGINS_DIR / f"{name}.py").exists()
        disabled = (DISABLED_DIR / f"{name}.py").exists()
        def _missing(mod):
            if mod in sys.modules:
                return False
            try:
                return importlib.util.find_spec(mod) is None
            except (ValueError, ImportError):
                return False
        missing = [m for m in info["imports"] if _missing(m)]
        lines = [
            f"🧩 <b>Yeni plugin:</b> <code>{escape(name)}.py</code>",
            f"📃 {info['lines']} sətir · {doc.file_size or 0} bayt",
            f"⚙️ PRIORITY: {info['priority'] if info['priority'] is not None else '50 (default)'}"
            + (" · teardown ✅" if info["teardown"] else ""),
        ]
        if exists:
            lines.append("♻️ <b>Eyni adlı plugin var</b> — köhnəsi .backup/-a köçürüləcək")
        if disabled:
            lines.append("⏸ Bu adla söndürülmüş plugin də var — yenisi aktiv olacaq")
        if missing:
            lines.append(f"⚠️ Quraşdırılmamış kitabxanalar: <code>{escape(', '.join(missing))}</code>")
        lines.append("\n⚠️ <i>Plugin botun içində tam icazə ilə işləyir — yalnız etibar etdiyin kodu quraşdır.</i>")
        sent = await message.reply("\n".join(lines), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[btn("✅ Quraşdır və işə sal", "pm:install"), btn("❌ Ləğv et", "pm:cancel")]]))
        old = pending.pop(message.chat.id, None)
        if old:
            Path(old["tmp"]).unlink(missing_ok=True)
        pending[message.chat.id] = {"name": name, "tmp": str(tmp), "msg": sent.message_id}

    # ───────────── /plugins siyahısı ─────────────
    def plugin_files():
        active = sorted(p.stem for p in PLUGINS_DIR.glob("*.py") if p.stem != "__init__")
        off = sorted(p.stem for p in DISABLED_DIR.glob("*.py")) if DISABLED_DIR.exists() else []
        return active, off

    def list_view():
        active, off = plugin_files()
        lines = ["🧩 <b>Plugin-lər</b>\n"]
        for n in active:
            st = status.get(f"{n}.py")
            icon = "✅" if st == "ok" else ("❌" if st else "⚪")
            lines.append(f"{icon} <code>{escape(n)}</code> · {count_handlers(f'plugins.{n}')} handler")
        for n in off:
            lines.append(f"⏸ <code>{escape(n)}</code> <i>(söndürülüb)</i>")
        lines.append("\n<i>Yeni plugin üçün .py faylını bota göndər.</i>")
        rows = [[btn(f"{'⏸' if n in off else '🧩'} {n}"[:40], f"pm:open:{n}")] for n in active + off]
        rows.append([btn("❌ Bağla", "pm:close")])
        return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)

    def card_view(name: str, note: str = ""):
        active, off = plugin_files()
        path = (DISABLED_DIR if name in off else PLUGINS_DIR) / f"{name}.py"
        size = path.stat().st_size if path.exists() else 0
        st = status.get(f"{name}.py")
        state = "⏸ söndürülüb" if name in off else ("✅ işləyir" if st == "ok" else f"❌ {st}" if st else "⚪ yüklənməyib")
        text = (f"🧩 <b>{escape(name)}</b>\n\n📶 {escape(state)[:300]}\n"
                f"📄 {size} bayt · 🔌 {count_handlers(f'plugins.{name}')} handler"
                + (f"\n\n{note}" if note else ""))
        rows = []
        if name in off:
            rows.append([btn("▶️ Aktivləşdir", f"pm:enable:{name}")])
        elif name != SELF_NAME:
            rows.append([btn("🔄 Yenidən yüklə", f"pm:reload:{name}"), btn("⏸ Söndür", f"pm:disable:{name}")])
        if name != SELF_NAME:
            rows.append([btn("🗑 Sil", f"pm:delete:{name}")])
        rows.append([btn("⬅️ Geri", "pm:list"), btn("❌ Bağla", "pm:close")])
        return text, InlineKeyboardMarkup(inline_keyboard=rows)

    @dp.message(Command("plugins"))
    async def plugins_cmd(message: types.Message):
        if not message.from_user or not is_creator(message.from_user.id):
            return
        text, kb = list_view()
        await message.answer(text, parse_mode="HTML", reply_markup=kb)

    @dp.callback_query(F.data.startswith("pm:"))
    async def pm_callback(cb: types.CallbackQuery):
        if not is_creator(cb.from_user.id):
            await cb.answer("⛔ İcazə yoxdur", show_alert=True)
            return
        parts = cb.data.split(":")
        action, name = parts[1], (parts[2] if len(parts) > 2 else "")

        async def show(text, kb):
            try:
                await cb.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
            except Exception as e:
                if "not modified" not in str(e):
                    logger.warning(f"Plugin paneli yenilənmədi: {e}")

        if action == "close":
            await cb.answer()
            try:
                await cb.message.delete()
            except Exception:
                pass
            return
        if action == "list":
            await cb.answer()
            await show(*list_view())
            return
        if action == "open":
            await cb.answer()
            await show(*card_view(name))
            return

        if action == "cancel":
            p = pending.pop(cb.message.chat.id, None)
            if p:
                Path(p["tmp"]).unlink(missing_ok=True)
            await cb.answer("Ləğv edildi")
            await show("❌ Quraşdırma ləğv edildi.", None)
            return

        if action == "install":
            p = pending.pop(cb.message.chat.id, None)
            if not p or not Path(p["tmp"]).exists():
                await cb.answer("Fayl tapılmadı — yenidən göndər", show_alert=True)
                return
            await cb.answer("Quraşdırılır...")
            name = p["name"]
            target = PLUGINS_DIR / f"{name}.py"
            backup = None
            if target.exists():
                BACKUP_DIR.mkdir(exist_ok=True)
                backup = BACKUP_DIR / f"{name}_{time.strftime('%Y%m%d_%H%M%S')}.py"
                shutil.copy2(target, backup)
            (DISABLED_DIR / f"{name}.py").unlink(missing_ok=True)
            shutil.move(p["tmp"], target)
            try:
                result = await load_plugin(name)
                await show(f"✅ <b>{escape(name)}</b> quraşdırıldı və işləyir\n<i>{escape(result)}</i>"
                           + (f"\n♻️ Köhnə versiya: <code>.backup/{escape(backup.name)}</code>" if backup else ""),
                           InlineKeyboardMarkup(inline_keyboard=[[btn("🧩 Plugin-lər", "pm:list")]]))
            except Exception as e:
                status[f"{name}.py"] = f"yüklənmə xətası: {type(e).__name__}: {e}"
                logger.error(f"Plugin yüklənmədi ({name}): {e}", exc_info=True)
                # köhnə versiyanı geri qaytar
                note = ""
                if backup:
                    shutil.copy2(backup, target)
                    try:
                        await load_plugin(name)
                        note = "\n♻️ Köhnə versiya geri qaytarıldı və işləyir."
                    except Exception:
                        note = "\n⚠️ Köhnə versiya da yüklənmədi."
                await show(f"❌ <b>{escape(name)}</b> yüklənmədi:\n<code>{escape(str(e))[:400]}</code>{note}",
                           InlineKeyboardMarkup(inline_keyboard=[[btn("🧩 Plugin-lər", "pm:list")]]))
            return

        if not name or name == SELF_NAME:
            await cb.answer("Bu plugin-ə toxunmaq olmaz", show_alert=True)
            return

        if action == "reload":
            await cb.answer("Yenidən yüklənir...")
            try:
                result = await load_plugin(name)
                await show(*card_view(name, f"✅ Yenidən yükləndi: {escape(result)}"))
            except Exception as e:
                status[f"{name}.py"] = f"yüklənmə xətası: {type(e).__name__}: {e}"
                await show(*card_view(name, f"❌ <code>{escape(str(e))[:300]}</code>"))
            return

        if action == "disable":
            await cb.answer()
            removed = await unload_plugin(name)
            DISABLED_DIR.mkdir(exist_ok=True)
            src = PLUGINS_DIR / f"{name}.py"
            if src.exists():
                shutil.move(src, DISABLED_DIR / f"{name}.py")
            await show(*card_view(name, f"⏸ Söndürüldü ({removed} handler çıxarıldı). Restartdan sonra da yüklənməyəcək."))
            return

        if action == "enable":
            await cb.answer()
            src = DISABLED_DIR / f"{name}.py"
            if src.exists():
                shutil.move(src, PLUGINS_DIR / f"{name}.py")
            try:
                result = await load_plugin(name)
                await show(*card_view(name, f"▶️ Aktivləşdirildi: {escape(result)}"))
            except Exception as e:
                status[f"{name}.py"] = f"yüklənmə xətası: {type(e).__name__}: {e}"
                await show(*card_view(name, f"❌ <code>{escape(str(e))[:300]}</code>"))
            return

        if action == "delete":
            await cb.answer()
            await show(f"⚠️ <b>{escape(name)}</b> silinsin?\n<i>Fayl .backup/-a köçürüləcək.</i>",
                       InlineKeyboardMarkup(inline_keyboard=[
                           [btn("✅ Bəli, sil", f"pm:deletey:{name}")],
                           [btn("⬅️ Geri", f"pm:open:{name}")]]))
            return

        if action == "deletey":
            await cb.answer("Silindi")
            await unload_plugin(name)
            BACKUP_DIR.mkdir(exist_ok=True)
            for d in (PLUGINS_DIR, DISABLED_DIR):
                f = d / f"{name}.py"
                if f.exists():
                    shutil.move(f, BACKUP_DIR / f"{name}_deleted_{time.strftime('%Y%m%d_%H%M%S')}.py")
            await show(*list_view())
            return

        await cb.answer()

    context.plugin_manager_load = load_plugin
    logger.info("✅ Plugin meneceri yükləndi")
