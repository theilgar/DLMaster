"""
🩹 Artist düzəlişi — yalnız creator.

Depoda artisti "YouTube" (və ya boş) yazılmış mahnıları tapır, sayını və nümunələrini göstərir,
istəsən hamısını yenidən yükləyir: artist song.link → YouTube Music → kanal adından götürülür,
yeni fayl depoya düşür, köhnə (səhv) post depodan silinir.

  /fix               — panel
  /menu → 📦 Depo doldurucu → 🩹 Artist düzəlişi
"""
import asyncio
import logging
import threading
import time
from html import escape

from aiogram import F, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from core.database import get_db

logger = logging.getLogger(__name__)

DELAY = 3          # mahnılar arası fasilə (san.)
BAD_SQL = "performer IS NULL OR TRIM(performer) IN ('', 'YouTube')"


def bad_rows(limit=None):
    db = get_db()
    with db._lock:
        q = f"SELECT video_id, title, performer, duration, chat_id, message_id FROM audio_cache WHERE {BAD_SQL} " \
            f"ORDER BY hits DESC, created DESC" + (f" LIMIT {int(limit)}" if limit else "")
        return [dict(r) for r in db._conn.execute(q).fetchall()]


def bad_count() -> int:
    db = get_db()
    with db._lock:
        return db._conn.execute(f"SELECT COUNT(*) FROM audio_cache WHERE {BAD_SQL}").fetchone()[0]


