from aiogram import types, F
from aiogram.filters import Command
from aiogram.exceptions import TelegramBadRequest
import logging

logger = logging.getLogger(__name__)

def setup(context):
    dp = context.dp
    creator_id = context.creator_id  # Bot owner's ID

    @dp.message(Command("del"))
    async def delete_message(message: types.Message):
        """
        /del command to delete messages by replying to them.
        Works in both private chats and groups (if bot has admin rights).
        """
        # Only the bot owner can use this command
        if message.from_user.id != creator_id:
            await message.answer("❌ Bu əmri yalnız botun yaradıcısı istifadə edə bilər.")
            return

        # Check if the command is a reply to a message
        if not message.reply_to_message:
            await message.answer("❌ Silmək üçün bir mesaja yanıt verin.")
            return

        try:
            # Delete the replied-to message
            await message.bot.delete_message(
                chat_id=message.chat.id,
                message_id=message.reply_to_message.message_id
            )
            # Try to delete the /del command message itself
            try:
                await message.delete()
            except Exception as e:
                logger.warning(f"Could not delete command message: {str(e)}")
                
        except TelegramBadRequest as e:
            error_message = str(e)
            if "message to delete not found" in error_message:
                await message.answer("❌ Mesaj artıq silinib və ya tapılmır.")
            elif "not enough rights" in error_message:
                await message.answer("❌ Bu mesajı silmək üçün yetəri qədər hüququm yoxdur.")
            else:
                await message.answer("❌ Mesaj silinərkən xəta baş verdi.")
            logger.error(f"Message deletion failed: {error_message}")
        except Exception as e:
            await message.answer("❌ Mesaj silinərkən xəta baş verdi.")
            logger.error(f"Message deletion error: {str(e)}")