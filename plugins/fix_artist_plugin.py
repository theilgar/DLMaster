"""
🩹 Depo təmizliyi — yalnız creator.

  🔁 Dublikatlar   — eyni mahnı (artist + ad, müddət ±8 san.) depoya bir neçə dəfə düşübsə birləşdirilir:
                     ən çox istənilən post qalır, qalan video_id-lər ona bağlanır, artıq postlar depodan silinir.
  🏷 Zibilli adlar — "(Official Video)", tarix (12.05.2024), "Yeni 2024", #tag, emoji və s. olan adlar təmizlənir.
  🎤 Artisti "YouTube" / boş olanlar.
  ❓ YouTube adı ilə uyğun gəlməyənlər — depodakı ad videonun YouTube adı ilə müqayisə olunur
                     (YouTube adları oEmbed ilə alınıb bazada saxlanır).

Ad düzəldiləndə fayl YouTube-dan YENİDƏN YÜKLƏNMİR: depodakı fayl Telegram-dan götürülüb düzgün adla
təzədən depoya göndərilir, köhnə post silinir, bütün bağlı video_id-lər yeni posta yönəldilir.

  /fix               — panel
  /menu → 📦 Depo doldurucu → 🩹 Depo təmizliyi
"""
import asyncio
import io
import json
import logging
import os
import threading
import time
from html import escape

import aiohttp
from aiogram import F, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from core import audio_cache
from core import song_names as names
from core.database import get_db
from core.utilities import sanitize_filename

logger = logging.getLogger(__name__)

DELAY = 3                     # yenidən göndərmələr arası fasilə (san.)
CHECK_CONCURRENCY = 5         # YouTube adlarının yoxlanması (oEmbed)
DUP_MAX_DIFF = 8              # dublikat: müddət fərqi (san.)
BAD_PERFORMERS = ("", "youtube", "naməlum", "unknown")
NO_YT = "-"                   # YouTube adı alınmadı (video silinib / embed bağlıdır)
TG_DOWNLOAD_LIMIT = 20 * 1024 * 1024
ANALYSIS_TTL = 60


def bad_performer(p) -> bool:
    return (p or "").strip().lower() in BAD_PERFORMERS


# ───────────── analiz (bazadan, şəbəkəsiz) ─────────────
def load_posts() -> list:
    """audio_cache sətirlərini depo postlarına görə qruplaşdırır (birləşdirilmiş dublikatlar bir post)."""
    posts = {}
    for r in get_db().cache_all():
        key = (r["chat_id"], r["message_id"]) if r.get("message_id") else ("f", r["file_id"])
        p = posts.get(key)
        if p is None:
            p = posts[key] = {**r, "vids": [], "hits": 0, "yt": None, "yt_vid": None,
                              "all_checked": True, "has_unchecked_yt": False}
        p["vids"].append(r["video_id"])
        p["hits"] += r.get("hits") or 0
        if r.get("yt_title") and r["yt_title"] != NO_YT and not p["yt"]:
            p["yt"], p["yt_vid"] = r["yt_title"], r["video_id"]
        if not r.get("checked"):
            p["all_checked"] = False
        if not r.get("yt_title"):
            p["has_unchecked_yt"] = True
    return list(posts.values())


def find_duplicates(posts) -> list:
    """[[saxlanılacaq, dublikat1, ...], ...]"""
    buckets = {}
    for p in posts:
        if bad_performer(p.get("performer")):
            continue
        tt = names.track_tokens(p.get("title") or "")
        if tt:
            buckets.setdefault(tt, []).append(p)
    groups = []
    for items in buckets.values():
        if len(items) < 2:
            continue
        items.sort(key=lambda p: (-(p["hits"] or 0), p.get("created") or 0))
        clusters = []
        for p in items:
            at = names.artist_tokens(p.get("performer") or "")
            for c in clusters:
                lead = c[0]
                d1, d2 = int(lead.get("duration") or 0), int(p.get("duration") or 0)
                if (names.artist_tokens(lead.get("performer") or "") & at) and \
                        not (d1 and d2 and abs(d1 - d2) > DUP_MAX_DIFF):
                    c.append(p)
                    break
            else:
                clusters.append([p])
        groups += [c for c in clusters if len(c) > 1]
    return groups


