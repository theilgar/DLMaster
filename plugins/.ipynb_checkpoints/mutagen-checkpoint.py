# plugins/mutagen_plugin.py
import os
import logging
from pathlib import Path
from aiogram import types, F, Router
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.types.input_file import BufferedInputFile
from aiogram.utils.chat_action import ChatActionSender

logger = logging.getLogger(__name__)
router = Router()

# User states üçün temporary storage
temp_data = {}

async def setup(context):
    context.main_router.include_router(router)
    logger.info("✅ Mutagen plugin yükləndi")

def get_edit_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✏️ Adı dəyiş", callback_data="edit_title"),
            InlineKeyboardButton(text="🎤 Artist dəyiş", callback_data="edit_artist")
        ],
        [
            InlineKeyboardButton(text="🖼️ Thumbnail dəyiş", callback_data="edit_thumbnail"),
            InlineKeyboardButton(text="✅ Təsdiqlə", callback_data="confirm_edit")
        ]
    ])

async def process_audio(message: types.Message, audio: types.Audio):
    user_id = message.from_user.id
    temp_data[user_id] = {
        'file_id': audio.file_id,
        'title': audio.title or "Naməlum Mahnı",
        'performer': audio.performer or "Naməlum Artist",
        'thumbnail': None
    }
    
    await message.answer(
        f"🔧 Düzəliş edin:\n"
        f"📀 Mahnı: {temp_data[user_id]['title']}\n"
        f"🎤 Artist: {temp_data[user_id]['performer']}",
        reply_markup=get_edit_keyboard()
    )

@router.message(Command("mutagen"))
async def mutagen_command(message: types.Message):
    if message.reply_to_message and message.reply_to_message.audio:
        await process_audio(message, message.reply_to_message.audio)
    else:
        await message.answer("🎵 Zəhmət olmasa düzəliş etmək istədiyiniz mahnıya reply edin və ya mahnı göndərin:")
        temp_data[message.from_user.id] = {'awaiting_audio': True}

@router.message(F.audio)
async def handle_audio_input(message: types.Message):
    user_id = message.from_user.id
    if user_id in temp_data and temp_data[user_id].get('awaiting_audio'):
        await process_audio(message, message.audio)
        temp_data[user_id].pop('awaiting_audio', None)

@router.callback_query(F.data.startswith("edit_"))
async def edit_callback_handler(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    action = callback.data.split("_")[1]
    
    if action == "title":
        await callback.message.answer("Yeni mahnı adını yazın:")
        temp_data[user_id]['editing'] = 'title'
    elif action == "artist":
        await callback.message.answer("Yeni artist adını yazın:")
        temp_data[user_id]['editing'] = 'artist'
    elif action == "thumbnail":
        await callback.message.answer("Yeni thumbnail şəkli göndərin:")
        temp_data[user_id]['editing'] = 'thumbnail'
    
    await callback.answer()

@router.message(F.text)
async def handle_text_edit(message: types.Message):
    user_id = message.from_user.id
    if user_id in temp_data and 'editing' in temp_data[user_id]:
        edit_type = temp_data[user_id]['editing']
        if edit_type in ['title', 'artist']:
            temp_data[user_id][edit_type] = message.text
            await message.answer(f"✅ {edit_type.capitalize()} yeniləndi!")
            await show_edit_menu(message)

async def show_edit_menu(message: types.Message):
    user_id = message.from_user.id
    if user_id in temp_data:
        await message.answer(
            f"🔧 Cari dəyərlər:\n"
            f"📀 Mahnı: {temp_data[user_id]['title']}\n"
            f"🎤 Artist: {temp_data[user_id]['performer']}",
            reply_markup=get_edit_keyboard()
        )

@router.message(F.photo)
async def handle_photo_edit(message: types.Message):
    user_id = message.from_user.id
    if user_id in temp_data and temp_data[user_id].get('editing') == 'thumbnail':
        # Thumbnail-i yüklə və saxla
        photo = message.photo[-1]
        file = await message.bot.get_file(photo.file_id)
        temp_data[user_id]['thumbnail'] = await message.bot.download_file(file.file_path)
        await message.answer("✅ Thumbnail yeniləndi!")
        await show_edit_menu(message)

@router.callback_query(F.data == "confirm_edit")
async def confirm_edit(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if user_id not in temp_data:
        return await callback.answer("❌ Session zaman aşımı!")

    async with ChatActionSender.upload_audio(bot=callback.bot, chat_id=callback.message.chat.id):
        try:
            # Original audio faylını yüklə
            file = await callback.bot.get_file(temp_data[user_id]['file_id'])
            audio_data = await callback.bot.download_file(file.file_path)
            
            # Yeni metadata ilə yüklə
            edited_audio = BufferedInputFile(
                audio_data.read(),
                filename=f"{temp_data[user_id]['title']}.mp3"
            )
            
            # Thumbnail-i tətbiq et
            thumbnail = None
            if temp_data[user_id]['thumbnail']:
                thumbnail = BufferedInputFile(
                    temp_data[user_id]['thumbnail'].read(),
                    filename="thumbnail.jpg"
                )

            await callback.message.answer_audio(
                audio=edited_audio,
                title=temp_data[user_id]['title'][:64],
                performer=temp_data[user_id]['performer'][:64],
                thumb=thumbnail
            )
            
            await callback.message.delete()
            await callback.answer("✅ Mahnı uğurla redaktə edildi!")
            
        except Exception as e:
            logger.error(f"Audio redaktə xətası: {e}")
            await callback.answer("❌ Xəta baş verdi!", show_alert=True)
        finally:
            temp_data.pop(user_id, None)