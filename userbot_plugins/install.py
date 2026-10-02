"""📥 .install / .plugins / .uninstall / .reload — userbot pluginlərinin canlı idarəsi.

.install     — .py sənədinə reply edib yaz: fayl userbot_plugins/ qovluğuna yazılır və dərhal yüklənir
               (restartdan sonra da qalır). Eyni adlı plugin varsa yenilənir (reload).
.plugins     — yüklü pluginlər və onların komandaları
.reload <ad> — faylı diskdən yenidən yüklə
.uninstall <ad> — plugini söndür və faylını sil

⚠️ Yüklənən fayl tam Python kodudur və userbot-un daxilində icra olunur — yalnız öz etibar etdiyin
kodu install et. Komanda creator hesabından işləyir.
"""
import ast
import os
import re
from html import escape

from core.userbot_api import logger, safe_edit

_NAME_RE = re.compile(r"[^a-z0-9_]")


def safe_name(filename: str) -> str:
    stem = os.path.splitext(os.path.basename(filename or ""))[0].lower()
    stem = _NAME_RE.sub("_", stem).strip("_")
    stem = re.sub(r"_+", "_", stem)
    if not stem or stem[0].isdigit():
        stem = "p_" + stem
    return stem[:40]


def register(ub):
    def fmt_info(name, info):
        parts = []
        if info["commands"]:
            parts.append("komanda: " + ", ".join("." + c for c in info["commands"]))
        if info["sections"]:
            parts.append("panel: " + ", ".join(info["sections"]))
        if info["raw"]:
            parts.append(f"{info['raw']} fon handler")
        return f"<b>{escape(name)}</b> — " + (" · ".join(parts) if parts else "boş (register heç nə qeyd etmədi)")

    @ub.command("install", pattern=r"^\.install(?:\s+(\S+))?$",
                help=("plugin yüklə (.py)",
                      "Bir <code>.py</code> sənədinə <b>reply</b> edib <code>.install</code> yaz. Fayl "
                      "<code>userbot_plugins/</code> qovluğuna yazılır və dərhal yüklənir (restartdan sonra qalır). "
                      "Eyni adlı plugin varsa yenilənir.\n\n"
                      "Fayl <code>def register(ub):</code> funksiyası olan plugin olmalıdır — nümunə: "
                      "<code>userbot_plugins/_example.py.txt</code>.\n\n"
                      "⚠️ Yüklənən kod userbot-un içində icra olunur — yalnız etibar etdiyin faylı install et."))
    async def on_install(event):
        plugins_dir = ub.state.get("plugins_dir")
        load_fn = ub.state.get("load_plugin_file")
        if not plugins_dir or not load_fn:
            await safe_edit(event, "❌ Host plugin yükləyicisi hazır deyil.")
            return
        if not event.is_reply:
            await safe_edit(event, "ℹ️ Bir <code>.py</code> faylına <b>reply</b> edib <code>.install</code> yaz.")
            return
        reply = await event.get_reply_message()
        doc = getattr(reply, "document", None) if reply else None
        fname = None
        if doc:
            for a in getattr(doc, "attributes", None) or []:
                if getattr(a, "file_name", None):
                    fname = a.file_name
                    break
        if not doc or not (fname or "").lower().endswith(".py"):
            await safe_edit(event, "❌ Reply etdiyin mesajda <code>.py</code> sənədi yoxdur.")
            return
        await safe_edit(event, f"⏳ <i>{escape(fname)} yüklənir...</i>")
        try:
            data = await event.client.download_media(reply, bytes)
            code = data.decode("utf-8")
        except UnicodeDecodeError:
            await safe_edit(event, "❌ Fayl UTF-8 mətn deyil.")
            return
        except Exception as e:
            await safe_edit(event, f"❌ Fayl endirilmədi: <code>{escape(str(e))[:150]}</code>")
            return
        # sintaksis + register(ub) yoxlaması (icra etmədən)
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            await safe_edit(event, f"❌ Sintaksis xətası: <code>{escape(str(e))[:200]}</code>")
            return
        has_register = any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "register"
                           for n in tree.body)
        if not has_register:
            await safe_edit(event, "❌ Faylda <code>def register(ub):</code> funksiyası yoxdur — bu, userbot "
                                   "plugini deyil.")
            return
        name = safe_name(fname)
        path = os.path.join(plugins_dir, name + ".py")
        existed = name in ub.plugin_files
        # köhnə faylın məzmununu saxla ki, xəta olsa geri qaytaraq
        backup = None
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    backup = f.read()
            except Exception:
                backup = None
        try:
            os.makedirs(plugins_dir, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(code)
            loaded, info = await _maybe_async(load_fn, path)
        except Exception as e:
            # geri qaytar
            try:
                if backup is not None:
                    with open(path, "w", encoding="utf-8") as f:
                        f.write(backup)
                elif os.path.exists(path):
                    os.remove(path)
            except Exception:
                pass
            if backup is not None:
                try:
                    load_fn(path)                     # köhnə işləyən versiyanı geri yüklə
                except Exception:
                    pass
            logger.info(f".install xətası ({name}): {e}")
            await safe_edit(event, f"❌ Plugin yüklənmədi: <code>{escape(str(e))[:250]}</code>\n"
                                   + ("<i>Köhnə versiya saxlanıldı.</i>" if backup is not None else ""))
            return
        verb = "yeniləndi 🔄" if existed else "yükləndi ✅"
        await safe_edit(event, f"📥 Plugin {verb}\n{fmt_info(loaded, info)}\n\n"
                               f"<i>Fayl: userbot_plugins/{escape(name)}.py — restartdan sonra da qalır.</i>")

    @ub.command("plugins", help=("yüklü pluginlər",
                                 "Diskdən yüklənmiş userbot pluginlərini və onların komandalarını göstərir.\n\n"
                                 "<b>İstifadə:</b> <code>.plugins</code>"))
    async def on_plugins(event):
        if not ub.plugin_files:
            await safe_edit(event, "ℹ️ Diskdən yüklənmiş plugin yoxdur.")
            return
        L = [f"🧩 <b>Yüklü pluginlər</b> ({len(ub.plugin_files)}):", ""]
        for nm in sorted(ub.plugin_files):
            L.append("• " + fmt_info(nm, ub.plugin_summary(nm)))
        L += ["", "<i>Yenilə:</i> <code>.reload ad</code> · <i>Sil:</i> <code>.uninstall ad</code>"]
        await safe_edit(event, "\n".join(L))

    @ub.command("reload", pattern=r"^\.reload(?:\s+(\S+))?$",
                help=("plugini yenidən yüklə",
                      "Faylı diskdən yenidən oxuyub yükləyir.\n\n<b>İstifadə:</b> <code>.reload ad</code>"))
    async def on_reload(event):
        load_fn = ub.state.get("load_plugin_file")
        name = (event.pattern_match.group(1) or "").strip()
        if name not in ub.plugin_files:
            await safe_edit(event, f"❌ Belə plugin yoxdur: <code>{escape(name)}</code>\n<i>Siyahı:</i> .plugins")
            return
        try:
            loaded, info = await _maybe_async(load_fn, ub.plugin_files[name])
        except Exception as e:
            await safe_edit(event, f"❌ Yenidən yüklənmədi: <code>{escape(str(e))[:200]}</code>")
            return
        await safe_edit(event, f"🔄 <b>{escape(loaded)}</b> yenidən yükləndi.\n{fmt_info(loaded, info)}")

    @ub.command("uninstall", pattern=r"^\.uninstall(?:\s+(\S+))?$",
                help=("plugini sil",
                      "Plugini söndürür və faylını silir.\n\n<b>İstifadə:</b> <code>.uninstall ad</code>"))
    async def on_uninstall(event):
        name = (event.pattern_match.group(1) or "").strip()
        if name == "install":
            await safe_edit(event, "⛔ .install plugini özünü silə bilməz.")
            return
        if name not in ub.plugin_files:
            await safe_edit(event, f"❌ Belə plugin yoxdur: <code>{escape(name)}</code>\n<i>Siyahı:</i> .plugins")
            return
        ub.remove_plugin(name)
        path = ub.plugin_files.pop(name, None)
        removed_file = False
        if path and os.path.exists(path):
            try:
                os.remove(path)
                removed_file = True
            except Exception as e:
                logger.info(f".uninstall fayl silinmədi: {e}")
        await safe_edit(event, f"🗑 <b>{escape(name)}</b> söndürüldü"
                               + (" və faylı silindi." if removed_file else " (fayl silinmədi)."))


async def _maybe_async(fn, *a):
    r = fn(*a)
    if hasattr(r, "__await__"):
        return await r
    return r
