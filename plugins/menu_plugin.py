"""
/menu — yalnız creator üçün admin paneli (şəxsi çatda).

  🚀 Speedtest        — serverin internet sürəti
  🖥 Fastfetch        — server məlumatları
  📊 Statistika       — ümumi / qrafik / top, 👥 istifadəçilər (+ CSV) və 💬 qruplar / kanallar
  📢 Broadcast        — şəxsi / qruplar / kanallar / hamısı üçün mətn göndərmə
  💎 Premium          — ID və ya @username ilə premium vermə / alma, siyahı
                        ⭐ Ulduz satışı: plan qiymətləri, ödənişlər, geri qaytarma
  🎨 Mesaj və media   — /start, qrup salamı, bələdçilər və premium mesajının mətni + şəkli / GIF-i / videosu

/premium — istənilən istifadəçi öz statusunu (Free / Premium) görür.

Məlumatlar SQLite bazasında saxlanır (core/database.py).
"""
import asyncio
import csv
import io
import json
import logging
import os
import re
import shutil
import time
from datetime import datetime, timedelta, timezone
from html import escape

from aiogram import BaseMiddleware, types, F
from aiogram.exceptions import (
    TelegramRetryAfter, TelegramForbiddenError, TelegramBadRequest, TelegramMigrateToChat,
)
from aiogram.filters import Command
from aiogram.types import (
    BufferedInputFile, InlineKeyboardMarkup, InlineKeyboardButton,
    Message, CallbackQuery, ChatMemberUpdated,
)
from core.database import get_db, creator_caption_enabled, set_creator_caption, is_premium
from core.stars import PLANS, PLAN_MAP, MIN_STARS, MAX_STARS, get_price, set_price, per_month, buy_keyboard
from core import audio_cache
from core.welcome import (
    SLOTS, get_media, set_media, media_label, send_media,
    TEXT_SLOTS, TEXT_LIMIT, CAPTION_LIMIT, get_text_template, set_text_template,
)

logger = logging.getLogger(__name__)

PAGE_SIZE = 10
PREMIUM_PAGE_SIZE = 8
PREMIUM_DURATIONS = [("7 gün", 7), ("30 gün", 30), ("90 gün", 90), ("1 il", 365), ("♾ Ömürlük", 0)]
TRACK_THROTTLE = 60          # eyni istifadəçini/qrupu bazaya ən çox 60 saniyədən bir yaz
BROADCAST_DELAY = 0.05       # ~20 mesaj/san (Telegram limiti ~30/san)
TARGET_LABELS = {
    "users": "👤 Şəxsi",
    "groups": "👥 Qruplar",
    "channels": "📣 Kanallar",
    "all": "🌐 Hamısı",
}
ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07")


def get_tz():
    name = os.getenv("BOT_TZ", "Asia/Baku")
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name), name
    except Exception:
        return timezone(timedelta(hours=4)), "UTC+4"


SOURCE_LABELS = {
    "music": "🔎 Axtarış (/music)",
    "inline": "⚡ İnline",
    "mix": "🔀 /mix",
    "batch": "⬇️ Toplu yükləmə",
    "spotify": "🟢 Spotify",
    "playlist": "📃 Playlist",
    "youtube": "▶️ YouTube",
    "editor": "✏️ Redaktor",
}
USER_FILTERS = [
    ("all", "👥 Hamısı"), ("active", "🟢 Aktiv"), ("new", "🆕 Yeni"),
    ("top", "🏆 Ən çox"), ("prem", "💎 Premium"), ("blocked", "🚫 Bloklayan"),
]
USERS_PAGE = 8
CHATS_PAGE = 8
CHAT_FILTERS = [("groups", "👥 Qruplar"), ("channels", "📣 Kanallar"), ("left", "🚪 Çıxılmış")]
CHAT_TYPES = {"group": "👥 Qrup", "supergroup": "👥 Superqrup", "channel": "📣 Kanal"}
BOT_STATUS = {
    "creator": "👑 Sahib", "administrator": "🛡 Admin", "member": "👤 Üzv",
    "restricted": "🔇 Məhdud", "left": "🚪 Çıxıb", "kicked": "⛔ Çıxarılıb",
}


def stats_tabs(view: str) -> list:
    """Statistikanın bütün bölmələrində eyni tab sətirləri."""
    def t(key, label):
        return _btn(("• " if view == key else "") + label, key)
    return [
        [t("stats", "📊 Ümumi"), t("stats_chart", "📈 Qrafik"), t("stats_top", "🏆 Top")],
        [_btn(("• " if view == "users" else "") + "👥 İstifadəçilər", "users:all:0"),
         _btn(("• " if view == "grp" else "") + "💬 Qruplar", "grp:groups:0")],
    ]


def tz_offset_seconds() -> int:
    tz, _ = get_tz()
    off = datetime.now(tz).utcoffset()
    return int(off.total_seconds()) if off else 0


def fmt_dt(ts) -> str:
    if not ts:
        return "—"
    return datetime.fromtimestamp(ts, get_tz()[0]).strftime("%d.%m.%Y %H:%M")


def ago(ts, now=None) -> str:
    """"5 dəq. əvvəl" kimi."""
    if not ts:
        return "heç vaxt"
    d = int((now or time.time()) - ts)
    if d < 60:
        return "indicə"
    if d < 3600:
        return f"{d // 60} dəq. əvvəl"
    if d < 86400:
        return f"{d // 3600} saat əvvəl"
    if d < 30 * 86400:
        return f"{d // 86400} gün əvvəl"
    return fmt_date(ts)


def trend(cur: int, prev: int) -> str:
    if not prev:
        return " 🆕" if cur else ""
    pct = round((cur - prev) * 100 / prev)
    if pct > 0:
        return f" 📈 +{pct}%"
    if pct < 0:
        return f" 📉 {pct}%"
    return " ➖ 0%"


def pct(part: int, whole: int) -> str:
    return f"{round(part * 100 / whole)}%" if whole else "0%"


def bar_chart(labels, values, width=12) -> str:
    top = max(values) if values and max(values) > 0 else 1
    lines = []
    for label, v in zip(labels, values):
        filled = round(v / top * width)
        lines.append(f"{label} {'█' * filled}{'░' * (width - filled)} {v}")
    return "\n".join(lines)


def sparkline(values) -> str:
    blocks = "▁▂▃▄▅▆▇█"
    top = max(values) if values and max(values) > 0 else 1
    return "".join(blocks[min(len(blocks) - 1, round(v / top * (len(blocks) - 1)))] if v else "·" for v in values)


def display_name(u: dict, user_id=None) -> str:
    u = u or {}
    name = " ".join(filter(None, [u.get("first_name"), u.get("last_name")]))
    return name or (f"@{u['username']}" if u.get("username") else str(user_id or u.get("user_id", "?")))


def fmt_date(ts: int) -> str:
    tz, _ = get_tz()
    return datetime.fromtimestamp(ts, tz).strftime("%d.%m.%Y")


# ───────────────────────── Sistem əmrləri ─────────────────────────
async def run_cmd(*cmd, timeout=60):
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise RuntimeError(f"Vaxt bitdi ({timeout} san.)")
    return proc.returncode, out.decode(errors="ignore"), err.decode(errors="ignore")


async def run_speedtest() -> dict:
    """speedtest-cli (python) və ya Ookla CLI ilə ölçür, ortaq formata salır."""
    cli, ookla = shutil.which("speedtest-cli"), shutil.which("speedtest")
    if cli:
        cmd, kind = [cli, "--json"], "python"
    elif ookla:
        _, ver, _ = await run_cmd(ookla, "--version", timeout=15)
        if "ookla" in ver.lower():
            cmd, kind = [ookla, "--accept-license", "--accept-gdpr", "--format=json", "--progress=no"], "ookla"
        else:
            cmd, kind = [ookla, "--json"], "python"
    else:
        raise RuntimeError("speedtest quraşdırılmayıb.\nQuraşdır: sudo pacman -S speedtest-cli")

    rc, out, err = await run_cmd(*cmd, timeout=180)
    if rc != 0:
        msg = (err or out).strip()[-300:]
        if "403" in msg:
            msg += "\n(speedtest-cli köhnəlib — Ookla CLI istifadə edin: ookla-speedtest-bin)"
        raise RuntimeError(msg or f"Çıxış kodu {rc}")

    start = out.find("{")
    data = json.loads(out[start:] if start >= 0 else out)
    if kind == "ookla":
        server = data.get("server", {})
        return {
            "down": data["download"]["bandwidth"] * 8 / 1e6,
            "up": data["upload"]["bandwidth"] * 8 / 1e6,
            "ping": data["ping"]["latency"],
            "server": f"{server.get('name', '?')} ({server.get('location', '')}, {server.get('country', '')})",
            "isp": data.get("isp", "?"),
        }
    server, client = data.get("server", {}), data.get("client", {})
    return {
        "down": data["download"] / 1e6,
        "up": data["upload"] / 1e6,
        "ping": data["ping"],
        "server": f"{server.get('sponsor', '?')} ({server.get('name', '')}, {server.get('country', '')})",
        "isp": client.get("isp", "?"),
    }


async def run_fastfetch() -> str:
    ff = shutil.which("fastfetch")
    if not ff:
        raise RuntimeError("fastfetch quraşdırılmayıb.\nQuraşdır: sudo pacman -S fastfetch")
    rc, out, err = await run_cmd(ff, "--logo", "none", "--pipe", timeout=30)
    if rc != 0 and not out.strip():
        raise RuntimeError((err or f"Çıxış kodu {rc}").strip()[-300:])
    text = ANSI_RE.sub("", out).strip()
    return text[:3500]


# ───────────────────────── İstifadəçi izləmə ─────────────────────────
class UserTrackerMiddleware(BaseMiddleware):
    """
    Şəxsi çatda yazan/düymə basan istifadəçiləri, həmçinin botun olduğu qrup və kanalları bazaya yazır.
    Bloklama, qrupdan çıxarılma və kanaldan silinmə də izlənir.
    """

    def __init__(self):
        self._last = {}

    async def __call__(self, handler, event, data):
        try:
            await self._track(event)
        except Exception as e:
            logger.warning(f"İstifadəçi izləmə xətası: {e}")
        return await handler(event, data)

    async def _track(self, event):
        db = get_db()

        if isinstance(event, ChatMemberUpdated):
            status = event.new_chat_member.status
            if event.chat.type in ("group", "supergroup", "channel"):
                active = status in ("member", "administrator", "creator", "restricted")
                await self._save_chat(event.chat, active=active, force=True)
                return
            if event.chat.type != "private" or not event.from_user or event.from_user.is_bot:
                return
            if status == "kicked":
                await asyncio.to_thread(db.set_blocked, event.from_user.id, True)
            elif status == "member":
                await self._save(event.from_user, force=True)
            return

        if isinstance(event, Message):
            chat = event.chat
        elif isinstance(event, CallbackQuery) and event.message:
            chat = event.message.chat
        else:
            return
        if chat.type in ("group", "supergroup", "channel"):
            await self._save_chat(chat)
            return
        user = event.from_user
        if chat.type != "private" or not user or user.is_bot:
            return
        await self._save(user)

    async def _save(self, user, force=False):
        now = time.time()
        if not force and now - self._last.get(user.id, 0) < TRACK_THROTTLE:
            return
        self._last[user.id] = now
        await asyncio.to_thread(
            get_db().upsert_user, user.id, user.username, user.first_name, user.last_name
        )

    async def _save_chat(self, chat, active=True, force=False):
        key = f"chat:{chat.id}"
        now = time.time()
        if not force and now - self._last.get(key, 0) < TRACK_THROTTLE:
            return
        self._last[key] = now
        await asyncio.to_thread(
            get_db().upsert_chat, chat.id, chat.type, getattr(chat, "title", None),
            getattr(chat, "username", None), active,
        )


# ───────────────────────── Menyu ─────────────────────────
def _btn(text, data):
    return InlineKeyboardButton(text=text, callback_data=f"menu:{data}")


