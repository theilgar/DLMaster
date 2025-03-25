from aiogram import types
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram import F
from speedtest import Speedtest  # Correct import statement
import logging

logger = logging.getLogger(__name__)

def setup(context):
    dp = context.dp
    sudo_users = context.sudo_users
    creator_id = context.creator_id

    @dp.callback_query(F.data == "speedtest_cmd")
    async def speedtest_cmd_callback(callback: types.CallbackQuery):
        user_id = callback.from_user.id
        if user_id not in sudo_users and user_id != creator_id:
            await callback.answer("❌ Bu əmri icra etmək üçün yetkiniz yoxdur.", show_alert=True)
            return

        # Speedtest başladığını bildirən mesaj
        await callback.message.edit_text("🚀 Şəbəkə sürəti yoxlanılır...")

        # Speedtest edirik
        st = Speedtest()  # Use the Speedtest class
        best_server = st.get_best_server()  # Ən yaxşı serveri seçirik və məlumatları saxlayırıq
        download_speed = st.download() / 1_000_000  # Mbps-ə çeviririk
        upload_speed = st.upload() / 1_000_000  # Mbps-ə çeviririk
        ping = st.results.ping  # Ping dəyəri

        # Seçilən server haqqında məlumat
        server_info = (
            f"• 🌍 **Server:** {best_server['name']} ({best_server['country']})\n"
            f"• 📍 **Sponsor:** {best_server['sponsor']}\n"
            f"• 📏 **Uzaqlıq:** {best_server['d']:.2f} km"
        )

        # Nəticəni formatlaşdırırıq
        response = [
            "🚀 **Speedtest Nəticələri:**",
            f"• ⬇️ **Download Speed:** {download_speed:.2f} Mbps",
            f"• ⬆️ **Upload Speed:** {upload_speed:.2f} Mbps",
            f"• 🏓 **Ping:** {ping:.2f} ms",
            "\n**Seçilən Server:**",
            server_info
        ]

        # Düymələri yaradırıq
        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="🔙 Command menyusu", callback_data="back_to_command")  # Command menyusuna qayıt
        keyboard.button(text="🔄 Reboot Bot", callback_data="reboot_bot")  # Reboot Bot düyməsi
        keyboard.button(text="❌ Close", callback_data="close_window")  # Ümumi Close düyməsi
        keyboard.adjust(2)  # Düymələri 2 sütuna düz

        # Köhnə mesajı yenisi ilə əvəz edirik
        await callback.message.edit_text("\n".join(response), reply_markup=keyboard.as_markup())
        await callback.answer()