import re
import asyncio
import os
import random
from yt_dlp import YoutubeDL
import logging
from handlers.cookies_handler import get_cookies_from_browser  # Cookies handleri import edirik

logger = logging.getLogger(__name__)

# Müxtəlif User-Agent-lər siyahısı
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:110.0) Gecko/20100101 Firefox/110.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]

def get_random_user_agent():
    return random.choice(USER_AGENTS)

def sanitize_filename(filename: str) -> str:
    return re.sub(r'[\\/*?:"<>|]', "", filename).strip()

def format_duration(ms: int) -> str:
    try:
        seconds = ms // 1000
        minutes, seconds = divmod(seconds, 60)
        hours, minutes = divmod(minutes, 60)
        return f"{hours:02}:{minutes:02}:{seconds:02}" if hours else f"{minutes:02}:{seconds:02}"
    except Exception:
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
            'headers': {
                'User-Agent': get_random_user_agent() # Hər dəfə random User-Agent seçilir
            }
        }

    async def youtube_search(self, query: str) -> list:
        """
        YouTube-da axtarış edir və nəticələri qaytarır.
        """
        try:
            with YoutubeDL(self.get_ydl_opts()) as ydl:
                info = await asyncio.to_thread(ydl.extract_info, f"ytsearch5:{query}", download=False)
                return [{
                    'title': entry.get('title', 'Naməlum Mahnı'),
                    'url': entry.get('url', ''),
                    'duration': format_duration((entry.get('duration', 0) or 0) * 1000),
                    'raw_duration': entry.get('duration', 0)
                } for entry in info.get('entries', [])]
        except Exception as e:
            raise RuntimeError(f"Axtarış xətası: {str(e)}")

    async def download_track(self, query: str, base_name: str) -> str:
        """
        YouTube-dan mahnı yükləyir və fayl yolunu qaytarır.
        """
        ydl_opts = {
            "format": "bestaudio[ext=m4a]",
            "outtmpl": f"{base_name}.%(ext)s",
            "nopart": True,
            "retries": 3,
            "cookiesfrombrowser": get_cookies_from_browser(self.browser),
            "headers": {
                'User-Agent': get_random_user_agent()  # Hər dəfə random User-Agent seçilir
            }
        }
        try:
            with YoutubeDL(ydl_opts) as ydl:
                await asyncio.to_thread(ydl.download, [query])
                file_path = f"{base_name}.m4a"
                if not os.path.exists(file_path):
                    raise FileNotFoundError("Fayl yaradıla bilmədi")
                return file_path
        except Exception as e:
            raise RuntimeError(f"Yükləmə xətası: {str(e)}")

# Əgər bu fayl birbaşa işlədilirsə, asyncio.run() istifadə et
if __name__ == "__main__":
    async def main():
        youtube_manager = YoutubeManager(browser="firefox")
        query = "example song"
        
        # Axtarış edin
        results = await youtube_manager.youtube_search(query)
        print("Axtarış nəticələri:", results)
        
        # Nəticələrdən bir mahnı yükləyin
        if results:
            file_path = await youtube_manager.download_track(results[0]['url'], sanitize_filename(results[0]['title']))
            print(f"Fayl yükləndi: {file_path}")

    asyncio.run(main())
