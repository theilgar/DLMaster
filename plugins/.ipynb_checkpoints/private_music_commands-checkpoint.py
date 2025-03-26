from aiogram import types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from handlers.utilities import sanitize_filename
import asyncio
import re
import logging

logger = logging.getLogger(__name__)

def setup(context):
    dp = context.dp
    user_searches = context.user_searches
    db = context.db
    youtube_manager = context.youtube_manager
    
    # URL pattern for detection
    url_pattern = re.compile(r"https?://[^\s]+")

    @dp.message(F.text & ~F.text.startswith('/') & F.chat.type == "private")  
    async def handle_private_music_search(message: types.Message):
        """Handle music search requests in private chats"""
        if url_pattern.search(message.text):
            await message.answer("⚠️ Linklər üçün şəxsi söhbətlərdə axtarış edilmir.")
            return
        
        try:
            search_query = message.text.strip()
            if not search_query:
                usage_msg = await message.answer(
                    "<b>🎵 Musiqi axtarışı üçün istifadə qaydası:</b>\n"
                    "👉 Sadəcə mahnı adını yazın\n"
                    "👉 Və ya <code>/music &lt;mahnı adı&gt;</code> istifadə edin\n\n"
                    "<i>Məsələn:</i> <code>Imagine Dragons Believer</code>",
                    parse_mode="HTML"
                )
                await asyncio.sleep(20)
                try:
                    await usage_msg.delete()
                except Exception as e:
                    logger.error(f"Usage mesajı silinərkən xəta: {e}")
                return
            
            user_id = message.from_user.id
            username = message.from_user.username or "Naməlum"
            db.add_user(user_id, username)
            db.increment_user_message_count(user_id)
            db.log_command_usage(
                command="private_music_search",
                user_id=user_id,
                group_id=None  # Always private chat
            )

            search_msg = await message.answer("<i>🔍 Axtarış edirəm...</i>", parse_mode="HTML")
            results = await youtube_manager.youtube_search(search_query)
            
            if not results:
                raise ValueError("Nəticə tapılmadı")
            
            response = ["<b>🎵 Tapılan Mahnılar:</b>", ""]
            
            for i, res in enumerate(results[:5]):
                response.append(
                    f"<b>{i+1}.</b> <a href='{res['url']}'>{res['title']}</a>\n"
                    f"   ⏳ <i>{res['duration']}</i>"
                )
            
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=f"{i+1}", callback_data=f"choice_{i}") for i in range(len(results[:5]))],
                [
                    InlineKeyboardButton(text="❌ Ləğv et", callback_data="cancel"),
                    InlineKeyboardButton(text="⬇️ Hamısını Yüklə", callback_data="download_all")
                ]
            ])
            
            await search_msg.edit_text(
                "\n".join(response),
                reply_markup=keyboard,
                parse_mode="HTML",
                disable_web_page_preview=True
            )
            
            user_searches[message.from_user.id] = {
                'results': results,
                'search_message_id': search_msg.message_id,
                'original_message_id': message.message_id
            }
            
        except Exception as e:
            error_msg = await message.answer(
                f"❌ <b>Xəta:</b> <code>{str(e)}</code>",
                parse_mode="HTML"
            )
            db.log_error(
                error_message=str(e),
                user_id=message.from_user.id,
                group_id=None
            )
            await asyncio.sleep(5)
            try:
                await error_msg.delete()
            except Exception as del_err:
                logger.error(f"Xəta mesajı silinərkən xəta: {del_err}")