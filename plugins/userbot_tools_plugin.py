"""
🛠 Userbot host — Telethon userbot-un plugin yükləyicisi və admin paneli.

Ayrıca client yaratmır: depo_filler_plugin-in qoşduğu userbot-u (context.telethon_client) istifadə edir.
Hər komanda ayrıca fayldır → userbot_plugins/ qovluğunda. Bu host:
  1) UB reyestrini qurur,
  2) userbot_plugins/*.py fayllarının hər birini yükləyib register(ub) çağırır,
  3) userbot qoşulan kimi bütün komanda və xam handler-ləri client-ə bağlayır,
  4) .menu / .help / .panel admin panelini reyestrdən (komandalar, help, bölmələr) qurur.

Yeni komanda əlavə etmək: userbot_plugins/ içində yeni .py faylı yarat, içində register(ub) yaz.
Nümunə üçün userbot_plugins/_example.py.txt fayllarına bax.
"""
import asyncio
import importlib.util
import logging
import os
import sys
import time
from html import escape

from aiogram import F, types as ag_types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, InlineQueryResultArticle, InputTextMessageContent

from core.userbot_api import UB, telethon_version, utils

logger = logging.getLogger(__name__)

# komanda pluginlərinin qovluğu: <bot kökü>/userbot_plugins
PLUGINS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "userbot_plugins")


