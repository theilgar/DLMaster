from aiogram import types
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram import F
import speedtest
import logging

logger = logging.getLogger(__name__)

# Bütün pluginlərdə:
def setup(context):
    dp = context.dp
    db = context.db  # Əlavə edilir
    sudo_users = db.get_sudo_users()  # Köhnə context.sudo_users əvəzinə
    creator_id = context.creator_id

    @dp.callback_query(F.data == "speedtest_cmd")
    async def speedtest_cmd_callback(callback: types.CallbackQuery):
        user_id = callback.from_user.id
        if user_id not in sudo_users and user_id != creator_id:
            await callback.answer("❌ Bu əmri icra etmək üçün yetkiniz yoxdur.", show_alert=True)
            return

        # Speedtest başladığını bildirən mesaj
        await callback.message.edit_text("<i>🚀 Şəbəkə sürəti yoxlanılır...</i>", parse_mode="HTML")

        # Speedtest edirik
        st = speedtest.Speedtest()
        best_server = st.get_best_server()  # Ən yaxşı serveri seçirik və məlumatları saxlayırıq
        download_speed = st.download() / 1_000_000  # Mbps-ə çeviririk
        upload_speed = st.upload() / 1_000_000  # Mbps-ə çeviririk
        ping = st.results.ping  # Ping dəyəri

        # Seçilən server haqqında məlumat
        server_info = (
            f"• <b>🌍 Server:</b> <code>{best_server['name']} ({best_server['country']})</code>\n"
            f"• <b>📍 Sponsor:</b> <code>{best_server['sponsor']}</code>\n"
            f"• <b>📏 Uzaqlıq:</b> <code>{best_server['d']:.2f} km</code>"
        )

        # Nəticəni HTML formatında formatlaşdırırıq
        response = [
            "<b>🚀 Speedtest Nəticələri:</b>",
            f"• <b>⬇️ Download Speed:</b> <code>{download_speed:.2f} Mbps</code>",
            f"• <b>⬆️ Upload Speed:</b> <code>{upload_speed:.2f} Mbps</code>",
            f"• <b>🏓 Ping:</b> <code>{ping:.2f} ms</code>",
            "\n<b>Seçilən Server:</b>",
            server_info
        ]

        # Düymələri yaradırıq
        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="🔙 Command menyusu", callback_data="back_to_command")  # Command menyusuna qayıt
        keyboard.button(text="❌ Close", callback_data="close_window")  # Ümumi Close düyməsi
        keyboard.adjust(2)  # Düymələri 2 sütuna düz

        # Köhnə mesajı yenisi ilə əvəz edirik (HTML formatında)
        await callback.message.edit_text("\n".join(response), parse_mode="HTML", reply_markup=keyboard.as_markup())
        await callback.answer()