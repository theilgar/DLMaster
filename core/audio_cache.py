"""
📦 Mahnı depo kanalı və keş — core/audio_cache.py

Hər yüklənən mahnı depo kanalına (default: @dllmasterdepo) göndərilir və onun Telegram file_id-si
bazada video_id ilə saxlanılır. Eyni mahnını kim istəsə, YouTube-dan yükləmədən dərhal göndərilir.

config.env:
  DEPO_CHANNEL=@dllmasterdepo     (və ya kanalın rəqəmli ID-si: -100...)
Bot kanalda "Mesaj göndərmə" icazəsi olan admin olmalıdır.
"""
import asyncio
import contextlib
import logging
import os
import time
from html import escape

from aiogram.types import BufferedInputFile, FSInputFile

from core import song_names as names
from core.database import get_db

logger = logging.getLogger(__name__)

_locks = {}
_depo = {"id": None, "checked": 0, "error": None}
RETRY_AFTER = 600          # kanal tapılmasa 10 dəq. sonra yenidən yoxla


def depo_setting() -> str:
    return (os.getenv("DEPO_CHANNEL") or "@dllmasterdepo").strip()


def lock_for(video_id: str) -> asyncio.Lock:
    """Eyni mahnını iki nəfər eyni anda istəyəndə ikinci birincini gözləsin, iki dəfə yüklənməsin."""
    lock = _locks.get(video_id)
    if lock is None:
        if len(_locks) > 2000:          # yaddaş böyüməsin
            for k in [k for k, l in _locks.items() if not l.locked()][:1000]:
                _locks.pop(k, None)
        lock = _locks[video_id] = asyncio.Lock()
    return lock


async def depo_chat_id(bot):
    """Depo kanalının rəqəmli ID-si (username-dən bir dəfə çevrilir); əlçatan deyilsə None."""
    if _depo["id"]:
        return _depo["id"]
    if time.time() - _depo["checked"] < RETRY_AFTER and _depo["checked"]:
        return None
    _depo["checked"] = time.time()
    ref = depo_setting()
    try:
        chat = await bot.get_chat(int(ref) if ref.lstrip("-").isdigit() else ref)
        _depo["id"], _depo["error"] = chat.id, None
        logger.info(f"📦 Depo kanalı: {getattr(chat, 'title', ref)} ({chat.id})")
        return chat.id
    except Exception as e:
        _depo["error"] = str(e)
        logger.warning(f"📦 Depo kanalı əlçatan deyil ({ref}): {e} — botu kanala admin kimi əlavə edin")
        return None


def depo_status() -> dict:
    return {"ref": depo_setting(), "id": _depo["id"], "error": _depo["error"]}


def get_cached(video_id: str):
    if not video_id:
        return None
    try:
        return get_db().cache_get(video_id)
    except Exception as e:
        logger.warning(f"Keş oxunmadı: {e}")
        return None


def refetch_once(video_id: str) -> bool:
    """Artisti "YouTube" olan köhnə keşi yenidən yükləmək olar? Hər video üçün yalnız bir dəfə True.
    Əvvəl hər dəfə yenidən yüklənirdi — artist yenə tapılmayanda eyni mahnı depoya təkrar-təkrar düşürdü."""
    if not video_id:
        return False
    try:
        db = get_db()
        with db._lock:
            db._conn.execute("CREATE TABLE IF NOT EXISTS cache_refetch (video_id TEXT PRIMARY KEY, ts INTEGER)")
            cur = db._conn.execute("INSERT OR IGNORE INTO cache_refetch VALUES (?, ?)", (video_id, int(time.time())))
            db._conn.commit()
            return cur.rowcount > 0
    except Exception as e:
        logger.debug(f"cache_refetch: {e}")
        return False


def mark_hit(video_id: str):
    try:
        get_db().cache_hit(video_id)
    except Exception:
        pass


def invalidate(video_id: str):
    try:
        get_db().cache_delete(video_id)
        mark_dirty()
        logger.info(f"📦 Keşdən silindi (file_id etibarsızdır): {video_id}")
    except Exception:
        pass


def save_from_message(video_id: str, msg, title=None, performer=None, duration=None, yt_title=None):
    """Göndərilmiş audio mesajından file_id-ni keşə yazır."""
    audio = getattr(msg, "audio", None)
    if not video_id or not audio:
        return None
    try:
        get_db().cache_put(
            video_id, audio.file_id, audio.file_unique_id, title or audio.title, performer or audio.performer,
            duration or audio.duration, audio.file_size, msg.chat.id, msg.message_id, yt_title=yt_title,
        )
        mark_dirty()
    except Exception as e:
        logger.warning(f"Keşə yazılmadı: {e}")
    return audio.file_id


