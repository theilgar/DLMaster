import os
import logging
import asyncio
import re
import shutil
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadCancelled   # plugin-lər "dayandırıldı" halını tanımaq üçün import edir
from core.utilities import format_duration, sanitize_filename
from core.webprofile import get_random_user_agent, get_cookies_from_browser

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

DOWNLOAD_DIR = "download"

# YouTube artıq formatları açmaq üçün JS challenge həll etməyi tələb edir:
# yt-dlp-yə JS runtime (deno tövsiyə olunur, ya node>=20) və yt-dlp-ejs lazımdır.
def _build_js_opts() -> dict:
    opts = {'remote_components': ['ejs:github']}
    runtimes = {}
    for name in ('deno', 'node', 'bun'):
        path = shutil.which(name)
        if path:
            runtimes[name] = {'path': path}
    if runtimes:
        opts['js_runtimes'] = runtimes
        logger.info("JS runtime tapıldı: " + ", ".join(f"{n}={v['path']}" for n, v in runtimes.items()))
    else:
        logger.error("JS runtime TAPILMADI (deno/node/bun). YouTube yükləməsi işləməyəcək — deno və ya node>=20 quraşdırın.")
    return opts


JS_OPTS = _build_js_opts()


def cookie_opts(browser_cookies) -> dict:
    """YOUTUBE_COOKIES_FILE (Netscape cookies.txt) varsa onu, yoxdursa brauzer cookies-ini istifadə edir."""
    cookies_file = os.getenv("YOUTUBE_COOKIES_FILE")  # load_dotenv import-dan sonra işlədiyi üçün burada oxunur
    if cookies_file and os.path.isfile(cookies_file):
        return {'cookiefile': cookies_file}
    return {'cookiesfrombrowser': browser_cookies}


def remove_partial(base_name: str):
    """Dayandırılmış yükləmədən qalan faylları silir (base_name.*)."""
    try:
        for name in os.listdir(DOWNLOAD_DIR):
            if name.startswith(base_name + "."):
                try:
                    os.remove(os.path.join(DOWNLOAD_DIR, name))
                except OSError:
                    pass
    except OSError:
        pass


async def convert_to_m4a(src_path: str, base_name: str, cancel_event=None) -> str:
    """Yüklənmiş video/audio faylını ffmpeg ilə m4a (AAC) formatına çevirir."""
    dst_path = os.path.join(DOWNLOAD_DIR, f"{base_name}.m4a")

    # Mənbə artıq m4a-dırsa, təkrar çevirməyə ehtiyac yoxdur
    if os.path.abspath(src_path) == os.path.abspath(dst_path):
        return dst_path

    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", src_path,
        "-vn", "-map", "0:a:0",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        dst_path,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    # Çevirmə zamanı "dayandır" basılarsa ffmpeg dərhal söndürülür
    while True:
        try:
            await asyncio.wait_for(proc.wait(), 0.5)
            break
        except asyncio.TimeoutError:
            if cancel_event is not None and cancel_event.is_set():
                proc.kill()
                await proc.wait()
                for p in (src_path, dst_path):
                    try:
                        os.remove(p)
                    except OSError:
                        pass
                raise DownloadCancelled("İstifadəçi yükləməni dayandırdı")
    stderr = await proc.stderr.read()

    # Mənbə fayl artıq lazım deyil
    try:
        os.remove(src_path)
    except OSError:
        pass

    if proc.returncode != 0 or not os.path.exists(dst_path):
        raise RuntimeError(f"ffmpeg çevirmə xətası: {stderr.decode(errors='ignore').strip()[-300:]}")
    return dst_path


# ───────────────────────── Yükləmə rejimi (/menu → 🖥 Sistem → 🎧 Yükləmə formatı) ─────────────────────────
# ⚡ direct_m4a: YouTube-un hazır m4a (AAC) audio axını birbaşa endirilir — çevirmə yoxdur, ən sürətli yol
# 🎞 ffmpeg:     m4a axını yoxdursa (və ya birbaşa rejim söndürülübsə) ən yaxşı audio endirilib m4a-ya çevrilir
DL_STATS = {"direct": 0, "converted": 0, "original": 0}

