"""
📦 Depo doldurucu — yalnız creator.

Fonda işləyir: populyar və istifadəçilərin sevdiyi mahnıları əvvəlcədən depo kanalına yükləyir ki,
kimsə istəyəndə dərhal (yükləməsiz) göndərilsin.

Mənbələr (növbə bu sıra ilə qurulur):
  1. 👥 İstifadəçilərin son 7 gündə ən çox yüklədiyi ifaçılar → Spotify-da onların top mahnıları
  2. 🔀 Ən çox yüklənən mahnıların YouTube Mix-i (oxşar mahnılar)
  3. 📋 Sənin əlavə etdiyin mənbələr: Spotify playlist / albom / ifaçı, YouTube playlist,
     ✈️ Telegram kanalı (public: t.me/kanal) — kanaldakı mahnıların adları ilə YouTube-dan
     (default: Spotify "Today's Top Hits" və "Top 50 Global")

Spotify mahnısı üçün YouTube videosu əvvəl song.link ilə (dəqiq), alınmasa axtarışla tapılır.
Depoda artıq olan mahnılar ötürülür.

Komandalar:
  /depo                — panel (status, sürət, mənbələr)
  /depo on | /depo off — işə sal / söndür (vəziyyət yadda qalır, restartdan sonra davam edir)
  /depo add <link>     — mənbə əlavə et
"""
import asyncio
import json
import logging
import os
import re
import threading
import time
from collections import deque
from html import escape, unescape

import aiohttp

from aiogram import F, types
from aiogram.filters import Command, CommandObject
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from core import audio_cache
from core.database import get_db
from core.links import parse_link, yt_list_url

logger = logging.getLogger(__name__)
PRIORITY = 60

DEFAULT_SOURCES = [
    {"kind": "sp_playlist", "id": "37i9dQZF1DXcBWIGoYBM5M", "label": "Today's Top Hits"},
    {"kind": "sp_playlist", "id": "37i9dQZEVXbMDoHDwVN2tF", "label": "Top 50 Global"},
]
SPEEDS = {"slow": 60, "normal": 25, "fast": 8}          # mahnılar arası fasilə (san.)
SPEED_LABELS = {"slow": "🐢 Yavaş", "normal": "🚶 Normal", "fast": "🏃 Sürətli"}
MAX_DURATION = 15 * 60
IDLE_SLEEP = 30 * 60                                   # növbə bitəndə yenidən qurmağa qədər


# ───────────────────────── ✈️ Telegram kanalı ─────────────────────────
TG_PAGES = 15                  # t.me/s/kanal — hər səhifə ~20 post
_TG_NAME = re.compile(r"^(?:https?://)?(?:t\.me|telegram\.me)/(?:s/)?([A-Za-z][A-Za-z0-9_]{3,31})/?(?:\?.*)?$|^@([A-Za-z][A-Za-z0-9_]{3,31})$")
_SIZE_RE = re.compile(r"^\s*[\d.,]+\s*(?:B|KB|MB|GB)\s*$", re.I)
_TAG_RE = re.compile(r"<[^>]+>")


def parse_tg_channel(text: str):
    m = _TG_NAME.match((text or "").strip())
    if not m:
        return None
    name = m.group(1) or m.group(2)
    return None if name.lower() in ("s", "joinchat", "share", "addstickers", "proxy") else name


def _clean(html_part: str) -> str:
    return unescape(_TAG_RE.sub("", html_part or "")).strip()


def tg_item(artist: str, title: str) -> dict:
    artist, title = (artist or "").strip(), (title or "").strip()
    full = f"{artist} - {title}" if artist else title
    return {"title": full, "query": f"{artist} {title}".strip(), "url": None, "raw_duration": 0,
            "meta": None, "thumb": None, "sp_id": None}


