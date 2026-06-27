import re
import os
import logging
import importlib
import asyncio
from aiogram import BaseMiddleware
from aiogram.types import Message
from typing import Callable, Dict, Any

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

async def load_plugins(context):
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

class CommandLoggerMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable,
        event: Message,
        data: Dict[str, Any]
    ) -> Any:
        if event.text and event.text.startswith("/"):
            logger.info(f"User {event.from_user.id} executed command: {event.text}")
        return await handler(event, data)