# Son yüklənən videoların YouTube metadata-sı (video_id → artist / track / kanal) — music_plugin oxuyur
VIDEO_META = {}


def remember_meta(info: dict):
    vid = info.get("id")
    if not vid:
        return
    artists = info.get("artists") or []
    VIDEO_META[vid] = {
        "artist": info.get("artist") or (", ".join(artists[:3]) if artists else None),   # YouTube Music metadata
        "track": info.get("track"),
        "channel": info.get("channel") or info.get("uploader"),
        "title": info.get("title"),
    }
    if len(VIDEO_META) > 2000:
        for k in list(VIDEO_META)[:1000]:
            VIDEO_META.pop(k, None)


def _setting_on(key: str, default: bool = True) -> bool:
    try:
        from core.database import get_db
        value = get_db().get_setting(key)
    except Exception:
        value = None
    if value is None:
        return default
    return str(value).strip().lower() in ("1", "on", "true", "yes")


def download_mode() -> dict:
    return {"direct_m4a": _setting_on("dl:direct_m4a"), "ffmpeg": _setting_on("dl:ffmpeg")}


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def _format_string(direct: bool, ffmpeg: bool) -> str:
    if direct:
        # əvvəl hazır m4a audio, olmasa istənilən audio; birləşdirmə (video+audio) yalnız ffmpeg ilə
        return "bestaudio[ext=m4a]/bestaudio/best" + ("/bestvideo+bestaudio" if ffmpeg else "")
    return "bestaudio/best" + ("/bestvideo+bestaudio" if ffmpeg else "")


async def download_as_m4a(url: str, base_name: str, browser: str, cookies=None, cancel_event=None) -> str:
    """Videonu yükləyir (mümkün olarsa yalnız audio axını, yoxdursa video) və m4a-ya çevirir.

    YouTube tərəfi tez-tez "page needs to be reloaded" / "format not available" verir,
    ona görə bir neçə strategiya ardıcıl sınanır.
    """
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    browser_cookies = cookies if cookies is not None else (browser,)

    def cancelled() -> bool:
        return cancel_event is not None and cancel_event.is_set()

    def progress_hook(_):
        # yt-dlp hər yüklənən hissədən sonra çağırır → dayandırılıbsa dərhal kəsir
        if cancelled():
            raise DownloadCancelled("İstifadəçi yükləməni dayandırdı")

    mode = download_mode()
    use_ffmpeg = mode["ffmpeg"] and ffmpeg_available()
    fmt = _format_string(mode["direct_m4a"], use_ffmpeg)

    # (təsvir, cookies istifadə olunsun?, player_client)
    attempts = [
        ("cookies + default client", True, None),
        ("cookies + tv/web_safari", True, ['tv', 'web_safari']),
        ("cookies-siz + android_vr", False, ['android_vr']),
    ]

    last_error = None
    for label, use_cookies, clients in attempts:
        if cancelled():
            remove_partial(base_name)
            raise DownloadCancelled("İstifadəçi yükləməni dayandırdı")
        ydl_opts = {
            'format': fmt,
            'outtmpl': os.path.join(DOWNLOAD_DIR, f"{base_name}.%(ext)s"),
            'noplaylist': True,
            'nopart': True,
            'retries': 3,
            'quiet': True,
            'headers': {'User-Agent': get_random_user_agent()},
            'progress_hooks': [progress_hook],
            **JS_OPTS,
        }
        if use_cookies:
            ydl_opts.update(cookie_opts(browser_cookies))
        if clients:
            ydl_opts['extractor_args'] = {'youtube': {'player_client': clients}}

        try:
            logger.info(f"Yükləmə cəhdi: {label}")
            with YoutubeDL(ydl_opts) as ydl:
                info = await asyncio.to_thread(ydl.extract_info, url, download=True)
                remember_meta(info)
                downloads = info.get('requested_downloads') or []
                src_path = downloads[0]['filepath'] if downloads else ydl.prepare_filename(info)

            if not os.path.exists(src_path):
                raise FileNotFoundError("Yüklənən fayl tapılmadı")

            if cancelled():
                raise DownloadCancelled("İstifadəçi yükləməni dayandırdı")

            if src_path.lower().endswith(".m4a"):
                DL_STATS["direct"] += 1                  # ⚡ çevirməsiz
                return src_path
            if use_ffmpeg:
                DL_STATS["converted"] += 1               # 🎞 ffmpeg ilə m4a
                return await convert_to_m4a(src_path, base_name, cancel_event)
            DL_STATS["original"] += 1                    # ffmpeg söndürülüb — olduğu kimi (webm/opus və s.)
            logger.info(f"ffmpeg söndürülüb, fayl çevrilmədən göndərilir: {os.path.basename(src_path)}")
            return src_path

        except DownloadCancelled:
            logger.info(f"Yükləmə dayandırıldı: {url}")
            remove_partial(base_name)
            raise
        except Exception as e:
            if cancelled():
                remove_partial(base_name)
                raise DownloadCancelled("İstifadəçi yükləməni dayandırdı")
            last_error = e
            logger.warning(f"Cəhd uğursuz ({label}): {e}")

    raise last_error