def is_junk(p) -> bool:
    t, a = p.get("title") or "", p.get("performer") or ""
    return names.tokens(names.clean_track(t)) != names.tokens(t) or \
        (not bad_performer(a) and names.tokens(names.clean_artist(a)) != names.tokens(a))


def is_mismatch(p) -> bool:
    return bool(p["yt"]) and not p["all_checked"] and not bad_performer(p.get("performer")) and \
        not names.meta_matches(p.get("performer") or "", p.get("title") or "", p["yt"])


def analyse() -> dict:
    posts = load_posts()
    dups = find_duplicates(posts)
    dup_ids = {id(x) for g in dups for x in g[1:]}
    live = [p for p in posts if id(p) not in dup_ids]
    res = {
        "posts": len(posts),
        "dups": dups,
        "dup_extra": sum(len(g) - 1 for g in dups),
        "bad_artist": [p for p in live if bad_performer(p.get("performer"))],
        "junk": [p for p in live if not bad_performer(p.get("performer")) and is_junk(p)],
        "mismatch": [p for p in live if is_mismatch(p) and not is_junk(p)],
        "unchecked": [p for p in posts if p["has_unchecked_yt"]],
        "time": time.time(),
    }
    res["fix_total"] = len(res["bad_artist"]) + len(res["junk"]) + len(res["mismatch"])
    return res


# ───────────── YouTube adı (oEmbed) ─────────────
async def yt_oembed(session, vid: str):
    """(ad, kanal) | (None, None) — video yoxdursa / embed bağlıdırsa."""
    url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={vid}&format=json"
    async with session.get(url) as r:
        if r.status in (401, 403, 404):
            return None, None
        if r.status != 200:
            raise RuntimeError(f"oEmbed status {r.status}")
        data = json.loads(await r.text())
    return data.get("title"), data.get("author_name")