def parse_tg_page(html: str) -> list:
    """t.me/s/... səhifəsindən mahnılar: audio postları (ad + ifaçı) və "Artist - Ad" mətnləri."""
    items = []
    for block in re.split(r'<div class="tgme_widget_message_wrap', html)[1:]:
        found = False
        for m in re.finditer(
                r'tgme_widget_message_document_title[^>]*>(.*?)</div>\s*'
                r'<div class="tgme_widget_message_document_extra[^>]*>(.*?)</div>', block, re.S):
            title, extra = _clean(m.group(1)), _clean(m.group(2))
            if not title:
                continue
            title = re.sub(r"\.(mp3|m4a|flac|ogg|wav|opus)$", "", title, flags=re.I)
            if extra and not _SIZE_RE.match(extra):
                items.append(tg_item(extra, title))           # audio: ifaçı "extra"-dadır
            elif " - " in title:
                a, t = title.split(" - ", 1)
                items.append(tg_item(a, t))
            else:
                items.append(tg_item("", title))
            found = True
        if not found:
            tm = re.search(r'tgme_widget_message_text[^>]*>(.*?)</div>', block, re.S)
            text = _clean((tm.group(1) if tm else "").replace("<br/>", "\n").replace("<br>", "\n"))
            first = text.split("\n")[0].strip() if text else ""
            if " - " in first and len(first) <= 120 and "http" not in first:
                a, t = first.split(" - ", 1)
                items.append(tg_item(a, t))
    return items


async def tg_channel_items(name: str, pages: int = TG_PAGES) -> dict:
    """Public kanalın son postlarından mahnı siyahısı (yeni → köhnə)."""
    items, title, before = [], name, None
    timeout = aiohttp.ClientTimeout(total=20)
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0 Safari/537.36"}
    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        for _ in range(pages):
            url = f"https://t.me/s/{name}" + (f"?before={before}" if before else "")
            async with session.get(url) as r:
                if r.status != 200:
                    break
                html = await r.text()
            if "tgme_channel_info" not in html and "tgme_widget_message" not in html:
                raise RuntimeError("Kanal tapılmadı və ya public deyil")
            t = re.search(r'tgme_channel_info_header_title[^>]*>(.*?)</div>', html, re.S)
            if t:
                title = _clean(t.group(1)) or title
            page_items = parse_tg_page(html)
            items += page_items[::-1]             # səhifədə köhnə → yeni; bizə yeni → köhnə lazımdır
            m = re.search(r'data-before="(\d+)"', html) or re.search(r'\?before=(\d+)', html)
            nb = m.group(1) if m else None
            if not nb or nb == before:
                break
            before = nb
    seen, uniq = set(), []
    for it in items:
        k = it["query"].lower()
        if k and k not in seen:
            seen.add(k)
            uniq.append(it)
    return {"name": title, "items": uniq}


def btn(text, data):
    return InlineKeyboardButton(text=text, callback_data=data)


