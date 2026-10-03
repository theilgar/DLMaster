"""
📱 Telethon userbot — vahid client sahibi (şəxsi hesab) + 📱 Userbot hesabları paneli.

Bu plugin client-i yaradır və qoşur (core/tg_session.py) — bütün proses üçün bir client:
  context.telethon_client — depo_filler_plugin, userbot_tools_plugin, userbot_plugins/* hamısı bunu işlədir.

PRIORITY 10 — digər plugin-lərdən əvvəl yüklənir ki, onlar setup() zamanı client-i hazır görsün.

Hesablar (/menu → 🖥 Sistem → 📱 Userbotlar):
  1️⃣ Əsas userbot — TAM səlahiyyət: depo skanı və Telegram mənbələri, 🧬 depo indeksi / dublikat silmə,
     .menu və bütün userbot komandaları, /scan_chat, köməkçi işlər. Bot menyusundan 🔑 ilə qoşulur
     (data/userbot.string) və ya köhnə qayda ilə login.py (userbot.session).
  2️⃣ Canlı yayım asistanı — YALNIZ səs: qrup səsli yayımlarında /play ilə mahnı oxudur.
     Heç bir komanda qəbul etmir, depoya / bota toxunmur (voice_assistant_plugin).
  Eyni hesab iki rola qoşula bilməz.
"""
import asyncio
import contextlib
import logging
import time
from html import escape

from aiogram import F, types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from core import tg_login, tg_session

logger = logging.getLogger(__name__)
PRIORITY = 10


def _fmt_ago(ts) -> str:
    sec = int(time.time() - ts)
    return f"{sec // 3600} saat" if sec >= 3600 else f"{sec // 60} dəq."


