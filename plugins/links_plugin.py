"""
🔗 YouTube və Spotify linkləri — bütün növlər bir yerdə.

  YouTube:  video · Shorts · YouTube Music · playlist · YT Music albomu · Mix / Radio
  Spotify:  mahnı · albom · playlist · Radio / Daily Mix / editorial siyahılar
            ifaçı — BÜTÜN albomları və sinqlları (təkrarsız) və ya yalnız top mahnılar
            spotify.link qısa linkləri də açılır

Bütün yükləmələr music_plugin-in ümumi axını ilə gedir:
  📦 depo kanalı (təkrar yükləmə yoxdur) · ⏹ Dayandır · song.link metadata · statistika

Spotify mahnıları YouTube-da tapılır (müddətə görə ən uyğun video), amma ad / artist / üz qabığı
Spotify-dan götürülür.
"""
import asyncio
import json
import logging
import re
from html import escape

import aiohttp
from aiogram import F, types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from core.links import parse_link, parse_url, is_music_link, yt_list_url, list_kind_label
from core.utilities import format_duration

logger = logging.getLogger(__name__)

# music_plugin-in axtarış handler-indən ƏVVƏL qeydiyyatdan keçsin
PRIORITY = 10

BATCH_MAX_DURATION = 15 * 60       # siyahılarda bundan uzun videolar ötürülür
MIX_LIMIT = 50                     # YouTube Mix / Radio sonsuzdur — ilk 50
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


# ───────────────────────── köməkçilər ─────────────────────────
def pick_image(images, target=300):
    """Spotify şəkilləri (640/300/64) → thumbnail üçün ~300px."""
    if not images:
        return None
    imgs = sorted(images, key=lambda i: i.get("width") or 0)
    fit = [i for i in imgs if (i.get("width") or 0) <= 320]
    chosen = (fit[-1] if fit else imgs[0])
    return (chosen.get("url") or "").split("?")[0] or None


def sp_item(name: str, artists: list, duration_ms: int, image_url=None) -> dict:
    artist = ", ".join([a for a in artists if a][:3]) or "Naməlum"
    first = artists[0] if artists else ""
    return {
        "title": f"{artist} - {name}",
        "query": f"{first} {name}".strip(),
        "url": None,                              # music_plugin YouTube-da tapacaq
        "raw_duration": int((duration_ms or 0) // 1000),
        "meta": {"artist": artist, "track": name},
        "thumb": image_url,
    }


_VERSION_RE = re.compile(
    r"\s*[-–(\[]\s*(?:\d{4}\s+)?(?:remaster(?:ed)?|deluxe|bonus track|single version|album version|"
    r"radio edit|explicit|clean)[^)\]]*[)\]]?", re.I)


def track_key(name: str) -> str:
    """"Song - Remastered 2011" / "Song (Deluxe)" → "song" — təkrarları tutmaq üçün."""
    base = _VERSION_RE.sub("", name or "")
    return re.sub(r"[^\w]+", " ", base.lower()).strip()


def total_duration(items) -> str:
    return format_duration(sum(int(i.get("raw_duration") or 0) for i in items))


async def http_get(url: str, as_text=True, allow_redirects=True):
    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout, headers={"User-Agent": UA}) as session:
        async with session.get(url, allow_redirects=allow_redirects) as r:
            body = await (r.text() if as_text else r.read())
            return r.status, str(r.url), body


async def resolve_short(url: str):
    """spotify.link/... → open.spotify.com/... (yönləndirmə və ya səhifədəki link)."""
    try:
        status, final, body = await http_get(url)
    except Exception as e:
        logger.warning(f"Qısa link açılmadı: {e}")
        return None
    parsed = parse_url(final)
    if parsed and parsed["kind"] != "sp_short":
        return parsed
    m = re.search(r"https://open\.spotify\.com/[a-z\-/]*(?:track|album|playlist|artist)/[A-Za-z0-9]+", body or "")
    return parse_url(m.group(0)) if m else None