class Filler:
    def __init__(self, context):
        self.ctx = context
        self.task = None
        self.stop_event = threading.Event()
        self.queue = deque()
        self.seen = set()
        self.stats = {"done": 0, "cached": 0, "failed": 0, "started": None, "current": None,
                      "last_build": None, "queue_built": 0, "note": ""}

    # ── ayarlar (bazada) ──
    def _get(self, key, default=None):
        try:
            return get_db().get_setting(f"depo_fill:{key}", default)
        except Exception:
            return default

    def _set(self, key, value):
        get_db().set_setting(f"depo_fill:{key}", value)

    @property
    def enabled(self) -> bool:
        return self._get("on") == "1"

    @property
    def speed(self) -> str:
        s = self._get("speed") or "normal"
        return s if s in SPEEDS else "normal"

    def sources(self) -> list:
        try:
            data = json.loads(self._get("sources") or "null")
        except ValueError:
            data = None
        return data if isinstance(data, list) else list(DEFAULT_SOURCES)

    def save_sources(self, sources):
        self._set("sources", json.dumps(sources))

    @property
    def running(self) -> bool:
        return bool(self.task and not self.task.done())

    # ── idarə ──
    def start(self):
        self._set("on", "1")
        if self.running:
            return
        self.stop_event = threading.Event()
        self.stats.update(started=time.time(), note="")
        self.task = asyncio.create_task(self.loop())
        logger.info("📦 Depo doldurucu işə düşdü")

    async def stop(self, persist=True):
        if persist:
            self._set("on", "0")
        self.stop_event.set()
        if self.task and not self.task.done():
            self.task.cancel()
            try:
                await self.task
            except (asyncio.CancelledError, Exception):
                pass
        self.task = None
        self.stats["current"] = None
        logger.info("📦 Depo doldurucu dayandı")

    # ── növbə ──
    def _add(self, item):
        key = item.get("url") or (item.get("query") or "").lower()
        if not key or key in self.seen or (item.get("raw_duration") or 0) > MAX_DURATION:
            return
        self.seen.add(key)
        self.queue.append(item)

    async def build_queue(self):
        ctx = self.ctx
        ym = ctx.youtube_manager
        spotify_data = getattr(ctx, "links_spotify_data", None)
        spotify = getattr(ctx, "spotify", None)
        now = int(time.time())
        db = get_db()

        # 1) istifadəçilərin sevdiyi ifaçılar
        try:
            rows = db._conn.execute(
                """SELECT title, url, COUNT(*) AS n FROM downloads
                   WHERE ts >= ? AND title IS NOT NULL GROUP BY COALESCE(url, title) ORDER BY n DESC LIMIT 200""",
                (now - 7 * 86400,)).fetchall()
        except Exception:
            rows = []
        artists = {}
        for r in rows:
            artist = (r["title"] or "").split(" - ", 1)[0].split(",")[0].strip()
            if artist and artist != "YouTube":
                artists[artist] = artists.get(artist, 0) + r["n"]
        top_artists = sorted(artists, key=artists.get, reverse=True)[:12]
        if spotify and spotify_data:
            for name in top_artists:
                if self.stop_event.is_set():
                    return
                try:
                    res = await asyncio.to_thread(spotify.sp.search, q=f"artist:{name}", type="artist", limit=1)
                    found = (res.get("artists") or {}).get("items") or []
                    if not found:
                        continue
                    data = await spotify_data("artist", found[0]["id"])
                    for it in (data.get("top") or data.get("items") or [])[:10]:
                        self._add(it)
                except Exception as e:
                    logger.debug(f"Doldurucu: ifaçı {name}: {e}")

        # 2) ən çox yüklənən mahnıların Mix-i
        for r in rows[:8]:
            if self.stop_event.is_set():
                return
            m = re.search(r"v=([\w-]{11})", r["url"] or "")
            if not m:
                continue
            try:
                for it in await ym.youtube_mix(m.group(1), limit=15):
                    self._add(it)
            except Exception as e:
                logger.debug(f"Doldurucu: mix {m.group(1)}: {e}")

        # 3) mənbələr
        for src in self.sources():
            if self.stop_event.is_set():
                return
            try:
                if src["kind"].startswith("sp_") and spotify_data:
                    data = await spotify_data(src["kind"][3:], src["id"])
                    for it in data.get("items") or []:
                        self._add(it)
                elif src["kind"] == "yt_list":
                    data = await ym.playlist_entries(yt_list_url(src["id"]), limit=200)
                    for it in data["entries"]:
                        self._add(it)
                elif src["kind"] == "tg_channel":
                    data = await tg_channel_items(src["id"])
                    for it in data["items"]:
                        self._add(it)
            except Exception as e:
                logger.warning(f"Doldurucu: mənbə {src.get('label') or src['id']}: {e}")

        self.stats["last_build"] = time.time()
        self.stats["queue_built"] = len(self.queue)
        logger.info(f"📦 Doldurucu növbəsi: {len(self.queue)} mahnı")

    async def resolve(self, item) -> str:
        """Mahnının YouTube linki: song.link (Spotify ID varsa) → axtarış."""
        if item.get("url"):
            return item["url"]
        songlink = getattr(self.ctx, "music_songlink", None)
        if item.get("sp_id") and songlink:
            try:
                sl = await songlink(f"https://open.spotify.com/track/{item['sp_id']}")
                links = (sl or {}).get("links") or {}
                url = links.get("youtube") or links.get("youtubeMusic")
                if url:
                    vid = re.search(r"(?:v=|youtu\.be/)([\w-]{11})", url)
                    if vid:
                        return f"https://www.youtube.com/watch?v={vid.group(1)}"
            except Exception as e:
                logger.debug(f"song.link alınmadı: {e}")
        resolve = getattr(self.ctx, "music_resolve_youtube", None)
        return await resolve(item.get("query") or item["title"], item.get("raw_duration") or 0) if resolve else None

    async def loop(self):
        await asyncio.sleep(5)
        fetch = getattr(self.ctx, "music_fetch_audio", None)
        while not self.stop_event.is_set():
            if not fetch:
                self.stats["note"] = "music_plugin yüklənməyib"
                await asyncio.sleep(60)
                fetch = getattr(self.ctx, "music_fetch_audio", None)
                continue
            if not await audio_cache.depo_chat_id(self.ctx.bot):
                self.stats["note"] = "⚠️ depo kanalı əlçatan deyil — 10 dəq. sonra yenidən"
                await asyncio.sleep(600)
                continue
            if not self.queue:
                # növbə ən çox 30 dəqiqədə bir qurulur — hamısı depodadırsa API-ləri boş yerə yükləməsin
                since = time.time() - (self.stats["last_build"] or 0)
                if self.stats["last_build"] and since < IDLE_SLEEP:
                    self.stats["note"] = "növbə bitdi — yeni mahnılar üçün gözləyir"
                    await asyncio.sleep(IDLE_SLEEP - since)
                    continue
                self.seen.clear()
                await self.build_queue()
                if not self.queue:
                    self.stats["note"] = "növbə boşdur — 30 dəq. sonra yenidən"
                    await asyncio.sleep(IDLE_SLEEP)
                    continue
            self.stats["note"] = ""
            item = self.queue.popleft()
            self.stats["current"] = item["title"]
            needs_lookup = not item.get("url")
            try:
                url = await self.resolve(item)
                vid = re.search(r"v=([\w-]{11})", url or "")
                if not vid:
                    raise ValueError("YouTube-da tapılmadı")
                if await asyncio.to_thread(audio_cache.get_cached, vid.group(1)):
                    self.stats["cached"] += 1
                    if needs_lookup:                  # song.link / axtarış edildi — API-ləri yormasın
                        await asyncio.sleep(min(2, SPEEDS[self.speed]))
                    continue
                res = await fetch(url, item["title"], int(item.get("raw_duration") or 0), self.stop_event,
                                  meta=item.get("meta"), thumb_url=item.get("thumb"))
                if res.get("path"):
                    try:
                        os.remove(res["path"])
                    except OSError:
                        pass
                if res.get("file_id") and not res.get("cached"):
                    self.stats["done"] += 1
                else:
                    self.stats["cached"] += 1
            except asyncio.CancelledError:
                raise
            except Exception as e:
                if self.stop_event.is_set():
                    break
                self.stats["failed"] += 1
                logger.info(f"Doldurucu: {item['title']}: {e}")
            finally:
                self.stats["current"] = None
            # istifadəçilər yükləyirsə onlara mane olmasın deyə fasilə
            await asyncio.sleep(SPEEDS[self.speed])