def setup(context):
    dp = context.dp
    bot = context.bot
    job = {"task": None, "stop": None, "done": 0, "fixed": 0, "failed": 0, "total": 0,
           "current": None, "started": None, "panel": None, "last_edit": 0}

    def is_creator(uid) -> bool:
        return bool(context.creator_id) and uid == context.creator_id

    def running() -> bool:
        return bool(job["task"] and not job["task"].done())

    def panel(note: str = ""):
        n = bad_count()
        samples = bad_rows(8)
        lines = [
            "🩹 <b>Artist düzəlişi</b>\n━━━━━━━━━━━━━━━━━━",
            f"🎵 Depoda artisti <b>\"YouTube\"</b> / boş olan: <b>{n}</b> mahnı",
        ]
        if samples:
            lines.append("")
            for r in samples:
                lines.append(f"• {escape((r['title'] or r['video_id'])[:50])} <i>({escape(r['performer'] or 'boş')})</i>")
            if n > len(samples):
                lines.append(f"<i>… və daha {n - len(samples)}</i>")
        if running() or job["total"]:
            lines.append("━━━━━━━━━━━━━━━━━━")
            state = "🔄 işləyir" if running() else "✅ bitdi"
            lines.append(f"{state}: {job['done']}/{job['total']} · ✅ düzəldi {job['fixed']} · ❌ {job['failed']}")
            if job["current"] and running():
                lines.append(f"⏳ <i>{escape(job['current'][:60])}</i>")
        if not n and not running():
            lines.append("\n✅ <i>Düzəldiləcək mahnı yoxdur.</i>")
        lines.append("━━━━━━━━━━━━━━━━━━\n<i>Hər mahnı yenidən yüklənir: artist song.link → YouTube Music → "
                     "kanal adından götürülür, köhnə post depodan silinir.</i>")
        if note:
            lines.append(f"\n{note}")
        rows = []
        if running():
            rows.append([InlineKeyboardButton(text="⏹ Dayandır", callback_data="fx:stop")])
        elif n:
            rows.append([InlineKeyboardButton(text=f"▶️ Hamısını düzəlt ({n})", callback_data="fx:start")])
        rows.append([InlineKeyboardButton(text="🔄 Yenilə", callback_data="fx:panel")])
        rows.append([InlineKeyboardButton(text="⬅️ Depo", callback_data="df:panel"),
                     InlineKeyboardButton(text="❌ Bağla", callback_data="fx:close")])
        return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)

    async def refresh_panel(force=False):
        if not job["panel"] or (not force and time.time() - job["last_edit"] < 4):
            return
        job["last_edit"] = time.time()
        text, kb = await asyncio.to_thread(panel)
        try:
            await bot.edit_message_text(chat_id=job["panel"][0], message_id=job["panel"][1], text=text,
                                        parse_mode="HTML", reply_markup=kb)
        except Exception:
            pass

    async def worker():
        fetch = getattr(context, "music_fetch_audio", None)
        if not fetch:
            job["failed"] = -1
            return
        rows = await asyncio.to_thread(bad_rows)
        job.update(total=len(rows), done=0, fixed=0, failed=0, started=time.time())
        for r in rows:
            if job["stop"].is_set():
                break
            job["current"] = r["title"] or r["video_id"]
            await refresh_panel()
            url = f"https://www.youtube.com/watch?v={r['video_id']}"
            try:
                # fetch_audio "YouTube" artistli keşi özü silib yenidən yükləyir
                res = await fetch(url, r["title"] or "", int(r["duration"] or 0), job["stop"])
                if res.get("path"):
                    try:
                        import os
                        os.remove(res["path"])
                    except OSError:
                        pass
                new = await asyncio.to_thread(get_db().cache_get, r["video_id"])
                if new and (new.get("performer") or "").strip() not in ("", "YouTube"):
                    job["fixed"] += 1
                    # köhnə səhv post depodan silinsin
                    if r["chat_id"] and r["message_id"] and (new.get("message_id") != r["message_id"]):
                        try:
                            await bot.delete_message(r["chat_id"], r["message_id"])
                        except Exception:
                            pass
                else:
                    job["failed"] += 1
            except Exception as e:
                if job["stop"].is_set():
                    break
                job["failed"] += 1
                logger.info(f"Artist düzəlişi alınmadı ({r['video_id']}): {e}")
            finally:
                job["done"] += 1
            await asyncio.sleep(DELAY)
        job["current"] = None

    async def run():
        try:
            await worker()
        finally:
            await refresh_panel(force=True)

    @dp.message(Command("fix"))
    async def fix_cmd(message: types.Message):
        if not message.from_user or not is_creator(message.from_user.id):
            return
        text, kb = await asyncio.to_thread(panel)
        sent = await message.answer(text, parse_mode="HTML", reply_markup=kb)
        job["panel"] = (sent.chat.id, sent.message_id)

    @dp.callback_query(F.data.startswith("fx:"))
    async def fix_callback(cb: types.CallbackQuery):
        if not is_creator(cb.from_user.id):
            await cb.answer("⛔ İcazə yoxdur", show_alert=True)
            return
        action = cb.data.split(":", 1)[1]
        job["panel"] = (cb.message.chat.id, cb.message.message_id)
        note = ""
        if action == "close":
            await cb.answer()
            try:
                await cb.message.delete()
            except Exception:
                pass
            return
        if action == "start":
            if running():
                await cb.answer("Artıq işləyir")
            elif not getattr(context, "music_fetch_audio", None):
                await cb.answer("music_plugin yüklənməyib", show_alert=True)
                return
            else:
                job["stop"] = threading.Event()
                job["task"] = asyncio.create_task(run())
                await cb.answer("▶️ Başladı")
                note = "▶️ Düzəliş başladı — panel avtomatik yenilənir."
        elif action == "stop":
            if job["stop"]:
                job["stop"].set()
            await cb.answer("⏹ Dayandırılır...")
            note = "⏹ Dayandırılır — hazırkı mahnı bitəndən sonra dayanacaq."
        else:
            await cb.answer()
        text, kb = await asyncio.to_thread(panel, note)
        try:
            await cb.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        except Exception:
            pass

    context.fix_artist_count = bad_count
    context.fix_artist_job = job
    logger.info("✅ Artist düzəlişi plugin-i yükləndi (/fix)")


async def teardown(context):
    job = getattr(context, "fix_artist_job", None)
    if job and job.get("stop"):
        job["stop"].set()