async def youtube_oembed(video_id: str) -> dict:
    """Videonun adı (yt-dlp-siz, sürətli)."""
    try:
        status, _, body = await http_get(
            f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={video_id}&format=json")
        if status == 200:
            data = json.loads(body)
            return {"title": data.get("title"), "author": data.get("author_name")}
    except Exception as e:
        logger.debug(f"oEmbed alınmadı: {e}")
    return {}


def _find_key(obj, key):
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            r = _find_key(v, key)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = _find_key(v, key)
            if r is not None:
                return r
    return None


async def spotify_embed(kind: str, sid: str) -> dict:
    """
    API-siz ehtiyat yol: open.spotify.com/embed səhifəsindən siyahı.
    Spotify Web API Spotify-a məxsus siyahıları (Radio, Daily Mix, editorial) yeni tətbiqlərə vermir —
    embed səhifəsi isə verir (adətən ilk ~100 mahnı).
    """
    status, _, body = await http_get(f"https://open.spotify.com/embed/{kind}/{sid}")
    if status != 200:
        raise RuntimeError(f"Spotify səhifəsi açılmadı ({status})")
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', body, re.S)
    if not m:
        raise RuntimeError("Spotify səhifəsində məlumat tapılmadı")
    data = json.loads(m.group(1))
    entity = _find_key(data, "entity") or {}
    name = entity.get("name") or entity.get("title") or "Spotify"
    cover = None
    sources = _find_key(entity.get("coverArt") or entity.get("visualIdentity") or {}, "sources") or []
    if sources:
        cover = pick_image([{"url": s.get("url"), "width": s.get("width") or s.get("maxWidth")} for s in sources])

    items = []
    if kind == "track":
        artists = [a.get("name") for a in (entity.get("artists") or [])] or \
                  [s.strip() for s in (entity.get("subtitle") or "").split(",")]
        items.append(sp_item(entity.get("title") or name, artists, entity.get("duration") or 0, cover))
    else:
        for t in entity.get("trackList") or []:
            artists = [s.strip() for s in (t.get("subtitle") or "").replace("\u00a0", " ").split(",")]
            items.append(sp_item(t.get("title") or "", artists, t.get("duration") or 0, cover))
    return {"name": name, "items": [i for i in items if i["meta"]["track"]]}


# ───────────────────────── plugin ─────────────────────────
def setup(context):
    dp = context.dp
    spotify = getattr(context, "spotify", None)
    pending = {}        # (chat_id, status_msg_id) -> təsdiq gözləyən siyahı / seçim

    def kb(*rows):
        return InlineKeyboardMarkup(inline_keyboard=[list(r) for r in rows])

    def btn(text, data):
        return InlineKeyboardButton(text=text, callback_data=data)

    # ── Spotify məlumatı: əvvəl API, alınmasa embed ──
    async def spotify_data(kind: str, sid: str) -> dict:
        api_error = None
        if spotify:
            try:
                if kind == "track":
                    t = await asyncio.to_thread(spotify.get_track_data, sid)
                    return {"name": t["name"], "items": [sp_item(
                        t["name"], [a["name"] for a in t["artists"]], t["duration_ms"],
                        pick_image(t["album"].get("images")))]}
                if kind == "album":
                    a = await asyncio.to_thread(spotify.get_album_data, sid)
                    cover = pick_image(a.get("images"))
                    return {"name": f"{a['artist']} — {a['name']}", "items": [
                        sp_item(t["name"], [x["name"] for x in t["artists"]], t["duration_ms"], cover)
                        for t in a["tracks"]]}
                if kind == "playlist":
                    p = await asyncio.to_thread(spotify.get_playlist_data, sid)
                    return {"name": p["name"], "items": [
                        sp_item(t["name"], [x["name"] for x in t["artists"]], t["duration_ms"],
                                pick_image(t["album"].get("images")))
                        for t in p["tracks"] if t.get("name")]}
                if kind == "artist":
                    return await asyncio.to_thread(artist_full_sync, sid)
            except Exception as e:
                api_error = e
                logger.info(f"Spotify API ({kind}/{sid}) alınmadı, embed sınanır: {e}")
        try:
            return await spotify_embed(kind, sid)
        except Exception as e:
            raise RuntimeError(f"{api_error or e}")

    def artist_full_sync(sid: str) -> dict:
        """
        İfaçının albomları + sinqlları (yeni → köhnə), hər birinin mahnıları, təkrarsız.
        Kompilyasiyalar və "appears on" (başqasının albomunda iştirak) daxil deyil.
        """
        sp = spotify.sp
        art = sp.artist(sid)
        name = art.get("name", "İfaçı")
        top = sp.artist_top_tracks(sid, country="US").get("tracks", [])
        top_items = [sp_item(t["name"], [x["name"] for x in t["artists"]], t["duration_ms"],
                             pick_image(t["album"].get("images"))) for t in top]

        albums, seen_album = [], set()
        res = sp.artist_albums(sid, include_groups="album,single", country="US", limit=50)
        while res:
            for a in res.get("items", []):
                key = (a.get("name", "").lower(), a.get("album_type"))
                if key in seen_album:              # eyni albomun başqa bazar versiyası
                    continue
                seen_album.add(key)
                albums.append(a)
            res = sp.next(res) if res.get("next") else None
        # əvvəl albomlar, sonra sinqllar; hər qrupda yenidən köhnəyə
        albums = sorted([a for a in albums if a.get("album_type") == "album"],
                        key=lambda a: a.get("release_date") or "", reverse=True) + \
                 sorted([a for a in albums if a.get("album_type") != "album"],
                        key=lambda a: a.get("release_date") or "", reverse=True)

        items, seen_track, album_names = [], set(), []
        n_album = n_single = 0
        for a in albums:
            cover = pick_image(a.get("images"))
            res = sp.album_tracks(a["id"], limit=50)
            added = 0
            while res:
                for t in res.get("items", []):
                    k = track_key(t.get("name"))
                    if not k or k in seen_track:
                        continue
                    seen_track.add(k)
                    items.append(sp_item(t["name"], [x["name"] for x in t.get("artists", [])],
                                         t.get("duration_ms") or 0, cover))
                    added += 1
                res = sp.next(res) if res.get("next") else None
            if added:
                if a.get("album_type") == "album":
                    n_album += 1
                    album_names.append(a.get("name"))
                else:
                    n_single += 1
        return {"name": name, "items": items, "top": top_items, "albums": n_album, "singles": n_single,
                "album_names": album_names}

    async def show_artist_card(status: types.Message, message: types.Message, data: dict):
        items = data["items"]
        top = data.get("top") or []
        if not items and not top:
            await status.edit_text("❌ Bu ifaçının mahnıları tapılmadı.")
            return
        await show_list_card(status, message, "🎤 Spotify ifaçısı", f"{data['name']} — bütün mahnılar",
                             items or top, "spotify")
        p = pending.get((status.chat.id, status.message_id))
        if not p:
            return
        if top:
            p["alt_top"] = [it for it in top if (it.get("raw_duration") or 0) <= BATCH_MAX_DURATION]
            p["artist_name"] = data["name"]
        albums_line = ""
        if data.get("albums") is not None:
            names = ", ".join(escape(n[:30]) for n in (data.get("album_names") or [])[:6])
            albums_line = (f"💿 {data['albums']} albom · 🎵 {data.get('singles', 0)} sinql/EP"
                           + (f"\n<i>{names}{' …' if len(data.get('album_names') or []) > 6 else ''}</i>" if names else "")
                           + "\n<i>Təkrarlar (deluxe, remaster) çıxarılıb.</i>\n\n")
        else:
            albums_line = "<i>ℹ️ Spotify API qoşulmayıb — yalnız top mahnılar əlçatandır.</i>\n\n"
        n = len(p["items"])
        await status.edit_text(
            f"🎤 <b>{escape(data['name'])}</b>\n\n{albums_line}"
            f"📊 Cəmi: <b>{n}</b> mahnı · ⏳ {total_duration(p['items'])}"
            + (f"\n<i>⏭ {p['skipped']} uzun video (15 dəq.+) ötürüləcək</i>" if p.get("skipped") else ""),
            parse_mode="HTML",
            reply_markup=kb(
                [btn(f"💿 Bütün mahnılar ({n})", "lk:go")],
                *([[btn(f"🔥 Yalnız top mahnılar ({len(p['alt_top'])})", "lk:top")]] if p.get("alt_top") else []),
                [btn("❌ Ləğv et", "lk:no")],
            ),
        )

    # ── siyahı kartı → təsdiq ──
    async def show_list_card(status: types.Message, message: types.Message, label: str, name: str,
                             items: list, source: str):
        seen, clean, skipped = set(), [], 0
        for it in items:
            key = it.get("url") or it.get("query")
            if not key or key in seen:
                continue
            seen.add(key)
            if (it.get("raw_duration") or 0) > BATCH_MAX_DURATION:
                skipped += 1
                continue
            clean.append(it)
        if not clean:
            await status.edit_text("❌ Bu siyahıda yüklənəcək mahnı tapılmadı.")
            return
        pending[(status.chat.id, status.message_id)] = {
            "uid": message.from_user.id, "items": clean, "header": f"{label}: {name}",
            "source": source, "skipped": skipped, "orig": message.message_id,
        }
        preview = "\n".join(f"{i}. {escape(it['title'][:55])}" for i, it in enumerate(clean[:8], 1))
        more = f"\n<i>… və daha {len(clean) - 8}</i>" if len(clean) > 8 else ""
        await status.edit_text(
            f"{label}\n<b>{escape(name[:100])}</b>\n\n"
            f"📊 <b>{len(clean)}</b> mahnı · ⏳ {total_duration(clean)}"
            + (f"\n<i>⏭ {skipped} uzun video (15 dəq.+) ötürüləcək</i>" if skipped else "")
            + f"\n\n{preview}{more}",
            parse_mode="HTML", disable_web_page_preview=True,
            reply_markup=kb([btn(f"⬇️ Hamısını yüklə ({len(clean)})", "lk:go")],
                            [btn("❌ Ləğv et", "lk:no")]),
        )

    async def start_single(status: types.Message, message: types.Message, item: dict, source: str):
        run_batch = getattr(context, "music_run_batch", None)
        if not run_batch:
            await status.edit_text("❌ Yükləmə modulu (music_plugin) yüklənməyib.")
            return
        await run_batch(status, [item], item["title"], message.from_user.id, source, single=True)

    # ── link mesajı ──
    @dp.message(F.text.func(is_music_link))
    async def handle_link(message: types.Message):
        parsed = parse_link(message.text)
        if not parsed or not message.from_user:
            return
        status = await message.reply("🔎 <i>Link yoxlanılır...</i>", parse_mode="HTML")
        try:
            if parsed["kind"] == "sp_short":
                parsed = await resolve_short(parsed["url"])
                if not parsed:
                    await status.edit_text("❌ Spotify qısa linki açılmadı. Tam linki (open.spotify.com/...) göndər.")
                    return
            await process(parsed, status, message)
        except Exception as e:
            logger.warning(f"Link xətası ({message.text[:80]}): {e}")
            try:
                await status.edit_text(f"❌ <b>Xəta:</b> <code>{escape(str(e))[:300]}</code>", parse_mode="HTML")
            except Exception:
                pass

    async def process(parsed: dict, status: types.Message, message: types.Message):
        kind = parsed["kind"]

        if kind == "sp_jam":
            await status.edit_text(
                "🎧 <b>Spotify Jam</b> canlı sessiyadır — Spotify onun növbəsini kənar tətbiqlərə açmır.\n\n"
                "<i>Jam-dakı mahnının, playlistin və ya albomun linkini göndər — yükləyim.</i>",
                parse_mode="HTML",
            )
            return

        if kind == "yt_video":
            info = await youtube_oembed(parsed["id"])
            title = info.get("title") or f"YouTube {parsed['id']}"
            await start_single(status, message, {
                "title": title, "url": f"https://www.youtube.com/watch?v={parsed['id']}", "raw_duration": 0,
            }, "youtube")
            return

        if kind == "yt_video_list":
            info = await youtube_oembed(parsed["id"])
            pending[(status.chat.id, status.message_id)] = {
                "uid": message.from_user.id, "parsed": parsed, "title": info.get("title"), "orig": message.message_id,
            }
            await status.edit_text(
                f"🎵 <b>{escape(info.get('title') or 'Bu video')}</b>\n"
                f"{list_kind_label(parsed['list'])} içindən açılıb.\n\n<i>Nəyi yükləyək?</i>",
                parse_mode="HTML",
                reply_markup=kb([btn("🎵 Yalnız bu mahnı", "lk:one"), btn("📃 Bütün siyahı", "lk:all")],
                                [btn("❌ Ləğv et", "lk:no")]),
            )
            return

        if kind == "yt_list":
            await open_yt_list(status, message, parsed["list"], None)
            return

        if kind.startswith("sp_"):
            sp_kind = kind[3:]
            if sp_kind == "artist":
                await status.edit_text("🎤 <i>İfaçının albomları toplanır... (bir az çəkə bilər)</i>", parse_mode="HTML")
            data = await spotify_data(sp_kind, parsed["id"])
            if not data["items"] and not data.get("top"):
                raise RuntimeError("Spotify-dan mahnı siyahısı alınmadı")
            if sp_kind == "track":
                await start_single(status, message, data["items"][0], "spotify")
                return
            if sp_kind == "artist":
                await show_artist_card(status, message, data)
                return
            label = {"album": "💿 Spotify albomu", "playlist": "🎧 Spotify playlist",
                     "artist": "🎤 Spotify ifaçısı"}.get(sp_kind, "🎧 Spotify")
            if sp_kind == "playlist" and parsed["id"].startswith("37i9dQZF1E"):
                label = "📻 Spotify Radio / Mix"
            await show_list_card(status, message, label, data["name"], data["items"], "spotify")

    async def open_yt_list(status, message, list_id: str, video_id):
        vid = video_id
        if not vid and list_id.startswith("RDAMVM"):
            vid = list_id[6:]
        elif not vid and list_id.startswith("RD") and len(list_id) == 13:
            vid = list_id[2:]
        limit = MIX_LIMIT if list_id.startswith("RD") and not list_id.startswith("RDCLAK") else 5000
        await status.edit_text("📃 <i>Siyahı açılır...</i>", parse_mode="HTML")
        data = await context.youtube_manager.playlist_entries(yt_list_url(list_id, vid), limit=limit)
        if not data["entries"]:
            raise RuntimeError("Siyahı boşdur və ya gizlidir")
        await show_list_card(status, message, list_kind_label(list_id), data["title"], data["entries"], "playlist")

    @dp.callback_query(F.data.startswith("lk:"))
    async def link_callback(cb: types.CallbackQuery):
        key = (cb.message.chat.id, cb.message.message_id)
        p = pending.get(key)
        action = cb.data.split(":", 1)[1]
        if not p:
            await cb.answer("Bu sorğu köhnəlib — linki yenidən göndər", show_alert=True)
            return
        if cb.from_user.id not in (p["uid"], context.creator_id):
            await cb.answer("⛔ Yalnız linki göndərən seçə bilər", show_alert=True)
            return

        if action == "no":
            pending.pop(key, None)
            await cb.answer("Ləğv edildi")
            try:
                await cb.message.delete()
            except Exception:
                pass
            return

        if action == "one":
            pending.pop(key, None)
            await cb.answer()
            await _single_for(cb, p, p["parsed"]["id"])
            return

        if action == "all":
            pending.pop(key, None)
            await cb.answer()
            try:
                await open_yt_list(cb.message, _Sender(p["uid"], p["orig"]), p["parsed"]["list"], p["parsed"]["id"])
            except Exception as e:
                await cb.message.edit_text(f"❌ <b>Xəta:</b> <code>{escape(str(e))[:300]}</code>", parse_mode="HTML")
            return

        if action == "top":
            if not p.get("alt_top"):
                await cb.answer()
                return
            p["items"] = p["alt_top"]
            p["header"] = f"🔥 {p.get('artist_name', 'İfaçı')} — top mahnılar"
            p["skipped"] = 0
            action = "go"

        if action == "go":
            busy = getattr(context, "music_batch_busy", None)
            if busy and busy(cb.from_user.id):
                await cb.answer("Artıq bir toplu yükləmə gedir — bitməsini gözlə və ya ⏹ Dayandır", show_alert=True)
                return
            run_batch = getattr(context, "music_run_batch", None)
            if not run_batch:
                await cb.answer("Yükləmə modulu yüklənməyib", show_alert=True)
                return
            pending.pop(key, None)
            await cb.answer(f"⬇️ {len(p['items'])} mahnı yüklənir...")
            await run_batch(cb.message, p["items"], p["header"], p["uid"], p["source"],
                            skipped=p["skipped"], original_message_id=None)
            return

        await cb.answer()

    async def _single_for(cb, p, vid):
        run_batch = getattr(context, "music_run_batch", None)
        if not run_batch:
            await cb.message.edit_text("❌ Yükləmə modulu (music_plugin) yüklənməyib.")
            return
        item = {"title": p.get("title") or f"YouTube {vid}",
                "url": f"https://www.youtube.com/watch?v={vid}", "raw_duration": 0}
        await run_batch(cb.message, [item], item["title"], p["uid"], "youtube", single=True)

    class _Sender:
        """open_yt_list üçün minimal "mesaj" — kim göndərib, orijinal mesaj ID-si."""
        def __init__(self, uid, mid):
            self.from_user = type("U", (), {"id": uid})()
            self.message_id = mid

    logger.info("✅ Links plugin yükləndi (YouTube + Spotify)")

