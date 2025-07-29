import os
import sys
import asyncio
import importlib
import logging
from aiogram import Bot, Dispatcher, Router, BaseMiddleware
from dotenv import load_dotenv
from collections import defaultdict
from core.youtube_handler import YoutubeManagerPlaylist, YoutubeManager
from aiogram.exceptions import TelegramRetryAfter
from core.logger import setup_logging
from core.utilities import cleanup_downloads, load_plugins, CommandLoggerMiddleware
from aiogram.types import Message
from typing import Callable, Dict, Any

setup_logging()
logger = logging.getLogger(__name__)

load_dotenv('config.env')

class AppContext:
    def __init__(self):
        logger.info("🚀 Starting bot initialization...")
        
        self.bot = Bot(token=os.getenv("TELEGRAM_TOKEN"))
        self.dp = Dispatcher()
        self.dp.app_context = self
        self.main_router = Router()
        self.dp.include_router(self.main_router)
        
        self.creator_id = int(os.getenv("CREATOR_ID", "0"))
        self.creator_info = os.getenv("CREATOR_INFO", "🤖 Created by bot")
        self.user_searches = {}
        self.active_tasks = {}
        self.creator_messages = defaultdict(list)
        self.spotify = None
        self.youtube = None
        self.youtube_playlist = None
        self.bot.data = {'app_context': self}

        os.makedirs("download", exist_ok=True)

    async def initialize_services(self):
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

async def main():
    try:
        logger.info("🤖 Starting bot...")
        await cleanup_downloads()
        
        context = AppContext()
        
        logger.debug("🛠 Initializing services...")
        await context.initialize_services()
        
        logger.info("🔌 Loading plugins...")
        await load_plugins(context)
        
        context.dp.message.middleware(CommandLoggerMiddleware())
        logger.info("🚀 Starting polling...")
        await context.dp.start_polling(context.bot)

        logger.info("🛑 Polling stopped")

    except Exception as e:
        logger.critical(f"💥 Fatal error: {str(e)}", exc_info=True)
    finally:
        logger.info("🔴 Bot stopped")
            
        await cleanup_downloads()

if __name__ == "__main__":
    os.environ["PYTHONUNBUFFERED"] = "1"
    
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🛑 Stopped by user")
    except Exception as e:
        logger.critical(f"💥 Unhandled exception: {str(e)}", exc_info=True)