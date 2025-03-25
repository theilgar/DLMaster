import os
import sys
import asyncio
import logging
from aiogram import Bot, Dispatcher
from dotenv import load_dotenv
from collections import defaultdict
from handlers.plugin_handlers import PluginReloadHandler, load_plugins
from watchdog.observers import Observer
from database import Database
from inline_plugin import setup_inline_plugin
from plugins.start_plugin import register_start_handlers  # Start və qrup handlerlərini daxil edirik

# Python-un modul axtarış yollarına əsas qovluğu əlavə edin
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Loqlama konfiqurasiyası
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

load_dotenv()

class AppContext:
    def __init__(self):
        logger.info("Bot başladı...")
        self.bot = Bot(token=os.getenv("TELEGRAM_TOKEN"))
        self.dp = Dispatcher()
        self.creator_id = int(os.getenv("CREATOR_ID", "0").split()[0])
        self.creator_info = os.getenv("CREATOR_INFO", "🤖 Bot tərəfindən yaradılıb")
        self.sudo_users = self.load_sudo_users()
        self.user_searches = {}
        self.active_tasks = {}
        self.creator_messages = defaultdict(list)
        self.db = Database()
        self.spotify = None
        self.youtube = None

        # Bot başladığı zaman log yaz
        self.db.add_log(level="INFO", message="Bot başladı.")

    async def initialize_services(self):
        from handlers.spotify_handler import SpotifyManager
        from handlers.youtube_handler import YoutubeManager
        self.spotify = SpotifyManager(
            os.getenv("SPOTIFY_CLIENT_ID"),
            os.getenv("SPOTIFY_CLIENT_SECRET")
        )
        self.youtube = YoutubeManager(browser="firefox")
        logger.info("Xarici servislər yükləndi.")

    def load_sudo_users(self):
        if os.path.exists("sudo_users.txt"):
            logger.info("sudo_users.txt faylı tapıldı.")
            with open("sudo_users.txt", "r") as f:
                return [int(line.strip()) for line in f.readlines()]
        logger.warning("sudo_users.txt faylı tapılmadı.")
        return []

    def save_sudo_users(self):
        with open("sudo_users.txt", "w") as f:
            for user_id in self.sudo_users:
                f.write(f"{user_id}\n")

async def main():
    context = AppContext()
    await context.initialize_services()
    load_plugins(context)

    # Inline plugin-i işə sal
    await setup_inline_plugin(context.dp, context.youtube)

    # Start və qrup handlerlərini yükləyirik
    await register_start_handlers(context.dp, context)

    # Plugin reloading functionality
    plugins_dir = "plugins"
    loop = asyncio.get_event_loop()
    event_handler = PluginReloadHandler(context, plugins_dir, context.bot, loop)
    observer = Observer()
    observer.schedule(event_handler, path=plugins_dir, recursive=True)
    observer.start()
    logger.info(f"Fayl monitorinqi başladı: {plugins_dir}")

    logger.info("Bot polling başladı...")
    await context.dp.start_polling(context.bot)

    # Cleanup on shutdown
    observer.stop()
    observer.join()

if __name__ == "__main__":
    asyncio.run(main())