def main_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn("🚀 Speedtest", "speed"), _btn("🖥 Fastfetch", "fetch")],
        [_btn("📊 Statistika", "stats"),
         InlineKeyboardButton(text="🌿 GitHub", callback_data="ghf:open")],   # update_notifier_plugin
        [_btn("📢 Broadcast", "bc"), _btn("💎 Premium", "prm:0")],
        [_btn(f"💬 Caption (mənim): {'✅ Açıq' if creator_caption_enabled() else '❌ Bağlı'}", "cap")],
        [_btn("🎨 Mesaj və media", "ms")],
        [_btn("✖️ Bağla", "close")],
    ])


def nav_row(back: str = "main") -> list:
    """
    Hər pəncərənin altındakı naviqasiya sətri:
      ⬅️ Geri     — bir addım əvvəlki pəncərə (yarımçıq əməliyyat ləğv olunur)
      🏠 Menyu    — əsas menyu (yalnız dərin pəncərələrdə)
      ❌ Ləğv et  — hər şeyi ləğv edib menyunu bağlayır
    """
    row = [_btn("⬅️ Geri", back)]
    if back != "main":
        row.append(_btn("🏠 Menyu", "main"))
    row.append(_btn("❌ Ləğv et", "cancel"))
    return row


def sub_kb(refresh=None):
    rows = []
    if refresh:
        rows.append([_btn("🔄 Yenilə", refresh)])
    rows.append(nav_row("main"))
    return InlineKeyboardMarkup(inline_keyboard=rows)


MAIN_TEXT = "🛠 <b>Admin menyusu</b>\n\n<i>Nəyi yoxlamaq istəyirsən?</i>"


# ───────────────────────── Premium köməkçiləri ─────────────────────────
def fmt_until(until) -> str:
    return "♾ Ömürlük" if until is None else f"{fmt_date(until)}-dək"


def user_label(u: dict, user_id: int) -> str:
    """HTML: ad (link) · @username · <code>id</code>"""
    name = " ".join(filter(None, [(u or {}).get("first_name"), (u or {}).get("last_name")])) or "—"
    uname = f" · @{escape(u['username'])}" if u and u.get("username") else ""
    return f'<a href="tg://user?id={user_id}">{escape(name)}</a>{uname} · <code>{user_id}</code>'


def short_name(u: dict, user_id: int) -> str:
    """Düymə üçün qısa ad"""
    if u and u.get("username"):
        return f"@{u['username']}"[:24]
    name = " ".join(filter(None, [(u or {}).get("first_name"), (u or {}).get("last_name")]))
    return (name or str(user_id))[:24]


def parse_user_ref(text: str):
    """
    "123456789" → ("id", 123456789)
    "@name" / "name" / "t.me/name" → ("username", "name")
    """
    t = (text or "").strip()
    t = re.sub(r"^(?:https?://)?(?:t\.me|telegram\.me)/", "", t, flags=re.I).strip("/ ")
    if re.fullmatch(r"\d{1,20}", t):   # username rəqəmlə başlaya bilməz, deməli bu ID-dir
        return "id", int(t)
    t = t.lstrip("@")
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{3,31}", t):
        return "username", t
    return None, None


# ───────────────────────── Broadcast ─────────────────────────
def target_counts(db) -> dict:
    uc, cc = db.user_counts(), db.chat_counts()
    c = {"users": uc["active"], "groups": cc["groups"], "channels": cc["channels"]}
    c["all"] = c["users"] + c["groups"] + c["channels"]
    return c


def bc_target_kb(counts: dict) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn(f"{TARGET_LABELS['users']} ({counts['users']})", "bc_t:users"),
         _btn(f"{TARGET_LABELS['groups']} ({counts['groups']})", "bc_t:groups")],
        [_btn(f"{TARGET_LABELS['channels']} ({counts['channels']})", "bc_t:channels"),
         _btn(f"{TARGET_LABELS['all']} ({counts['all']})", "bc_t:all")],
        nav_row("main"),
    ])


async def send_one(bot, db, kind: str, chat_id: int, text: str) -> str:
    """Bir çata göndərir → "ok" | "removed" (bloklayıb/çıxarılıb) | "failed"."""
    for _ in range(4):
        try:
            await bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")
            return "ok"
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after + 1)
        except TelegramMigrateToChat as e:
            await asyncio.to_thread(db.migrate_chat, chat_id, e.migrate_to_chat_id)
            chat_id = e.migrate_to_chat_id
        except TelegramForbiddenError:
            # bot bloklanıb / qrupdan çıxarılıb / hesab silinib
            if kind == "user":
                await asyncio.to_thread(db.set_blocked, chat_id, True)
            else:
                await asyncio.to_thread(db.set_chat_active, chat_id, False)
            return "removed"
        except TelegramBadRequest as e:
            msg = str(e).lower()
            if kind != "user" and ("chat not found" in msg or "not a member" in msg or "kicked" in msg):
                await asyncio.to_thread(db.set_chat_active, chat_id, False)
                return "removed"
            logger.warning(f"Broadcast xətası ({chat_id}): {e}")
            return "failed"
        except Exception as e:
            logger.warning(f"Broadcast xətası ({chat_id}): {e}")
            return "failed"
    return "failed"


