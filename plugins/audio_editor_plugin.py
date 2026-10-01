"""
Audio redaktoru pluginu.

İstifadəçi botun şəxsi çatına mahnı (audio) göndərir → bot panel göstərir:
  ✏️ Ad  |  🎤 Artist  |  🖼 Thumbnail  |  ✅ Göndər  |  ❌ Ləğv et
İstifadəçi istədiyini seçib dəyişir, sonda "Göndər" basanda bot faylı ffmpeg ilə
yenidən yazır (ID3/MP4 teqləri + qapaq şəkli) və Telegram-a title/performer/thumbnail
ilə yükləyir.

Qeyd: Bot API botlara yalnız 20 MB-a qədər faylı yükləməyə icazə verir.
"""
import asyncio
import logging
import os
import time
from html import escape

from aiogram import types, F
from aiogram.types import FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton
from core.utilities import sanitize_filename
from core.database import caption_for_user

logger = logging.getLogger(__name__)

DOWNLOAD_DIR = "download"
MAX_DOWNLOAD = 20 * 1024 * 1024          # Bot API limiti
SESSION_TTL = 30 * 60                    # 30 dəq. fəaliyyətsizlikdən sonra sessiya silinir
EMBED_COVER_EXTS = {".mp3", ".m4a", ".flac"}   # qapaq şəklini faylın içinə yazmaq mümkün olan formatlar
MIME_EXT = {
    "audio/mpeg": ".mp3", "audio/mp3": ".mp3",
    "audio/mp4": ".m4a", "audio/x-m4a": ".m4a", "audio/aac": ".m4a", "audio/m4a": ".m4a",
    "audio/flac": ".flac", "audio/x-flac": ".flac",
    "audio/ogg": ".ogg", "audio/opus": ".ogg",
    "audio/wav": ".wav", "audio/x-wav": ".wav",
}


# ───────────────────────── ffmpeg köməkçiləri ─────────────────────────
async def run_ffmpeg(*args):
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, err = await proc.communicate()
    return proc.returncode, err.decode(errors="ignore").strip()


def _scale_filter(size: int) -> str:
    return f"scale='min({size},iw)':'min({size},ih)':force_original_aspect_ratio=decrease"


async def prepare_cover(src_img: str, base: str):
    """Şəkli JPEG-ə çevirir: qapaq (max 800px) və Telegram thumbnail-i (max 320px, ≤200 KB)."""
    cover, thumb = f"{base}_cover.jpg", f"{base}_thumb.jpg"
    for out, size, q in ((cover, 800, "3"), (thumb, 320, "5")):
        rc, err = await run_ffmpeg("-i", src_img, "-vf", _scale_filter(size), "-frames:v", "1", "-q:v", q, out)
        if rc != 0 or not os.path.exists(out):
            raise RuntimeError(f"Şəkil emal olunmadı: {err[-200:]}")
    if os.path.getsize(thumb) > 200 * 1024:
        await run_ffmpeg("-i", src_img, "-vf", _scale_filter(320), "-frames:v", "1", "-q:v", "12", thumb)
    return cover, thumb


async def write_tags(src: str, dst: str, ext: str, title: str, artist: str, cover_path: str = None):
    """Audionu yenidən qablaşdırır (kodek dəyişmir): title/artist teqləri + istəyə görə yeni qapaq."""
    embed = bool(cover_path) and ext in EMBED_COVER_EXTS
    args = ["-i", src]
    if embed:
        args += ["-i", cover_path, "-map", "0:a", "-map", "1:v"]
    else:
        # köhnə qapaq (əgər varsa) qalsın
        args += ["-map", "0:a", "-map", "0:v?"]
    args += ["-c", "copy"]
    if embed:
        args += ["-disposition:v:0", "attached_pic"]
        if ext == ".mp3":
            args += ["-id3v2_version", "3",
                     "-metadata:s:v", "title=Album cover",
                     "-metadata:s:v", "comment=Cover (front)"]
    args += ["-metadata", f"title={title}", "-metadata", f"artist={artist}", dst]

    rc, err = await run_ffmpeg(*args)
    if rc != 0 or not os.path.exists(dst):
        raise RuntimeError(f"ffmpeg xətası: {err[-300:]}")