# ───────────── 🔎 Depo indeksi: adla axtarış və dublikatların qarşısı ─────────────
_index = {"rows": [], "built": 0, "dirty": True}
INDEX_TTL = 300


def mark_dirty():
    _index["dirty"] = True


def _rows():
    if _index["dirty"] or time.time() - _index["built"] > INDEX_TTL:
        try:
            rows = get_db().cache_all()
        except Exception as e:
            logger.warning(f"Depo indeksi qurulmadı: {e}")
            return _index["rows"]
        posts = {}
        for r in rows:                       # birləşdirilmiş dublikatlar: hər post bir dəfə
            if not r.get("file_id") or (r.get("performer") or "").strip() in ("", "YouTube"):
                continue
            k = r.get("unique_id") or r["file_id"]
            p = posts.get(k)
            if p is None:
                r["_tt"] = names.track_tokens(r.get("title") or "")
                r["_at"] = names.artist_tokens(r.get("performer") or "")
                posts[k] = r
            else:
                p["hits"] = (p.get("hits") or 0) + (r.get("hits") or 0)
        _index.update(rows=list(posts.values()), built=time.time(), dirty=False)
    return _index["rows"]


def search_depo(query: str, limit: int = 5) -> list:
    """Sorğuya uyğun depo mahnıları (ən uyğun və ən çox istənilən əvvəl)."""
    q = names.tokens(query)
    if not q:
        return []
    found = []
    for r in _rows():
        s = names.query_score(query, r.get("performer") or "", r.get("title") or "")
        if s >= 0.75:
            found.append((s, r.get("hits") or 0, r))
    found.sort(key=lambda x: (-x[0], -x[1]))
    return [r for _, _, r in found[:limit]]


def find_same(artist: str, track: str, duration: int = 0):
    """Eyni mahnı (ad + artist, müddət ±8 san.) depoda artıq varsa onun sətri."""
    tt, at = names.track_tokens(track), names.artist_tokens(artist)
    if not tt or not at:
        return None
    best = None
    for r in _rows():
        if r["_tt"] != tt or not (r["_at"] & at):
            continue
        d = r.get("duration") or 0
        if duration and d and abs(int(duration) - int(d)) > 8:
            continue
        if best is None or (r.get("hits") or 0) > (best.get("hits") or 0):
            best = r
    return best


def alias(video_id: str, row: dict, yt_title=None):
    """Bu video_id-ni depodakı mövcud posta bağlayır (təkrar yükləmə olmadan)."""
    try:
        get_db().cache_put(video_id, row["file_id"], row.get("unique_id"), row.get("title"), row.get("performer"),
                           row.get("duration"), row.get("size"), row.get("chat_id"), row.get("message_id"),
                           yt_title=yt_title)
        mark_dirty()
        logger.info(f"📦 Dublikat qarşısı: {video_id} → depodakı '{row.get('performer')} - {row.get('title')}'")
    except Exception as e:
        logger.warning(f"Alias yazılmadı: {e}")


def cached_ids(video_ids) -> set:
    try:
        return get_db().cache_cached_ids(list(video_ids))
    except Exception:
        return set()