def setup(context):
    dp = context.dp
    speed_lock = asyncio.Lock()
    # Broadcast vəziyyəti (yalnız creator istifadə edir). music_plugin də oxuyur ki,
    # broadcast mətni mahnı axtarışı kimi qəbul edilməsin.
    bc = {"stage": None}
    context.broadcast_state = bc
    # Premium: creator-dan ID/@username gözlənilir
    prm = {"stage": None}
    wm = {"stage": None}
    tx = {"stage": None}      # Mesajlar: creator-dan yeni mətn gözlənilir
    stp = {"stage": None}      # ⭐ qiymət: creator-dan rəqəm gözlənilir
    us = {"stage": None}      # İstifadəçi axtarışı: sorğu gözlənilir      # Welcome media: creator-dan şəkil/GIF/video gözlənilir

    def menu_waiting_text(user_id: int) -> bool:
        return is_creator(user_id) and (
            bc.get("stage") == "await_text" or prm.get("stage") == "await_user"
            or wm.get("stage") == "await_media" or tx.get("stage") == "await_text"
            or us.get("stage") == "await_query" or stp.get("stage") == "await_price"
        )

    context.menu_waiting_text = menu_waiting_text

    get_db()  # bazanı əvvəlcədən yarat
    tracker = UserTrackerMiddleware()
    dp.message.outer_middleware(tracker)
    dp.callback_query.outer_middleware(tracker)
    dp.my_chat_member.outer_middleware(tracker)
    dp.channel_post.outer_middleware(tracker)

    def is_creator(user_id: int) -> bool:
        return bool(context.creator_id) and user_id == context.creator_id

    async def edit(cb: CallbackQuery, text: str, kb=None):
        try:
            await cb.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        except Exception as e:
            if "not modified" not in str(e):
                logger.warning(f"Menyu mesajı yenilənmədi: {e}")

    def bc_reset():
        bc.clear()
        bc["stage"] = None

    async def delete_preview():
        if bc.get("preview_id"):
            try:
                await context.bot.delete_message(bc["chat_id"], bc["preview_id"])
            except Exception:
                pass
            bc["preview_id"] = None

    async def edit_panel(text: str, kb=None):
        try:
            await context.bot.edit_message_text(
                chat_id=bc["chat_id"], message_id=bc["panel_id"],
                text=text, parse_mode="HTML", reply_markup=kb,
            )
        except Exception as e:
            if "not modified" not in str(e):
                logger.warning(f"Broadcast paneli yenilənmədi: {e}")

    async def waiting_broadcast_text(message: Message) -> bool:
        return bc.get("stage") == "await_text" and is_creator(message.from_user.id)

    # ── Broadcast mətni ──
    @dp.message(F.chat.type == "private", F.text, ~F.text.startswith("/"), waiting_broadcast_text)
    async def broadcast_text(message: Message):
        text = message.html_text  # qalın/kursiv/link formatı saxlanılır
        count = len(await asyncio.to_thread(get_db().broadcast_targets, bc["target"]))
        bc["text"] = text
        bc["stage"] = "confirm"
        await delete_preview()
        try:
            preview = await message.answer(
                text, parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [_btn(f"✅ Göndər ({count})", "bc_send")],
                    [_btn("✏️ Yenidən yaz", "bc_edit"), _btn("❌ Ləğv et", "bc_cancel")],
                ]),
            )
        except Exception as e:
            bc["stage"] = "await_text"
            await message.reply(f"❌ Mesaj göndərilə bilmir: <code>{escape(str(e))[:300]}</code>\nYenidən yazın:",
                                parse_mode="HTML")
            return
        bc["preview_id"] = preview.message_id
        await edit_panel(
            f"📢 <b>Broadcast</b> — {TARGET_LABELS[bc['target']]} (<b>{count}</b>)\n\n"
            "👇 <i>Aşağıda önizləmə var. Göndərilən mesaj məhz belə görünəcək.</i>"
        )

    @dp.message(F.chat.type == "private", ~F.text, waiting_broadcast_text)
    async def broadcast_not_text(message: Message):
        await message.reply("✍️ Yalnız mətn göndərilə bilər. Mesajı mətn kimi yazın.")

    async def run_broadcast():
        db = get_db()
        targets = await asyncio.to_thread(db.broadcast_targets, bc["target"])
        total, text = len(targets), bc["text"]
        res = {"ok": 0, "removed": 0, "failed": 0}
        started = last_edit = time.time()
        stop_kb = InlineKeyboardMarkup(inline_keyboard=[[_btn("⏹ Dayandır", "bc_stop")]])
        header = f"📢 <b>Broadcast</b> — {TARGET_LABELS[bc['target']]}\n\n"

        await edit_panel(header + f"⏳ Göndərilir... 0/{total}", stop_kb)
        done = 0
        for kind, chat_id in targets:
            if bc.get("stop"):
                break
            res[await send_one(context.bot, db, kind, chat_id, text)] += 1
            done += 1
            if time.time() - last_edit > 3:
                last_edit = time.time()
                await edit_panel(
                    header + f"⏳ Göndərilir... {done}/{total}\n"
                    f"✅ {res['ok']} · 🚫 {res['removed']} · ❌ {res['failed']}", stop_kb,
                )
            await asyncio.sleep(BROADCAST_DELAY)

        took = int(time.time() - started)
        status = "⏹ <b>Dayandırıldı</b>" if bc.get("stop") else "✅ <b>Tamamlandı</b>"
        await edit_panel(
            header + f"{status} ({done}/{total}, {took} san.)\n\n"
            f"✅ Çatdı: <b>{res['ok']}</b>\n"
            f"🚫 Bloklayıb / çıxarıb: <b>{res['removed']}</b>\n"
            f"❌ Xəta: <b>{res['failed']}</b>",
            InlineKeyboardMarkup(inline_keyboard=[nav_row("bc")]),
        )
        logger.info(f"Broadcast bitdi ({bc['target']}): {res}, {done}/{total}")

    async def broadcast_task():
        try:
            await run_broadcast()
        except Exception as e:
            logger.error(f"Broadcast xətası: {e}", exc_info=True)
            await edit_panel(f"❌ <b>Broadcast xətası:</b> <code>{escape(str(e))[:300]}</code>",
                             InlineKeyboardMarkup(inline_keyboard=[nav_row("bc")]))
        finally:
            bc_reset()

    # ═════════════ Premium ═════════════
    async def premium_panel(page: int = 0):
        db = get_db()
        total = await asyncio.to_thread(db.premium_count)
        pages = max(1, -(-total // PREMIUM_PAGE_SIZE))
        page = min(max(page, 0), pages - 1)
        rows = await asyncio.to_thread(db.premium_page, page * PREMIUM_PAGE_SIZE, PREMIUM_PAGE_SIZE)

        if rows:
            lines = [
                f"{i}. 💎 {user_label(r, r['user_id'])} · {fmt_until(r['until'])}"
                for i, r in enumerate(rows, start=page * PREMIUM_PAGE_SIZE + 1)
            ]
            body = "\n".join(lines) + "\n\n<i>Premiumu almaq üçün aşağıda istifadəçiyə bas.</i>"
        else:
            body = "<i>Hələ premium istifadəçi yoxdur.</i>"
        text = f"💎 <b>Premium istifadəçilər</b> — <b>{total}</b>\n<i>Səhifə {page + 1}/{pages}</i>\n\n{body}"

        kb = [[_btn(f"❌ {short_name(r, r['user_id'])}", f"prm_rm:{r['user_id']}:{page}")] for r in rows]
        if pages > 1:
            nav = []
            if page > 0:
                nav.append(_btn("◀️", f"prm:{page - 1}"))
            nav.append(_btn(f"{page + 1}/{pages}", "noop"))
            if page < pages - 1:
                nav.append(_btn("▶️", f"prm:{page + 1}"))
            kb.append(nav)
        kb.append([_btn("➕ Premium ver", "prm_add"), _btn("⭐ Ulduz satışı", "st")])
        kb.append(nav_row("main"))
        return text, InlineKeyboardMarkup(inline_keyboard=kb)

    async def prm_edit_panel(text: str, kb=None):
        try:
            await context.bot.edit_message_text(
                chat_id=prm["chat_id"], message_id=prm["panel_id"],
                text=text, parse_mode="HTML", reply_markup=kb,
            )
        except Exception as e:
            if "not modified" not in str(e):
                logger.warning(f"Premium paneli yenilənmədi: {e}")

    async def duration_panel(user_id: int, u: dict, back: str = "prm:0"):
        cur = await asyncio.to_thread(get_db().get_premium, user_id)
        status = f"💎 Premium ({fmt_until(cur['until'])})" if cur else "🆓 Free"
        note = "" if u else "\n<i>⚠️ Bu ID botda qeydiyyatda deyil (heç /start etməyib).</i>"
        extend = "\n<i>Aktiv premium var — seçilən müddət üstünə əlavə olunacaq.</i>" if cur and cur["until"] else ""
        text = (
            "💎 <b>Premium ver</b>\n\n"
            f"👤 {user_label(u, user_id)}\n"
            f"📌 Hazırkı status: {status}{note}{extend}\n\n"
            "<b>Müddəti seçin:</b>"
        )
        btns = [_btn(label, f"prm_g:{user_id}:{days}") for label, days in PREMIUM_DURATIONS]
        kb = InlineKeyboardMarkup(inline_keyboard=[
            btns[:2], btns[2:4], btns[4:],
            nav_row(back),
        ])
        return text, kb

    async def waiting_premium_user(message: Message) -> bool:
        return prm.get("stage") == "await_user" and is_creator(message.from_user.id)

    @dp.message(F.chat.type == "private", F.text, ~F.text.startswith("/"), waiting_premium_user)
    async def premium_user_input(message: Message):
        kind, value = parse_user_ref(message.text)
        db = get_db()
        back_kb = InlineKeyboardMarkup(inline_keyboard=[nav_row("prm:0")])
        try:
            await message.delete()
        except Exception:
            pass

        if kind == "id":
            user_id, u = value, await asyncio.to_thread(db.get_user, value)
        elif kind == "username":
            u = await asyncio.to_thread(db.find_user_by_username, value)
            if not u:
                await prm_edit_panel(
                    f"💎 <b>Premium ver</b>\n\n❌ <b>@{escape(value)}</b> tapılmadı.\n\n"
                    "<i>Username ilə vermək üçün istifadəçi əvvəlcə botu /start etməlidir "
                    "(Telegram botlara başqasının username-ini ID-yə çevirməyə icazə vermir).\n"
                    "Başqa username və ya rəqəmli ID yazın:</i>",
                    back_kb,
                )
                return
            user_id = u["user_id"]
        else:
            await prm_edit_panel(
                "💎 <b>Premium ver</b>\n\n❌ Düzgün format deyil.\n\n"
                "<i>Rəqəmli ID (məs. <code>123456789</code>) və ya username (məs. <code>@ilgarww</code>) yazın:</i>",
                back_kb,
            )
            return

        prm["stage"] = "choose"
        text, kb = await duration_panel(user_id, u)
        await prm_edit_panel(text, kb)

    # ── /premium: istifadəçi öz statusunu görür ──
    @dp.message(Command("premium"), F.chat.type == "private")
    async def premium_status(message: Message):
        uid = message.from_user.id
        if is_creator(uid):
            text = (
                "👑 <b>Status: Creator</b>\n"
                "Bütün imkanlar açıqdır.\n\n"
                f"💬 Caption: {'✅ Açıq' if creator_caption_enabled() else '❌ Bağlı'} "
                "<i>(/menu-dan dəyişilir)</i>"
            )
        else:
            cur = await asyncio.to_thread(get_db().get_premium, uid)
            lifetime = bool(cur) and cur["until"] is None
            kb = await asyncio.to_thread(buy_keyboard, lifetime)
            if cur:
                text = (
                    f"💎 <b>Status: Premium</b>\n⏳ {fmt_until(cur['until'])}\n\n"
                    "✨ Mahnılar \"via @dllmasterbot\" yazısı olmadan göndərilir."
                    + ("\n\n⭐ <b>Uzatmaq üçün plan seç</b> — müddət üstünə əlavə olunur:" if kb else "")
                )
            else:
                text = (
                    "🆓 <b>Status: Free</b>\n\n"
                    "💎 <i>Premium ilə mahnılar \"via @dllmasterbot\" yazısı olmadan göndərilir.</i>"
                    + ("\n\n⭐ <b>Telegram Stars ilə premium al:</b>" if kb else "")
                )
            await message.answer(text, parse_mode="HTML", reply_markup=kb)
            return
        await message.answer(text, parse_mode="HTML")

    def reset_inputs():
        """Yeni bölməyə keçəndə yarımçıq qalan mətn gözləmələrini ləğv edir."""
        prm.clear()
        prm["stage"] = None
        wm.clear()
        wm["stage"] = None
        tx.clear()
        tx["stage"] = None
        stp.clear()
        stp["stage"] = None
        last_query = us.get("last_query")
        us.clear()
        us["stage"] = None
        if last_query:
            us["last_query"] = last_query

    # ═════════════ 🎨 Mesaj və media (birləşmiş bölmə) ═════════════
    # Hər mesajın həm mətni (TEXT_SLOTS), həm mediası (SLOTS) bir paneldən dəyişilir.
    # Qrup salamı salam mesajının mediasını istifadə edir.
    MS_WHERE = {
        "welcome": "/start yazanda göndərilir.",
        "group": "Bot qrupa əlavə olunanda və qrupda /start yazılanda göndərilir.",
        "premium": "/start → 💎 Premium al düyməsinə basanda göndərilir.",
    }

    def media_slot(slot: str) -> str:
        return TEXT_SLOTS[slot]["media"]

    def short_media(slot: str) -> str:
        kind = get_media(media_slot(slot))["type"]
        return {"default": "🎞 GIF", "photo": "🖼 şəkil", "animation": "🎞 GIF",
                "video": "🎬 video", "none": "🚫 yoxdur"}.get(kind, kind)

    def ms_list_panel(note: str = ""):
        lines = [
            f"{info['label']}\n    📝 {'✏️ dəyişdirilib' if get_text_template(slot) else 'default'}"
            f" · 🖼 {short_media(slot)}"
            for slot, info in TEXT_SLOTS.items()
        ]
        text = (
            "🎨 <b>Mesaj və media</b>\n\n"
            "Botun göndərdiyi mesajların mətni və şəkli / GIF-i / videosu.\n\n"
            + "\n".join(lines)
            + (f"\n\n{note}" if note else "")
            + "\n\n<i>Dəyişmək istədiyini seç:</i>"
        )
        btns = [_btn(info["label"], f"ms:{slot}") for slot, info in TEXT_SLOTS.items()]
        rows = [btns[i:i + 2] for i in range(0, len(btns), 2)]
        rows.append(nav_row("main"))
        return text, InlineKeyboardMarkup(inline_keyboard=rows)

    def ms_panel(slot: str, note: str = ""):
        info = TEXT_SLOTS[slot]
        mslot = media_slot(slot)
        custom = get_text_template(slot)
        shared = " <i>(salam mesajı ilə ortaq)</i>" if mslot != slot else ""
        where = MS_WHERE.get(slot, "/start altındakı düyməyə basanda göndərilir.")
        vars_text = " ".join(f"<code>{escape(k)}</code>" for k in info["vars"])
        text = (
            f"🎨 <b>{escape(info['label'])}</b>\n<i>{where}</i>\n\n"
            f"📝 <b>Mətn:</b> {'✏️ sənin mətnin' if custom else 'default'}"
            + (f" ({len(custom)} simvol)" if custom else "")
            + f"\n🖼 <b>Media:</b> {escape(media_label(mslot))}{shared}\n\n"
            f"🔤 Mətndə dəyişənlər: {vars_text}"
            + (f"\n\n{note}" if note else "")
        )
        text_row = [_btn("✏️ Mətni dəyiş", f"tx_set:{slot}"), _btn("📋 Hazırkı mətn", f"tx_show:{slot}")]
        media_row = [_btn("🖼 Media göndər", f"wm_set:{slot}"), _btn("🚫 Mediasız", f"wm_none:{slot}")]
        rows = [text_row]
        if custom:
            rows.append([_btn("↩️ Default mətn", f"tx_def:{slot}")])
        rows.append(media_row)
        if mslot == "welcome":
            rows.append([_btn("↩️ Default GIF", f"wm_def:{slot}")])
        rows.append([_btn("👁 Önizləmə", f"tx_prev:{slot}")])
        rows.append(nav_row("ms"))
        return text, InlineKeyboardMarkup(inline_keyboard=rows)

    async def wm_edit_panel(text: str, kb=None):
        try:
            await context.bot.edit_message_text(
                chat_id=wm["chat_id"], message_id=wm["panel_id"],
                text=text, parse_mode="HTML", reply_markup=kb,
            )
        except Exception as e:
            if "not modified" not in str(e):
                logger.warning(f"Media paneli yenilənmədi: {e}")

    async def waiting_media(message: Message) -> bool:
        return wm.get("stage") == "await_media" and is_creator(message.from_user.id)

    @dp.message(F.chat.type == "private", F.photo | F.animation | F.video, waiting_media)
    async def welcome_media_input(message: Message):
        slot = wm.get("slot", "welcome")          # media slotu
        back = wm.get("back", "welcome")          # panelin slotu
        if message.animation:
            kind, file_id = "animation", message.animation.file_id
        elif message.video:
            kind, file_id = "video", message.video.file_id
        else:
            kind, file_id = "photo", message.photo[-1].file_id
        await asyncio.to_thread(set_media, slot, kind, file_id)
        try:
            await message.delete()
        except Exception:
            pass
        text, kb = ms_panel(back, "✅ <b>Yeni media saxlandı.</b> Yoxlamaq üçün 👁 Önizləmə bas.")
        await wm_edit_panel(text, kb)
        wm["stage"] = None
        logger.info(f"Media dəyişdi ({slot}): {kind}")

    @dp.message(F.chat.type == "private", ~F.photo, ~F.animation, ~F.video, waiting_media)
    async def welcome_media_wrong(message: Message):
        if message.text and message.text.startswith("/"):
            return
        await message.reply(
            "🖼 Şəkil, GIF və ya video göndərin.\n"
            "<i>Şəkli \"fayl kimi\" yox, adi şəkil kimi göndərin.</i>",
            parse_mode="HTML",
        )

    async def tx_edit_panel(text: str, kb=None):
        try:
            await context.bot.edit_message_text(
                chat_id=tx["chat_id"], message_id=tx["panel_id"],
                text=text, parse_mode="HTML", reply_markup=kb,
            )
        except Exception as e:
            if "not modified" not in str(e):
                logger.warning(f"Mesajlar paneli yenilənmədi: {e}")

    async def send_sample(chat_id: int, slot: str, first_name: str, user_id: int):
        """Mesajı istifadəçilərin görəcəyi kimi göndərir (media ilə). Xəta olsa exception atır."""
        import plugins.start_plugin as sp
        username = (await context.bot.me()).username
        text, kb = await asyncio.to_thread(sp.sample_message, slot, first_name, user_id, username)
        await send_media(context.bot, chat_id, text, kb, TEXT_SLOTS[slot]["media"])
        return text

    async def waiting_tx_text(message: Message) -> bool:
        return tx.get("stage") == "await_text" and is_creator(message.from_user.id)

    @dp.message(F.chat.type == "private", F.text, ~F.text.startswith("/"), waiting_tx_text)
    async def tx_text_input(message: Message):
        slot = tx.get("slot")
        template = message.html_text.strip()      # qalın, kursiv, link və s. saxlanılır
        if len(template) > TEXT_LIMIT - 200:
            await message.reply(f"❌ Mətn çox uzundur ({len(template)} simvol). Maksimum ~{TEXT_LIMIT - 200}.")
            return

        old = get_text_template(slot)
        await asyncio.to_thread(set_text_template, slot, template)
        # Yoxlama: mesajı elə indi sənə göndərək — Telegram qəbul etməsə köhnə mətnə qaytarılır
        try:
            rendered = await send_sample(message.chat.id, slot, message.from_user.first_name or "Dostum",
                                         message.from_user.id)
        except Exception as e:
            await asyncio.to_thread(set_text_template, slot, old)
            await message.reply(
                f"❌ Bu mətn göndərilə bilmir, saxlanmadı:\n<code>{escape(str(e))[:300]}</code>",
                parse_mode="HTML",
            )
            return

        tx["stage"] = None
        note = "✅ <b>Yeni mətn saxlandı.</b> Yuxarıda istifadəçilərin görəcəyi formadır."
        if get_media(TEXT_SLOTS[slot]["media"])["type"] != "none" and len(rendered) > CAPTION_LIMIT:
            note += (f"\n⚠️ Mətn {len(rendered)} simvoldur — media ilə göndərmək üçün maksimum "
                     f"{CAPTION_LIMIT}. Bu halda media olmadan, yalnız mətn gedəcək.")
        text, kb = ms_panel(slot, note)
        await tx_edit_panel(text, kb)
        logger.info(f"Mətn dəyişdi ({slot}): {len(template)} simvol")

    # ═════════════ Statistika və istifadəçilər ═════════════
    async def stats_view(view: str):
        tz, tz_name = get_tz()
        now_dt = datetime.now(tz)
        now = int(now_dt.timestamp())
        day_start = int(now_dt.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
        db = get_db()
        st = await asyncio.to_thread(db.stats_overview, day_start, now, tz_offset_seconds())
        pc = await asyncio.to_thread(db.premium_count)
        cc = await asyncio.to_thread(db.chat_counts)
        grp_dl = await asyncio.to_thread(db.group_download_count, now - 30 * 86400)
        depo = audio_cache.depo_status()
        cs = await asyncio.to_thread(db.cache_stats, depo["id"])
        dl, du, us_ = st["dl"], st["dl_users"], st["users"]
        tabs = stats_tabs(view) + [[_btn("🔄 Yenilə", view)], nav_row("main")]

        if view == "stats":
            src_total = sum(st["sources"].values())
            src_lines = "\n".join(
                f"   {SOURCE_LABELS.get(k, k)}: <b>{v}</b> ({pct(v, src_total)})" for k, v in st["sources"].items()
            ) or "   <i>məlumat yoxdur</i>"
            text = (
                "📊 <b>Statistika</b>\n\n"
                "🎵 <b>Yükləmələr</b>\n"
                f"   Bu gün: <b>{dl['today']}</b> · {du['today']} nəfər\n"
                f"   Son 7 gün: <b>{dl['week']}</b> · {du['week']} nəfər{trend(dl['week'], dl['prev_week'])}\n"
                f"   Son 30 gün: <b>{dl['month']}</b> · {du['month']} nəfər\n"
                f"   Gündəlik orta (30 gün): <b>{dl['month'] / 30:.1f}</b>\n"
                f"   Cəmi: <b>{dl['total']}</b>\n\n"
                "👥 <b>İstifadəçilər</b>\n"
                f"   Cəmi: <b>{us_['total']}</b> · 🚫 bloklayan {us_['blocked']} ({pct(us_['blocked'], us_['total'])})\n"
                f"   🆕 Yeni: bu gün <b>{us_['new_today']}</b> · 7 gün <b>{us_['new_week']}</b>"
                f"{trend(us_['new_week'], us_['new_prev_week'])} · 30 gün <b>{us_['new_month']}</b>\n"
                f"   🟢 Aktiv: bu gün <b>{us_['active_today']}</b> · 7 gün <b>{us_['active_week']}</b> · "
                f"30 gün <b>{us_['active_month']}</b>\n"
                f"   💎 Premium: <b>{pc}</b> · 🆓 Free: <b>{max(us_['total'] - pc, 0)}</b>\n"
                f"   🎧 Mahnı yükləyənlər (30 gün): <b>{pct(du['month'], us_['total'])}</b>\n\n"
                f"💬 <b>Çatlar:</b> 👥 {cc['groups']} qrup · 📣 {cc['channels']} kanal\n"
                f"   Qruplarda yükləmə (30 gün): <b>{grp_dl}</b> ({pct(grp_dl, dl['month'])})\n\n"
                f"📦 <b>Depo</b> ({escape(depo['ref'])}{'' if depo['id'] else ' — ⚠️ əlçatan deyil'})\n"
                f"   Saxlanan mahnı: <b>{cs['songs']}</b> · {cs['size'] / 1048576:.0f} MB\n"
                f"   Keşdən göndərilib: <b>{cs['hits']}</b> dəfə "
                f"({pct(cs['hits'], cs['hits'] + cs['songs'])} yükləməsiz)\n\n"
                f"📥 <b>Mənbələr (30 gün)</b>\n{src_lines}\n\n"
                f"<i>🕒 {now_dt.strftime('%d.%m.%Y %H:%M')} · {escape(tz_name)}</i>"
            )
            return text, InlineKeyboardMarkup(inline_keyboard=tabs)

        if view == "stats_chart":
            days = len(st["daily_dl"])
            labels = [
                datetime.fromtimestamp(st["chart_start"] + i * 86400, tz).strftime("%d.%m") for i in range(days)
            ]
            hours = st["hours"]
            busiest = sorted(range(24), key=lambda h: hours[h], reverse=True)[:3]
            busy_text = " · ".join(f"{h:02d}:00 ({hours[h]})" for h in busiest if hours[h]) or "—"
            text = (
                f"📈 <b>Qrafik — son {days} gün</b>\n\n"
                f"🎵 <b>Yükləmələr</b> (cəmi {sum(st['daily_dl'])})\n"
                f"<pre>{bar_chart(labels, st['daily_dl'])}</pre>\n"
                f"🆕 <b>Yeni istifadəçilər</b> (cəmi {sum(st['daily_new'])})\n"
                f"<pre>{bar_chart(labels, st['daily_new'])}</pre>\n"
                "🕒 <b>Saatlara görə yükləmə (30 gün)</b>\n"
                f"<pre>00    06    12    18   23\n{sparkline(hours)}</pre>\n"
                f"🔥 Ən aktiv saatlar: {busy_text}"
            )
            return text, InlineKeyboardMarkup(inline_keyboard=tabs)

        # stats_top
        song_lines = []
        for i, sg in enumerate(st["top_songs"], 1):
            title = escape((sg["title"] or "—")[:45])
            link = f'<a href="{escape(sg["url"], quote=True)}">{title}</a>' if sg.get("url") else title
            song_lines.append(f"{i}. {link} — <b>{sg['n']}</b> dəfə · {sg['users']} nəfər")
        user_lines, user_btns = [], []
        medals = ["🥇", "🥈", "🥉"]
        for i, u in enumerate(st["top_users"], 1):
            mark = medals[i - 1] if i <= 3 else f"{i}."
            user_lines.append(f"{mark} {user_label(u, u['user_id'])} — <b>{u['n']}</b> mahnı")
            user_btns.append(_btn(f"{mark} {display_name(u, u['user_id'])[:18]}", f"user:{u['user_id']}:stats_top"))
        top_chats = await asyncio.to_thread(db.top_chats, now - 30 * 86400)
        chat_lines, chat_btns = [], []
        for i, ch in enumerate(top_chats, 1):
            title = escape((ch["title"] or str(ch["chat_id"]))[:35])
            chat_lines.append(f"{i}. <b>{title}</b> — <b>{ch['n']}</b> mahnı · {ch['users']} nəfər")
            chat_btns.append(_btn(f"💬 {(ch['title'] or str(ch['chat_id']))[:20]}", f"chat:{ch['chat_id']}:stats_top"))
        text = (
            "🏆 <b>Top — son 30 gün</b>\n\n"
            "🎵 <b>Ən çox yüklənən mahnılar</b>\n"
            + ("\n".join(song_lines) or "<i>məlumat yoxdur</i>")
            + "\n\n👤 <b>Ən aktiv istifadəçilər</b>\n"
            + ("\n".join(user_lines) or "<i>məlumat yoxdur</i>")
            + "\n\n💬 <b>Ən aktiv qruplar</b>\n"
            + ("\n".join(chat_lines) or "<i>məlumat yoxdur</i>")
        )
        rows = ([user_btns[i:i + 2] for i in range(0, len(user_btns), 2)]
                + [chat_btns[i:i + 2] for i in range(0, len(chat_btns), 2)] + tabs)
        return text, InlineKeyboardMarkup(inline_keyboard=rows)

    def users_list_kb(rows, flt: str, page: int, pages: int, counts: dict):
        back = f"users:{flt}:{page}"
        kb = []
        for u in rows:
            marks = ("💎" if u["premium"] else "") + ("🚫" if u["blocked"] else "")
            kb.append([_btn(
                f"{marks}{' ' if marks else ''}{display_name(u)[:22]} · 🎵{u['dl_total']}",
                f"user:{u['user_id']}:{back}",
            )])
        if pages > 1:
            nav = []
            if page > 0:
                nav.append(_btn("◀️", f"users:{flt}:{page - 1}"))
            nav.append(_btn(f"{page + 1}/{pages}", "noop"))
            if page < pages - 1:
                nav.append(_btn("▶️", f"users:{flt}:{page + 1}"))
            kb.append(nav)
        f_btns = [
            _btn(("• " if key == flt else "") + f"{label} {counts.get(key, 0)}", f"users:{key}:0")
            for key, label in USER_FILTERS
        ]
        kb += [f_btns[0:3], f_btns[3:6]]
        kb.append([_btn("🔍 Axtar", "usearch"), _btn("📄 CSV", "csv")])
        kb += stats_tabs("users")
        kb.append(nav_row("stats"))
        return InlineKeyboardMarkup(inline_keyboard=kb)

    async def groups_view(flt: str, page: int):
        now = int(time.time())
        db = get_db()
        counts = await asyncio.to_thread(db.chat_filter_counts)
        total = counts.get(flt, 0)
        pages = max(1, -(-total // CHATS_PAGE))
        page = min(max(page, 0), pages - 1)
        rows, total = await asyncio.to_thread(db.chats_filtered, flt, page * CHATS_PAGE, CHATS_PAGE, now)
        label = dict(CHAT_FILTERS)[flt]

        if rows:
            lines = []
            for i, ch in enumerate(rows, start=page * CHATS_PAGE + 1):
                icon = "📣" if ch["type"] == "channel" else "👥"
                title = escape((ch["title"] or str(ch["chat_id"]))[:40])
                uname = f" · @{escape(ch['username'])}" if ch.get("username") else ""
                lines.append(
                    f"{i}. {icon} <b>{title}</b>{uname}\n"
                    f"    🎵 30 gün {ch['dl_30d']} (cəmi {ch['dl_total']}) · "
                    f"📅 {fmt_date(ch['first_seen'])} · 🕒 {ago(ch['last_seen'], now)}"
                )
            body = "\n".join(lines) + "\n\n<i>Ətraflı məlumat üçün qrupa bas.</i>"
        else:
            body = {
                "groups": "<i>Bot hələ heç bir qrupda qeydə alınmayıb.\n"
                          "Qrup bot əlavə olunanda və ya orada komanda yazılanda siyahıya düşür.</i>",
                "channels": "<i>Bot hələ heç bir kanalda qeydə alınmayıb.</i>",
                "left": "<i>Botun çıxdığı / çıxarıldığı çat yoxdur.</i>",
            }[flt]
        text = f"💬 <b>{label}</b> (<b>{total}</b>)\n<i>Səhifə {page + 1}/{pages}</i>\n\n{body}"

        kb = []
        for ch in rows:
            icon = "📣" if ch["type"] == "channel" else "👥"
            kb.append([_btn(f"{icon} {(ch['title'] or str(ch['chat_id']))[:24]} · 🎵{ch['dl_30d']}",
                            f"chat:{ch['chat_id']}:grp:{flt}:{page}")])
        if pages > 1:
            nav = []
            if page > 0:
                nav.append(_btn("◀️", f"grp:{flt}:{page - 1}"))
            nav.append(_btn(f"{page + 1}/{pages}", "noop"))
            if page < pages - 1:
                nav.append(_btn("▶️", f"grp:{flt}:{page + 1}"))
            kb.append(nav)
        kb.append([_btn(("• " if k == flt else "") + f"{lbl} {counts.get(k, 0)}", f"grp:{k}:0")
                   for k, lbl in CHAT_FILTERS])
        kb += stats_tabs("grp")
        kb.append(nav_row("stats"))
        return text, InlineKeyboardMarkup(inline_keyboard=kb)

    async def chat_live(chat_id: int) -> dict:
        """Telegram-dan canlı məlumat: ad, üzv sayı, botun statusu və yetkiləri."""
        bot = context.bot
        info = {}
        try:
            ch = await bot.get_chat(chat_id)
            info["title"], info["username"] = ch.title, getattr(ch, "username", None)
            info["type"] = str(getattr(ch, "type", "") or "")
        except (TelegramForbiddenError, TelegramBadRequest) as e:
            info["gone"] = str(e)
            await asyncio.to_thread(get_db().set_chat_active, chat_id, False)
            return info
        except Exception as e:
            info["error"] = str(e)
            return info
        try:
            info["members"] = await bot.get_chat_member_count(chat_id)
        except Exception:
            pass
        try:
            me = await bot.me()
            m = await bot.get_chat_member(chat_id, me.id)
            info["status"] = str(getattr(m.status, "value", m.status))
            info["can_delete"] = bool(getattr(m, "can_delete_messages", False))
            info["can_pin"] = bool(getattr(m, "can_pin_messages", False))
            info["can_post"] = bool(getattr(m, "can_post_messages", False))
        except Exception:
            pass
        await asyncio.to_thread(get_db().update_chat_info, chat_id, info.get("title"),
                                info.get("username"), info.get("members"))
        return info

    async def chat_card(chat_id: int, back: str, live: bool = True):
        now = int(time.time())
        db = get_db()
        row = await asyncio.to_thread(db.get_chat, chat_id)
        if not row:
            return (f"❌ <code>{chat_id}</code> bazada tapılmadı.",
                    InlineKeyboardMarkup(inline_keyboard=[nav_row(back)]))
        info = await chat_live(chat_id) if live else {}
        if info.get("title") or info.get("gone"):
            row = await asyncio.to_thread(db.get_chat, chat_id)
        d = await asyncio.to_thread(db.chat_detail, chat_id, now)
        dl = d["dl"]
        is_channel = row["type"] == "channel"

        lines = [f"{'📣' if is_channel else '👥'} <b>{escape(row['title'] or str(chat_id))}</b>"]
        if row.get("username"):
            lines.append(f"🔗 <a href=\"https://t.me/{escape(row['username'])}\">@{escape(row['username'])}</a>")
        lines.append(f"🆔 <code>{chat_id}</code> · {CHAT_TYPES.get(row['type'], row['type'])}")

        if info.get("gone"):
            lines.append("⛔ <b>Bot artıq bu çatda deyil</b> <i>(siyahıda \"Çıxılmış\" kimi qeyd olundu)</i>")
        elif info.get("error"):
            lines.append(f"⚠️ Canlı məlumat alınmadı: <i>{escape(info['error'][:120])}</i>")
        else:
            status = info.get("status")
            if status:
                s_line = f"🤖 Bot: <b>{BOT_STATUS.get(status, status)}</b>"
                if status == "administrator":
                    if is_channel:
                        s_line += f" · ✍️ post {'✅' if info.get('can_post') else '❌'}"
                    else:
                        s_line += (f" · 🗑 {'✅' if info.get('can_delete') else '❌'}"
                                   f" · 📌 {'✅' if info.get('can_pin') else '❌'}")
                lines.append(s_line)
        members = info.get("members", d["members"])
        if members is not None:
            lines.append(f"👤 Üzv sayı: <b>{members}</b>" + ("" if "members" in info else " <i>(son yoxlama)</i>"))
        lines += [
            f"📅 Qeydə alınıb: {fmt_dt(row['first_seen'])} ({ago(row['first_seen'], now)})",
            f"🕒 Son aktivlik: {ago(row['last_seen'], now)}",
            f"📶 Vəziyyət: {'✅ aktiv' if row['active'] else '🚪 bot çıxıb'}",
        ]

        if not is_channel:
            lines.append("\n🎵 <b>Yükləmələr</b>")
            if dl["total"]:
                lines += [
                    f"   Cəmi: <b>{dl['total']}</b> · 7 gün <b>{dl['week']}</b> · 30 gün <b>{dl['month']}</b>",
                    f"   Yükləyən: <b>{dl['users']}</b> nəfər · son: {ago(dl['last_ts'], now)}",
                    "\n🏆 <b>Ən aktiv üzvlər</b>",
                ]
                medals = ["🥇", "🥈", "🥉"]
                for i, u in enumerate(d["top_users"], 1):
                    mark = medals[i - 1] if i <= 3 else f"{i}."
                    lines.append(f"   {mark} {user_label(u, u['user_id'])} — <b>{u['n']}</b>")
                lines.append("\n🕘 <b>Son mahnılar</b>")
                for r in d["recent"]:
                    title = escape((r["title"] or "—")[:45])
                    link = f'<a href="{escape(r["url"], quote=True)}">{title}</a>' if r.get("url") else title
                    lines.append(f"   • {link} <i>({ago(r['ts'], now)})</i>")
            else:
                lines.append("   <i>Bu qrupda hələ /music ilə mahnı yüklənməyib</i>")

        me_back = f"chat:{chat_id}:{back}"
        kb = []
        user_btns = [
            _btn(f"👤 {display_name(u, u['user_id'])[:16]}", f"user:{u['user_id']}:{me_back}")
            for u in d["top_users"][:4]
        ]
        if user_btns and len(f"menu:user:{d['top_users'][0]['user_id']}:{me_back}") <= 64:
            kb += [user_btns[i:i + 2] for i in range(0, len(user_btns), 2)]
        action_row = [_btn("🔄 Yenilə", f"chat:{chat_id}:{back}")]
        if row["active"] and not info.get("gone"):
            action_row.append(_btn("🚪 Çatdan çıx", f"chat_leave:{chat_id}:{back}"))
        kb.append(action_row)
        kb.append(nav_row(back))
        return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=kb)

    async def users_view(flt: str, page: int):
        now = int(time.time())
        db = get_db()
        counts = await asyncio.to_thread(db.filter_counts, now)
        total = counts.get(flt, 0)
        pages = max(1, -(-total // USERS_PAGE))
        page = min(max(page, 0), pages - 1)
        rows, total = await asyncio.to_thread(db.users_filtered, flt, page * USERS_PAGE, USERS_PAGE, now)
        label = dict(USER_FILTERS)[flt]

        if rows:
            lines = []
            for i, u in enumerate(rows, start=page * USERS_PAGE + 1):
                marks = ("💎 " if u["premium"] else "") + ("🚫 " if u["blocked"] else "")
                lines.append(
                    f"{i}. {marks}{user_label(u, u['user_id'])}\n"
                    f"    🎵 {u['dl_total']} · 🆕 {fmt_date(u['first_seen'])} · 🕒 {ago(u['last_seen'], now)}"
                )
            body = "\n".join(lines) + "\n\n<i>Ətraflı məlumat üçün istifadəçiyə bas.</i>"
        else:
            body = "<i>Bu siyahıda heç kim yoxdur.</i>"
        hint = {
            "active": "son 7 gündə botla işləyənlər",
            "new": "son 7 gündə qoşulanlar",
            "top": "ən çox mahnı yükləyənlər",
        }.get(flt)
        text = (
            f"👥 <b>İstifadəçilər — {label}</b> (<b>{total}</b>)"
            + (f"\n<i>{hint}</i>" if hint else "")
            + f"\n<i>Səhifə {page + 1}/{pages}</i>\n\n{body}"
        )
        return text, users_list_kb(rows, flt, page, pages, counts)

    async def user_card(uid: int, back: str):
        now = int(time.time())
        db = get_db()
        d = await asyncio.to_thread(db.user_detail, uid, now)
        prem = await asyncio.to_thread(db.get_premium, uid)
        u, dl = d["user"], d["dl"]

        if not u and not dl["total"]:
            return (f"❌ <code>{uid}</code> bazada tapılmadı.",
                    InlineKeyboardMarkup(inline_keyboard=[nav_row(back)]))

        if is_creator(uid):
            role = "👑 Creator"
        elif prem:
            role = f"💎 Premium ({fmt_until(prem['until'])})"
        else:
            role = "🆓 Free"
        lines = [f"👤 <b>{escape(display_name(u, uid))}</b>"]
        if u and u.get("username"):
            lines.append(f"🔗 @{escape(u['username'])}")
        lines += [
            f"🆔 <code>{uid}</code> · <a href=\"tg://user?id={uid}\">profil</a>",
            f"🏷 Status: <b>{role}</b>",
        ]
        if u:
            state = "🚫 botu bloklayıb" if u["blocked"] else "✅ aktiv"
            lines += [
                f"📶 Vəziyyət: {state}",
                f"🆕 Qoşulub: {fmt_dt(u['first_seen'])} ({ago(u['first_seen'], now)})",
                f"🕒 Son aktivlik: {fmt_dt(u['last_seen'])} ({ago(u['last_seen'], now)})",
            ]
        else:
            lines.append("<i>ℹ️ Botu şəxsi çatda başlatmayıb (yalnız inline/qrup istifadəsi)</i>")

        lines.append("\n🎵 <b>Yükləmələr</b>")
        if dl["total"]:
            rank = f" · reytinq: <b>#{d['rank']}</b>" if d.get("rank") else ""
            lines += [
                f"   Cəmi: <b>{dl['total']}</b>{rank}",
                f"   Son 7 gün: <b>{dl['week']}</b> · son 30 gün: <b>{dl['month']}</b>",
                f"   İlk: {fmt_dt(dl['first_ts'])} · son: {ago(dl['last_ts'], now)}",
                "   Mənbə: " + " · ".join(f"{SOURCE_LABELS.get(k, k)} {v}" for k, v in d["sources"].items()),
                "\n🕘 <b>Son mahnılar</b>",
            ]
            for r in d["recent"]:
                title = escape((r["title"] or "—")[:45])
                link = f'<a href="{escape(r["url"], quote=True)}">{title}</a>' if r.get("url") else title
                lines.append(f"   • {link} <i>({ago(r['ts'], now)})</i>")
        else:
            lines.append("   <i>Hələ mahnı yükləməyib</i>")

        kb = []
        if not is_creator(uid):
            if prem:
                kb.append([_btn("❌ Premiumu al", f"prm_rm:{uid}:0")])
            else:
                kb.append([_btn("💎 Premium ver", f"prm_gv:{uid}:{back}")])
        kb.append(nav_row(back))
        return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=kb)

    async def waiting_user_query(message: Message) -> bool:
        return us.get("stage") == "await_query" and is_creator(message.from_user.id)

    @dp.message(F.chat.type == "private", F.text, ~F.text.startswith("/"), waiting_user_query)
    async def user_search_input(message: Message):
        query = message.text.strip()
        try:
            await message.delete()
        except Exception:
            pass
        now = int(time.time())
        rows = await asyncio.to_thread(get_db().search_users, query, now)
        kb = [[_btn(
            f"{'💎 ' if u['premium'] else ''}{'🚫 ' if u['blocked'] else ''}{display_name(u)[:22]} · 🎵{u['dl_total']}",
            f"user:{u['user_id']}:usearch_res",
        )] for u in rows]
        kb.append([_btn("🔍 Yenidən axtar", "usearch")])
        kb.append(nav_row("users:all:0"))
        us["last_query"] = query
        us["stage"] = None
        if rows:
            text = f"🔍 <b>\"{escape(query)}\"</b> üzrə <b>{len(rows)}</b> nəticə\n\n<i>Ətraflı məlumat üçün bas.</i>"
        else:
            text = f"🔍 <b>\"{escape(query)}\"</b> üzrə heç kim tapılmadı."
        try:
            await context.bot.edit_message_text(
                chat_id=us["chat_id"], message_id=us["panel_id"], text=text,
                parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb),
            )
        except Exception as e:
            logger.warning(f"Axtarış paneli yenilənmədi: {e}")

    # ═════════════ ⭐ Ulduz satışı ═════════════
    def plan_line(key, days, label):
        price = get_price(key)
        if not price:
            return f"{escape(label)}: <i>satışda deyil</i>"
        return f"{escape(label)}: <b>{price}⭐</b>{per_month(days, price)}"

    async def stars_panel(note: str = ""):
        now = int(time.time())
        summ = await asyncio.to_thread(get_db().payments_summary, now)
        lines = "\n".join(plan_line(k, d, l) for k, d, l in PLANS)
        on_sale = sum(1 for k, _, _ in PLANS if get_price(k))
        text = (
            "⭐ <b>Ulduz satışı</b>\n\n"
            f"📋 <b>Planlar</b> ({on_sale}/{len(PLANS)} satışda)\n{lines}\n\n"
            "💰 <b>Gəlir</b>\n"
            f"   Cəmi: <b>{summ['stars']}⭐</b> · {summ['n']} ödəniş · {summ['buyers']} alıcı\n"
            f"   Son 30 gün: <b>{summ['stars_30d']}⭐</b> · {summ['n_30d']} ödəniş\n"
            + (f"   💝 O cümlədən bəxşiş: <b>{summ['tips_stars']}⭐</b> · {summ['tips_n']} dəfə\n" if summ.get("tips_n") else "")
            + (f"   ↩️ Geri qaytarılıb: {summ['refunded_n']} ({summ['refunded_stars']}⭐)\n" if summ["refunded_n"] else "")
            + ("\n<i>İstifadəçilər /premium yazanda satışdakı planları görüb ulduzla ala bilər.</i>"
               if on_sale else "\n⚠️ <i>Heç bir plan satışda deyil — qiymət təyin et.</i>")
            + (f"\n\n{note}" if note else "")
        )
        rows = [[_btn(f"{'⭐' if get_price(k) else '▫️'} {l}", f"st_plan:{k}")] for k, _, l in PLANS]
        rows.append([_btn("🧾 Ödənişlər", "st_pay:0")])
        rows.append(nav_row("prm:0"))
        return text, InlineKeyboardMarkup(inline_keyboard=rows)

    def stars_plan_panel(key: str, note: str = ""):
        days, label = PLAN_MAP[key]
        price = get_price(key)
        text = (
            f"⭐ <b>{escape(label)}</b>\n\n"
            f"⏳ Müddət: <b>{'ömürlük' if days is None else f'{days} gün'}</b>\n"
            f"💰 Qiymət: <b>{f'{price}⭐' if price else 'satışda deyil'}</b>{per_month(days, price) if price else ''}"
            + (f"\n\n{note}" if note else "")
        )
        rows = [[_btn("✏️ Qiymət təyin et", f"st_set:{key}")]]
        if price:
            rows[0].append(_btn("🚫 Satışdan çıxar", f"st_off:{key}"))
        rows.append(nav_row("st"))
        return text, InlineKeyboardMarkup(inline_keyboard=rows)

    async def payments_view(page: int):
        per = 8
        rows, total = await asyncio.to_thread(get_db().payments_page, page * per, per)
        pages = max(1, -(-total // per))
        page = min(max(page, 0), pages - 1)
        if page and not rows:
            rows, total = await asyncio.to_thread(get_db().payments_page, page * per, per)
        if rows:
            lines = []
            for r in rows:
                label = "💝 Bəxşiş" if r["plan"] == "tip" else PLAN_MAP.get(r["plan"], (None, r["plan"]))[1]
                mark = " ↩️ <i>qaytarılıb</i>" if r["refunded"] else ""
                lines.append(f"#{r['id']} · {fmt_dt(r['ts'])} · <b>{r['stars']}⭐</b> · {escape(label)}\n"
                             f"    {user_label(r, r['user_id'])}{mark}")
            body = "\n".join(lines)
        else:
            body = "<i>Hələ ödəniş yoxdur.</i>"
        text = f"🧾 <b>Ödənişlər</b> (<b>{total}</b>)\n<i>Səhifə {page + 1}/{pages}</i>\n\n{body}"
        kb = [[_btn(f"{'↩️' if r['refunded'] else '🧾'} #{r['id']} · {r['stars']}⭐ · {display_name(r, r['user_id'])[:16]}",
                    f"st_p:{r['id']}:{page}")] for r in rows]
        if pages > 1:
            nav = []
            if page > 0:
                nav.append(_btn("◀️", f"st_pay:{page - 1}"))
            nav.append(_btn(f"{page + 1}/{pages}", "noop"))
            if page < pages - 1:
                nav.append(_btn("▶️", f"st_pay:{page + 1}"))
            kb.append(nav)
        kb.append(nav_row("st"))
        return text, InlineKeyboardMarkup(inline_keyboard=kb)

    async def payment_card(pid: int, page: int, note: str = ""):
        r = await asyncio.to_thread(get_db().get_payment, pid)
        if not r:
            return "❌ Ödəniş tapılmadı.", InlineKeyboardMarkup(inline_keyboard=[nav_row(f"st_pay:{page}")])
        label = "💝 Bəxşiş" if r["plan"] == "tip" else PLAN_MAP.get(r["plan"], (None, r["plan"]))[1]
        prem = await asyncio.to_thread(get_db().get_premium, r["user_id"])
        text = (
            f"🧾 <b>Ödəniş #{r['id']}</b>\n\n"
            f"👤 {user_label(r, r['user_id'])}\n"
            f"💎 Plan: <b>{escape(label)}</b> · <b>{r['stars']}⭐</b>\n"
            f"🕒 {fmt_dt(r['ts'])}\n"
            f"🔖 <code>{escape(r['charge_id'] or '—')}</code>\n"
            f"📌 İstifadəçinin hazırkı statusu: {('💎 ' + fmt_until(prem['until'])) if prem else '🆓 Free'}\n"
            + (f"↩️ <b>Geri qaytarılıb</b> — {fmt_dt(r['refund_ts'])}" if r["refunded"] else "")
            + (f"\n\n{note}" if note else "")
        )
        kb = []
        if not r["refunded"] and r.get("charge_id"):
            kb.append([_btn("↩️ Ulduzları geri qaytar", f"st_ref:{pid}:{page}")])
        kb.append([_btn("👤 İstifadəçi kartı", f"user:{r['user_id']}:st_p:{pid}:{page}")])
        kb.append(nav_row(f"st_pay:{page}"))
        return text, InlineKeyboardMarkup(inline_keyboard=kb)

    async def waiting_price(message: Message) -> bool:
        return stp.get("stage") == "await_price" and is_creator(message.from_user.id)

    @dp.message(F.chat.type == "private", F.text, ~F.text.startswith("/"), waiting_price)
    async def stars_price_input(message: Message):
        key = stp.get("plan")
        raw = message.text.strip().replace("⭐", "").replace(" ", "")
        try:
            await message.delete()
        except Exception:
            pass
        panel = dict(chat_id=stp["chat_id"], message_id=stp["panel_id"], parse_mode="HTML")
        if not raw.isdigit() or not (MIN_STARS <= int(raw) <= MAX_STARS):
            text, kb = stars_plan_panel(key, f"❌ <b>{escape(message.text[:20])}</b> düzgün deyil. "
                                             f"{MIN_STARS}–{MAX_STARS} arası rəqəm yazın:")
            kb = InlineKeyboardMarkup(inline_keyboard=[nav_row(f"st_plan:{key}")])   # gözləmə davam edir
            await context.bot.edit_message_text(text=text, reply_markup=kb, **panel)
            return
        await asyncio.to_thread(set_price, key, int(raw))
        stp["stage"] = None
        text, kb = stars_plan_panel(key, f"✅ Qiymət saxlandı: <b>{int(raw)}⭐</b>")
        try:
            await context.bot.edit_message_text(text=text, reply_markup=kb, **panel)
        except Exception as e:
            logger.warning(f"Ulduz paneli yenilənmədi: {e}")
        logger.info(f"⭐ Qiymət dəyişdi: {key} = {raw}")

    # ── /menu ──
    @dp.message(Command("menu"))
    async def menu_command(message: Message):
        if not is_creator(message.from_user.id):
            logger.info(f"/menu rədd edildi: {message.from_user.id}")
            return
        if message.chat.type != "private":
            await message.reply("🔒 Bu əmri yalnız botun şəxsi çatında işlət.")
            return
        if bc.get("stage") and bc.get("stage") != "sending":
            await delete_preview()
            bc_reset()
        reset_inputs()
        await message.answer(MAIN_TEXT, parse_mode="HTML", reply_markup=main_kb())

    # ── düymələr ──
    @dp.callback_query(F.data.startswith("menu:"))
    async def menu_callback(cb: CallbackQuery):
        if not is_creator(cb.from_user.id):
            await cb.answer("⛔ İcazə yoxdur", show_alert=True)
            return
        if not cb.message:
            await cb.answer()
            return

        parts = cb.data.split(":")
        action = parts[1]

        if action.startswith("bc") and bc.get("stage") == "sending" and action != "bc_stop":
            await cb.answer("Broadcast hələ gedir...", show_alert=True)
            return

        if action == "noop":
            await cb.answer()

        elif action == "bc":
            await cb.answer()
            await delete_preview()
            bc_reset()
            reset_inputs()
            counts = await asyncio.to_thread(target_counts, get_db())
            await edit(
                cb,
                "📢 <b>Broadcast</b>\n\nMesajı kimə göndərək?\n\n"
                "<i>Qruplar və kanallar bot oraya əlavə ediləndə və ya orada yazışma olanda qeydə alınır.</i>",
                bc_target_kb(counts),
            )

        elif action == "bc_t":
            target = parts[2] if len(parts) > 2 else ""
            if target not in TARGET_LABELS:
                await cb.answer()
                return
            count = len(await asyncio.to_thread(get_db().broadcast_targets, target))
            if count == 0:
                await cb.answer("Bu kateqoriyada heç kim yoxdur", show_alert=True)
                return
            await cb.answer()
            bc_reset()
            bc.update(stage="await_text", target=target, chat_id=cb.message.chat.id,
                      panel_id=cb.message.message_id, preview_id=None)
            await edit(
                cb,
                f"📢 <b>Broadcast</b> — {TARGET_LABELS[target]} (<b>{count}</b>)\n\n"
                "✍️ <b>Göndəriləcək mesajı yazın.</b>\n"
                "<i>Qalın, kursiv, link kimi formatlar saxlanılır.</i>",
                InlineKeyboardMarkup(inline_keyboard=[nav_row("bc")]),
            )

        elif action == "bc_edit":
            if bc.get("stage") != "confirm":
                await cb.answer("Sessiya bitib", show_alert=True)
                return
            await cb.answer()
            await delete_preview()
            bc["stage"] = "await_text"
            await edit_panel(
                f"📢 <b>Broadcast</b> — {TARGET_LABELS[bc['target']]}\n\n✍️ <b>Yeni mesajı yazın.</b>",
                InlineKeyboardMarkup(inline_keyboard=[nav_row("bc")]),
            )

        elif action == "bc_cancel":
            await cb.answer("Ləğv edildi")
            if bc.get("stage"):
                await delete_preview()
                if bc.get("panel_id"):
                    await edit_panel(MAIN_TEXT, main_kb())
            bc_reset()

        elif action == "bc_send":
            if bc.get("stage") != "confirm" or cb.message.message_id != bc.get("preview_id"):
                await cb.answer("Sessiya bitib", show_alert=True)
                return
            await cb.answer("Göndərilir...")
            bc["stage"] = "sending"
            bc["stop"] = False
            try:
                await cb.message.edit_reply_markup(reply_markup=None)
            except Exception:
                pass
            bc["preview_id"] = None
            asyncio.create_task(broadcast_task())

        elif action == "bc_stop":
            if bc.get("stage") == "sending":
                bc["stop"] = True
                await cb.answer("Dayandırılır...")
            else:
                await cb.answer()

        elif action == "main":
            await cb.answer()
            if bc.get("stage") and bc.get("stage") != "sending":
                await delete_preview()
                bc_reset()
            reset_inputs()
            await edit(cb, MAIN_TEXT, main_kb())

        elif action == "prm":
            await cb.answer()
            if bc.get("stage") == "await_text":
                await delete_preview()
                bc_reset()
            reset_inputs()
            text, kb = await premium_panel(int(parts[2]) if len(parts) > 2 else 0)
            await edit(cb, text, kb)

        elif action == "prm_add":
            await cb.answer()
            if bc.get("stage") == "await_text":
                await delete_preview()
                bc_reset()
            prm.clear()
            prm.update(stage="await_user", chat_id=cb.message.chat.id, panel_id=cb.message.message_id)
            await edit(
                cb,
                "💎 <b>Premium ver</b>\n\n"
                "✍️ İstifadəçinin <b>ID</b>-sini və ya <b>@username</b>-ini yazın:\n"
                "<i>məs. <code>123456789</code> və ya <code>@ilgarww</code></i>",
                InlineKeyboardMarkup(inline_keyboard=[nav_row("prm:0")]),
            )

        elif action == "prm_g":
            try:
                user_id, days = int(parts[2]), int(parts[3])
            except (IndexError, ValueError):
                await cb.answer()
                return
            db = get_db()
            until = await asyncio.to_thread(db.grant_premium, user_id, days or None, cb.from_user.id)
            reset_inputs()
            u = await asyncio.to_thread(db.get_user, user_id)
            await cb.answer("💎 Premium verildi")

            # İstifadəçiyə bildiriş (botu başlatmayıbsa/bloklayıbsa göndərilmir)
            notified = False
            try:
                await context.bot.send_message(
                    user_id,
                    f"🎉 <b>Sizə Premium verildi!</b>\n⏳ {fmt_until(until)}\n\n"
                    "✨ Mahnılar artıq \"via @dllmasterbot\" yazısı olmadan göndəriləcək.",
                    parse_mode="HTML",
                )
                notified = True
            except Exception as e:
                logger.info(f"Premium bildirişi göndərilmədi ({user_id}): {e}")

            await edit(
                cb,
                "✅ <b>Premium verildi</b>\n\n"
                f"👤 {user_label(u, user_id)}\n"
                f"⏳ {fmt_until(until)}\n"
                f"🔔 Bildiriş: {'göndərildi' if notified else 'göndərilmədi (botu başlatmayıb və ya bloklayıb)'}",
                InlineKeyboardMarkup(inline_keyboard=[
                    [_btn("➕ Daha birinə", "prm_add")],
                    nav_row("prm:0"),
                ]),
            )

        elif action == "prm_rm":
            try:
                user_id = int(parts[2])
                page = int(parts[3]) if len(parts) > 3 else 0
            except (IndexError, ValueError):
                await cb.answer()
                return
            await cb.answer()
            db = get_db()
            u = await asyncio.to_thread(db.get_user, user_id)
            cur = await asyncio.to_thread(db.get_premium, user_id)
            if not cur:
                text, kb = await premium_panel(page)
                await edit(cb, "ℹ️ <i>Bu istifadəçinin artıq premiumu yoxdur.</i>\n\n" + text, kb)
                return
            await edit(
                cb,
                f"⚠️ <b>Premium alınsın?</b>\n\n👤 {user_label(u, user_id)}\n⏳ {fmt_until(cur['until'])}",
                InlineKeyboardMarkup(inline_keyboard=[
                    [_btn("✅ Bəli, al", f"prm_rmy:{user_id}:{page}")],
                    nav_row(f"prm:{page}"),
                ]),
            )

        elif action == "prm_rmy":
            try:
                user_id = int(parts[2])
                page = int(parts[3]) if len(parts) > 3 else 0
            except (IndexError, ValueError):
                await cb.answer()
                return
            removed = await asyncio.to_thread(get_db().revoke_premium, user_id)
            await cb.answer("Premium alındı" if removed else "Artıq premium deyil")
            text, kb = await premium_panel(page)
            await edit(cb, text, kb)

        elif action == "cap":
            enabled = not creator_caption_enabled()
            await asyncio.to_thread(set_creator_caption, enabled)
            await cb.answer(
                ("✅ Caption açıldı: sənə göndərilən mahnıların altında \"via @dllmasterbot\" yazılacaq"
                 if enabled else "❌ Caption bağlandı: sənə göndərilən mahnılar yazısız olacaq")
                + "\n\n🆓 Free: həmişə açıq · 💎 Premium: həmişə bağlı",
                show_alert=True,
            )
            await edit(cb, MAIN_TEXT, main_kb())

        elif action in ("ms", "wm", "tx"):          # "wm"/"tx" — köhnə düymələr üçün
            await cb.answer()
            if bc.get("stage") == "await_text":
                await delete_preview()
                bc_reset()
            reset_inputs()
            slot = parts[2] if len(parts) > 2 else None
            text, kb = ms_panel(slot) if slot in TEXT_SLOTS else ms_list_panel()
            await edit(cb, text, kb)

        elif action == "wm_set":
            slot = parts[2] if len(parts) > 2 else ""
            if slot not in TEXT_SLOTS:
                await cb.answer()
                return
            await cb.answer()
            reset_inputs()
            wm.update(stage="await_media", slot=media_slot(slot), back=slot,
                      chat_id=cb.message.chat.id, panel_id=cb.message.message_id)
            shared = ("\n\n<i>ℹ️ Bu media salam mesajı ilə ortaqdır — orada da dəyişəcək.</i>"
                      if media_slot(slot) != slot else "")
            await edit(
                cb,
                f"🖼 <b>{escape(TEXT_SLOTS[slot]['label'])}</b>\n\n"
                "📤 Yeni <b>şəkil</b>, <b>GIF</b> və ya <b>video</b> göndərin.\n"
                "<i>Mesajın mətni onun altında caption kimi görünəcək.</i>" + shared,
                InlineKeyboardMarkup(inline_keyboard=[nav_row(f"ms:{slot}")]),
            )

        elif action in ("wm_def", "wm_none"):
            slot = parts[2] if len(parts) > 2 else ""
            if slot not in TEXT_SLOTS:
                await cb.answer()
                return
            kind = "default" if action == "wm_def" else "none"
            await asyncio.to_thread(set_media, media_slot(slot), kind)
            reset_inputs()
            await cb.answer("✅ Yadda saxlandı")
            text, kb = ms_panel(slot)
            await edit(cb, text, kb)

        elif action == "tx_set":
            slot = parts[2] if len(parts) > 2 else ""
            if slot not in TEXT_SLOTS:
                await cb.answer()
                return
            await cb.answer()
            reset_inputs()
            tx.update(stage="await_text", slot=slot, chat_id=cb.message.chat.id, panel_id=cb.message.message_id)
            vars_line = ", ".join(f"<code>{escape(k)}</code>" for k in TEXT_SLOTS[slot]["vars"])
            await edit(
                cb,
                f"📝 <b>{escape(TEXT_SLOTS[slot]['label'])}</b>\n\n"
                "✍️ <b>Yeni mətni yazın.</b>\n"
                "Qalın, kursiv, link, <code>kod</code> kimi formatlar saxlanılır.\n\n"
                f"🔤 İstifadə edə biləcəyin dəyişənlər: {vars_line}\n\n"
                "<i>💡 Köhnə mətni əsas götürmək üçün əvvəlcə 📋 Hazırkı mətn ilə onu al, kopyala və dəyiş.</i>",
                InlineKeyboardMarkup(inline_keyboard=[nav_row(f"ms:{slot}")]),
            )

        elif action == "tx_show":
            slot = parts[2] if len(parts) > 2 else ""
            if slot not in TEXT_SLOTS:
                await cb.answer()
                return
            await cb.answer()
            import plugins.start_plugin as sp
            template = await asyncio.to_thread(sp.text_template, slot)
            # Şablon olduğu kimi (dəyişənlərlə) — kopyalayıb dəyişmək üçün
            try:
                await cb.message.answer(template, parse_mode="HTML", disable_web_page_preview=True)
            except Exception as e:
                await cb.message.answer(f"❌ Göstərilmədi: <code>{escape(str(e))[:300]}</code>", parse_mode="HTML")

        elif action == "tx_prev":
            slot = parts[2] if len(parts) > 2 else ""
            if slot not in TEXT_SLOTS:
                await cb.answer()
                return
            await cb.answer("Önizləmə göndərilir...")
            try:
                await send_sample(cb.message.chat.id, slot, cb.from_user.first_name or "Dostum", cb.from_user.id)
            except Exception as e:
                await cb.message.answer(f"❌ Önizləmə alınmadı: <code>{escape(str(e))[:300]}</code>", parse_mode="HTML")

        elif action == "tx_def":
            slot = parts[2] if len(parts) > 2 else ""
            if slot not in TEXT_SLOTS:
                await cb.answer()
                return
            await asyncio.to_thread(set_text_template, slot, None)
            reset_inputs()
            await cb.answer("↩️ Default mətnə qaytarıldı")
            text, kb = ms_panel(slot)
            await edit(cb, text, kb)

        elif action == "cancel":
            if bc.get("stage") == "sending":
                await cb.answer("📢 Broadcast gedir — əvvəl ⏹ Dayandır ilə saxla", show_alert=True)
                return
            await delete_preview()
            bc_reset()
            reset_inputs()
            await cb.answer("❌ Ləğv edildi")
            try:
                await cb.message.delete()
            except Exception:
                await edit(cb, "❌ <b>Ləğv edildi.</b>\n<i>Yenidən açmaq üçün /menu</i>")

        elif action == "close":
            await cb.answer()
            try:
                await cb.message.delete()
            except Exception:
                await edit(cb, "✖️ Bağlandı. Yenidən açmaq üçün /menu")

        elif action == "speed":
            if speed_lock.locked():
                await cb.answer("Speedtest artıq işləyir...")
                return
            await cb.answer()
            async with speed_lock:
                await edit(cb, "🚀 <b>Speedtest</b>\n\n⏳ <i>Ölçülür... (30–60 saniyə çəkə bilər)</i>")
                try:
                    r = await run_speedtest()
                    text = (
                        "🚀 <b>Speedtest</b>\n\n"
                        f"📥 Download: <b>{r['down']:.2f} Mbps</b>\n"
                        f"📤 Upload: <b>{r['up']:.2f} Mbps</b>\n"
                        f"🏓 Ping: <b>{r['ping']:.1f} ms</b>\n\n"
                        f"🌐 Server: {escape(r['server'])}\n"
                        f"🏢 ISP: {escape(str(r['isp']))}"
                    )
                except Exception as e:
                    logger.error(f"Speedtest xətası: {e}")
                    text = f"🚀 <b>Speedtest</b>\n\n❌ <code>{escape(str(e))[:400]}</code>"
                await edit(cb, text, sub_kb("speed"))

        elif action == "fetch":
            await cb.answer()
            await edit(cb, "🖥 <b>Fastfetch</b>\n\n⏳ <i>Yoxlanılır...</i>")
            try:
                out = await run_fastfetch()
                text = f"🖥 <b>Fastfetch</b>\n\n<pre>{escape(out)}</pre>"
            except Exception as e:
                logger.error(f"Fastfetch xətası: {e}")
                text = f"🖥 <b>Fastfetch</b>\n\n❌ <code>{escape(str(e))[:400]}</code>"
            await edit(cb, text, sub_kb("fetch"))

        elif action in ("stats", "stats_chart", "stats_top"):
            await cb.answer()
            text, kb = await stats_view(action)
            await edit(cb, text, kb)

        elif action == "users":
            await cb.answer()
            reset_inputs()
            # köhnə format: users:<səhifə> · yeni: users:<filtr>:<səhifə>
            flt, page = "all", 0
            if len(parts) > 2 and parts[2].isdigit():
                page = int(parts[2])
            elif len(parts) > 2:
                flt = parts[2] if parts[2] in dict(USER_FILTERS) else "all"
                page = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
            text, kb = await users_view(flt, page)
            await edit(cb, text, kb)

        elif action == "user":
            await cb.answer()
            try:
                uid = int(parts[2])
            except (IndexError, ValueError):
                return
            back = ":".join(parts[3:]) or "users:all:0"
            text, kb = await user_card(uid, back)
            await edit(cb, text, kb)

        elif action == "grp":
            await cb.answer()
            reset_inputs()
            flt = parts[2] if len(parts) > 2 and parts[2] in dict(CHAT_FILTERS) else "groups"
            page = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
            text, kb = await groups_view(flt, page)
            await edit(cb, text, kb)

        elif action == "chat":
            try:
                chat_id = int(parts[2])
            except (IndexError, ValueError):
                await cb.answer()
                return
            await cb.answer("Yoxlanılır...")
            back = ":".join(parts[3:]) or "grp:groups:0"
            text, kb = await chat_card(chat_id, back)
            await edit(cb, text, kb)

        elif action == "chat_leave":
            try:
                chat_id = int(parts[2])
            except (IndexError, ValueError):
                await cb.answer()
                return
            await cb.answer()
            back = ":".join(parts[3:]) or "grp:groups:0"
            row = await asyncio.to_thread(get_db().get_chat, chat_id)
            title = escape((row or {}).get("title") or str(chat_id))
            await edit(
                cb,
                f"⚠️ <b>Bot bu çatdan çıxsın?</b>\n\n💬 <b>{title}</b>\n🆔 <code>{chat_id}</code>\n\n"
                "<i>Bot yenidən yalnız kimsə onu əlavə edəndə qayıda bilər.</i>",
                InlineKeyboardMarkup(inline_keyboard=[
                    [_btn("✅ Bəli, çıx", f"chat_leavey:{chat_id}:{back}")],
                    nav_row(f"chat:{chat_id}:{back}"),
                ]),
            )

        elif action == "chat_leavey":
            try:
                chat_id = int(parts[2])
            except (IndexError, ValueError):
                await cb.answer()
                return
            back = ":".join(parts[3:]) or "grp:groups:0"
            try:
                await context.bot.leave_chat(chat_id)
                note = "✅ Bot çatdan çıxdı"
            except Exception as e:
                note = f"Çıxmaq alınmadı: {str(e)[:120]}"
                logger.warning(f"leave_chat xətası ({chat_id}): {e}")
            await asyncio.to_thread(get_db().set_chat_active, chat_id, False)
            await cb.answer(note, show_alert=True)
            if back.startswith("grp:"):
                p2 = back.split(":")
                text, kb = await groups_view(p2[1] if len(p2) > 1 else "groups", 0)
            else:
                text, kb = await groups_view("groups", 0)
            await edit(cb, text, kb)

        elif action == "st":
            await cb.answer()
            reset_inputs()
            text, kb = await stars_panel()
            await edit(cb, text, kb)

        elif action in ("st_plan", "st_off", "st_set"):
            key = parts[2] if len(parts) > 2 else ""
            if key not in PLAN_MAP:
                await cb.answer()
                return
            reset_inputs()
            if action == "st_off":
                await asyncio.to_thread(set_price, key, None)
                await cb.answer("🚫 Satışdan çıxarıldı")
                text, kb = stars_plan_panel(key)
            elif action == "st_set":
                await cb.answer()
                stp.update(stage="await_price", plan=key, chat_id=cb.message.chat.id, panel_id=cb.message.message_id)
                days, label = PLAN_MAP[key]
                text = (f"⭐ <b>{escape(label)}</b>\n\n✍️ Qiyməti ulduzla yazın ({MIN_STARS}–{MAX_STARS}):\n"
                        "<i>məs.</i> <code>50</code>")
                kb = InlineKeyboardMarkup(inline_keyboard=[nav_row(f"st_plan:{key}")])
            else:
                await cb.answer()
                text, kb = stars_plan_panel(key)
            await edit(cb, text, kb)

        elif action == "st_pay":
            await cb.answer()
            text, kb = await payments_view(int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0)
            await edit(cb, text, kb)

        elif action in ("st_p", "st_ref", "st_refy"):
            try:
                pid = int(parts[2])
                page = int(parts[3]) if len(parts) > 3 else 0
            except (IndexError, ValueError):
                await cb.answer()
                return
            if action == "st_p":
                await cb.answer()
                text, kb = await payment_card(pid, page)
            elif action == "st_ref":
                await cb.answer()
                r = await asyncio.to_thread(get_db().get_payment, pid)
                if not r or r["refunded"]:
                    text, kb = await payment_card(pid, page)
                else:
                    days = PLAN_MAP.get(r["plan"], (None, ""))[0]
                    effect = ("premiuma toxunulmayacaq (bəxşişdir)" if r["plan"] == "tip"
                              else "premium tam silinəcək" if days is None
                              else f"premiumdan {days} gün çıxılacaq")
                    text = (f"⚠️ <b>Ödəniş #{pid} geri qaytarılsın?</b>\n\n"
                            f"⭐ {r['stars']} ulduz istifadəçiyə qaytarılacaq, {effect}.")
                    kb = InlineKeyboardMarkup(inline_keyboard=[
                        [_btn("✅ Bəli, qaytar", f"st_refy:{pid}:{page}")],
                        nav_row(f"st_p:{pid}:{page}"),
                    ])
            else:
                r = await asyncio.to_thread(get_db().get_payment, pid)
                if not r or r["refunded"]:
                    await cb.answer("Artıq qaytarılıb", show_alert=True)
                    text, kb = await payment_card(pid, page)
                else:
                    try:
                        await context.bot.refund_star_payment(
                            user_id=r["user_id"], telegram_payment_charge_id=r["charge_id"]
                        )
                    except Exception as e:
                        await cb.answer("Geri qaytarma alınmadı", show_alert=True)
                        text, kb = await payment_card(pid, page, f"❌ <code>{escape(str(e))[:250]}</code>")
                        await edit(cb, text, kb)
                        return
                    await asyncio.to_thread(get_db().mark_refunded, pid)
                    days = PLAN_MAP.get(r["plan"], (None, ""))[0]
                    if r["plan"] == "tip":
                        res = "tip"                    # bəxşiş — premiuma toxunulmur
                    else:
                        res = await asyncio.to_thread(get_db().shorten_premium, r["user_id"], days)
                    await cb.answer("↩️ Ulduzlar qaytarıldı")
                    try:
                        await context.bot.send_message(
                            r["user_id"],
                            f"↩️ <b>{r['stars']}⭐ hesabınıza qaytarıldı.</b>\n"
                            + ("Premium ləğv edildi." if res == "revoked" else
                               "Premium müddəti uyğun olaraq qısaldıldı." if isinstance(res, int) else ""),
                            parse_mode="HTML",
                        )
                    except Exception:
                        pass
                    note = {"revoked": "💎 Premium silindi.", "lifetime": "💎 Ömürlük premium toxunulmadı.",
                            "tip": "💝 Bəxşiş idi — premiuma toxunulmadı.",
                            None: "ℹ️ İstifadəçinin aktiv premiumu yox idi."}.get(
                        res, f"💎 Premium yeni bitmə: {fmt_dt(res)}" if isinstance(res, int) else "")
                    text, kb = await payment_card(pid, page, f"✅ Qaytarıldı. {note}")
            await edit(cb, text, kb)

        elif action == "usearch_res":
            # istifadəçi kartından axtarış nəticələrinə qayıt
            await cb.answer()
            query = us.get("last_query")
            if not query:
                text, kb = await users_view("all", 0)
                await edit(cb, text, kb)
                return
            rows = await asyncio.to_thread(get_db().search_users, query, int(time.time()))
            kb = [[_btn(
                f"{'💎 ' if u['premium'] else ''}{'🚫 ' if u['blocked'] else ''}{display_name(u)[:22]} · 🎵{u['dl_total']}",
                f"user:{u['user_id']}:usearch_res",
            )] for u in rows]
            kb.append([_btn("🔍 Yenidən axtar", "usearch")])
            kb.append(nav_row("users:all:0"))
            await edit(cb, f"🔍 <b>\"{escape(query)}\"</b> üzrə <b>{len(rows)}</b> nəticə",
                       InlineKeyboardMarkup(inline_keyboard=kb))

        elif action == "prm_gv":
            # istifadəçi kartından birbaşa premium vermə
            await cb.answer()
            try:
                uid = int(parts[2])
            except (IndexError, ValueError):
                return
            card_back = ":".join(parts[3:]) or "users:all:0"
            u = await asyncio.to_thread(get_db().get_user, uid)
            text, kb = await duration_panel(uid, u, back=f"user:{uid}:{card_back}")
            await edit(cb, text, kb)

        elif action == "usearch":
            await cb.answer()
            reset_inputs()
            us.update(stage="await_query", chat_id=cb.message.chat.id, panel_id=cb.message.message_id)
            await edit(
                cb,
                "🔍 <b>İstifadəçi axtarışı</b>\n\n"
                "ID, <code>@username</code> və ya adın bir hissəsini yazın:",
                InlineKeyboardMarkup(inline_keyboard=[nav_row("users:all:0")]),
            )

        elif action == "csv":
            await cb.answer("Hazırlanır...")
            now = int(time.time())
            users = await asyncio.to_thread(get_db().users_export, now)
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow(["user_id", "username", "first_name", "last_name", "first_seen", "last_seen",
                        "blocked", "premium", "premium_until", "downloads_total", "downloads_30d", "last_download"])
            for u in users:
                w.writerow([
                    u["user_id"], u["username"] or "", u["first_name"] or "", u["last_name"] or "",
                    fmt_dt(u["first_seen"]), fmt_dt(u["last_seen"]), u["blocked"],
                    int(bool(u["premium"])),
                    ("ömürlük" if u["premium"] and u["premium_until"] is None else fmt_dt(u["premium_until"]) if u["premium"] else ""),
                    u["dl_total"], u["dl_30d"], fmt_dt(u["last_download"]) if u["last_download"] else "",
                ])
            await cb.message.answer_document(
                BufferedInputFile(buf.getvalue().encode("utf-8-sig"), filename="users.csv"),
                caption=f"👥 {len(users)} istifadəçi",
            )

        else:
            await cb.answer()

    logger.info("✅ Menu plugin yükləndi")