def guess_ext(file_name: str, mime: str) -> str:
    ext = os.path.splitext(file_name or "")[1].lower()
    if ext in (".mp3", ".m4a", ".flac", ".ogg", ".opus", ".wav", ".aac"):
        return ext
    return MIME_EXT.get((mime or "").lower(), ".mp3")


def safe_remove(*paths):
    for p in paths:
        if p and os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                pass


# ───────────────────────── Panel ─────────────────────────
def panel_text(s) -> str:
    return (
        "🎧 <b>Audio redaktoru</b>\n\n"
        f"🎵 <b>Ad:</b> {escape(s['title'])}\n"
        f"🎤 <b>Artist:</b> {escape(s['artist']) if s['artist'] else '—'}\n"
        f"🖼 <b>Thumbnail:</b> {'✅ yeni seçildi' if s.get('cover') else 'orijinal'}\n\n"
        "<i>Nəyi dəyişmək istəyirsən?</i>"
    )


def _btn(text, data):
    return InlineKeyboardButton(text=text, callback_data=data)


def panel_kb(s) -> InlineKeyboardMarkup:
    thumb_row = [_btn("🖼 Thumbnail", "ae:thumb")]
    if s.get("cover"):
        thumb_row.append(_btn("↩️ Orijinal thumbnail", "ae:resetthumb"))
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn("✏️ Ad", "ae:title"), _btn("🎤 Artist", "ae:artist")],
        thumb_row,
        [_btn("✅ Göndər", "ae:apply"), _btn("❌ Ləğv et", "ae:cancel")],
    ])


def back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[_btn("⬅️ Geri", "ae:back")]])


