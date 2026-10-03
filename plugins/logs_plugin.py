"""
📜 Bot logları — kanala avtomatik göndərmə (yalnız creator).

/menu → 🖥 Sistem → 📜 Loglar:
  • 📢 Kanal: logların gedəcəyi kanal / qrup (@username, -100... ID və ya oradan yönləndirilmiş mesaj)
  • ⏱ Interval: 15 dəq · 1 saat · 6 saat · 12 saat · 24 saat · söndür
  • 📊 Səviyyə: hamısı və ya yalnız ⚠️ xəbərdarlıq + ❌ xəta
  • 📤 İndi göndər

Hər göndərişdə: .log faylı (son göndərişdən bəri) + qısa xülasə (INFO / WARNING / ERROR sayı, son xətalar).
Bot kanalda "Mesaj göndərmək" icazəsi olan admin olmalıdır.
"""
import asyncio
import logging
import os
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from html import escape

from aiogram import F, types
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup

from core.database import get_db

logger = logging.getLogger(__name__)

# menu_plugin-dən SONRA yüklənsin (onun "creator mətn yazır" yoxlamasına qoşulur)
PRIORITY = 70

INTERVALS = [(15, "15 dəq"), (60, "1 saat"), (360, "6 saat"), (720, "12 saat"), (1440, "24 saat")]
MAX_LINES = 30000                     # yaddaşda saxlanan maksimum sətir
FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def get_tz():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(os.getenv("BOT_TZ", "Asia/Baku"))
    except Exception:
        return timezone(timedelta(hours=4))


def fmt_ts(ts) -> str:
    return datetime.fromtimestamp(ts, get_tz()).strftime("%d.%m %H:%M") if ts else "—"


class BufferHandler(logging.Handler):
    """Bütün logları son göndərişə qədər yaddaşda saxlayır."""

    def __init__(self):
        super().__init__(level=logging.INFO)
        self.lines = deque(maxlen=MAX_LINES)          # (səviyyə, sətir)
        self.since = time.time()
        self.dropped = 0
        self.setFormatter(logging.Formatter(FORMAT, "%Y-%m-%d %H:%M:%S"))

    def emit(self, record):
        try:
            if len(self.lines) == self.lines.maxlen:
                self.dropped += 1
            line = self.format(record)
            self.lines.append((record.levelno, line))
        except Exception:
            pass

    def take(self, min_level: int):
        """Buferi boşaldır → (sətirlər, sayğaclar, başlanğıc vaxtı)."""
        items = list(self.lines)
        self.lines.clear()
        since, self.since = self.since, time.time()
        dropped, self.dropped = self.dropped, 0
        counts = {"INFO": 0, "WARNING": 0, "ERROR": 0}
        errors = []
        out = []
        for lvl, line in items:
            if lvl >= logging.ERROR:
                counts["ERROR"] += 1
                errors.append(line)
            elif lvl >= logging.WARNING:
                counts["WARNING"] += 1
            else:
                counts["INFO"] += 1
            if lvl >= min_level:
                out.append(line)
        return out, counts, errors, since, dropped