def load_plugin_file(ub, path, attach=True):
    """Bir .py faylını yükləyib register(ub) çağırır; owner = fayl adı. (name, info) qaytarır.

    Köhnə eyniadlı plugin varsa əvvəlcə onun qeydləri geri alınır (reload).
    """
    name = os.path.splitext(os.path.basename(path))[0]
    mod_name = f"userbot_plugins.{name}"
    if name in ub.plugin_files:
        ub.remove_plugin(name)                       # reload — köhnəni təmizlə
    prev = ub._loading
    ub._loading = name
    try:
        spec = importlib.util.spec_from_file_location(mod_name, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = mod
        spec.loader.exec_module(mod)
        if not hasattr(mod, "register"):
            raise RuntimeError("register(ub) funksiyası yoxdur")
        mod.register(ub)
    except Exception:
        ub.remove_plugin(name)                       # yarımçıq qeydləri geri al
        sys.modules.pop(mod_name, None)
        ub._loading = prev
        raise
    ub._loading = prev
    ub.plugin_files[name] = path
    if attach:
        ub.attach_pending()
    return name, ub.plugin_summary(name)


def load_command_plugins(ub) -> list:
    names = []
    if not os.path.isdir(PLUGINS_DIR):
        logger.warning(f"⚠️ userbot_plugins qovluğu yoxdur: {PLUGINS_DIR}")
        return names
    for fn in sorted(os.listdir(PLUGINS_DIR)):
        if not fn.endswith(".py") or fn.startswith("_"):
            continue
        try:
            name, _ = load_plugin_file(ub, os.path.join(PLUGINS_DIR, fn), attach=False)
            names.append(name)
        except Exception as e:
            logger.error(f"❌ Userbot plugini yüklənmədi ({fn}): {e}", exc_info=True)
    return names


def setup(context):
    dp = context.dp
    ub = UB(context)
    context.userbot = ub
    context.userbot_tools_state = ub            # köhnə adla uyğunluq

    # help kateqoriyaları (komandalar bu kateqoriyalara add_help edir)
    ub.help_category("acc", "💬 Hesab komandaları", "Öz hesabından istənilən çatda yazılır", order=10)
    ub.help_category("bot", "🤖 Bot komandaları", "Botun özündə (@dllmasterbot) yazılır", order=20)
    ub.help_category("panel", "🛠 Panel", "Bu admin panelinin bölmələri", order=30)

    loaded = load_command_plugins(ub)
    logger.info(f"🧩 Userbot pluginləri yükləndi ({len(loaded)}): {', '.join(loaded) or '—'}")

    # bot tərəfindəki komandalar (userbot deyil — sadəcə help üçün)
    ub.add_help("userbot", "/userbot", "userbot statusu",
                "Botda yazılır: userbot qoşulubmu, hesab, Telegram mənbələrinin sayı.\n\n<i>Paneldə:</i> 📊 Status", "bot")
    ub.add_help("scan_chat", "/scan_chat", "çatı birdəfəlik skan et",
                "Kanal / qrupdakı audioları userbot ilə oxuyur, adları təmizləyir, depo növbəsinə əlavə edir.\n\n"
                "<b>İstifadə (botda):</b>\n• <code>/scan_chat https://t.me/+dəvət</code>\n"
                "• <code>/scan_chat @kanal 300</code>\n• <code>/scan_chat -1001234567890</code>", "bot")
    ub.add_help("stop_scan", "/stop_scan", "skanı dayandır",
                "Gedən skanı dayandırır.\n\n<b>İstifadə (botda):</b> <code>/stop_scan</code>\n"
                "<i>Paneldə:</i> 📦 Depo → ⏹ Skanı dayandır", "bot")
    ub.add_help("depo_add", "/depo add", "daimi Telegram mənbəyi",
                "Kanalı / qrupu depoya daimi mənbə kimi əlavə edir: dərhal skan, sonra yeni audiolar canlı növbəyə.\n\n"
                "<b>İstifadə (botda):</b>\n• <code>/depo add @kanal</code>\n• <code>/depo add https://t.me/+dəvət 500</code>", "bot")
    ub.add_help("depo", "/depo", "depo doldurucu paneli",
                "Doldurucunun tam paneli (botda): sürət, mənbələr, növbə.\n\n"
                "<b>İstifadə:</b> <code>/depo</code>, <code>/depo on</code>, <code>/depo off</code>\n"
                "<i>Paneldə:</i> 📦 Depo", "bot")
    ub.add_help("fix", "/fix", "depo təmizliyi",
                "Dublikatları birləşdirir, adlardakı tarix / zibili təmizləyir, YouTube adı ilə uyğun gəlməyənləri "
                "düzəldir.\n\n<b>İstifadə (botda):</b> <code>/fix</code>", "bot")

    # ───────────── 🛠 .menu — admin paneli (bot inline rejimi ilə) ─────────────
    async def bot_username():
        if not ub.state.get("bot_username"):
            ub.state["bot_username"] = (await context.bot.get_me()).username
        return ub.state["bot_username"]

    def text_menu() -> str:
        L = ["🛠 <b>Userbot paneli</b> <i>(mətn rejimi — bu çatda düymə göndərilə bilmədi)</i>"]
        for key, cat in ub.ordered_cats():
            keys = ub.help_keys(key)
            if not keys:
                continue
            L += ["", f"<b>{cat['title']}</b>"]
            L += [f"• <code>{escape(ub.help[k][0])}</code> — {escape(ub.help[k][1])}" for k in keys]
        return "\n".join(L)

    async def on_menu(event):
        client = event.client
        tok = os.urandom(4).hex()[:6]
        reply_uid = None
        if event.is_reply:
            try:
                reply = await event.get_reply_message()
                reply_uid = reply.sender_id if reply else None
            except Exception:
                pass
        ub.menus[tok] = {"chat_id": event.chat_id, "reply_uid": reply_uid, "msg_id": None, "time": time.time()}
        if len(ub.menus) > 200:
            ub.menus.pop(next(iter(ub.menus)))
        try:
            results = await client.inline_query(await bot_username(), f"ub:menu:{tok}",
                                                entity=await event.get_input_chat())
            if not results:
                raise RuntimeError("inline nəticə gəlmədi")
            sent = await results[0].click(event.chat_id, reply_to=event.reply_to_msg_id, hide_via=True)
            if sent is not None:
                ub.menus[tok]["msg_id"] = sent.id
            await event.delete()
        except Exception as e:
            from core.userbot_api import safe_edit
            logger.info(f".menu inline alınmadı: {e}")
            await safe_edit(event, text_menu())

    # .menu komandasını reyestrə qoymaq əvəzinə birbaşa handler kimi əlavə edirik (paneli host idarə edir)
    ub.commands.append({"name": "menu", "pattern": r"^\.(?:menu|help|panel)$", "outgoing": True, "fn": on_menu})
    ub.add_help("menu", ".menu", "admin paneli",
                "Bu admin panelini açır: bütün bölmələr və komandalar buradadır. <code>.help</code>, "
                "<code>.panel</code> də eynidir.\n\n<b>İstifadə:</b> istənilən çatda <code>.menu</code>\n"
                "💡 Kiməsə <b>reply edib</b> <code>.menu</code> yazsan, 👥 Bu çat bölməsində o şəxs üzrə "
                "əlavə düymələr olur.\n<i>Botlar vasitəsilə göndərmə qadağan olan çatda sadə mətn göstərilir.</i>",
                "acc")

    # ───────────── panel görünüşləri ─────────────
    def Bt(tok, text, data):
        return InlineKeyboardButton(text=text, callback_data=f"ubm:{tok}:{data}"[:64])

    def main_view(tok):
        client = ub.client
        on = client is not None and client.is_connected()
        L = ["🛠 <b>Userbot admin paneli</b>", "", f"📶 Userbot: {'🟢 qoşulub' if on else '🔴 qoşulmayıb'}"]
        filler = getattr(context, "depo_filler", None)
        if filler is not None:
            try:
                L.append(f"📦 Depo: {'🟢' if filler.running else '🔴'} · növbə {len(filler.queue)}")
            except Exception:
                pass
        active = ub.active_tasks()
        if active:
            L.append(f"⚙️ Aktiv iş: {active}")
        m = ub.menus.get(tok) or {}
        if m.get("reply_uid"):
            L.append("👤 <i>Reply edilən şəxs var — bölmələrdə əlavə düymələr</i>")
        L += ["", "<i>Bölmə seç:</i>"]
        sec_btns = [Bt(tok, v["label"], key) for key, v in ub.ordered_sections()]
        rows = [sec_btns[i:i + 2] for i in range(0, len(sec_btns), 2)]
        rows.append([Bt(tok, "❓ Help", "help"), Bt(tok, "❌ Bağla", "close")])
        return "\n".join(L), InlineKeyboardMarkup(inline_keyboard=rows)

    # ── help bölməsi (reyestrdən) ──
    def help_view(tok):
        L = ["❓ <b>Help</b>", "", "Bölmə seç, sonra komandanın düyməsinə bas:"]
        rows = []
        for key, cat in ub.ordered_cats():
            if not ub.help_keys(key):
                continue
            L.append(f"• <b>{cat['title']}</b> — <i>{escape(cat['sub'])}</i>")
            rows.append([Bt(tok, cat["title"], f"help:c:{key}")])
        rows.append([Bt(tok, "⬅️ Menyu", "main")])
        return "\n".join(L), InlineKeyboardMarkup(inline_keyboard=rows)

    def help_cat_view(tok, cat_key):
        cat = ub.help_cats.get(cat_key)
        if not cat:
            return help_view(tok)
        keys = ub.help_keys(cat_key)
        L = [f"<b>{cat['title']}</b>", f"<i>{escape(cat['sub'])}</i>", ""]
        L += [f"• <code>{escape(ub.help[k][0])}</code> — {escape(ub.help[k][1])}" for k in keys]
        btns = [Bt(tok, ub.help[k][0], f"help:x:{k}") for k in keys]
        rows = [btns[i:i + 3] for i in range(0, len(btns), 3)]
        rows.append([Bt(tok, "⬅️ Help", "help"), Bt(tok, "🏠 Menyu", "main")])
        return "\n".join(L), InlineKeyboardMarkup(inline_keyboard=rows)

    def help_cmd_view(tok, k):
        if k not in ub.help:
            return help_view(tok)
        button, short, long, category = ub.help[k]
        rows = [[Bt(tok, "⬅️ Bölmə", f"help:c:{category}"), Bt(tok, "🏠 Menyu", "main")]]
        return f"<b>{escape(button)}</b> — {escape(short)}\n\n{long}", InlineKeyboardMarkup(inline_keyboard=rows)

    def cancel_ptask(tok):
        t = ub.ptasks.pop(tok, None)
        if t and not t.done():
            t.cancel()

    # ── bot tərəfi: inline sorğu və düymələr ──
    @dp.inline_query(F.query.startswith("ub:"))
    async def ub_inline(q: ag_types.InlineQuery):
        if not ub.is_creator(q.from_user.id):
            await q.answer([], cache_time=0, is_personal=True)
            return
        parts = q.query.split(":")
        tok = parts[2] if len(parts) > 2 and parts[2].isalnum() else "0"
        text, kb = main_view(tok)
        await q.answer([InlineQueryResultArticle(
            id=f"ubmenu{tok}", title="🛠 Userbot admin paneli", description="Status, sistem, çat, depo, help",
            input_message_content=InputTextMessageContent(message_text=text, parse_mode="HTML"),
            reply_markup=kb,
        )], cache_time=0, is_personal=True)

    @dp.callback_query(F.data.startswith("ubm:"))
    async def ub_panel_cb(cb: ag_types.CallbackQuery):
        if not ub.is_creator(cb.from_user.id):
            await cb.answer("⛔ Bu panel yalnız sahibi üçündür", show_alert=True)
            return
        parts = cb.data.split(":")
        if len(parts) < 3:
            await cb.answer("Bu menyu köhnədir — yenidən .menu yaz", show_alert=True)
            return
        tok, screen, args = parts[1], parts[2], parts[3:]
        view = ":".join([screen] + args)
        if view != "sys:live":                       # canlı rejim yalnız özü davam etsin
            cancel_ptask(tok)
        ub.views[tok] = view
        if len(ub.views) > 300:
            ub.views.pop(next(iter(ub.views)))

        try:
            if screen == "main":
                await cb.answer()
                await ub.edit_panel(cb, *main_view(tok))
            elif screen == "help":
                await cb.answer()
                if args[:1] == ["c"] and len(args) > 1:
                    await ub.edit_panel(cb, *help_cat_view(tok, args[1]))
                elif args[:1] == ["x"] and len(args) > 1:
                    await ub.edit_panel(cb, *help_cmd_view(tok, args[1]))
                else:
                    await ub.edit_panel(cb, *help_view(tok))
            elif screen == "close":
                await cb.answer("Bağlandı")
                cancel_ptask(tok)
                m = ub.menus.pop(tok, None)
                deleted = False
                if m and m.get("msg_id") and ub.client:
                    try:
                        await ub.client.delete_messages(m["chat_id"], [m["msg_id"]])
                        deleted = True
                    except Exception as e:
                        logger.debug(f"panel silinmədi: {e}")
                if not deleted:
                    await ub.edit_panel(cb, "✖️ <i>Panel bağlandı</i>", None)
            elif screen in ub.sections:
                sec = ub.sections[screen]
                from core.userbot_api import SectionCtx
                sctx = SectionCtx(ub, screen, tok, cb, args[0] if args else "", args[1:] if len(args) > 1 else [])
                handle = sec.get("handle")
                if args and handle:
                    await handle(sctx)
                else:
                    await cb.answer()
                    text, rows = await sec["render"](sctx)
                    await sctx.show(text, rows)
            else:
                await cb.answer()
                await ub.edit_panel(cb, *main_view(tok))
        except Exception as e:
            logger.warning(f"ub panel ({view}) xətası: {e}", exc_info=True)
            try:
                await cb.answer(f"Xəta: {str(e)[:150]}", show_alert=True)
            except Exception:
                pass

    # host-un özünün köməkçiləri pluginlərə lazım ola bilər
    ub.state["main_view"] = main_view
    ub.state["Bt"] = Bt
    ub.state["plugins_dir"] = PLUGINS_DIR
    ub.state["load_plugin_file"] = lambda path, attach=True: load_plugin_file(ub, path, attach)

    # ───────────── userbot-a qoşulma ─────────────
    async def attach():
        for _ in range(120):
            client = getattr(context, "telethon_client", None)
            if client:
                break
            await asyncio.sleep(1)
        else:
            logger.warning("⚠️ userbot host: telethon_client tapılmadı (depo_filler_plugin yüklənməyib?)")
            return
        ub.client = client
        ub.attach_pending()
        cmds = " ".join("." + c["name"] for c in ub.commands)
        logger.info(f"✅ Userbot komandaları qoşuldu: {cmds}")

    try:
        ub.state["attach_task"] = asyncio.create_task(attach())
    except RuntimeError:
        logger.error("userbot host: event loop işləmir")

    logger.info("✅ Userbot host yükləndi (/.menu paneli)")


async def teardown(context):
    ub = getattr(context, "userbot", None)
    if not ub:
        return
    t = ub.state.get("attach_task")
    if t and not t.done():
        t.cancel()
    for kinds in ub.tasks.values():
        for task in kinds.values():
            if task and not task.done():
                task.cancel()
    for task in ub.ptasks.values():
        if task and not task.done():
            task.cancel()
    if ub.client:
        for fn, _ in ub._handlers:
            try:
                ub.client.remove_event_handler(fn)
            except Exception:
                pass