# ───────────────────────── Plugin ─────────────────────────
def setup(context):
    dp = context.dp
    bot = context.bot
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)

    sessions = {}
    # music_plugin mətn yazarkən (ad/artist gözlənərkən) axtarış etməsin deyə paylaşılır
    context.audio_edit_sessions = sessions

    # ── köməkçilər ──
    def drop_session(uid):
        s = sessions.pop(uid, None)
        if s:
            safe_remove(s.get("cover"), s.get("thumb"))

    def purge_expired():
        now = time.time()
        for uid in [u for u, s in sessions.items() if now - s["touched"] > SESSION_TTL and not s.get("busy")]:
            drop_session(uid)

    async def safe_delete(chat_id, message_id):
        try:
            await bot.delete_message(chat_id, message_id)
        except Exception as e:
            logger.debug(f"Mesaj silinmədi: {e}")

    async def show_panel(s, text=None, kb="panel"):
        s["touched"] = time.time()
        if kb == "panel":
            kb = panel_kb(s)
        try:
            await bot.edit_message_text(
                chat_id=s["chat_id"], message_id=s["panel_id"],
                text=text or panel_text(s), reply_markup=kb, parse_mode="HTML",
            )
        except Exception as e:
            logger.debug(f"Panel yenilənmədi: {e}")

    # ── filterlər ──
    async def waiting_text(message: types.Message) -> bool:
        s = sessions.get(message.from_user.id)
        return bool(s and s.get("awaiting") in ("title", "artist"))

    async def waiting_thumb(message: types.Message) -> bool:
        s = sessions.get(message.from_user.id)
        return bool(s and s.get("awaiting") == "thumb")

    # ── 1) Audio gəldi → sessiya + panel ──
    @dp.message(F.chat.type == "private", F.audio | F.document.mime_type.startswith("audio/"))
    async def on_audio(message: types.Message):
        media = message.audio or message.document
        await start_session(
            chat_id=message.chat.id, uid=message.from_user.id, file_id=media.file_id,
            file_name=getattr(media, "file_name", None) or "", title=getattr(media, "title", None),
            artist=getattr(media, "performer", None), duration=getattr(media, "duration", None),
            mime=getattr(media, "mime_type", None), size=media.file_size, reply_to=message.message_id,
        )

    async def start_session(chat_id, uid, file_id, file_name="", title=None, artist=None,
                            duration=None, mime=None, size=None, reply_to=None):
        """
        Redaktə sessiyası + panel. Başqa plugin-lər də çağırır (context.audio_editor_start) —
        məs. music_plugin: inline ilə bota göndərilən mahnı üçün "✅ Bəli" basılanda.
        """
        if size and size > MAX_DOWNLOAD:
            await bot.send_message(
                chat_id,
                "❌ <b>Fayl 20 MB-dan böyükdür.</b>\n"
                "<i>Telegram Bot API botlara yalnız 20 MB-a qədər faylı yükləməyə icazə verir.</i>",
                parse_mode="HTML", reply_to_message_id=reply_to,
            )
            return None

        purge_expired()
        drop_session(uid)

        stem = os.path.splitext(file_name or "")[0].strip()
        if not title:
            if not artist and " - " in stem:
                artist, title = [p.strip() for p in stem.split(" - ", 1)]
            else:
                title = stem or "Naməlum"

        s = {
            "chat_id": chat_id,
            "file_id": file_id,
            "ext": guess_ext(file_name, mime),
            "duration": duration,
            "title": title,
            "artist": artist or "",
            "awaiting": None,
            "cover": None,
            "thumb": None,
            "busy": False,
            "touched": time.time(),
            "panel_id": None,
        }
        sessions[uid] = s
        panel = await bot.send_message(chat_id, panel_text(s), reply_markup=panel_kb(s), parse_mode="HTML",
                                       reply_to_message_id=reply_to)
        s["panel_id"] = panel.message_id
        return panel

    context.audio_editor_start = start_session

    # ── 2) Düymələr ──
    @dp.callback_query(F.data.startswith("ae:"))
    async def on_button(cb: types.CallbackQuery):
        action = cb.data.split(":", 1)[1]
        uid = cb.from_user.id
        s = sessions.get(uid)

        if not s or s["panel_id"] != cb.message.message_id:
            await cb.answer("Sessiya bitib. Audionu yenidən göndərin.", show_alert=True)
            return
        if s["busy"]:
            await cb.answer("Bir az gözləyin...")
            return
        s["touched"] = time.time()

        if action == "title":
            s["awaiting"] = "title"
            await show_panel(s, "✏️ <b>Yeni adı yazın:</b>", back_kb())
        elif action == "artist":
            s["awaiting"] = "artist"
            await show_panel(s, "🎤 <b>Yeni artist adını yazın:</b>\n<i>Boş buraxmaq üçün</i> <code>-</code> <i>yazın.</i>", back_kb())
        elif action == "thumb":
            s["awaiting"] = "thumb"
            await show_panel(s, "🖼 <b>Yeni thumbnail üçün şəkil göndərin</b>\n<i>(foto və ya fayl kimi)</i>", back_kb())
        elif action == "resetthumb":
            safe_remove(s.get("cover"), s.get("thumb"))
            s["cover"] = s["thumb"] = None
            await show_panel(s)
        elif action == "back":
            s["awaiting"] = None
            await show_panel(s)
        elif action == "cancel":
            drop_session(uid)
            await safe_delete(cb.message.chat.id, cb.message.message_id)
        elif action == "apply":
            await cb.answer()
            await apply_edit(uid, s)
            return
        await cb.answer()

    # ── 3) Yeni ad / artist (mətn) ──
    @dp.message(F.chat.type == "private", F.text, ~F.text.startswith("/"), waiting_text)
    async def on_text(message: types.Message):
        s = sessions.get(message.from_user.id)
        value = message.text.strip()
        if not s or not value:
            return
        field = s["awaiting"]
        s[field] = "" if (field == "artist" and value == "-") else value[:128]
        s["awaiting"] = None
        await safe_delete(message.chat.id, message.message_id)
        await show_panel(s)

    # ── 4) Yeni thumbnail (şəkil) ──
    @dp.message(F.chat.type == "private", F.photo | F.document.mime_type.startswith("image/"), waiting_thumb)
    async def on_photo(message: types.Message):
        s = sessions.get(message.from_user.id)
        if not s:
            return
        if message.photo:
            file_id = message.photo[-1].file_id
        else:
            if message.document.file_size and message.document.file_size > MAX_DOWNLOAD:
                await show_panel(s, "❌ <b>Şəkil 20 MB-dan böyükdür.</b>\n🖼 Başqa şəkil göndərin:", back_kb())
                return
            file_id = message.document.file_id

        base = os.path.join(DOWNLOAD_DIR, f"ae_{message.from_user.id}_{int(time.time())}")
        img = f"{base}_img"
        try:
            await bot.download(file_id, destination=img)
            cover, thumb = await prepare_cover(img, base)
        except Exception as e:
            logger.error(f"Thumbnail xətası: {e}")
            await show_panel(s, f"❌ <b>Şəkil emal olunmadı.</b>\n🖼 Başqa şəkil göndərin:", back_kb())
            return
        finally:
            safe_remove(img)

        safe_remove(s.get("cover"), s.get("thumb"))
        s["cover"], s["thumb"], s["awaiting"] = cover, thumb, None
        await safe_delete(message.chat.id, message.message_id)
        await show_panel(s)

    # ── 5) Tətbiq et və göndər ──
    async def apply_edit(uid, s):
        s["busy"] = True
        s["awaiting"] = None
        await show_panel(s, "⏳ <i>İşlənir...</i>", None)

        base = os.path.join(DOWNLOAD_DIR, f"ae_{uid}_{int(time.time())}")
        ext = s["ext"]
        src, dst = f"{base}_src{ext}", f"{base}_out{ext}"
        try:
            await bot.download(s["file_id"], destination=src)

            send_path = src
            try:
                await write_tags(src, dst, ext, s["title"], s["artist"], s.get("cover"))
                send_path = dst
            except Exception as e:
                logger.warning(f"Teq yazma (qapaqlı) alınmadı: {e}")
                try:  # qapaq faylın içinə yazılmasa da Telegram thumbnail-i yenə tətbiq olunur
                    await write_tags(src, dst, ext, s["title"], s["artist"], None)
                    send_path = dst
                except Exception as e2:
                    logger.warning(f"Teq yazma alınmadı, orijinal fayl göndəriləcək: {e2}")

            fname = sanitize_filename(f"{s['artist']} - {s['title']}" if s["artist"] else s["title"])[:100] or "audio"
            kwargs = dict(
                chat_id=s["chat_id"],
                audio=FSInputFile(send_path, filename=f"{fname}{ext}"),
                title=s["title"][:64],
                caption="<i>via @dllmasterbot</i>" if caption_for_user(uid) else None,
                parse_mode="HTML",
            )
            if s["artist"]:
                kwargs["performer"] = s["artist"][:64]
            if s.get("duration"):
                kwargs["duration"] = int(s["duration"])
            if s.get("thumb"):
                kwargs["thumbnail"] = FSInputFile(s["thumb"])

            await bot.send_audio(**kwargs)
            await safe_delete(s["chat_id"], s["panel_id"])
            drop_session(uid)

        except Exception as e:
            logger.error(f"Audio redaktə xətası: {e}", exc_info=True)
            s["busy"] = False
            await show_panel(s, f"❌ <b>Xəta:</b> <code>{escape(str(e))[:200]}</code>\n\n" + panel_text(s))
        finally:
            safe_remove(src, dst)
            s["busy"] = False
