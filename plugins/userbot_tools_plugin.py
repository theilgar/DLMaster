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

from core.userbot_api import UB, ff, telethon_version, utils

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
    def plain(title: str) -> str:
        """'💬 Hesab komandaları' → 'Hesab komandaları' (<pre> içində emoji hizanı pozur)."""
        head, _, rest = title.partition(" ")
        return rest if rest and not head[:1].isalnum() else title

    def text_menu() -> str:
        body = []
        for key, cat in ub.ordered_cats():
            keys = ub.help_keys(key)
            if not keys:
                continue
            body.append(f"# {plain(cat['title'])}")
            body += [(ub.help[k][0], ub.help[k][1]) for k in keys]
        return ff(ub.title("menu"), body, footer="mətn rejimi — bu çatda inline düymə göndərmək olmur")

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
            results = await client.inline_query(await ub.bot_username(), f"ub:menu:{tok}",
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
        body = [("Userbot", "● qoşulub" if on else "○ qoşulmayıb")]
        filler = getattr(context, "depo_filler", None)
        if filler is not None:
            try:
                body.append(("Depo", f"{'● işləyir' if filler.running else '○ dayanıb'} · növbə {len(filler.queue)}"))
            except Exception:
                pass
        body.append(("İşlər", f"{ub.active_tasks()} aktiv"))
        body.append(("Komanda", f"{len(ub.commands)} · bölmə {len(ub.sections)}"))
        if ub.cards:
            body.append(("Kart", f"{len(ub.cards)} açıq"))
        m = ub.menus.get(tok) or {}
        if m.get("reply_uid"):
            body.append(("Reply", "● şəxs seçilib"))
        text = ff(ub.title("menu"), body, footer="bölmə seç ↓")
        sec_btns = [Bt(tok, v["label"], key) for key, v in ub.ordered_sections()]
        rows = [sec_btns[i:i + 2] for i in range(0, len(sec_btns), 2)]
        rows.append([Bt(tok, "❓ Help", "help"), Bt(tok, "❌ Bağla", "close")])
        return text, InlineKeyboardMarkup(inline_keyboard=rows)

    # ── help bölməsi (reyestrdən) ──
    def help_view(tok):
        body, rows = [], []
        for key, cat in ub.ordered_cats():
            keys = ub.help_keys(key)
            if not keys:
                continue
            body.append((plain(cat["title"]).split(" ")[0], f"{len(keys)} · {cat['sub']}"))
            rows.append([Bt(tok, cat["title"], f"help:c:{key}")])
        rows.append([Bt(tok, "⬅️ Menyu", "main")])
        return ff(ub.title("help"), body, footer="bölmə seç, sonra komandaya bas"), \
            InlineKeyboardMarkup(inline_keyboard=rows)

    def help_cat_view(tok, cat_key):
        cat = ub.help_cats.get(cat_key)
        if not cat:
            return help_view(tok)
        keys = ub.help_keys(cat_key)
        body = [f"# {plain(cat['title'])}"] + [(ub.help[k][0], ub.help[k][1]) for k in keys]
        btns = [Bt(tok, ub.help[k][0], f"help:x:{k}") for k in keys]
        rows = [btns[i:i + 3] for i in range(0, len(btns), 3)]
        rows.append([Bt(tok, "⬅️ Help", "help"), Bt(tok, "🏠 Menyu", "main")])
        return ff(ub.title("help"), body, footer=cat["sub"]), InlineKeyboardMarkup(inline_keyboard=rows)

    def help_cmd_view(tok, k):
        if k not in ub.help:
            return help_view(tok)
        button, short, long, category = ub.help[k]
        cat = ub.help_cats.get(category, {})
        head = ff(ub.title("help"), [("Komanda", button), ("Nədir", short), ("Bölmə", plain(cat.get("title", "—")))])
        rows = [[Bt(tok, "⬅️ Bölmə", f"help:c:{category}"), Bt(tok, "🏠 Menyu", "main")]]
        return f"{head}\n\n{long}", InlineKeyboardMarkup(inline_keyboard=rows)

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
        kind = parts[1] if len(parts) > 1 else ""
        tok = parts[2] if len(parts) > 2 and parts[2].isalnum() else "0"

        if kind == "card":                                   # komanda kartı (.speedtest, .ff, .purge ...)
            out = ub.cards.get(tok)
            if out is None:
                await q.answer([], cache_time=0, is_personal=True)
                return
            await q.answer([InlineQueryResultArticle(
                id=f"ubcard_{tok}", title="🧩 Userbot", description="komanda nəticəsi",
                input_message_content=InputTextMessageContent(message_text=out.text, parse_mode="HTML",
                                                              disable_web_page_preview=True),
                reply_markup=out.kb(),
            )], cache_time=0, is_personal=True)
            return

        text, kb = main_view(tok)
        await q.answer([InlineQueryResultArticle(
            id=f"ubmenu_{tok}", title="🛠 Userbot admin paneli", description="Status, sistem, çat, depo, help",
            input_message_content=InputTextMessageContent(message_text=text, parse_mode="HTML"),
            reply_markup=kb,
        )], cache_time=0, is_personal=True)

    # inline_message_id — BotFather-də /setinlinefeedback aktivdirsə buradan gəlir (yoxdursa Out özü alır)
    @dp.chosen_inline_result(F.result_id.startswith(("ubcard_", "ubmenu_")))
    async def ub_chosen(r: ag_types.ChosenInlineResult):
        if not ub.is_creator(r.from_user.id) or not r.inline_message_id:
            return
        if r.result_id.startswith("ubcard_"):
            out = ub.cards.get(r.result_id[len("ubcard_"):])
            if out is not None:
                out.set_iid(r.inline_message_id)
        else:
            m = ub.menus.get(r.result_id[len("ubmenu_"):])
            if m is not None:
                m["iid"] = r.inline_message_id

    # ── kart düymələri: ubc:<tok>:<action> ──
    @dp.callback_query(F.data.startswith("ubc:"))
    async def ub_card_cb(cb: ag_types.CallbackQuery):
        if not ub.is_creator(cb.from_user.id):
            await cb.answer("⛔ Bu kart yalnız sahibi üçündür", show_alert=True)
            return
        _, tok, action = (cb.data.split(":", 2) + ["", ""])[:3]
        out = ub.cards.get(tok)
        if out is None:
            await cb.answer("Bu kart köhnədir — komandanı yenidən yaz", show_alert=True)
            return
        out.set_iid(cb.inline_message_id)
        if out._acquiring:                                  # userbot-un özü-klik (id almaq üçün)
            await cb.answer()
            return
        try:
            if action == "x":
                await cb.answer("Bağlandı")
                await out.close()
            elif action == "s":
                if out.running():
                    out.task.cancel()
                    await cb.answer("⏹ Dayandırılır...")
                else:
                    await cb.answer("İşləyən iş yoxdur")
            elif action == "r":
                await cb.answer("🔄")
                if out.refresh:
                    await out.refresh(out)
                else:
                    await out.update()
            elif action in out.actions:
                await cb.answer()
                await out.actions[action](out, cb)
            else:
                await cb.answer()
        except Exception as e:
            logger.warning(f"kart ({action}) xətası: {e}", exc_info=True)
            try:
                await cb.answer(f"Xəta: {str(e)[:150]}", show_alert=True)
            except Exception:
                pass

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
        ub.state.setdefault("attach_time", time.time())
        ub.attach_pending()
        cmds = " ".join("." + c["name"] for c in ub.commands)
        logger.info(f"✅ Userbot komandaları qoşuldu: {cmds}")

    try:
        ub.state["attach_task"] = asyncio.create_task(attach())
    except RuntimeError:
        logger.error("userbot host: event loop işləmir")

    logger.info("✅ Userbot host yükləndi (.menu paneli + inline kartlar)")


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
    for out in list(ub.cards.values()):
        if out.running():
            out.task.cancel()
    if ub.client:
        for fn, _ in ub._handlers:
            try:
                ub.client.remove_event_handler(fn)
            except Exception:
                pass
