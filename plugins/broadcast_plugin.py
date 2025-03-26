from aiogram import types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramBadRequest
import logging
from hashlib import sha256
import re

logger = logging.getLogger(__name__)

def setup(context):
    dp = context.dp
    creator_id = context.creator_id  # Bot owner's ID

    # Temporary storage for broadcast messages and media
    context.temp_broadcast_data = {}

    @dp.message(Command("broadcast"))
    async def broadcast_cmd(message: types.Message):
        """
        /broadcast command to send messages/media to:
        - All users/groups (default)
        - Specific user/group IDs when provided
        """
        # Only the bot owner can use this command
        if message.from_user.id != creator_id:
            await message.answer("❌ Bu əmri yalnız botun yaradıcısı istifadə edə bilər.")
            return

        # Show usage if no content provided
        if not message.text and not (message.photo or message.video or message.document):
            await message.answer(
                "📌 İstifadə qaydası:\n"
                "• Ümumi broadcast: /broadcast <mesaj> və ya media\n"
                "• Xüsusi ID-lərə: /broadcast <ID1,ID2,...> <mesaj> və ya media\n"
                "Nümunələr:\n"
                "• /broadcast Salam hamıya!\n"
                "• /broadcast 123456789 Salam fərdi istifadəçiyə\n"
                "• /broadcast -100123456789,-100987654321 Qruplara mesaj\n"
                "• /broadcast 123456789 (media ilə birlikdə)"
            )
            return

        # Parse command arguments
        args = message.text.split() if message.text else []
        potential_ids = args[1] if len(args) > 1 else None

        # Check if first argument contains comma-separated IDs
        target_ids = []
        remaining_text = None
        
        if potential_ids and re.match(r'^(-?\d+)(,-?\d+)*$', potential_ids):
            target_ids = [int(id_str) for id_str in potential_ids.split(',')]
            # Get remaining text if exists
            if len(args) > 2:
                remaining_text = ' '.join(args[2:])
            elif message.caption:  # If sent with media and caption
                remaining_text = message.caption
        elif message.text and len(args) > 1:
            remaining_text = ' '.join(args[1:])
        elif message.caption:
            remaining_text = message.caption

        # Prepare broadcast content
        broadcast_content = {
            'text': remaining_text,
            'media_type': None,
            'media_id': None,
            'caption': None,
            'target_ids': target_ids if target_ids else None  # None means broadcast to all
        }

        # Handle media
        if message.photo:
            broadcast_content['media_type'] = 'photo'
            broadcast_content['media_id'] = message.photo[-1].file_id
            broadcast_content['caption'] = remaining_text
        elif message.video:
            broadcast_content['media_type'] = 'video'
            broadcast_content['media_id'] = message.video.file_id
            broadcast_content['caption'] = remaining_text
        elif message.document:
            broadcast_content['media_type'] = 'document'
            broadcast_content['media_id'] = message.document.file_id
            broadcast_content['caption'] = remaining_text

        # If no content (just IDs with no text/media)
        if not broadcast_content['text'] and not broadcast_content['media_type']:
            await message.answer("❌ Mesaj və ya media təyin edilməyib!")
            return

        # Generate a unique hash for the broadcast content
        content_str = str(broadcast_content)
        broadcast_hash = sha256(content_str.encode()).hexdigest()[:10]

        # Store the broadcast content in a temporary dictionary
        context.temp_broadcast_data[broadcast_hash] = broadcast_content

        # Create the confirmation keyboard
        confirm_keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Təsdiqlə", callback_data=f"confirm_broadcast:{broadcast_hash}")],
            [InlineKeyboardButton(text="❌ Ləğv et", callback_data="cancel_broadcast")]
        ])

        # Prepare preview message
        preview_message = "📢 Göndəriləcək mesaj:\n\n"
        
        if broadcast_content['target_ids']:
            preview_message += f"🎯 Xüsusi ID-lər: {', '.join(map(str, broadcast_content['target_ids']))}\n"
        else:
            preview_message += "🌍 Ümumi broadcast (bütün istifadəçilər və qruplar)\n"
        
        if broadcast_content['media_type']:
            preview_message += f"📎 Media növü: {broadcast_content['media_type']}\n"
            if broadcast_content['caption']:
                preview_message += f"📝 Açıqlama: {broadcast_content['caption']}\n"
        elif broadcast_content['text']:
            preview_message += f"📝 Mətn: {broadcast_content['text']}\n"
        
        preview_message += "\nBu mesajı göndərmək istəyirsiniz?"

        await message.answer(preview_message, reply_markup=confirm_keyboard)

    @dp.callback_query(F.data.startswith("confirm_broadcast:"))
    async def handle_confirm_broadcast(callback: types.CallbackQuery):
        """
        Handle the confirmation of the broadcast.
        """
        broadcast_hash = callback.data.split(":", 1)[1]

        # Retrieve the broadcast content from the temporary dictionary
        broadcast_content = context.temp_broadcast_data.get(broadcast_hash)
        if not broadcast_content:
            await callback.answer("❌ Mesaj tapılmadı.")
            return

        sent_count = 0
        failed_count = 0

        # Function to send the message based on content type
        async def send_content(target_id):
            nonlocal sent_count, failed_count
            try:
                if broadcast_content['media_type'] == 'photo':
                    await callback.message.bot.send_photo(
                        target_id,
                        photo=broadcast_content['media_id'],
                        caption=broadcast_content['caption']
                    )
                elif broadcast_content['media_type'] == 'video':
                    await callback.message.bot.send_video(
                        target_id,
                        video=broadcast_content['media_id'],
                        caption=broadcast_content['caption']
                    )
                elif broadcast_content['media_type'] == 'document':
                    await callback.message.bot.send_document(
                        target_id,
                        document=broadcast_content['media_id'],
                        caption=broadcast_content['caption']
                    )
                elif broadcast_content['text']:
                    await callback.message.bot.send_message(
                        target_id,
                        text=broadcast_content['text']
                    )
                sent_count += 1
            except TelegramBadRequest as e:
                logger.error(f"TelegramBadRequest for target {target_id}: {str(e)}")
                failed_count += 1
            except Exception as e:
                logger.error(f"Error sending message to target {target_id}: {str(e)}")
                failed_count += 1

        # Determine targets
        if broadcast_content['target_ids']:
            # Send to specific IDs only
            targets = broadcast_content['target_ids']
            target_type = "xüsusi ID-lər"
            users = []
            groups = []
        else:
            # Send to all users and groups
            users = context.db.get_all_users()
            groups = context.db.get_all_groups()
            targets = [user["user_id"] for user in users] + [group["group_id"] for group in groups]
            target_type = "ümumi istifadəçi/qruplar"

        # Send to all targets
        for target_id in targets:
            await send_content(target_id)

        # Prepare the stats message
        stats_message = (
            f"✅ Mesaj uğurla göndərildi!\n\n"
            f"📊 Statistikalar:\n"
            f"• Hədəf: {target_type}\n"
            f"• Göndərilən: {sent_count}\n"
            f"• Uğursuz: {failed_count}\n"
        )

        if not broadcast_content['target_ids']:
            stats_message += (
                f"\n• İstifadəçi: {len(users)}\n"
                f"• Qrup: {len(groups)}\n"
                f"• Ümumi istifadəçi/qrup: {len(users) + len(groups)}"
            )

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