async def fetch_thumb(vid: str):
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as s:
            async with s.get(f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg") as r:
                data = await r.read() if r.status == 200 else None
        return data if data and len(data) <= 200 * 1024 else None
    except Exception:
        return None


def setup(context):
    dp = context.dp
    bot = context.bot
    job = {"task": None, "stop": None, "kind": None, "done": 0, "ok": 0, "failed": 0, "total": 0,
           "current": None, "panel": None, "last_edit": 0, "note": ""}
    cache = {"a": None}

    def is_creator(uid) -> bool:
        return bool(context.creator_id) and uid == context.creator_id

    def running() -> bool:
        return bool(job["task"] and not job["task"].done())

    def get_analysis(force=False) -> dict:
        a = cache["a"]
        if force or not a or time.time() - a["time"] > ANALYSIS_TTL:
            a = cache["a"] = analyse()
        return a

    def problem_count() -> int:
        """depo_filler panelindəki düymə üçün — ağır analiz etmir, son nəticəni qaytarır."""
        a = cache["a"]
        if a:
            return a["fix_total"] + a["dup_extra"]
        try:
            db = get_db()
            with db._lock:
                return db._conn.execute(
                    "SELECT COUNT(*) FROM audio_cache WHERE performer IS NULL OR TRIM(performer) IN ('', 'YouTube')"
                ).fetchone()[0]
        except Exception:
            return 0

    KIND_LABELS = {"merge": "🔁 Dublikatlar birləşdirilir", "check": "🔎 YouTube adları yoxlanılır",
                   "fix": "🩹 Adlar düzəldilir"}

    def panel(note: str = ""):
        a = get_analysis(force=not running())
        L = ["🩹 <b>Depo təmizliyi</b>\n━━━━━━━━━━━━━━━━━━",
             f"📦 Depoda: <b>{a['posts']}</b> post",
             f"🔁 Dublikat: <b>{len(a['dups'])}</b> mahnı · <b>{a['dup_extra']}</b> artıq post",
             f"🏷 Adında tarix / zibil: <b>{len(a['junk'])}</b>",
             f"🎤 Artisti \"YouTube\" / boş: <b>{len(a['bad_artist'])}</b>",
             f"❓ YouTube adı ilə uyğun deyil: <b>{len(a['mismatch'])}</b>"
             + (f" <i>(yoxlanılmayıb: {len(a['unchecked'])})</i>" if a["unchecked"] else "")]
        samples = []
        for g in a["dups"][:2]:
            name = f"{g[0].get('performer') or ''} - {g[0].get('title') or ''}"
            samples.append(f"🔁 {escape(name[:55])} ×{len(g)}")
        for p in a["junk"][:3]:
            t = p.get("title") or ""
            samples.append(f"🏷 {escape(t[:40])} → {escape(names.clean_track(t)[:30])}")
        for p in a["mismatch"][:3]:
            name = f"{p.get('performer') or ''} - {p.get('title') or ''}"
            samples.append(f"❓ {escape(name[:40])} ≠ <i>{escape(p['yt'][:40])}</i>")
        if samples:
            L += ["", *samples]
        if running() or job["total"]:
            L.append("━━━━━━━━━━━━━━━━━━")
            state = KIND_LABELS.get(job["kind"], "") if running() else "✅ Son iş bitdi"
            L.append(f"{state}: {job['done']}/{job['total']} · ✅ {job['ok']} · ❌ {job['failed']}")
            if job["current"] and running():
                L.append(f"⏳ <i>{escape(job['current'][:60])}</i>")
        if job["note"]:
            L.append(f"ℹ️ {escape(job['note'])}")
        L.append("━━━━━━━━━━━━━━━━━━\n<i>Sıra: əvvəl dublikatları birləşdir → YouTube adlarını yoxla → adları "
                 "düzəlt. Fayl YouTube-dan təkrar yüklənmir, depodakı fayl düzgün adla yenidən göndərilir.</i>")
        if note:
            L.append(f"\n{note}")
        rows = []
        if running():
            rows.append([InlineKeyboardButton(text="⏹ Dayandır", callback_data="fx:stop")])
        else:
            if a["dup_extra"]:
                rows.append([InlineKeyboardButton(text=f"🔁 Dublikatları birləşdir ({a['dup_extra']})",
                                                  callback_data="fx:merge")])
            if a["unchecked"]:
                rows.append([InlineKeyboardButton(text=f"🔎 YouTube adlarını yoxla ({len(a['unchecked'])})",
                                                  callback_data="fx:check")])
            if a["fix_total"]:
                rows.append([InlineKeyboardButton(text=f"🩹 Adları düzəlt ({a['fix_total']})",
                                                  callback_data="fx:fix")])
        rows.append([InlineKeyboardButton(text="🔄 Yenilə", callback_data="fx:panel")])
        rows.append([InlineKeyboardButton(text="⬅️ Depo", callback_data="df:panel"),
                     InlineKeyboardButton(text="❌ Bağla", callback_data="fx:close")])
        return "\n".join(L), InlineKeyboardMarkup(inline_keyboard=rows)

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

    def post_fields(row: dict) -> dict:
        return {k: row.get(k) for k in ("file_id", "unique_id", "title", "performer", "duration", "size",
                                        "chat_id", "message_id")}

    async def delete_post(p):
        if p.get("chat_id") and p.get("message_id"):
            try:
                await bot.delete_message(p["chat_id"], p["message_id"])
            except Exception as e:
                logger.debug(f"Depo postu silinmədi ({p['message_id']}): {e}")

    # ── 🔁 dublikatlar ──
    async def do_merge():
        a = await asyncio.to_thread(get_analysis, True)
        groups = a["dups"]
        job["total"] = sum(len(g) - 1 for g in groups)
        db = get_db()
        for g in groups:
            keep = g[0]
            for dup in g[1:]:
                if job["stop"].is_set():
                    return
                job["current"] = f"{dup.get('performer')} - {dup.get('title')}"
                try:
                    if dup.get("message_id"):
                        await asyncio.to_thread(db.cache_repoint, dup["chat_id"], dup["message_id"],
                                                post_fields(keep))
                    else:
                        await asyncio.to_thread(db.cache_repoint, None, None, post_fields(keep), dup["vids"])
                    await delete_post(dup)
                    job["ok"] += 1
                except Exception as e:
                    job["failed"] += 1
                    logger.info(f"Dublikat birləşdirilmədi: {e}")
                finally:
                    job["done"] += 1
                await refresh_panel()
                await asyncio.sleep(0.4)
        audio_cache.mark_dirty()

    # ── 🔎 YouTube adları ──
    async def do_check():
        db = get_db()
        vids = [r["video_id"] for r in await asyncio.to_thread(db.cache_all) if not r.get("yt_title")]
        job["total"] = len(vids)
        sem = asyncio.Semaphore(CHECK_CONCURRENCY)
        timeout = aiohttp.ClientTimeout(total=12)
        async with aiohttp.ClientSession(timeout=timeout, headers={"User-Agent": "Mozilla/5.0"}) as session:
            async def one(vid):
                async with sem:
                    if job["stop"].is_set():
                        return
                    try:
                        title, _ = await yt_oembed(session, vid)
                        await asyncio.to_thread(db.cache_set_yt_title, vid, title or NO_YT)
                        job["ok"] += 1
                        job["current"] = title or vid
                    except Exception as e:
                        job["failed"] += 1
                        logger.debug(f"oEmbed {vid}: {e}")
                    finally:
                        job["done"] += 1
                    await refresh_panel()
                    await asyncio.sleep(0.2)
            await asyncio.gather(*(one(v) for v in vids))

    # ── 🩹 adların düzəldilməsi ──
    async def correct_meta(p, session):
        """Postun düzgün (artist, ad) cütü; qərar vermək olmursa None."""
        title, perf = p.get("title") or "", p.get("performer") or ""
        bad, yt = bad_performer(perf), p.get("yt")
        mism = bool(yt) and not names.meta_matches(perf, title, yt)
        if not bad and not mism:
            return names.clean_artist(perf), names.clean_track(title)          # yalnız zibil təmizlənir
        vid = p.get("yt_vid") or p["vids"][0]
        author = None
        if not yt:
            try:
                yt, author = await yt_oembed(session, vid)
            except Exception:
                yt = None
        songlink = getattr(context, "music_songlink", None)
        if songlink:
            try:
                sl = await songlink(f"https://www.youtube.com/watch?v={vid}")
            except Exception:
                sl = None
            if sl and sl.get("artist") and sl.get("title") and \
                    (not yt or names.meta_matches(sl["artist"], sl["title"], yt)):
                return names.clean_artist(sl["artist"]), names.clean_track(sl["title"])
        if not yt:
            return (names.clean_artist(perf), names.clean_track(title)) if not bad else None
        artist, track = names.split_title(yt)
        if artist == "YouTube":
            if author is None:
                try:
                    _, author = await yt_oembed(session, vid)
                except Exception:
                    author = None
            artist = names.clean_artist(author or "") or ("" if bad else perf)
        return (artist, track) if artist and track else None

    async def reupload(p, artist: str, track: str):
        if (p.get("size") or 0) > TG_DOWNLOAD_LIMIT:
            raise RuntimeError("fayl 20 MB-dan böyükdür (Bot API limiti)")
        f = await bot.get_file(p["file_id"])
        buf = io.BytesIO()
        await bot.download_file(f.file_path, destination=buf)
        ext = os.path.splitext(f.file_path or "")[1] or ".m4a"
        vid = p.get("yt_vid") or p["vids"][0]
        fname = (sanitize_filename(f"{artist} - {track}")[:100] or "audio") + ext
        thumb = await fetch_thumb(vid)
        new_id = await audio_cache.upload_to_depo(
            bot, buf.getvalue(), fname, track, artist, int(p.get("duration") or 0),
            f"https://www.youtube.com/watch?v={vid}", vid, thumb, yt_title=p.get("yt"))
        if not new_id:
            raise RuntimeError("depoya göndərilmədi")
        db = get_db()
        new_row = await asyncio.to_thread(db.cache_get, vid)
        if p.get("message_id"):
            await asyncio.to_thread(db.cache_repoint, p["chat_id"], p["message_id"], post_fields(new_row))
        others = [v for v in p["vids"] if v != vid]
        if others:
            await asyncio.to_thread(db.cache_repoint, None, None, post_fields(new_row), others)
        await asyncio.to_thread(db.cache_mark_checked, p["vids"])
        await delete_post(p)
        audio_cache.mark_dirty()

    async def do_fix():
        a = await asyncio.to_thread(get_analysis, True)
        targets, seen = [], set()
        for p in a["bad_artist"] + a["junk"] + a["mismatch"]:
            if id(p) not in seen:
                seen.add(id(p))
                targets.append(p)
        job["total"] = len(targets)
        db = get_db()
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=12),
                                         headers={"User-Agent": "Mozilla/5.0"}) as session:
            for p in targets:
                if job["stop"].is_set():
                    break
                old = f"{p.get('performer') or ''} - {p.get('title') or ''}"
                job["current"] = old
                await refresh_panel()
                try:
                    meta = await correct_meta(p, session)
                    if not meta:
                        job["failed"] += 1
                        continue
                    artist, track = meta
                    if artist == (p.get("performer") or "") and track == (p.get("title") or ""):
                        await asyncio.to_thread(db.cache_mark_checked, p["vids"])   # artıq düzgündür
                        job["ok"] += 1
                        continue
                    job["current"] = f"{old} → {artist} - {track}"
                    await reupload(p, artist, track)
                    logger.info(f"🩹 Depo adı düzəldi: {old} → {artist} - {track}")
                    job["ok"] += 1
                    await asyncio.sleep(DELAY)
                except Exception as e:
                    if job["stop"].is_set():
                        break
                    job["failed"] += 1
                    logger.info(f"Ad düzəlişi alınmadı ({old}): {e}")
                finally:
                    job["done"] += 1

    async def run(kind):
        job.update(kind=kind, done=0, ok=0, failed=0, total=0, current=None, note="")
        try:
            await {"merge": do_merge, "check": do_check, "fix": do_fix}[kind]()
        except Exception as e:
            logger.error(f"Depo təmizliyi ({kind}) xətası: {e}", exc_info=True)
            job["note"] = f"Xəta: {e}"[:150]
        finally:
            job["current"] = None
            cache["a"] = None
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
        if action in ("merge", "check", "fix"):
            if running():
                await cb.answer("Artıq bir iş gedir")
            elif action != "check" and not await audio_cache.depo_chat_id(bot):
                await cb.answer("Depo kanalı əlçatan deyil", show_alert=True)
                return
            else:
                job["stop"] = threading.Event()
                job["task"] = asyncio.create_task(run(action))
                await cb.answer("▶️ Başladı")
                note = f"▶️ {KIND_LABELS[action]} — panel avtomatik yenilənir."
        elif action == "stop":
            if job["stop"]:
                job["stop"].set()
            await cb.answer("⏹ Dayandırılır...")
            note = "⏹ Dayandırılır — hazırkı addım bitəndən sonra dayanacaq."
        else:
            await cb.answer()
        text, kb = await asyncio.to_thread(panel, note)
        try:
            await cb.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        except Exception:
            pass

    context.fix_artist_count = problem_count
    context.fix_artist_job = job
    logger.info("✅ Depo təmizliyi plugin-i yükləndi (/fix)")


async def teardown(context):
    job = getattr(context, "fix_artist_job", None)
    if job and job.get("stop"):
        job["stop"].set()
    task = job.get("task") if job else None
    if task and not task.done():
        task.cancel()
