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
        swap = psutil.swap_memory()
        disk = psutil.disk_usage('/')
        boot_time = datetime.fromtimestamp(psutil.boot_time()).strftime("%Y-%m-%d %H:%M:%S")
        uptime = datetime.now() - datetime.fromtimestamp(psutil.boot_time())

        # HTML formatında məlumatları hazırlayırıq
        response = [
            "<b>🖥️ Sistem Məlumatları</b>",
            f"<b>• Sistem:</b> <code>{system} {release}</code>",
            f"<b>• Kernel:</b> <code>{version}</code>",
            f"<b>• Hostname:</b> <code>{node}</code>",
            f"<b>• Maşın:</b> <code>{machine}</code>",
            f"<b>• Prosessor:</b> <code>{processor}</code>",
            f"<b>• CPU:</b> <code>{cpu_count} cores, {cpu_usage}% istifadə</code>",
            f"<b>• RAM:</b> <code>{memory.used // 1024 // 1024}MB / {memory.total // 1024 // 1024}MB ({memory.percent}%)</code>",
            f"<b>• Swap:</b> <code>{swap.used // 1024 // 1024}MB / {swap.total // 1024 // 1024}MB ({swap.percent}%)</code>",
            f"<b>• Disk:</b> <code>{disk.used // 1024 // 1024}MB / {disk.total // 1024 // 1024}MB ({disk.percent}%)</code>",
            f"<b>• Boot Time:</b> <code>{boot_time}</code>",
            f"<b>• Uptime:</b> <code>{uptime}</code>"
        ]

        # Düymələri yaradırıq
        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="🔙 Command menyusu", callback_data="back_to_command")
        keyboard.button(text="❌ Close", callback_data="close_window")
        keyboard.adjust(2)

        # Köhnə mesajı yenisi ilə əvəz edirik (HTML parse_mode ilə)
        await callback.message.edit_text("\n".join(response), reply_markup=keyboard.as_markup(), parse_mode="HTML")
        await callback.answer()
