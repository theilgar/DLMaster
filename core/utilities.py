import re
import os
import logging
import importlib
import asyncio
from pathlib import Path
from aiogram import BaseMiddleware
from aiogram.types import Message
from typing import Callable, Dict, Any

logger = logging.getLogger(__name__)

# core/utilities.py -> layihənin kökü (plugins/ və download/ burada yerləşir)
BASE_DIR = Path(__file__).resolve().parent.parent
DOWNLOAD_DIR = "download"

# Fayl adında qadağan olunmuş simvollar + nəzarət simvolları + '%'
# ('%' yt-dlp-nin outtmpl şablonunu poza bilir)
_INVALID_CHARS = re.compile(r'[\\/*?:"<>|%\x00-\x1f]')


def sanitize_filename(filename: str, max_bytes: int = 120) -> str:
    name = _INVALID_CHARS.sub("", filename or "")
    name = re.sub(r"\s+", " ", name).strip(" .")
    # Fayl sisteminin limiti simvol yox, bayta görədir (kirill/ərəb/emoji uzun olur)
    while len(name.encode("utf-8")) > max_bytes:
        name = name[:-1]
    return name.strip(" .") or "track"


def format_duration(seconds, is_ms: bool = False) -> str:
    try:
        total = int(seconds or 0)
        if is_ms:
            total //= 1000
        total = max(total, 0)

        minutes, secs = divmod(total, 60)
        hours, minutes = divmod(minutes, 60)

        if hours > 0:
            return f"{hours:02d}:{minutes:02d}:{secs:02d}"
        return f"{minutes:02d}:{secs:02d}"
    except (TypeError, ValueError) as e:
        logger.error(f"Vaxt formatlama xətası: {e}")
        return "00:00"


async def load_plugins(context):
    plugins_dir = BASE_DIR / "plugins"
    if not plugins_dir.is_dir():
        plugins_dir = Path("plugins")
    logger.info(f"🔍 Scanning plugins in: {plugins_dir}")

    # Hər plugin-in yüklənmə nəticəsi (update_notifier bunu creator-a göndərir)
    status = {}
    context.plugin_status = status

    # 1) Hamısını import et
    modules = []
    for path in sorted(plugins_dir.glob("*.py")):
        if path.name.startswith("_"):
            continue
        module_name = f"plugins.{path.stem}"
        logger.info(f"🔄 Loading: {module_name}")
        try:
            modules.append(importlib.import_module(module_name))
        except Exception as e:
            status[path.name] = f"import xətası: {type(e).__name__}: {e}"
            logger.error(f"❌ Failed to import {module_name}: {e}", exc_info=True)

    # 2) PRIORITY-yə görə sırala (kiçik = əvvəl). Plugin-də PRIORITY yoxdursa 50.
    #    Ümumi ("catch-all") handler-i olan plugin PRIORITY yüksək qoyub sona keçir,
    #    beləcə digər plugin-lərin (məs. audio editor) handler-lərini kəsmir.
    modules.sort(key=lambda m: getattr(m, "PRIORITY", 50))

    # 3) setup() çağır
    for module in modules:
        name = module.__name__
        if not hasattr(module, "setup"):
            logger.warning(f"⚠️ No setup() in: {name}")
            continue
        try:
            if asyncio.iscoroutinefunction(module.setup):
                await module.setup(context)
            else:
                module.setup(context)
            status[f"{name.split('.')[-1]}.py"] = "ok"
            logger.info(f"✅ Loaded: {name}")
        except Exception as e:
            status[f"{name.split('.')[-1]}.py"] = f"setup xətası: {type(e).__name__}: {e}"
            logger.error(f"❌ Failed to load {name}: {e}", exc_info=True)


async def cleanup_downloads():
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)  # ilk işə salınmada qovluq yoxdursa çökməsin
    for filename in os.listdir(DOWNLOAD_DIR):
        file_path = os.path.join(DOWNLOAD_DIR, filename)
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
        if event.text and event.text.startswith("/") and event.from_user:
            logger.info(f"User {event.from_user.id} executed command: {event.text}")
        return await handler(event, data)