# ───────────────────────── Axtarış mənbəyi (/menu → 🖥 Sistem → 🔎 Axtarış mənbəyi) ─────────────────────────
# yt   — adi YouTube axtarışı (klip, cover, live — hamısı)
# ytm  — YouTube Music, yalnız mahnılar (rəsmi audio, təmiz ad); nəticə olmasa YouTube-a keçir
# both — ikisi paralel, nəticələr növbə ilə qarışdırılır, təkrarlar silinir
SEARCH_SOURCES = {
    "yt": "▶️ YouTube",
    "ytm": "🎵 YouTube Music",
    "both": "🔀 Hər ikisi",
}
SEARCH_SETTING = "search:source"
DEFAULT_SOURCE = "ytm"          # /menu-dan heç nə seçilməyibsə: əvvəl YouTube Music

# ── YT Music nəticəsi sorğuya həqiqətən uyğundurmu? ──
# YTM demək olar həmişə "nəsə" qaytarır; mahnı orada yoxdursa da oxşar adlı başqa mahnılar gəlir.
# Ona görə ilk nəticələrdə sorğunun sözlərinin çoxu yoxdursa → "tapılmadı" sayılır və YouTube göstərilir.
_AZ_MAP = str.maketrans("əışçğöüƏIŞÇĞÖÜİ", "eiscgouEISCGOUI")
_RELEVANT_TOP = 5               # yoxlanılan ilk nəticələr
_RELEVANT_MIN = 0.6             # sorğu sözlərinin ən az 60%-i nəticədə olmalıdır


