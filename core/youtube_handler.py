import os
import logging
import asyncio
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
            # Əvvəl audio axını, olmasa audio+video olan istənilən format
            'format': 'bestaudio/best/bestvideo+bestaudio',
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
                downloads = info.get('requested_downloads') or []
                src_path = downloads[0]['filepath'] if downloads else ydl.prepare_filename(info)

            if not os.path.exists(src_path):
                raise FileNotFoundError("Yüklənən fayl tapılmadı")

            if cancelled():
                raise DownloadCancelled("İstifadəçi yükləməni dayandırdı")
            return await convert_to_m4a(src_path, base_name, cancel_event)

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

    async def youtube_search(self, query: str) -> list:
        """
        YouTube-da axtarış edir və nəticələri qaytarır.
        """
        try:
            with YoutubeDL(self.get_ydl_opts()) as ydl:
                info = await asyncio.to_thread(ydl.extract_info, f"ytsearch25:{query}", download=False)
                return [{
                    'title': entry.get('title', 'Naməlum Mahnı'),
                    'url': entry.get('url', ''),
                    'duration': format_duration(entry.get('duration', 0)),
                    'raw_duration': entry.get('duration', 0)
                } for entry in info.get('entries', []) if entry]
        except Exception as e:
            logger.error(f"Axtarış xətası: {e}")
            raise RuntimeError(f"Axtarış xətası: {str(e)}")

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