def setup(context):
    client = tg_session.make_client(context)
    if client is not None:
        context.telethon_task = asyncio.create_task(tg_session.connect(context))

    dp, bot = context.dp, context.bot
    context.va_waiting = tg_login.any_waiting          # music_plugin: giriş mətni axtarış sayılmasın

    def is_creator(uid) -> bool:
        return bool(context.creator_id) and uid == context.creator_id

    # ── vəziyyət ──
    def main_status() -> tuple:
        c = getattr(context, "telethon_client", None)
        me = getattr(context, "telethon_me", None)
        if not tg_session.API_ID or not tg_session.API_HASH:
            return "❌", "TELETHON_API_ID / TELETHON_API_HASH yoxdur", None
        if c is None:
            return ("⚠️", "hesab qoşulmayıb", None) if not tg_session.session_source() \
                else ("⚠️", "client yaradılmadı (telethon?)", None)
        if c.is_connected() and me:
            return "🟢", "qoşulub", me
        return "🟡", "qoşulur / bağlantı yoxdur", me

    def va_status() -> tuple:
        va = getattr(context, "voice_assistant", None)
        if va is None:
            return "❌", "voice_assistant_plugin yüklənməyib", None
        if not va.has_session():
            return "⚠️", "hesab qoşulmayıb", None
        if not va.enabled:
            return "🔴", "söndürülüb", va.me
        if va.running:
            n = sum(1 for s in va.chats.values() if s.current)
            return "🟢", "işləyir" + (f" · {n} yayım" if n else ""), va.me
        return "⚠️", va.error or va.status, va.me

    def who(me) -> str:
        if not me:
            return "—"
        name = escape(" ".join(x for x in (me.first_name, me.last_name) if x) or "")
        return f"{name}" + (f" (@{escape(me.username)})" if me.username else "") + f" · <code>{me.id}</code>"

    def label() -> str:
        a, b = main_status()[0], va_status()[0]
        return f"📱 Userbotlar · 1️⃣{a} 2️⃣{b}"

    context.userbots_label = label

    def hub_view(note: str = ""):
        i1, s1, me1 = main_status()
        i2, s2, me2 = va_status()
        src = {"string": "🔑 bot menyusu ilə (data/userbot.string)",
               "file": f"📄 login.py ({escape(tg_session.SESSION_FILE)})"}.get(tg_session.session_source(), "—")
        mode = getattr(context, "telethon_session_mode", "")
        lines = [
            "📱 <b>Userbot hesabları</b>\n━━━━━━━━━━━━━━━━━━",
            "1️⃣ <b>Əsas userbot</b> — <i>tam səlahiyyət</i>",
            f"   {i1} {escape(s1)}",
            f"   👤 {who(me1)}",
            f"   🔐 Sessiya: {src}" + (" · yaddaşda" if mode == "memory" else ""),
            "   <i>📦 depo skanı və Telegram mənbələri · 🧬 depo indeksi · .menu və userbot komandaları · "
            "/scan_chat</i>",
            "",
            "2️⃣ <b>Canlı yayım asistanı</b> — <i>yalnız səs</i>",
            f"   {i2} {escape(s2)}",
            f"   👤 {who(me2)}",
            "   <i>🎙 qrup səsli yayımlarında /play ilə mahnı oxutmaq. Komanda qəbul etmir, depoya və bota "
            "toxunmur.</i>",
        ]
        if me1 and me2 and me1.id == me2.id:
            lines.append("\n⚠️ <b>Hər iki rol eyni hesabdadır!</b> Asistanı başqa hesabla qoş.")
        if note:
            lines.append(f"\n{note}")
        rows = []
        if tg_session.session_source():
            rows.append([InlineKeyboardButton(text="🔄 1️⃣ Yenidən qoşul", callback_data="ubacc:reconnect"),
                         InlineKeyboardButton(text="🔁 1️⃣ Hesabı dəyiş", callback_data="ubacc:login")])
            rows.append([InlineKeyboardButton(text="🚪 1️⃣ Hesabdan çıx", callback_data="ubacc:logout")])
        else:
            rows.append([InlineKeyboardButton(text="🔑 1️⃣ Əsas userbotu qoş", callback_data="ubacc:login")])
        rows.append([InlineKeyboardButton(text="🎙 2️⃣ Canlı yayım asistanı →", callback_data="va:panel")])
        rows.append([InlineKeyboardButton(text="🔄 Yenilə", callback_data="ubacc:panel"),
                     InlineKeyboardButton(text="⬅️ Sistem", callback_data="menu:sys")])
        return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)

    context.userbots_hub_view = hub_view

    # ── 🔑 1️⃣ giriş ──
    async def on_done(session_str, me, client):
        va = getattr(context, "voice_assistant", None)
        if va is not None and va.me and va.me.id == me.id:
            with contextlib.suppress(Exception):
                await client.log_out()
            return hub_view("❌ <b>Bu hesab artıq 2️⃣ canlı yayım asistanıdır.</b> Əsas userbot üçün başqa "
                            "hesab qoş.")
        old = getattr(context, "telethon_me", None)
        new_me = await tg_session.swap(context, session_str)
        if not new_me:
            return hub_view("⚠️ Sessiya saxlandı, amma qoşulma alınmadı — 🔄 ilə yenidən sına")
        note = f"✅ <b>1️⃣ Əsas userbot qoşuldu:</b> {who(new_me)}"
        if old and old.id != new_me.id:
            note += f"\n<i>Əvvəlki hesab ({escape(old.first_name or '')}) əvəz olundu.</i>"
        return hub_view(note)

    flow = tg_login.LoginFlow(
        "main", context, "1️⃣ Əsas userbot hesabını qoş",
        "Bu hesab tam səlahiyyətli olacaq: depo skanı, Telegram mənbələri, .menu komandaları.",
        on_done, "ubacc:cancel")

    async def show(cb, text, kb):
        with contextlib.suppress(Exception):
            await cb.message.edit_text(text, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True)

    @dp.callback_query(F.data.startswith("ubacc:"))
    async def ubacc_cb(cb: types.CallbackQuery):
        if not is_creator(cb.from_user.id):
            await cb.answer("⛔ Yalnız bot sahibi", show_alert=True)
            return
        act = cb.data.split(":", 1)[1]
        note = ""
        if act == "login":
            try:
                text, kb = await flow.begin(cb.message.chat.id, cb.message.message_id)
            except Exception as e:
                await cb.answer(str(e)[:190], show_alert=True)
                return
            await cb.answer()
            await show(cb, text, kb)
            return
        if act == "cancel":
            await flow.cancel()
            await cb.answer("❌ Ləğv edildi")
        elif act == "reconnect":
            await cb.answer("🔄 Qoşulur...")
            me = await tg_session.reconnect(context)
            note = f"✅ Qoşuldu: {who(me)}" if me else "⚠️ Qoşulma alınmadı — logda səbəbə bax"
        elif act == "logout":
            await cb.answer()
            await show(cb, "🚪 <b>1️⃣ Əsas userbotdan çıxılsın?</b>\n\nTelegram-da sessiya bağlanır, depo skanı, "
                           "Telegram mənbələri və userbot komandaları hesab yenidən qoşulana qədər işləməyəcək.",
                       InlineKeyboardMarkup(inline_keyboard=[[
                           InlineKeyboardButton(text="✅ Bəli, çıx", callback_data="ubacc:logoutok"),
                           InlineKeyboardButton(text="↩️ Xeyr", callback_data="ubacc:panel")]]))
            return
        elif act == "logoutok":
            await cb.answer("🚪 Çıxılır...")
            err = await tg_session.logout(context)
            note = "🚪 1️⃣ Hesabdan çıxıldı" + (f" <i>({escape(err)})</i>" if err else "")
        else:
            await cb.answer()
        await show(cb, *hub_view(note))

    @dp.message(F.chat.type == "private", F.text, F.from_user.func(lambda u: flow.waiting(u.id)))
    async def ubacc_login_input(message: types.Message):
        await flow.handle(message)


async def teardown(context):
    task = getattr(context, "telethon_task", None)
    if task and not task.done():
        task.cancel()
    client = getattr(context, "telethon_client", None)
    if client is not None:
        if client.is_connected():
            await client.disconnect()
            logger.info("🛑 Telethon userbot bağlantısı kəsildi.")
        context.telethon_client = None          # reload-da yeni client yaradılsın, handler-lər ona köçsün
