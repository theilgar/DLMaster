import datetime 
import os
import sys
import asyncio
import importlib
import logging
import colorlog
from aiogram import Bot, Dispatcher
from dotenv import load_dotenv
from collections import defaultdict
from database import Database
from core.youtube_handler import YoutubeManagerPlaylist
from core.utilities import YoutubeManager
from logging.handlers import RotatingFileHandler
from aiogram.exceptions import TelegramRetryAfter

# Windows üçün rəng dəstəyi
if os.name == 'nt':
    from colorama import init
    init()

# Python module paths
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

def setup_logging():
    """Rəngli və fayla yazılan log konfiqurasiyası"""
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    is_tty = hasattr(sys.stdout, 'isatty') and sys.stdout.isatty()
    console_handler = logging.StreamHandler()
    
    if is_tty:
        console_formatter = colorlog.ColoredFormatter(
            "%(log_color)s%(asctime)s %(bold)s%(name)-20s%(reset)s %(log_color)s%(levelname)-8s%(reset)s %(message)s",
            datefmt="%Y.%m.%d %H:%M:%S",
            log_colors={
                'DEBUG': 'cyan',
                'INFO': 'bold_green',
                'WARNING': 'bold_yellow',
                'ERROR': 'bold_red',
                'CRITICAL': 'bold_white,bg_red',
            },
            reset=True,
            style='%'
        )
    else:
        console_formatter = logging.Formatter(
            "%(asctime)s %(name)-20s %(levelname)-8s %(message)s",
            datefmt="%Y.%m.%d %H:%M:%S"
        )
    
    console_handler.setFormatter(console_formatter)

    # Log klasörü ve dosya adı ayarları
    log_dir = "bot_logs"
    os.makedirs(log_dir, exist_ok=True)
    current_date = datetime.datetime.now().strftime("%Y-%m-%d")
    log_filename = os.path.join(log_dir, f"bot({current_date}).log")

    file_handler = RotatingFileHandler(
        log_filename,
        maxBytes=1_000_000,
        backupCount=5,
        encoding='utf-8'
    )

    logging.basicConfig(
        level=log_level,
        handlers=[console_handler, file_handler],
        force=True
    )

    file_formatter = logging.Formatter(
        "%(asctime)s %(name)-20s %(levelname)-8s %(message)s",
        datefmt="%Y.%m.%d %H:%M:%S"
    )
    file_handler.setFormatter(file_formatter)

    logging.basicConfig(
        level=log_level,
        handlers=[console_handler, file_handler],
        force=True
    )

# Initialize logging first
setup_logging()
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv('config.env')

class AppContext:
    def __init__(self):
        logger.info("🚀 Starting bot initialization...")
        
        self.bot = Bot(token=os.getenv("TELEGRAM_TOKEN"))
        self.dp = Dispatcher()
        self.dp.app_context = self 
        self.creator_id = int(os.getenv("CREATOR_ID", "0"))
        self.creator_info = os.getenv("CREATOR_INFO", "🤖 Created by bot")
        self.user_searches = {}
        self.active_tasks = {}
        self.creator_messages = defaultdict(list)
        self.spotify = None
        self.youtube = None
        self.youtube_playlist = None


        # Database initialization
        try:
            self.db = Database()
            logger.info("✅ Database initialized successfully")
        except Exception as e:
            logger.error(f"❌ Database initialization failed: {str(e)}")
            self.db = None

        # Create download directory if not exists
        os.makedirs("download", exist_ok=True)

    async def initialize_services(self):
        """Xarici servislərin başlatılması"""
        logger.debug("⚙️ Starting services initialization...")
        try:
            from core.spotify_handler import SpotifyManager
            self.spotify = SpotifyManager(
                os.getenv("SPOTIFY_CLIENT_ID"),
                os.getenv("SPOTIFY_CLIENT_SECRET")
            )
            logger.info("✅ Spotify service initialized")
            
            browser = os.getenv("YOUTUBE_BROWSER", "firefox")
            self.youtube = YoutubeManager(browser=browser)
            self.youtube_manager = self.youtube
            self.youtube_playlist = YoutubeManagerPlaylist(browser=browser)
            logger.info(f"✅ YouTube services initialized ({browser})")
                        
        except Exception as e:
            logger.error(f"❌ Service initialization failed: {str(e)}", exc_info=True)
            raise

async def safe_send_media_group(bot, chat_id, media_group, retry_count=3):
    """Media group göndərmək üçün təhlükəsiz wrapper"""
    try:
        await bot.send_media_group(chat_id=chat_id, media=media_group)
    except TelegramRetryAfter as e:
        await asyncio.sleep(e.retry_after)
        await safe_send_media_group(bot, chat_id, media_group, retry_count)
    except Exception as e:
        if retry_count > 0:
            await asyncio.sleep(2)
            await safe_send_media_group(bot, chat_id, media_group, retry_count-1)
        else:
            raise
 
async def load_plugins(context):
    """Plugins'leri asinxron şəkildə yüklə"""
    plugins_dir = "plugins"
    logger.info(f"🔍 Scanning plugins in: {plugins_dir}")
    
    for filename in os.listdir(plugins_dir):
        if filename.endswith(".py") and filename != "__init__.py":
            module_name = f"{plugins_dir}.{filename[:-3]}"
            logger.info(f"🔄 Loading: {module_name}")
            
            try:
                module = importlib.import_module(module_name)
                if hasattr(module, "setup"):
                    if asyncio.iscoroutinefunction(module.setup):
                        await module.setup(context)
                        logger.info(f"✅ Loaded (async): {module_name}")
                    else:
                        module.setup(context)
                        logger.info(f"✅ Loaded (sync): {module_name}")
                else:
                    logger.warning(f"⚠️ No setup() in: {module_name}")
            except Exception as e:
                logger.error(f"❌ Failed to load {module_name}: {str(e)}", exc_info=True)

async def cleanup_downloads():
    for filename in os.listdir("download"):
        file_path = os.path.join("download", filename)
        try:
            if os.path.isfile(file_path):
                os.unlink(file_path)
        except Exception as e:
            logger.error(f"Failed to delete {file_path}: {e}")

async def main():
    try:
        logger.info("🤖 Starting bot...")
        
        await cleanup_downloads()
        
        context = AppContext()
        
        logger.debug("🛠 Initializing services...")
        await context.initialize_services()
        
        logger.info("🔌 Loading plugins...")
        await load_plugins(context)
        
        logger.info("🚀 Starting polling...")
        await context.dp.start_polling(context.bot)
        logger.info("🛑 Polling stopped")

    except Exception as e:
        logger.critical(f"💥 Fatal error: {str(e)}", exc_info=True)
    finally:
        logger.info("🔴 Bot stopped")
        if 'context' in locals() and context.db:
            context.db.close()
        # Bot dayandıqda yükləmələri təmizlə
        await cleanup_downloads()

if __name__ == "__main__":
    os.environ["PYTHONUNBUFFERED"] = "1"
    
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🛑 Stopped by user")
    except Exception as e:
        logger.critical(f"💥 Unhandled exception: {str(e)}", exc_info=True)