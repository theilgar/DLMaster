import os
import sys
import asyncio
import logging
import importlib
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import ChatMemberUpdated
from aiogram.enums import ChatMemberStatus
from dotenv import load_dotenv
from collections import defaultdict
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from database import Database  # SQLite3 mexanizmasını daxil edirik
from inline_plugin import setup_inline_plugin  # Inline plugin-i import edirik

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
        self.db = Database()  # SQLite3 mexanizmasını inisializasiya edirik
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
        await self.notify_reload()

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


async def main():
    context = AppContext()
    await context.initialize_services()
    load_plugins(context)

    # Inline plugin-i işə sal
    await setup_inline_plugin(context.dp, context.youtube)

    # Handlerləri əlavə edirik
    @context.dp.message(Command("start"))
    async def start_command(message: types.Message):
        # Bot haqqında məlumat
        bot_info = (
            "**Salam! 👋 Mən DLLMasterBot**\n"
            "Youtubedən və Spotifydan playlist yükləməyi bacarıram\n\n"
            "🚀 **Əsas Komandalarım:**\n"
            "1. `<spotify playlist link>` - Spotify playlistindən mahnıları yüklə.\n"
            "2. `<youtube playlist link>` - YouTube playlistindən mahnıları yüklə.\n"
            "3. `/music <mahnı adı>` - YouTubedən mahnı yüklə.\n\n"
            "📝 Playlist linkləri düzgün olmalıdır.\n\n"
            "Bot @ilgarrx tərəfindən yaradılmışdır 🚀"
        )

        # Əgər bot yenidən başladılıbsa, bu mesajı əlavə edirik
        if hasattr(message, "rebooted"):
            bot_info += "\n\n✅ Bot uğurla yenidən başladıldı."

        # İstifadəçiyə mesaj göndər
        await message.reply(bot_info, parse_mode="Markdown")

        # İstifadəçi məlumatlarını verilənlər bazasına əlavə edirik
        user_id = message.from_user.id
        username = message.from_user.username or "Naməlum"
        context.db.add_user(user_id, username)
        context.db.increment_user_message_count(user_id)

        # Əgər komanda qrupda işlədilibsə, qrup məlumatlarını da əlavə edirik
        if message.chat.type != "private":
            group_id = message.chat.id
            group_name = message.chat.title
            context.db.add_group(group_id, group_name)
            context.db.add_group_user(group_id, user_id)

        # Loglaşdırma
        context.db.add_log(
            level="INFO",
            message=f"User {username} başladı.",
            user_id=user_id,
            group_id=message.chat.id if message.chat.type != "private" else None
        )

    @context.dp.chat_member()
    async def on_bot_added_to_group(event: ChatMemberUpdated):
        # Yalnız bot qrupa əlavə edildikdə işlə
        if event.new_chat_member.status == ChatMemberStatus.MEMBER and event.new_chat_member.user.is_bot:
            # Qrupa mesaj göndər
            welcome_message = (
                "**Salam! 👋 Mən DLLMasterBot**\n\n"
                "Youtubedən və Spotifydan playlist yükləməyi bacarıram\n\n"
                "🚀 **Əsas Komandalarım:**\n"
                "1. `<spotify playlist link>` - Spotify playlistindən mahnıları yüklə.\n"
                "2. `<youtube playlist link>` - YouTube playlistindən mahnıları yüklə.\n"
                "3. `/music <mahnı adı>` - YouTubedən mahnı yüklə.\n\n"
                "📝 Playlist linkləri düzgün olmalıdır.\n\n"
                "Qrupda işləmək üçün aşağıdakı yetkiləri verməyiniz xahiş olunur:\n"
                "1. **Mesaj silmə** yetkisi - Mesajları idarə etmək üçün.\n"
                "2. **Mesaj pinləmə** yetkisi - Mesajların istifadəçilərə asanlıqla çıxışı təmin etmək üçün.\n\n"
                "Bu yetkilər olmadan düzgün işləyə bilmərəm. Zəhmət olmasa, yetkiləri verin!"
            )

            # Qrupa mesaj göndər
            await event.answer(welcome_message, parse_mode="Markdown")

            # Qrup məlumatlarını verilənlər bazasına əlavə edirik
            group_id = event.chat.id
            group_name = event.chat.title
            context.db.add_group(group_id, group_name)
            context.db.increment_group_bot_usage(group_id)
            context.db.add_log(level="INFO", message=f"Bot {group_name} qrupuna əlavə edildi.", group_id=group_id)

    # Plugin reloading functionality
    plugins_dir = "plugins"
    loop = asyncio.get_event_loop()  # Get the main event loop
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