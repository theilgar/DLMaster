import re
import os
import asyncio
import logging
from yt_dlp import YoutubeDL
from core.webprofile import get_cookies_from_browser, get_random_user_agent

logger = logging.getLogger(__name__)


def sanitize_filename(filename: str) -> str:
    return re.sub(r'[\\/*?:"<>|]', "", filename).strip()

def format_duration(seconds: int, is_ms: bool = False) -> str:
    try:
        duration = int(seconds)
        if is_ms:  # Əgər millisaniyədirsə, saniyəyə çevir
            duration = duration // 1000
            
        minutes, seconds = divmod(duration, 60)
        hours, minutes = divmod(minutes, 60)
        
        if hours > 0:
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        return f"{minutes:02d}:{seconds:02d}"
    except Exception as e:
        logger.error(f"Vaxt formatlama xətası: {e}")
        return "00:00"

class YoutubeManager:
    def __init__(self, browser: str = "firefox"):
        self.browser = browser

    def get_ydl_opts(self):
        return {
            'format': 'bestaudio',
            'extract_flat': True,
            'quiet': True,
            'socket_timeout': 30,
            'cookiesfrombrowser': get_cookies_from_browser(self.browser),
            'proxy': 'socks5://127.0.0.1:9050',
            'headers': {
                'User-Agent': get_random_user_agent()
            }
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

    async def download_track(self, query: str, base_name: str) -> str:
        ydl_opts = {
            "format": "bestaudio[ext=m4a]",
            "outtmpl": os.path.join("download", f"{base_name}.%(ext)s"),  # Updated path
            "nopart": True,
            "retries": 3,
            "cookiesfrombrowser": get_cookies_from_browser(self.browser),
            'proxy': 'socks5://127.0.0.1:9050',
            "headers": {
                'User-Agent': get_random_user_agent()
            }
        }
        try:
            with YoutubeDL(ydl_opts) as ydl:
                await asyncio.to_thread(ydl.download, [query])
                file_path = os.path.join("download", f"{base_name}.m4a")  # Updated path
                if not os.path.exists(file_path):
                    raise FileNotFoundError("Fayl yaradıla bilmədi")
                return file_path
        except Exception as e:
            logger.error(f"Yükləmə xətası: {e}")
            raise RuntimeError(f"Yükləmə xətası: {str(e)}")

