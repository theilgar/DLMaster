import os
import sys
import asyncio
import logging
from aiogram import Bot, Dispatcher
from dotenv import load_dotenv
from collections import defaultdict
from watchdog.observers import Observer
from database import Database
from plugins.start_plugin import register_start_handlers
from handlers.youtube_handler import YoutubeManagerPlaylist
from handlers.utilities import YoutubeManager
from handlers.plugin_handlers import PluginReloadHandler, load_plugins

# Python modul yolları
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Logging konfiqurasiyası
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

load_dotenv()

class AppContext:
    def __init__(self):
        logger.info("Bot işə salınır...")
        self.bot = Bot(token=os.getenv("TELEGRAM_TOKEN"))
        self.dp = Dispatcher()
        self.creator_id = int(os.getenv("CREATOR_ID", "0"))
        
        # Log chat ID yüklənir
        self.log_chat_id = int(os.getenv("LOG_CHAT_ID", "0")) or None
        
        # Sudo istifadəçiləri yüklənir
        sudo_users_str = os.getenv("SUDO_USERS", "")
        self.sudo_users = [int(user_id.strip()) for user_id in sudo_users_str.split(",") if user_id.strip()]
        
        self.creator_info = os.getenv("CREATOR_INFO", "🤖 Bot tərəfindən yaradıldı")
        self.user_searches = {}
        self.active_tasks = {}
        self.creator_messages = defaultdict(list)
        self.db = Database()
        self.spotify = None
        self.youtube = None
        self.youtube_playlist = None

        # Downloads qovluğunu yoxlamaq və yaratmaq
        self.downloads_dir = "downloads"
        self._ensure_downloads_dir_exists()

        self.db.add_log(level="INFO", message="Bot işə salındı")

    def _ensure_downloads_dir_exists(self):
        """Downloads qovluğunun mövcud olub-olmadığını yoxlayır və yoxdursa yaradır"""
        if not os.path.exists(self.downloads_dir):
            os.makedirs(self.downloads_dir)
            logger.info(f"'{self.downloads_dir}' qovluğu yaradıldı.")

    async def initialize_services(self):
        """Xarici servisləri işə salır"""
        try:
            from handlers.spotify_handler import SpotifyManager
            
            # Spotify servisini başlat
            self.spotify = SpotifyManager(
                os.getenv("SPOTIFY_CLIENT_ID"),
                os.getenv("SPOTIFY_CLIENT_SECRET")
            )
            
            # YouTube servislərini başlat (eyni brauzer konfiqurasiyası ilə)
            browser = os.getenv("YOUTUBE_BROWSER", "firefox")
            self.youtube = YoutubeManager(browser=browser)
            self.youtube_playlist = YoutubeManagerPlaylist(browser=browser)
            
            logger.info("Xarici servislər uğurla yükləndi")
            
            # Başlanğıc yoxlamalar
            await self._perform_initial_checks()
            
        except Exception as e:
            logger.error(f"Servislər başladılarkən xəta: {str(e)}")
            raise

    async def _perform_initial_checks(self):
        """Başlanğıc yoxlamalarını həyata keçirir"""
        # YouTube bağlantı testi
        try:
            test_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
            test_file = await self.youtube.download_track(test_url, "test_download")
            if test_file and os.path.exists(test_file):
                os.remove(test_file)
                logger.info("YouTube bağlantı testi uğurlu oldu")
            else:
                logger.warning("YouTube bağlantı testi uğursuz oldu")
        except Exception as e:
            logger.warning(f"YouTube bağlantı testi uğursuz oldu: {str(e)}")

    def update_env_file(self, key, value):
        """.env faylını yeniləyir"""
        env_file = ".env"
        lines = []
        key_found = False
        
        if os.path.exists(env_file):
            with open(env_file, "r") as f:
                lines = f.readlines()
        
        # Mövcud açarı yenilə
        for i, line in enumerate(lines):
            if line.startswith(f"{key}="):
                lines[i] = f"{key}={value}\n"
                key_found = True
                break
        
        # Yeni açar əlavə et
        if not key_found:
            lines.append(f"{key}={value}\n")
        
        # Faylı yenidən yaz
        with open(env_file, "w") as f:
            f.writelines(lines)

    def save_sudo_users(self):
        """SUDO_USERS-i .env faylına yadda saxlayır"""
        sudo_str = ",".join(map(str, self.sudo_users))
        self.update_env_file("SUDO_USERS", sudo_str)

    def save_log_chat(self, chat_id):
        """LOG_CHAT_ID-ni .env faylına yadda saxlayır"""
        self.log_chat_id = chat_id
        self.update_env_file("LOG_CHAT_ID", str(chat_id) if chat_id else "")

async def main():
    try:
        context = AppContext()
        await context.initialize_services()
        
        # Pluginləri yüklə
        load_plugins(context)
        
        # Başlanğıc handler-larını yüklə
        await register_start_handlers(context.dp, context)

        # Plugin yeniləməsi üçün fayl izləyicisi
        plugins_dir = "plugins"
        loop = asyncio.get_event_loop()
        event_handler = PluginReloadHandler(context, plugins_dir, context.bot, loop)
        observer = Observer()
        observer.schedule(event_handler, path=plugins_dir, recursive=True)
        observer.start()
        logger.info(f"Fayl izlənməsi başladı: {plugins_dir}")

        logger.info("Bot polling işə salınır...")
        await context.dp.start_polling(context.bot)

    except Exception as e:
        logger.error(f"Bot başladılarkən kritik xəta: {str(e)}")
    finally:
        # Təmizlik əməliyyatları
        if 'observer' in locals():
            observer.stop()
            observer.join()
        logger.info("Bot dayandırıldı")

if __name__ == "__main__":
    asyncio.run(main())