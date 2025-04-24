import os
import sys
import colorlog
import logging
import datetime
from logging.handlers import RotatingFileHandler
from aiogram import BaseMiddleware
from aiogram.types import Message
from typing import Callable, Dict, Any

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