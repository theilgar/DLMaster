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
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Edge/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (iPad; CPU OS 17_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Linux; Android 14; SM-G998B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 14; Pixel 8 Pro) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0"
]

def get_random_user_agent():
    return random.choice(USER_AGENTS)

def sanitize_filename(filename: str) -> str:
    return re.sub(r'[\\/*?:"<>|]', "", filename).strip()

def format_duration(seconds: int) -> str:
    """Saniyələri saat:dəqiqə:saniyə formatına çevirir"""
    try:
        seconds = int(seconds)
        minutes, seconds = divmod(seconds, 60)
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
                    'duration': format_duration(entry.get('duration', 0)),
                    'raw_duration': entry.get('duration', 0)
                } for entry in info.get('entries', []) if entry]
        except Exception as e:
            logger.error(f"Axtarış xətası: {e}")
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
                'User-Agent': get_random_user_agent()
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
            logger.error(f"Yükləmə xətası: {e}")
            raise RuntimeError(f"Yükləmə xətası: {str(e)}")

# Test üçün
if __name__ == "__main__":
    async def main():
        youtube_manager = YoutubeManager(browser="firefox")
        query = "example song"
        
        # Axtarış edin
        results = await youtube_manager.youtube_search(query)
        print("Axtarış nəticələri:")
        for result in results:
            print(f"- {result['title']} | {result['duration']} | {result['url']}")
        
        # Nəticələrdən bir mahnı yükləyin
        if results:
            file_path = await youtube_manager.download_track(
                results[0]['url'], 
                sanitize_filename(results[0]['title'])
            )
            print(f"\nFayl yükləndi: {file_path}")

    asyncio.run(main())