def setup(context):
    dp = context.dp
    filler = Filler(context)
    context.depo_filler = filler

    def is_creator(uid) -> bool:
        return bool(context.creator_id) and uid == context.creator_id

    # ➕ Mənbə əlavə et: creator-dan link gözlənilir (5 dəq.) — links_plugin bu linki yükləməyə götürməsin
    add_wait = {"until": 0, "chat_id": None, "msg_id": None}

    def depo_waiting(uid) -> bool:
        return is_creator(uid) and time.time() < add_wait["until"]

    context.depo_waiting = depo_waiting

    async def add_source(link_text: str):
        """(ok, mesaj) — linki mənbə kimi yoxlayıb əlavə edir."""
        tg = next((n for n in map(parse_tg_channel, (link_text or "").split()) if n), None)
        if tg:
            try:
                data = await tg_channel_items(tg, pages=3)
            except Exception as e:
                return False, f"❌ Telegram kanalı açılmadı: <code>{escape(str(e))[:200]}</code>"
            if not data["items"]:
                return False, ("❌ Bu kanalın son postlarında mahnı tapılmadı.\n"
                               "<i>Kanal public olmalıdır (t.me/kanal) və orada audio və ya \"Artist - Ad\" "
                               "formatlı postlar olmalıdır.</i>")
            src = {"kind": "tg_channel", "id": tg, "label": f"✈️ {data['name']} (@{tg})"}
            srcs = [s for s in filler.sources() if s["id"] != tg] + [src]
            filler.save_sources(srcs)
            return True, (f"✅ Telegram kanalı əlavə olundu: <b>{escape(data['name'])}</b>\n"
                          f"<i>Son postlarda {len(data['items'])}+ mahnı tapıldı — növbədə daha çoxu oxunacaq.</i>")
        parsed = parse_link(link_text)
        if not parsed or parsed["kind"] not in ("sp_playlist", "sp_album", "sp_artist", "yt_list", "yt_video_list"):
            return False, ("❌ Spotify playlist / albom / ifaçı, YouTube playlist və ya Telegram kanalı "
                           "(t.me/kanal, @kanal) göndər.\n<i>Mahnı linki mənbə ola bilməz.</i>")
        if parsed["kind"] == "yt_video_list":
            parsed = {"kind": "yt_list", "list": parsed["list"]}
        src = {"kind": parsed["kind"], "id": parsed.get("id") or parsed.get("list")}
        label = src["id"]
        try:
            if src["kind"].startswith("sp_") and getattr(context, "links_spotify_data", None):
                data = await context.links_spotify_data(src["kind"][3:], src["id"])
                label = f"{data['name']} ({len(data.get('items') or [])})"
            elif src["kind"] == "yt_list":
                data = await context.youtube_manager.playlist_entries(yt_list_url(src["id"]), limit=200)
                label = f"{data['title']} ({len(data['entries'])})"
        except Exception as e:
            return False, f"❌ Mənbə açılmadı: <code>{escape(str(e))[:200]}</code>"
        src["label"] = label
        srcs = [s for s in filler.sources() if s["id"] != src["id"]] + [src]
        filler.save_sources(srcs)
        return True, f"✅ Mənbə əlavə olundu: <b>{escape(label)}</b>"

    async def waiting_link(message: types.Message) -> bool:
        return bool(message.from_user) and depo_waiting(message.from_user.id)

    @dp.message(F.chat.type == "private", F.text, ~F.text.startswith("/"), waiting_link)
    async def depo_link_input(message: types.Message):
        ok, note = await add_source(message.text)
        with_panel = add_wait["msg_id"]
        if ok:
            add_wait["until"] = 0
        try:
            await message.delete()
        except Exception:
            pass
        if with_panel:
            text, kb = sources_view(note + ("" if ok else "\n\n✍️ Yenidən link göndər:"))
            try:
                await context.bot.edit_message_text(chat_id=add_wait["chat_id"], message_id=with_panel,
                                                    text=text, parse_mode="HTML", reply_markup=kb)
                return
            except Exception:
                pass
        await message.answer(note, parse_mode="HTML")

    def panel():
        st = filler.stats
        depo = audio_cache.depo_status()
        try:
            cs = get_db().cache_stats(depo["id"])
        except Exception:
            cs = {"songs": 0, "hits": 0, "size": 0}
        on = filler.running
        fix_count = getattr(context, "fix_artist_count", None)      # fix_artist_plugin
        try:
            bad = fix_count() if fix_count else 0
        except Exception:
            bad = 0
        uptime = ""
        if on and st["started"]:
            m = int((time.time() - st["started"]) / 60)
            uptime = f" · {m // 60} saat {m % 60} dəq."
        lines = [
            "📦 <b>Depo doldurucu</b>\n",
            f"📶 Vəziyyət: <b>{'🟢 işləyir' if on else '🔴 söndürülüb'}</b>{uptime}",
            f"⚡ Sürət: {SPEED_LABELS[filler.speed]} (mahnılar arası {SPEEDS[filler.speed]} san.)",
            f"📦 Depo: <b>{cs['songs']}</b> mahnı · {cs['size'] / 1048576:.0f} MB · {escape(depo['ref'])}"
            + ("" if depo["id"] or not on else " ⚠️"),
            "",
            f"⬆️ Bu sessiyada yükləndi: <b>{st['done']}</b> · ⏭ artıq depoda idi: {st['cached']} · ❌ {st['failed']}",
            f"📋 Növbədə: <b>{len(filler.queue)}</b>"
            + (f" / {st['queue_built']}" if st["queue_built"] else ""),
        ]
        if st["current"]:
            lines.append(f"⏳ İndi: <i>{escape(st['current'][:60])}</i>")
        if st["note"]:
            lines.append(f"ℹ️ {escape(st['note'])}")
        srcs = filler.sources()
        lines.append(f"\n📋 <b>Mənbələr</b> ({len(srcs)}) + 👥 istifadəçilərin sevdikləri + 🔀 Mix")
        for s in srcs[:8]:
            lines.append(f"   • {escape(s.get('label') or s['id'])}")
        lines.append("\n<i>Mənbə əlavə et:</i> <code>/depo add &lt;Spotify / YouTube / t.me linki&gt;</code>")
        rows = [
            [btn("⏸ Söndür", "df:off") if on else btn("▶️ İşə sal", "df:on"), btn("🔄 Yenilə", "df:panel")],
            [btn(("• " if filler.speed == k else "") + v, f"df:speed:{k}") for k, v in SPEED_LABELS.items()],
            [btn("🔁 Növbəni yenidən qur", "df:rebuild"), btn("📋 Mənbələr", "df:sources")],
            *([[btn(f"🩹 Artist düzəlişi ({bad})" if bad else "🩹 Artist düzəlişi", "fx:panel")]]
              if fix_count else []),
            [btn("⬅️ Menyu", "menu:main"), btn("❌ Bağla", "df:close")],
        ]
        return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)

    def sources_view(note: str = ""):
        srcs = filler.sources()
        text = "📋 <b>Doldurucu mənbələri</b>\n\n" + ("\n".join(
            f"{i}. {escape(s.get('label') or s['id'])} <i>({s['kind']})</i>" for i, s in enumerate(srcs, 1))
            or "<i>Mənbə yoxdur — yalnız istifadəçilərin sevdikləri və Mix işlənir.</i>") \
            + (f"\n\n{note}" if note else "")
        rows = [[btn(f"🗑 {(s.get('label') or s['id'])[:30]}", f"df:rm:{i}")] for i, s in enumerate(srcs)]
        rows.append([btn("➕ Mənbə əlavə et", "df:addsrc")])
        rows.append([btn("↩️ Default mənbələr", "df:defsrc")])
        rows.append([btn("⬅️ Geri", "df:panel"), btn("❌ Bağla", "df:close")])
        return text, InlineKeyboardMarkup(inline_keyboard=rows)

    @dp.message(Command("depo"))
    async def depo_cmd(message: types.Message, command: CommandObject):
        if not message.from_user or not is_creator(message.from_user.id):
            return
        args = (command.args or "").strip()
        low = args.lower()
        if low in ("on", "aç", "start"):
            filler.start()
            await message.answer("🟢 <b>Depo doldurucu işə salındı.</b>\n<i>Söndürmək üçün:</i> /depo off",
                                 parse_mode="HTML")
            return
        if low in ("off", "bağla", "stop"):
            await filler.stop()
            await message.answer("🔴 <b>Depo doldurucu söndürüldü.</b>", parse_mode="HTML")
            return
        if low.startswith("add"):
            ok, note = await add_source(args)
            await message.answer(note, parse_mode="HTML")
            return
        text, kb = panel()
        await message.answer(text, parse_mode="HTML", reply_markup=kb)

    @dp.callback_query(F.data.startswith("df:"))
    async def depo_callback(cb: types.CallbackQuery):
        if not is_creator(cb.from_user.id):
            await cb.answer("⛔ İcazə yoxdur", show_alert=True)
            return
        parts = cb.data.split(":")
        action = parts[1]
        if action != "addsrc":
            add_wait["until"] = 0                  # başqa düyməyə basıldı — link gözləmə bitir

        async def show(text, kb):
            try:
                await cb.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
            except Exception as e:
                if "not modified" not in str(e):
                    logger.warning(f"Depo paneli yenilənmədi: {e}")

        if action == "close":
            await cb.answer()
            try:
                await cb.message.delete()
            except Exception:
                pass
            return
        if action == "on":
            filler.start()
            await cb.answer("🟢 İşə salındı")
        elif action == "off":
            await filler.stop()
            await cb.answer("🔴 Söndürüldü")
        elif action == "speed" and len(parts) > 2 and parts[2] in SPEEDS:
            filler._set("speed", parts[2])
            await cb.answer(SPEED_LABELS[parts[2]])
        elif action == "rebuild":
            filler.queue.clear()
            filler.seen.clear()
            filler.stats["queue_built"] = 0
            filler.stats["last_build"] = None
            await cb.answer("Növbə növbəti addımda yenidən qurulacaq")
        elif action == "sources":
            await cb.answer()
            await show(*sources_view())
            return
        elif action == "addsrc":
            await cb.answer()
            add_wait.update(until=time.time() + 300, chat_id=cb.message.chat.id, msg_id=cb.message.message_id)
            await show(
                "➕ <b>Mənbə əlavə et</b>\n\n"
                "Linki göndər (adi link kimi, komandasız):\n"
                "• Spotify playlist / albom / ifaçı\n"
                "• YouTube playlist\n"
                "• ✈️ Telegram kanalı: <code>https://t.me/kanal</code> və ya <code>@kanal</code>\n\n"
                "<i>məs.</i> <code>https://open.spotify.com/playlist/...</code>",
                InlineKeyboardMarkup(inline_keyboard=[[btn("⬅️ Geri", "df:sources"), btn("❌ Bağla", "df:close")]]),
            )
            return
        elif action == "rm" and len(parts) > 2 and parts[2].isdigit():
            srcs = filler.sources()
            i = int(parts[2])
            if 0 <= i < len(srcs):
                srcs.pop(i)
                filler.save_sources(srcs)
            await cb.answer("Silindi")
            await show(*sources_view())
            return
        elif action == "defsrc":
            filler.save_sources(list(DEFAULT_SOURCES))
            await cb.answer("Default mənbələr")
            await show(*sources_view())
            return
        else:
            await cb.answer()
        await show(*panel())

    @dp.channel_post(F.audio)
    async def tg_source_post(post: types.Message):
        """Mənbə kimi əlavə olunmuş kanala (bot orada admin olanda) yeni mahnı düşdü → dərhal növbəyə."""
        name = (post.chat.username or "").lower()
        if not any(s["kind"] == "tg_channel" and s["id"].lower() == name for s in filler.sources()):
            return
        a = post.audio
        title = a.title or os.path.splitext(a.file_name or "")[0]
        if not title:
            return
        item = tg_item(a.performer or "", title)
        item["raw_duration"] = a.duration or 0
        key = item["query"].lower()
        if key not in filler.seen:
            filler.seen.add(key)
            filler.queue.appendleft(item)
            logger.info(f"📦 Kanal postu növbəyə: {item['title']}")

    # Restartdan sonra: əvvəl açıq idisə davam et
    if filler.enabled:
        try:
            filler.start()
        except RuntimeError:              # event loop hələ işləmirsə
            pass

    logger.info("✅ Depo doldurucu plugin-i yükləndi (/depo)")


async def teardown(context):
    """plugin_manager söndürəndə / yenidən yükləyəndə fon işini dayandırır (vəziyyət saxlanılır)."""
    filler = getattr(context, "depo_filler", None)
    if filler:
        await filler.stop(persist=False)