def _words(text: str) -> list:
    import unicodedata
    text = unicodedata.normalize("NFKD", (text or "").translate(_AZ_MAP).lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.findall(r"\w+", text)


def _word_match(word: str, words: list) -> bool:
    from difflib import SequenceMatcher
    for w in words:
        if w == word or (len(word) >= 3 and (w.startswith(word) or word.startswith(w) and len(w) >= 3)):
            return True
        if len(word) >= 4 and SequenceMatcher(None, word, w).ratio() >= 0.8:   # kiçik yazı xətaları
            return True
    return False


def ytm_relevant(query: str, results: list) -> bool:
    q = [w for w in _words(query) if len(w) >= 2] or _words(query)
    if not q:
        return bool(results)
    for r in results[:_RELEVANT_TOP]:
        words = _words(r.get("title"))
        if sum(_word_match(w, words) for w in q) / len(q) >= _RELEVANT_MIN:
            return True
    return False
SEARCH_STATS = {"yt": 0, "ytm": 0, "fallback": 0}
_VID_RE = re.compile(r"(?:v=|youtu\.be/|shorts/)([\w-]{11})")


def search_source() -> str:
    try:
        from core.database import get_db
        value = (get_db().get_setting(SEARCH_SETTING) or DEFAULT_SOURCE).strip().lower()
    except Exception:
        value = DEFAULT_SOURCE
    return value if value in SEARCH_SOURCES else DEFAULT_SOURCE


try:                                    # pip install ytmusicapi — artist + müddət ilə dəqiq nəticələr
    from ytmusicapi import YTMusic
except Exception:
    YTMusic = None
_ytmusic = None


def ytmusicapi_available() -> bool:
    return YTMusic is not None


def _get_ytmusic():
    global _ytmusic
    if _ytmusic is None and YTMusic is not None:
        _ytmusic = YTMusic()
    return _ytmusic


class YoutubeManagerPlaylist:
    def __init__(self, browser: str = "firefox"):
        self.browser = browser
        self.ydl_opts = {
            'extract_flat': True,
            'quiet': True,
            'socket_timeout': 15,
            'cookiesfrombrowser': (self.browser,),
            #'proxy': 'socks5://127.0.0.1:9050',
            'headers': {
                'User-Agent': get_random_user_agent()
            },
            **JS_OPTS,
        }
        logger.info(f"{self.browser} brauzerindən cookies istifadə edilir.")

    def get_playlist_info_playlist(self, url: str) -> dict:
        try:
            logger.info(f"Playlist məlumatları əldə edilir: {url}")
            with YoutubeDL(self.ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                entries = info.get('entries', [])
                if not entries:
                    raise ValueError("Playlistdə mahnı tapılmadı")
                total_duration = sum(entry.get('duration', 0) * 1 for entry in entries if entry)
                return {
                    "title": info.get('title', 'Naməlum Playlist'),
                    "total": len(entries),
                    "duration": format_duration(total_duration),
                    "entries": [
                        {
                            'id': entry.get('id', ''),
                            'title': entry.get('title', 'Naməlum Mahnı'),
                            'duration': entry.get('duration', 0),
                            'url': f"https://youtu.be/{entry.get('id', '')}"
                        }
                        for entry in entries
                    ]
                }
        except Exception as e:
            logger.error(f"Xəta: {str(e)}")
            return {"title": "Xəta baş verdi", "total": 0, "duration": format_duration(0), "entries": []}

    async def download_track(self, url: str, filename: str, cancel_event=None) -> str:
        logger.info(f"Audio faylı yüklənir: {url}")
        try:
            file_path = await download_as_m4a(url, filename, self.browser, cancel_event=cancel_event)
            logger.info(f"Audio faylı uğurla yükləndi: {file_path}")
            return file_path
        except Exception as e:
            logger.error(f"Yükləmə xətası: {str(e)}")
            return None  # Əvvəlki davranış: xəta olarsa None


class YoutubeManager:
    def __init__(self, browser: str = "firefox"):
        self.browser = browser

    def get_ydl_opts(self):
        return {
            'extract_flat': True,
            'quiet': True,
            'socket_timeout': 30,
            'cookiesfrombrowser': get_cookies_from_browser(self.browser),
            #'proxy': 'socks5://127.0.0.1:9050',
            'headers': {
                'User-Agent': get_random_user_agent()
            },
            **JS_OPTS,
        }

    async def _yt_search(self, query: str, limit: int) -> list:
        """Adi YouTube axtarışı (ytsearch)."""
        with YoutubeDL(self.get_ydl_opts()) as ydl:
            info = await asyncio.to_thread(ydl.extract_info, f"ytsearch{int(limit)}:{query}", download=False)
        return [{
            'title': entry.get('title', 'Naməlum Mahnı'),
            'url': entry.get('url', ''),
            'duration': format_duration(entry.get('duration', 0)),
            'raw_duration': entry.get('duration', 0),
            'source': 'yt',
        } for entry in info.get('entries', []) if entry]

    async def _ytm_search(self, query: str, limit: int) -> list:
        """YouTube Music — yalnız mahnılar. ytmusicapi varsa onu, yoxdursa yt-dlp-ni istifadə edir."""
        yt_music = _get_ytmusic()
        if yt_music is not None:
            items = await asyncio.to_thread(yt_music.search, query, filter="songs", limit=limit)
            results = []
            for it in items or []:
                vid = it.get('videoId')
                if not vid:
                    continue
                artists = ", ".join(a['name'] for a in (it.get('artists') or [])[:3] if a.get('name'))
                title = it.get('title') or 'Naməlum Mahnı'
                duration = int(it.get('duration_seconds') or 0)
                results.append({
                    'title': f"{artists} - {title}" if artists else title,
                    'url': f"https://www.youtube.com/watch?v={vid}",
                    'duration': format_duration(duration),
                    'raw_duration': duration,
                    'source': 'ytm',
                })
            return results[:limit]

        # yt-dlp yolu (#songs bölməsi): çox vaxt yalnız mahnı adı gəlir, artist / müddət olmaya bilər
        from urllib.parse import quote_plus
        opts = self.get_ydl_opts()
        opts['playlistend'] = limit
        with YoutubeDL(opts) as ydl:
            info = await asyncio.to_thread(
                ydl.extract_info, f"https://music.youtube.com/search?q={quote_plus(query)}#songs", download=False)
        results = []
        for entry in info.get('entries') or []:
            if not entry:
                continue
            vid = entry.get('id') or ''
            if len(vid) != 11:
                continue
            title = entry.get('title') or 'Naməlum Mahnı'
            artist = entry.get('artist') or entry.get('channel') or entry.get('uploader')
            if artist and artist.lower() not in title.lower():
                title = f"{artist} - {title}"
            duration = entry.get('duration') or 0
            results.append({
                'title': title,
                'url': f"https://www.youtube.com/watch?v={vid}",
                'duration': format_duration(duration),
                'raw_duration': duration,
                'source': 'ytm',
            })
        return results[:limit]

    async def youtube_search(self, query: str, limit: int = 25, source: str = None) -> list:
        """
        Axtarış — mənbə /menu-dan seçilir (search_source()): yt | ytm | both.
        Nəticə formatı hər mənbədə eynidir: {title, url, duration, raw_duration, source}.
        """
        source = source if source in SEARCH_SOURCES else search_source()
        try:
            if source == "yt":
                SEARCH_STATS["yt"] += 1
                return await self._yt_search(query, limit)

            if source == "ytm":
                try:
                    results = await self._ytm_search(query, limit)
                except Exception as e:
                    logger.warning(f"YouTube Music axtarışı alınmadı, YouTube-a keçilir: {e}")
                    results = []
                if results and ytm_relevant(query, results):
                    SEARCH_STATS["ytm"] += 1
                    return results
                # YT Music-də tapılmadı → YouTube nəticələri; YTM-in tapdıqları (varsa) siyahının sonunda qalır
                SEARCH_STATS["fallback"] += 1
                logger.info(f"YT Music-də uyğun nəticə yoxdur, YouTube göstərilir: {query!r}")
                try:
                    yt_res = await self._yt_search(query, limit)
                except Exception as e:
                    if results:
                        logger.warning(f"YouTube axtarışı alınmadı, YT Music nəticələri qalır: {e}")
                        return results
                    raise
                seen = {m.group(1) for r in yt_res if (m := _VID_RE.search(r.get('url') or ''))}
                extra = [r for r in results if (m := _VID_RE.search(r['url'])) and m.group(1) not in seen]
                return (yt_res + extra)[:limit]

            # both — paralel; növbə ilə qarışdırılır (🎵, ▶️, 🎵, ▶️ ...), eyni video bir dəfə
            ytm_res, yt_res = await asyncio.gather(
                self._ytm_search(query, limit), self._yt_search(query, limit), return_exceptions=True)
            if isinstance(ytm_res, Exception):
                logger.warning(f"YouTube Music axtarışı alınmadı: {ytm_res}")
                ytm_res = []
            if isinstance(yt_res, Exception):
                if not ytm_res:
                    raise yt_res
                logger.warning(f"YouTube axtarışı alınmadı: {yt_res}")
                yt_res = []
            SEARCH_STATS["ytm" if ytm_res else "fallback"] += 1
            merged, seen = [], set()
            for i in range(max(len(ytm_res), len(yt_res))):
                for lst in (ytm_res, yt_res):
                    if i >= len(lst):
                        continue
                    r = lst[i]
                    m = _VID_RE.search(r.get('url') or '')
                    key = m.group(1) if m else r.get('url')
                    if key in seen:
                        continue
                    seen.add(key)
                    merged.append(r)
            return merged[:limit]
        except Exception as e:
            logger.error(f"Axtarış xətası ({source}): {e}")
            raise RuntimeError(f"Axtarış xətası: {str(e)}")

    async def playlist_entries(self, url: str, limit: int = 5000) -> dict:
        """
        İstənilən YouTube / YouTube Music siyahısı: playlist, albom (OLAK5uy_), Mix/Radio (RD...).
        Nəticə: {"title": ..., "entries": [{title, url, duration, raw_duration}]}
        """
        opts = self.get_ydl_opts()
        opts['playlistend'] = limit
        opts['noplaylist'] = False
        try:
            with YoutubeDL(opts) as ydl:
                info = await asyncio.to_thread(ydl.extract_info, url, download=False)
        except Exception as e:
            logger.error(f"Playlist xətası: {e}")
            raise RuntimeError(f"Playlist açılmadı: {str(e)}")

        entries, seen = [], set()
        for entry in (info.get('entries') or []):
            if not entry:
                continue
            vid = entry.get('id')
            if not vid or vid in seen or len(vid) != 11:
                continue
            seen.add(vid)
            duration = entry.get('duration') or 0
            entries.append({
                'title': entry.get('title') or 'Naməlum Mahnı',
                'url': f"https://www.youtube.com/watch?v={vid}",
                'duration': format_duration(duration),
                'raw_duration': duration,
            })
        return {"title": info.get('title') or "YouTube siyahısı", "entries": entries}

    async def youtube_mix(self, video_id: str, limit: int = 25) -> list:
        """
        YouTube "Mix" (RD radio playlist) — verilən mahnıya oxşar mahnıları qaytarır.
        İlk element mahnının özüdür, ona görə süzülür.
        """
        url = f"https://www.youtube.com/watch?v={video_id}&list=RD{video_id}"
        opts = self.get_ydl_opts()
        opts['playlistend'] = limit + 10
        try:
            with YoutubeDL(opts) as ydl:
                info = await asyncio.to_thread(ydl.extract_info, url, download=False)
        except Exception as e:
            logger.error(f"Mix xətası: {e}")
            raise RuntimeError(f"Mix xətası: {str(e)}")

        results, seen = [], {video_id}
        for entry in (info.get('entries') or []):
            if not entry:
                continue
            vid = entry.get('id')
            if not vid or vid in seen:
                continue
            seen.add(vid)
            duration = entry.get('duration') or 0
            results.append({
                'title': entry.get('title') or 'Naməlum Mahnı',
                'url': f"https://www.youtube.com/watch?v={vid}",
                'duration': format_duration(duration),
                'raw_duration': duration,
            })
            if len(results) >= limit:
                break
        return results

    async def download_track(self, query: str, base_name: str, cancel_event=None) -> str:
        try:
            return await download_as_m4a(
                query, base_name, self.browser,
                cookies=get_cookies_from_browser(self.browser),
                cancel_event=cancel_event,
            )
        except DownloadCancelled:
            raise
        except Exception as e:
            logger.error(f"Yükləmə xətası: {e}")
            raise RuntimeError(f"Yükləmə xətası: {str(e)}")
