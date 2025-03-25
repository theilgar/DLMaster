from aiogram import types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramBadRequest
import logging

logger = logging.getLogger(__name__)

def setup(context):
    dp = context.dp
    creator_id = context.creator_id  # Bot owner's ID

    @dp.message(Command("broadcast"))
    async def broadcast_cmd(message: types.Message):
        """
        /broadcast command to send a message to all users and groups.
        """
        # Only the bot owner can use this command
        if message.from_user.id != creator_id:
            await message.answer("❌ Bu əmri yalnız botun yaradıcısı istifadə edə bilər.")
            return

        # Check if the command has arguments
        if not message.text or len(message.text.split()) < 2:
            await message.answer("❌ İstifadə: /broadcast <mesaj>")
            return

        # Prepare the broadcast message
        broadcast_text = " ".join(message.text.split()[1:])
        confirm_keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Təsdiqlə", callback_data=f"confirm_broadcast:{broadcast_text}")],
            [InlineKeyboardButton(text="❌ Ləğv et", callback_data="cancel_broadcast")]
        ])

        await message.answer(
            f"📢 Göndəriləcək mesaj:\n\n{broadcast_text}\n\nBu mesajı bütün istifadəçilərə və qruplara göndərmək istəyirsiniz?",
            reply_markup=confirm_keyboard
        )

    @dp.callback_query(F.data.startswith("confirm_broadcast:"))
    async def handle_confirm_broadcast(callback: types.CallbackQuery):
        """
        Handle the confirmation of the broadcast.
        """
        broadcast_text = callback.data.split(":", 1)[1]
        sent_count = 0
        failed_count = 0

        # Send to users
        users = context.db.get_all_users()
        for user in users:
            user_id = user["user_id"]
            try:
                await callback.message.bot.send_message(user_id, broadcast_text)
                sent_count += 1
            except TelegramBadRequest:
                failed_count += 1
            except Exception as e:
                logger.error(f"İstifadəçiyə mesaj göndərilərkən xəta: {str(e)}")
                failed_count += 1

        # Send to groups
        groups = context.db.get_all_groups()
        for group in groups:
            group_id = group["group_id"]
            try:
                await callback.message.bot.send_message(group_id, broadcast_text)
                sent_count += 1
            except TelegramBadRequest:
                failed_count += 1
            except Exception as e:
                logger.error(f"Qrupa mesaj göndərilərkən xəta: {str(e)}")
                failed_count += 1

        # Notify the bot owner about the results
        await callback.message.edit_text(
            f"✅ Mesaj uğurla göndərildi!\n\n"
            f"📊 Statistikalar:\n"
            f"• Göndərilən: {sent_count}\n"
            f"• Uğursuz: {failed_count}\n\n"
            f"• İstifadəçi: {len(users)}\n"
            f"• Qrup: {len(groups)}\n"
            f"• Ümumi istifadəçi/qrup: {len(users) + len(groups)}"
        )

    @dp.callback_query(F.data == "cancel_broadcast")
    async def handle_cancel_broadcast(callback: types.CallbackQuery):
        """
        Cancel the broadcast.
        """
        await callback.message.edit_text("❌ Broadcast ləğv edildi.")
