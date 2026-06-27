from aiogram import types, F
from aiogram.filters import Command, CommandObject
from aiogram.types import BufferedInputFile, InlineKeyboardMarkup, InlineKeyboardButton
from core.utilities import sanitize_filename
from core.youtube_handler import YoutubeManager
import asyncio
import os
import logging
import re
from datetime import datetime

logger = logging.getLogger(__name__)

def setup(context):
    dp = context.dp
    user_searches = context.user_searches

    if not hasattr(context, 'youtube_manager'):
        context.youtube_manager = YoutubeManager(browser="firefox")

    async def handle_music_search(message: types.Message, search_query: str, command_used: str = "music"):
        try:
            if not search_query:
                usage_msg = await message.answer(
                    f"<b>🎵 Musiqi axtarışı istifadə qaydası:</b>\n"
                    f"👉 <code>/music &lt;mahnı adı&gt;</code>\n"
                    f"<i>Nümunə:</i> <code>/music Eminem Lose Yourself</code>",
                    parse_mode="HTML"
                )
                await asyncio.sleep(20)
                try:
                    await usage_msg.delete()
                except Exception as e:
                    logger.error(f"İstifadə mesajı silinərkən xəta: {e}")
                return
            
            user_id = message.from_user.id

            search_msg = await message.answer("<i>🔍 Axtarılır...</i>", parse_mode="HTML")
            results = await context.youtube_manager.youtube_search(search_query)
            
            if not results:
                raise ValueError("Nəticə tapılmadı")
            
            # Clean titles in search results
            for result in results:
                result['original_title'] = result['title']
            
            total_pages = (len(results) + 4) // 5
            current_page = 0
            start = current_page * 5
            current_results = results[start:start+5]

            response = [
                f"<b>🎵 Tapılan Mahnılar (Səhifə {current_page +1}/{total_pages}):</b>",
                ""
            ]

            keyboard_rows = []
            for i, res in enumerate(current_results):
                original_index = start + i
                button_text = f"{res['title']} ({res['duration']})"[:64]
                keyboard_rows.append([InlineKeyboardButton(text=button_text, callback_data=f"choice_{original_index}")])

            pagination_buttons = []
            if total_pages > 1:
                if current_page > 0:
                    pagination_buttons.append(InlineKeyboardButton(text="◀️ Geri", callback_data=f"page_{current_page-1}"))
                if current_page < total_pages - 1:
                    pagination_buttons.append(InlineKeyboardButton(text="İrəli ▶️", callback_data=f"page_{current_page+1}"))

            if pagination_buttons:
                keyboard_rows.append(pagination_buttons)

            keyboard_rows.append([
                InlineKeyboardButton(text="❌ Ləğv et", callback_data="cancel")
            ])

            keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_rows)
            
            await search_msg.edit_text(
                "\n".join(response),
                reply_markup=keyboard,
                parse_mode="HTML",
                disable_web_page_preview=True
            )
            
            user_searches[message.from_user.id] = {
                'results': results,
                'search_message_id': search_msg.message_id,
                'original_message_id': message.message_id,
                'command_used': command_used,
                'current_page': current_page
            }
            
        except Exception as e:
            error_msg = await message.answer(
                f"❌ <b>Xəta:</b> <code>{str(e)}</code>",
                parse_mode="HTML"
            )
            await asyncio.sleep(5)
            try:
                await error_msg.delete()
            except Exception as del_err:
                logger.error(f"Xəta mesajı silinərkən xəta: {del_err}")

    @dp.callback_query(F.data.startswith("page_"))
    async def handle_page(callback: types.CallbackQuery):
        user_id = callback.from_user.id
        if user_id not in user_searches:
            return

        user_data = user_searches[user_id]
        new_page = int(callback.data.split("_")[1])
        user_data['current_page'] = new_page
        results = user_data['results']
        total_pages = (len(results) + 4) // 5
        start = new_page * 5
        current_results = results[start:start+5]

        response = [
            f"<b>🎵 Tapılan Mahnılar (Səhifə {new_page +1}/{total_pages}):</b>",
            ""
        ]

        keyboard_rows = []
        for i, res in enumerate(current_results):
            original_index = start + i
            button_text = f"{res['title']} ({res['duration']})"[:64]
            keyboard_rows.append([InlineKeyboardButton(text=button_text, callback_data=f"choice_{original_index}")])

        pagination_buttons = []
        if total_pages > 1:
            if new_page > 0:
                pagination_buttons.append(InlineKeyboardButton(text="◀️ Geri", callback_data=f"page_{new_page-1}"))
            if new_page < total_pages - 1:
                pagination_buttons.append(InlineKeyboardButton(text="İrəli ▶️", callback_data=f"page_{new_page+1}"))

        if pagination_buttons:
            keyboard_rows.append(pagination_buttons)

        keyboard_rows.append([
            InlineKeyboardButton(text="❌ Ləğv et", callback_data="cancel")
        ])

        keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_rows)
        
        await callback.message.edit_text(
            "\n".join(response),
            reply_markup=keyboard,
            parse_mode="HTML",
            disable_web_page_preview=True
        )
        await callback.answer()

    @dp.message(Command("music", "mahnı", "song"))
    async def music_cmd(message: types.Message, command: CommandObject):
        command_used = message.text.split()[0][1:]
        await handle_music_search(message, command.args, command_used)

    @dp.message(
        F.chat.type == "private",
        ~F.text.startswith("/"),
        ~(F.text.contains("youtube.com/playlist") | F.text.contains("youtu.be/playlist")),
        ~(F.text.contains("spotify.com/playlist") | F.text.contains("spotify.com/album") | F.text.contains("spotify.com/track"))
    )
    async def handle_private_music_request(message: types.Message):
        await handle_music_search(message, message.text, "music")

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
                text=f"<i>⏳ Yüklənir:</i>\n <b>{selected['title']}</b> <b>{selected['duration']}</b>",
                parse_mode="HTML"
            )
            
            file_path = await context.youtube_manager.download_track(
                selected['url'], 
                sanitize_filename(selected['title'])
            )
            
            # Optimized track parsing with regex
            title = selected['title']
            artist = "YouTube"
            track_name = title
            
            if ' - ' in title:
                parts = re.split(r"\s+-\s+", title, maxsplit=1)
                artist = parts[0].strip()
                track_name = parts[1].strip() if len(parts) > 1 else title
            
            track_data = {
                'name': track_name,
                'artists': [{'name': artist}],
                'album': {
                    'name': title,
                    'images': []
                }
            }
            
            with open(file_path, 'rb') as f:
                caption = "<i>via @dllmasterbot</i>"

                await callback.message.answer_audio(
                    audio=BufferedInputFile(f.read(), filename=os.path.basename(file_path)),
                    title=track_data['name'][:64],
                    performer=", ".join([a['name'] for a in track_data['artists']][:3])[:64],
                    duration=int(selected.get('raw_duration', 0)),
                    parse_mode="HTML",
                    caption=caption
                )

            try:
                await callback.message.bot.delete_message(callback.message.chat.id, user_data['search_message_id'])
            except Exception as e:
                logger.error(f"Axtarış mesajı silinərkən xəta (seçim): {e}")

            try:
                await callback.message.bot.delete_message(callback.message.chat.id, user_data['original_message_id'])
            except Exception as e:
                logger.error(f"Orijinal mesaj silinərkən xəta (seçim): {e}")
            
        except (IndexError, KeyError):
            await callback.message.answer("❌ <b>Yanlış seçim!</b>", parse_mode="HTML")
            
        except Exception as e:
            await callback.message.answer(
                f"❌ <b>Xəta:</b> <code>{str(e)}</code>",
                parse_mode="HTML"
            )
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
        
        cancel_msg = await callback.message.answer("❌ <b>Axtarış ləğv edildi.</b>", parse_mode="HTML")
        
        try:
            await callback.message.bot.delete_message(callback.message.chat.id, user_data['search_message_id'])
        except Exception as e:
            logger.error(f"Axtarış mesajı silinərkən xəta (ləğv): {e}")

        try:
            await callback.message.bot.delete_message(callback.message.chat.id, user_data['original_message_id'])
        except Exception as e:
            logger.error(f"Orijinal mesaj silinərkən xəta (ləğv): {e}")
        
        await asyncio.sleep(5)
        try:
            await cancel_msg.delete()
        except Exception as e:
            logger.error(f"Ləğv mesajı silinərkən xəta: {e}")
        
        user_searches.pop(user_id, None)