async def upload_to_depo(bot, file_path, filename: str, title: str, performer: str,
                         duration: int, url: str, video_id: str, thumb: bytes = None, yt_title: str = None):
    """Faylı (yol və ya bytes) depo kanalına yükləyir, keşə yazır və file_id qaytarır. Alınmasa None."""
    chat_id = await depo_chat_id(bot)
    if not chat_id:
        return None
    title = names.clean_track(title or "") if title else title           # tarix, (Official Video) və s.
    performer = names.clean_artist(performer or "") if performer and performer != "YouTube" else performer
    tag = "#v" + (video_id or "").replace("-", "_")
    caption = (
        f"🎵 <b>{escape(performer or '')}{' — ' if performer else ''}{escape(title or '')}</b>\n"
        f"🔗 <a href=\"{escape(url, quote=True)}\">YouTube</a> · {tag}"
    )
    async def _send_and_save():
        msg = await bot.send_audio(
            chat_id=chat_id,
            audio=(BufferedInputFile(file_path, filename=filename) if isinstance(file_path, (bytes, bytearray))
                   else FSInputFile(file_path, filename=filename)),
            title=(title or "")[:64] or None,
            performer=(performer or "")[:64] or None,
            duration=int(duration or 0) or None,
            caption=caption,
            parse_mode="HTML",
            disable_notification=True,
            thumbnail=BufferedInputFile(thumb, filename="cover.jpg") if thumb else None,
        )
        # keşə yazma göndərmə ilə bir yerdədir: restart / ləğv arada olsa belə post bazaya düşür
        return await asyncio.to_thread(save_from_message, video_id, msg, title, performer, duration,
                                       yt_title=yt_title)

    try:
        # 🛡 shield: işçi ləğv olunsa (restart, ⏹) göndərmə yarımçıq qalmır — yoxsa post depoda olur,
        # bazada olmur və növbəti dəfə eyni mahnı yenidən yüklənirdi
        inner = asyncio.ensure_future(_send_and_save())
        # çağıran ləğv olunsa da daxili xəta loglansın ("exception in shielded future" əvəzinə aydın mesaj)
        inner.add_done_callback(lambda f: f.cancelled() or not f.exception() or
                                logger.warning(f"📦 Depo yazısı (fon): {f.exception()}"))
        return await asyncio.shield(inner)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.warning(f"📦 Depoya yüklənmədi: {e}")
        if "chat not found" in str(e).lower() or "not enough rights" in str(e).lower() \
                or "forbidden" in str(e).lower():
            _depo["id"], _depo["error"], _depo["checked"] = None, str(e), time.time()
        return None


async def recover_bad_file(context, video_id: str) -> str:
    """
    Keşdəki file_id işləmədi. Əvvəl yenidən yükləmə əvəzinə depodakı postu sink çatına forward edib
    botun yeni file_id-sini alırıq (köməkçi bot file_id-si, çevrilməmiş indeks postu və s.).
    'retry'    — keş düzəldi, eyni postdan göndər
    'reupload' — post depoda yoxdur, keş silindi → yenidən yüklənəcək
    Müvəqqəti xətada (flood) RuntimeError — dublikat yaratmamaq üçün yenidən yükləmirik.
    """
    c = get_cached(video_id)
    pool = getattr(context, "depo_helpers", None)
    if c and c.get("chat_id") and c.get("message_id") and pool is not None:
        try:
            audio = await pool._forward_audio(context.bot, c["chat_id"], c["message_id"], None)
        except Exception as e:
            err = str(e).lower()
            if "not found" in err or "message_id_invalid" in err or "can't be forwarded" in err:
                logger.info(f"📦 Depo postu yoxdur ({c['chat_id']}/{c['message_id']}) — yenidən yüklənəcək")
                await asyncio.to_thread(_drop_post, c["chat_id"], c["message_id"])
                return "reupload"
            raise RuntimeError(f"Depo müvəqqəti əlçatan deyil, bir az sonra yenidən yoxla ({str(e)[:80]})")
        new = {"file_id": audio.file_id, "unique_id": audio.file_unique_id, "title": c.get("title"),
               "performer": c.get("performer"), "duration": c.get("duration"), "size": audio.file_size,
               "chat_id": c["chat_id"], "message_id": c["message_id"]}
        await asyncio.to_thread(get_db().cache_repoint, c["chat_id"], c["message_id"], new)
        with contextlib.suppress(Exception):
            pool.foreign.pop(c["file_id"], None)
            pool.converted[c["file_id"]] = audio.file_id
            await asyncio.to_thread(pool._save_converted, c["file_id"], audio.file_id)
        mark_dirty()
        logger.info(f"📦 file_id depo postundan bərpa olundu: {video_id}")
        return "retry"
    invalidate(video_id)
    return "reupload"


def _drop_post(chat_id, message_id):
    """Silinmiş depo postuna bağlı bütün keş sətirləri (aliaslar da)."""
    db = get_db()
    with db._lock:
        db._conn.execute("DELETE FROM audio_cache WHERE chat_id=? AND message_id=?", (chat_id, message_id))
        db._conn.commit()
    mark_dirty()


def is_bad_file_id(err: Exception) -> bool:
    e = str(err).lower()
    return any(k in e for k in ("wrong file identifier", "file reference", "wrong remote file",
                                "file_id", "failed to get http url content"))
