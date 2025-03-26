import os
import asyncio
import importlib
import logging
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from aiogram.filters import Command
from aiogram import Bot

logger = logging.getLogger(__name__)

class PluginReloadHandler(FileSystemEventHandler):
    def __init__(self, context, plugins_dir, bot: Bot, loop):
        self.context = context
        self.plugins_dir = plugins_dir
        self.bot = bot
        self.loop = loop  # Main event loop

    def on_modified(self, event):
        if event.src_path.endswith(".py") and "plugins" in event.src_path:
            logger.info(f"Fayl dəyişikliyi aşkarlandı: {event.src_path}")
            # Schedule the reload_plugins coroutine in the main event loop
            asyncio.run_coroutine_threadsafe(self.reload_plugins(), self.loop)

    async def reload_plugins(self):
        """Pluginləri yenidən yüklə və handlerləri yenilə."""
        logger.info("Pluginlər yenidən yüklənir...")

        # Köhnə handlerləri sil
        self.context.dp.message.handlers.clear()
        self.context.dp.callback_query.handlers.clear()
        self.context.dp.inline_query.handlers.clear()

        # Pluginləri yenidən yüklə
        load_plugins(self.context)

        # Adminlərə məlumat ver
        #await self.notify_reload()####################

        logger.info("Pluginlər uğurla yenidən yükləndi və handlerlər yeniləndi.")

    async def notify_reload(self):
        """Notify sudo users and the creator about the plugin reload."""
        creator_id = self.context.creator_id
        sudo_users = self.context.sudo_users

        message = "🔃 Pluginlər yenidən yükləndi!"
        try:
            await self.bot.send_message(chat_id=creator_id, text=message)
            for user_id in sudo_users:
                await self.bot.send_message(chat_id=user_id, text=message)
        except Exception as e:
            logger.error(f"Xəbərdarlıq göndərilərkən xəta baş verdi: {e}")

def load_plugins(context):
    plugins_dir = "plugins"
    logger.info(f"Plugins qovluğu tərəfə baxılır: {plugins_dir}")
    for filename in os.listdir(plugins_dir):
        if filename.endswith(".py") and filename != "__init__.py":
            module_name = f"{plugins_dir}.{filename[:-3]}"
            logger.info(f"Plugin yüklənir: {module_name}")
            try:
                module = importlib.import_module(module_name)
                if hasattr(module, "setup"):
                    logger.info(f"Pluginin setup funksiyası tapıldı: {module_name}")
                    if asyncio.iscoroutinefunction(module.setup):
                        asyncio.create_task(module.setup(context))
                    else:
                        module.setup(context)
                    logger.info(f"Plugin quruldu: {module_name}")
                else:
                    logger.warning(f"Pluginin setup funksiyası tapılmadı: {module_name}")
            except Exception as e:
                logger.error(f"Plugin yüklənərkən xəta baş verdi: {module_name}. Xəta: {e}")


def start_plugin_watcher(context, plugins_dir, bot: Bot, loop):
    """Start the plugin watcher to monitor changes in the plugins directory."""
    event_handler = PluginReloadHandler(context, plugins_dir, bot, loop)
    observer = Observer()
    observer.schedule(event_handler, path=plugins_dir, recursive=True)
    observer.start()
    logger.info(f"Pluginlər qovluğu izlənilir: {plugins_dir}")
    return observer