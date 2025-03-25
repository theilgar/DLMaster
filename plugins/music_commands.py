from aiogram import types, F
from aiogram.filters import Command, CommandObject
from aiogram.types import BufferedInputFile, InlineKeyboardMarkup, InlineKeyboardButton
from handlers.utilities import YoutubeManager, sanitize_filename
import asyncio
import os
import re

def setup(context):
    dp = context.dp
    user_searches = context.user_searches

    # Downloads qovluğunu yoxlamaq və yaratmaq
    downloads_dir = "downloads"
    if not os.path.exists(downloads_dir):
        os.makedirs(downloads_dir)
        print(f"'{downloads_dir}' qovluğu yaradıldı.")

    # YoutubeManager sinfini işə sal
    youtube_manager = YoutubeManager(browser="firefox")

    # URL tapmaq üçün regex
    url_pattern = re.compile(r"https?://[^\s]+")

    @dp.message(Command("music"))
    async def music_cmd(message: types.Message, command: CommandObject):
        await handle_music_search(message, command.args)

    @dp.message(F.text & ~F.text.startswith('/') & F.chat.type == "private")  
    async def handle_text_message(message: types.Message):
        # Mesajın link olub-olmadığını yoxla
        if url_pattern.search(message.text):
            await message.answer("⚠️ Şəxsi söhbətlərdə linklər üçün axtarış edilmir.")
            return

        await handle_music_search(message, message.text)

    async def handle_music_search(message: types.Message, search_query: str):
        try:
            if not search_query:
                usage_msg = await message.answer(
                    "🎵 Musiqi axtarışı üçün istifadə qaydası:\n"
                    "👉 /music <mahnı adı>\n"
                    "Məsələn: /music Imagine Dragons Believer"
                )
                await asyncio.sleep(5)
                await usage_msg.delete()
                return
            
            search_msg = await message.answer("🔍 Axtarış edirəm...")
            results = await youtube_manager.youtube_search(search_query)
            
            if not results:
                raise ValueError("Nəticə tapılmadı")
            
            response = ["🎵 Tapılan Mahnılar:"] + [
                f"{i+1}. {res['title']} ({res['duration']})" 
                for i, res in enumerate(results[:5])
            ]
            
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=f"{i+1}", callback_data=f"choice_{i}") for i in range(len(results[:5]))],
                [InlineKeyboardButton(text="❌ Cancel", callback_data="cancel")],
                [InlineKeyboardButton(text="⬇️ Download All", callback_data="download_all")]
            ])
            
            await search_msg.edit_text(
                "\n".join(response) + "\n\n👉 Aşağıdakı düymələrdən birini seçin:",
                reply_markup=keyboard
            )
            
            user_searches[message.from_user.id] = {
                'results': results,
                'search_message_id': search_msg.message_id,
                'original_message_id': message.message_id
            }
            
        except Exception as e:
            error_msg = await message.answer(f"❌ Xəta: {str(e)}")
            await asyncio.sleep(5)
            await error_msg.delete()

    @dp.callback_query(F.data.startswith("choice_"))
    async def handle_choice(callback: types.CallbackQuery):
        user_id = callback.from_user.id
        if user_id not in user_searches:
            return

        user_data = user_searches[user_id]
        file_path = None
        
        try:
            choice = int(callback.data.split("_")[1])
            selected = user_data['results'][choice]
            
            await callback.message.bot.edit_message_text(
                chat_id=callback.message.chat.id,
                message_id=user_data['search_message_id'],
                text=f"⏳ Mahnı yüklənilir: {selected['title']} ({selected['duration']})"
            )
            
            file_path = await youtube_manager.download_track(
                selected['url'], 
                sanitize_filename(selected['title'])
            )
            
            with open(file_path, 'rb') as f:
                await callback.message.answer_audio(
                    audio=BufferedInputFile(f.read(), filename=os.path.basename(file_path)),
                    title=selected.get('title', '')[:64],
                    performer="YouTube",
                    duration=int(selected.get('raw_duration', 0)))
            
            await callback.message.bot.delete_message(callback.message.chat.id, user_data['search_message_id'])
            await callback.message.bot.delete_message(callback.message.chat.id, user_data['original_message_id'])
            
        except (IndexError, KeyError):
            await callback.message.answer("❌ Yanlış seçim!")
        except Exception as e:
            await callback.message.answer(f"❌ Xəta: {str(e)}")
        finally:
            if file_path and os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except:
                    pass
            user_searches.pop(user_id, None)

    @dp.callback_query(F.data == "cancel")
    async def handle_cancel(callback: types.CallbackQuery):
        user_id = callback.from_user.id
        if user_id not in user_searches:
            return

        user_data = user_searches[user_id]
        
        cancel_msg = await callback.message.answer("❌ Axtarış ləğv edildi.")
        
        await callback.message.bot.delete_message(callback.message.chat.id, user_data['search_message_id'])
        await callback.message.bot.delete_message(callback.message.chat.id, user_data['original_message_id'])
        
        await asyncio.sleep(5)
        await cancel_msg.delete()
        
        user_searches.pop(user_id, None)

    @dp.callback_query(F.data == "download_all")
    async def handle_download_all(callback: types.CallbackQuery):
        user_id = callback.from_user.id
        if user_id not in user_searches:
            return

        user_data = user_searches[user_id]
        
        try:
            for result in user_data['results'][:5]:  
                file_path = await youtube_manager.download_track(
                    result['url'], 
                    sanitize_filename(result['title'])
                )
                
                with open(file_path, 'rb') as f:
                    await callback.message.answer_audio(
                        audio=BufferedInputFile(f.read(), filename=os.path.basename(file_path)),
                        title=result.get('title', '')[:64],
                        performer="YouTube",
                        duration=int(result.get('raw_duration', 0)))
                
                if os.path.exists(file_path):
                    try:
                        os.remove(file_path)
                    except:
                        pass
            
            await callback.message.answer("✅ Bütün mahnılar uğurla yükləndi!")
        
        except Exception as e:
            await callback.message.answer(f"❌ Xəta: {str(e)}")
        finally:
            await callback.message.bot.delete_message(callback.message.chat.id, user_data['search_message_id'])
            await callback.message.bot.delete_message(callback.message.chat.id, user_data['original_message_id'])
            user_searches.pop(user_id, None),