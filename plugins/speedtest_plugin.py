from aiogram import types
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram import F
from speedtest import Speedtest
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

        # Sadə yükləmə mesajı
        await callback.message.edit_text("🚀 Şəbəkə sürəti yoxlanılır...")

        try:
            # Speedtest prosesi
            st = Speedtest()
            best_server = st.get_best_server()
            download_speed = st.download() / 1_000_000
            upload_speed = st.upload() / 1_000_000
            ping = st.results.ping

            # Nəticələrin formatlanması
            server_info = (
                f"• 🌍 <b>Server:</b> {best_server['name']} ({best_server['country']})\n"
                f"• 📍 <b>Sponsor:</b> {best_server['sponsor']}\n"
                f"• 📏 <b>Uzaqlıq:</b> {best_server['d']:.2f} km"
            )

            response = [
                "<b>🚀 Speedtest Nəticələri:</b>",
                f"• ⬇️ <b>Download Speed:</b> <code>{download_speed:.2f}</code> Mbps",
                f"• ⬆️ <b>Upload Speed:</b> <code>{upload_speed:.2f}</code> Mbps",
                f"• 🏓 <b>Ping:</b> <code>{ping:.2f}</code> ms",
                "\n<b>Seçilən Server:</b>",
                server_info
            ]

            # Düymələr
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="🔙 Command menyusu", callback_data="back_to_command")
            keyboard.button(text="❌ Close", callback_data="close_window")
            keyboard.adjust(2)

            await callback.message.edit_text(
                "\n".join(response),
                reply_markup=keyboard.as_markup(),
                parse_mode="HTML"
            )
            
        except Exception as e:
            logger.error(f"Speedtest xətası: {e}")
            await callback.message.edit_text(
                "❌ Şəbəkə sürəti yoxlanılarkən xəta baş verdi.",
                parse_mode="HTML"
            )