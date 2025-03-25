from aiogram import types
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram import F
import platform
import psutil
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

def setup(context):
    dp = context.dp
    sudo_users = context.sudo_users
    creator_id = context.creator_id

    @dp.callback_query(F.data == "alive_cmd")
    async def alive_cmd_callback(callback: types.CallbackQuery):
        user_id = callback.from_user.id
        if user_id not in sudo_users and user_id != creator_id:
            await callback.answer("❌ Bu əmri icra etmək üçün yetkiniz yoxdur.", show_alert=True)
            return

        # Sistem məlumatlarını toplayırıq
        system = platform.system()
        node = platform.node()
        release = platform.release()
        version = platform.version()
        machine = platform.machine()
        processor = platform.processor()
        cpu_count = psutil.cpu_count(logical=True)
        cpu_usage = psutil.cpu_percent(interval=1)
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        boot_time = datetime.fromtimestamp(psutil.boot_time()).strftime("%Y-%m-%d %H:%M:%S")
        uptime = datetime.now() - datetime.fromtimestamp(psutil.boot_time())

        # Neofetch kimi formatlaşdırırıq
        response = [
            "`🖥️ Sistem Məlumatları`",
            f"`• Sistem`: {system} {release}",
            f"`• Kernel`: {version}",
            f"`• Hostname`: {node}",
            f"`• Maşın`: {machine}",
            f"`• Prosessor`: {processor}",
            f"`• CPU`: {cpu_count} cores, {cpu_usage}% istifadə",
            f"`• RAM`: {memory.used // 1024 // 1024}MB / {memory.total // 1024 // 1024}MB",
            f"`• Disk`: {disk.used // 1024 // 1024}MB / {disk.total // 1024 // 1024}MB",
            f"`• Boot Time`: {boot_time}",
            f"`• Uptime`: {uptime}"
        ]

        # Düymələri yaradırıq
        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="🔙 Command menyusu", callback_data="back_to_command")  # Command menyusuna qayıt
        keyboard.button(text="❌ Close", callback_data="close_window")  # Ümumi Close düyməsi
        keyboard.adjust(2)  # Düymələri 2 sütuna düz

        # Köhnə mesajı yenisi ilə əvəz edirik
        await callback.message.edit_text("\n".join(response), reply_markup=keyboard.as_markup())
        await callback.answer()