def setup(context):
    dp = context.dp
    bot = context.bot

    # Eyni handler iki dəfə qoşulmasın (plugin_manager ilə yenidən yükləmədə)
    root = logging.getLogger()
    old = getattr(context, "logs_buffer_handler", None)
    if old:
        root.removeHandler(old)
    handler = BufferHandler()
    root.addHandler(handler)
    context.logs_buffer_handler = handler

    state = {"await_chat": False, "panel": None, "task": None, "stop": False}

    def is_creator(uid) -> bool:
        return bool(context.creator_id) and uid == context.creator_id

    # ── ayarlar ──
    def get(key, default=None):
        try:
            return get_db().get_setting(f"logs:{key}", default)
        except Exception:
            return default

    def put(key, value):
        get_db().set_setting(f"logs:{key}", value)

    def interval() -> int:
        v = get("interval")
        return int(v) if v and str(v).isdigit() else 0

    def min_level() -> int:
        return logging.WARNING if get("level") == "warn" else logging.INFO

    # music_plugin creator-un yazdığı kanal adını mahnı axtarışı saymasın
    prev_waiting = getattr(context, "menu_waiting_text", None)

    def waiting_text(uid: int) -> bool:
        return (state["await_chat"] and is_creator(uid)) or bool(prev_waiting and prev_waiting(uid))

    context.menu_waiting_text = waiting_text

    # ── göndərmə ──
    async def send_logs(manual: bool = False) -> str:
        chat_id = get("chat")
        if not chat_id:
            return "❌ Əvvəlcə kanal təyin et"
        lines, counts, errors, since, dropped = handler.take(min_level())
        now = time.time()
        put("last_sent", str(int(now)))
        if not lines and not manual:
            return "boş"
        caption = (
            f"📜 <b>Bot logları</b>\n🕒 {fmt_ts(since)} → {fmt_ts(now)}\n\n"
            f"ℹ️ INFO: <b>{counts['INFO']}</b> · ⚠️ WARNING: <b>{counts['WARNING']}</b> · "
            f"❌ ERROR: <b>{counts['ERROR']}</b>"
            + (f"\n<i>⏭ yaddaş limitinə görə {dropped} köhnə sətir atıldı</i>" if dropped else "")
        )
        if errors:
            last = "\n".join(escape(e[-150:]) for e in errors[-3:])
            caption += f"\n\n<b>Son xətalar:</b>\n<code>{last}</code>"
        caption = caption[:1020]
        try:
            if lines:
                stamp = datetime.fromtimestamp(now, get_tz()).strftime("%Y%m%d_%H%M")
                data = ("\n".join(lines) + "\n").encode("utf-8", errors="replace")
                await bot.send_document(int(chat_id) if str(chat_id).lstrip("-").isdigit() else chat_id,
                                        BufferedInputFile(data, filename=f"bot_logs_{stamp}.log"),
                                        caption=caption, parse_mode="HTML", disable_notification=True)
            else:
                await bot.send_message(int(chat_id) if str(chat_id).lstrip("-").isdigit() else chat_id,
                                       caption + "\n\n<i>Bu müddətdə yeni log yoxdur.</i>", parse_mode="HTML",
                                       disable_notification=True)
            return f"✅ Göndərildi ({len(lines)} sətir)"
        except Exception as e:
            # alınmadısa sətirləri geri qaytar ki itməsin
            for line in lines:
                handler.lines.appendleft((logging.INFO, line))
            return f"❌ Göndərilmədi: {str(e)[:200]}"

    async def scheduler():
        await asyncio.sleep(20)
        while not state["stop"]:
            try:
                iv = interval()
                last = int(get("last_sent") or 0)
                if iv and get("chat") and time.time() - last >= iv * 60:
                    result = await send_logs()
                    if result.startswith("❌"):
                        print(f"[logs_plugin] {result}")      # loga yazsaq, yenidən buferə düşər
            except Exception as e:
                print(f"[logs_plugin] planlayıcı xətası: {e}")
            await asyncio.sleep(30)

    state["task"] = asyncio.create_task(scheduler())
    context.logs_state = state

    # ── panel ──
    def panel(note: str = ""):
        chat_title = get("chat_title") or get("chat")
        iv = interval()
        iv_label = next((l for m, l in INTERVALS if m == iv), f"{iv} dəq") if iv else "❌ söndürülüb"
        last = int(get("last_sent") or 0)
        nxt = ""
        if iv and get("chat"):
            nxt = f" · növbəti: {fmt_ts(max(last + iv * 60, time.time()))}"
        lvl_warn = get("level") == "warn"
        text = (
            "📜 <b>Bot logları</b>\n━━━━━━━━━━━━━━━━━━\n"
            f"📢 Kanal: <b>{escape(str(chat_title)) if chat_title else '— təyin edilməyib'}</b>\n"
            f"⏱ Interval: <b>{iv_label}</b>{nxt}\n"
            f"📊 Səviyyə: <b>{'⚠️ xəbərdarlıq + ❌ xəta' if lvl_warn else 'hamısı (INFO+)'}</b>\n"
            f"📦 Buferdə: <b>{len(handler.lines)}</b> sətir · son göndəriş: {fmt_ts(last)}\n"
            "━━━━━━━━━━━━━━━━━━"
            + (f"\n\n{note}" if note else "")
        )
        iv_btns = [InlineKeyboardButton(text=("• " if m == iv else "") + l, callback_data=f"lg:iv:{m}")
                   for m, l in INTERVALS]
        rows = [
            [InlineKeyboardButton(text="📢 Kanalı təyin et", callback_data="lg:setchat"),
             InlineKeyboardButton(text="📤 İndi göndər", callback_data="lg:send")],
            iv_btns[:3], iv_btns[3:] + [InlineKeyboardButton(text=("• " if not iv else "") + "❌ Söndür",
                                                             callback_data="lg:iv:0")],
            [InlineKeyboardButton(text=f"📊 Səviyyə: {'⚠️+❌' if lvl_warn else 'Hamısı'}", callback_data="lg:level"),
             InlineKeyboardButton(text="🔄 Yenilə", callback_data="lg:panel")],
            [InlineKeyboardButton(text="⬅️ Bot idarəsi", callback_data="menu:dev"),
             InlineKeyboardButton(text="❌ Bağla", callback_data="lg:close")],
        ]
        return text, InlineKeyboardMarkup(inline_keyboard=rows)

    async def show(cb_or_ids, text, kb):
        try:
            if isinstance(cb_or_ids, tuple):
                await bot.edit_message_text(chat_id=cb_or_ids[0], message_id=cb_or_ids[1], text=text,
                                            parse_mode="HTML", reply_markup=kb)
            else:
                await cb_or_ids.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        except Exception as e:
            if "not modified" not in str(e):
                logger.debug(f"Log paneli yenilənmədi: {e}")

    @dp.message(Command("logs"))
    async def logs_cmd(message: types.Message):
        if message.from_user and is_creator(message.from_user.id):
            text, kb = panel()
            await message.answer(text, parse_mode="HTML", reply_markup=kb)

    @dp.callback_query(F.data.startswith("lg:"))
    async def logs_callback(cb: types.CallbackQuery):
        if not is_creator(cb.from_user.id):
            await cb.answer("⛔ İcazə yoxdur", show_alert=True)
            return
        parts = cb.data.split(":")
        action = parts[1]
        if action != "setchat":
            state["await_chat"] = False
        note = ""
        if action == "close":
            await cb.answer()
            try:
                await cb.message.delete()
            except Exception:
                pass
            return
        if action == "setchat":
            state["await_chat"] = True
            state["panel"] = (cb.message.chat.id, cb.message.message_id)
            await cb.answer()
            await show(cb, "📢 <b>Log kanalı</b>\n\nBunlardan birini göndər:\n"
                           "• kanalın <code>@username</code>-i\n• rəqəmli ID: <code>-100...</code>\n"
                           "• və ya həmin kanaldan istənilən mesajı bura <b>yönləndir</b>\n\n"
                           "<i>Bot kanalda mesaj göndərə bilən admin olmalıdır.</i>",
                       InlineKeyboardMarkup(inline_keyboard=[[
                           InlineKeyboardButton(text="⬅️ Geri", callback_data="lg:panel")]]))
            return
        if action == "iv" and len(parts) > 2 and parts[2].isdigit():
            put("interval", parts[2])
            if parts[2] != "0":
                put("last_sent", str(int(time.time())))     # sayma indidən başlasın
            await cb.answer("❌ Avtomatik göndəriş söndürüldü" if parts[2] == "0" else "⏱ Interval yadda saxlandı")
        elif action == "level":
            put("level", "all" if get("level") == "warn" else "warn")
            await cb.answer("📊 Səviyyə dəyişdi")
        elif action == "send":
            await cb.answer("Göndərilir...")
            note = await send_logs(manual=True)
        else:
            await cb.answer()
        text, kb = panel(note)
        await show(cb, text, kb)

    async def waiting_chat(message: types.Message) -> bool:
        return state["await_chat"] and bool(message.from_user) and is_creator(message.from_user.id)

    @dp.message(F.chat.type == "private", waiting_chat)
    async def set_log_chat(message: types.Message):
        target = None
        origin = getattr(message, "forward_origin", None)
        fchat = getattr(origin, "chat", None) or getattr(message, "forward_from_chat", None)
        if fchat:
            target = fchat.id
        elif message.text:
            t = message.text.strip()
            if t.lstrip("-").isdigit():
                target = int(t)
            elif t.startswith("@") or "t.me/" in t:
                target = "@" + t.rstrip("/").split("/")[-1].lstrip("@")
        if target is None:
            await message.reply("❌ @username, -100... ID göndər və ya kanaldan mesaj yönləndir.")
            return
        try:
            chat = await bot.get_chat(target)
            await bot.send_message(chat.id, "✅ <b>Bot logları bu çata göndəriləcək.</b>", parse_mode="HTML",
                                   disable_notification=True)
        except Exception as e:
            await message.reply(f"❌ Bu çata yaza bilmirəm: <code>{escape(str(e))[:200]}</code>\n"
                                "<i>Botu kanala admin kimi əlavə et və yenidən cəhd et.</i>", parse_mode="HTML")
            return
        put("chat", str(chat.id))
        put("chat_title", getattr(chat, "title", None) or str(chat.id))
        state["await_chat"] = False
        try:
            await message.delete()
        except Exception:
            pass
        text, kb = panel(f"✅ Kanal təyin edildi: <b>{escape(getattr(chat, 'title', '') or str(chat.id))}</b>")
        if state["panel"]:
            await show(state["panel"], text, kb)
        else:
            await message.answer(text, parse_mode="HTML", reply_markup=kb)

    logger.info("✅ Logs plugin yükləndi (/logs)")


async def teardown(context):
    st = getattr(context, "logs_state", None)
    if st:
        st["stop"] = True
        if st.get("task"):
            st["task"].cancel()
    h = getattr(context, "logs_buffer_handler", None)
    if h:
        logging.getLogger().removeHandler(h)
