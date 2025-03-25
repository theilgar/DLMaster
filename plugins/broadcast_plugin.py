from aiogram import types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramBadRequest
import logging
from hashlib import sha256

logger = logging.getLogger(__name__)

def setup(context):
    dp = context.dp
    creator_id = context.creator_id  # Bot owner's ID

    # Temporary storage for broadcast messages
    context.temp_broadcast_data = {}

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

        # Generate a unique hash for the broadcast text
        broadcast_hash = sha256(broadcast_text.encode()).hexdigest()[:10]  # Use first 10 characters of the hash

        # Store the broadcast text in a temporary dictionary
        context.temp_broadcast_data[broadcast_hash] = broadcast_text

        # Create the confirmation keyboard
        confirm_keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Təsdiqlə", callback_data=f"confirm_broadcast:{broadcast_hash}")],
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
        broadcast_hash = callback.data.split(":", 1)[1]

        # Retrieve the broadcast text from the temporary dictionary
        broadcast_text = context.temp_broadcast_data.get(broadcast_hash)
        if not broadcast_text:
            await callback.answer("❌ Mesaj tapılmadı.")
            return

        sent_count = 0
        failed_count = 0

        # Send to users
        users = context.db.get_all_users()
        for user in users:
            user_id = user["user_id"]
            try:
                await callback.message.bot.send_message(user_id, broadcast_text)
                sent_count += 1
            except TelegramBadRequest as e:
                logger.error(f"TelegramBadRequest for user {user_id}: {str(e)}")
                failed_count += 1
            except Exception as e:
                logger.error(f"Error sending message to user {user_id}: {str(e)}")
                failed_count += 1

        # Send to groups
        groups = context.db.get_all_groups()
        for group in groups:
            group_id = group["group_id"]
            try:
                await callback.message.bot.send_message(group_id, broadcast_text)
                sent_count += 1
            except TelegramBadRequest as e:
                logger.error(f"TelegramBadRequest for group {group_id}: {str(e)}")
                failed_count += 1
            except Exception as e:
                logger.error(f"Error sending message to group {group_id}: {str(e)}")
                failed_count += 1

        # Prepare the stats message
        stats_message = (
            f"✅ Mesaj uğurla göndərildi!\n\n"
            f"📊 Statistikalar:\n"
            f"• Göndərilən: {sent_count}\n"
            f"• Uğursuz: {failed_count}\n\n"
            f"• İstifadəçi: {len(users)}\n"
            f"• Qrup: {len(groups)}\n"
            f"• Ümumi istifadəçi/qrup: {len(users) + len(groups)}"
        )

        # Log the stats message for debugging
        logger.info(f"Stats message: {stats_message}")

        # Send the stats message
        try:
            await callback.message.edit_text(stats_message, parse_mode=None)
        except TelegramBadRequest as e:
            logger.error(f"Failed to edit message: {str(e)}")
            await callback.message.answer("❌ Mesaj göndərilərkən xəta baş verdi.")

    @dp.callback_query(F.data == "cancel_broadcast")
    async def handle_cancel_broadcast(callback: types.CallbackQuery):
        """
        Cancel the broadcast.
        """
        await callback.message.edit_text("❌ Broadcast ləğv edildi.")