import os
import logging
from yt_dlp import YoutubeDL
from handlers.utilities import format_duration, sanitize_filename
import asyncio

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

class YoutubeManager:
    def __init__(self, browser: str = "firefox"):
        self.browser = browser  # browser dəyişənini sinif xüsusiyyəti kimi təyin edirik
        self.ydl_opts = {
            'extract_flat': True, 
            'quiet': True,
            'socket_timeout': 15,
            'cookiesfrombrowser': (self.browser,),  # self.browser istifadə edirik
            'headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:110.0) Gecko/20100101 Firefox/110.0'
            }
        }
        logger.info(f"{self.browser} brauzerindən cookies istifadə edilir.")

    def get_playlist_info(self, url: str) -> dict:
        try:
            logger.info(f"Playlist məlumatları əldə edilir: {url}")
            with YoutubeDL(self.ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                entries = info.get('entries', [])
                if not entries:
                    raise ValueError("Playlistdə mahnı tapılmadı")
                total_duration = sum(entry.get('duration', 0) * 1000 for entry in entries if entry)
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

    async def download_track(self, url: str, filename: str) -> str:
        logger.info(f"Audio faylı yüklənir: {url}")
        ydl_opts = {
            'format': 'bestaudio[ext=m4a]',
            'outtmpl': f'{filename}.%(ext)s',
            'noplaylist': True,
            'retries': 3,
            'cookiesfrombrowser': (self.browser,),  # self.browser istifadə edirik
            'headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:110.0) Gecko/20100101 Firefox/110.0'
            }
        }
        logger.info(f"{self.browser} brauzerindən cookies istifadə edilir.")
        
        try:
            with YoutubeDL(ydl_opts) as ydl:
                info = await asyncio.to_thread(ydl.extract_info, url, download=False)  # Fayl haqqında məlumat əldə edirik
                ext = info.get('ext', 'm4a')  # Fayl uzantısını dinamik şəkildə əldə edirik
                file_path = f"{filename}.{ext}"
                
                await asyncio.to_thread(ydl.download, [url])  # Faylı yükləyirik
                
                if not os.path.exists(file_path):
                    raise FileNotFoundError("Audio faylı yaradılmadı")
                
                logger.info(f"Audio faylı uğurla yükləndi: {file_path}")
                return file_path
        except Exception as e:
            logger.error(f"Yükləmə xətası: {str(e)}")
            return None  # Xəta baş verərsə, None qaytarırıq