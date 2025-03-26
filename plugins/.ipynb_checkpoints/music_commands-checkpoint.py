from aiogram import types, F
from aiogram.filters import Command, CommandObject
from aiogram.types import BufferedInputFile, InlineKeyboardMarkup, InlineKeyboardButton
from handlers.utilities import YoutubeManager, sanitize_filename
import asyncio
import os
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

def setup(context):
    dp = context.dp
    user_searches = context.user_searches
    db = context.db

    # Ensure downloads directory exists
    downloads_dir = "downloads"
    if not os.path.exists(downloads_dir):
        os.makedirs(downloads_dir)
        logger.info(f"'{downloads_dir}' directory created.")

    # Initialize YouTube Manager if not already in context
    if not hasattr(context, 'youtube_manager'):
        context.youtube_manager = YoutubeManager(browser="firefox")

    @dp.message(Command("music"))
    async def music_cmd(message: types.Message, command: CommandObject):
        """Handle /music command with search query"""
        await handle_music_search(message, command.args)

    async def handle_music_search(message: types.Message, search_query: str):
        """Process music search and display results"""
        try:
            if not search_query:
                usage_msg = await message.answer(
                    "<b>🎵 Music search usage:</b>\n"
                    "👉 <code>/music &lt;song name&gt;</code>\n\n"
                    "<i>Example:</i> <code>/music Imagine Dragons Believer</code>",
                    parse_mode="HTML"
                )
                await asyncio.sleep(20)
                try:
                    await usage_msg.delete()
                except Exception as e:
                    logger.error(f"Error deleting usage message: {e}")
                return
            
            user_id = message.from_user.id
            username = message.from_user.username or "Unknown"
            db.add_user(user_id, username)
            db.increment_user_message_count(user_id)
            db.log_command_usage(
                command="/music",
                user_id=user_id,
                group_id=message.chat.id if message.chat.type != "private" else None
            )

            search_msg = await message.answer("<i>🔍 Searching...</i>", parse_mode="HTML")
            results = await context.youtube_manager.youtube_search(search_query)
            
            if not results:
                raise ValueError("No results found")
            
            response = ["<b>🎵 Found Songs:</b>", ""]
            
            for i, res in enumerate(results[:5]):
                response.append(
                    f"<b>{i+1}.</b> <a href='{res['url']}'>{res['title']}</a>\n"
                    f"   ⏳ <i>{res['duration']}</i>"
                )
            
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=f"{i+1}", callback_data=f"choice_{i}") for i in range(len(results[:5]))],
                [
                    InlineKeyboardButton(text="❌ Cancel", callback_data="cancel"),
                    InlineKeyboardButton(text="⬇️ Download All", callback_data="download_all")
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
                f"❌ <b>Error:</b> <code>{str(e)}</code>",
                parse_mode="HTML"
            )
            db.log_error(
                error_message=str(e),
                user_id=message.from_user.id,
                group_id=message.chat.id if message.chat.type != "private" else None
            )
            await asyncio.sleep(5)
            try:
                await error_msg.delete()
            except Exception as del_err:
                logger.error(f"Error deleting error message: {del_err}")

    @dp.callback_query(F.data.startswith("choice_"))
    async def handle_choice(callback: types.CallbackQuery):
        """Handle song selection from search results"""
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
                text=f"<i>⏳ Downloading:</i>\n <b>{selected['title']}</b> <i>({selected['duration']})</i>",
                parse_mode="HTML"
            )
            
            file_path = await context.youtube_manager.download_track(
                selected['url'], 
                sanitize_filename(selected['title'])
            )
            
            try:
                with open(file_path, 'rb') as f:
                    await callback.message.answer_audio(
                        audio=BufferedInputFile(f.read(), filename=os.path.basename(file_path)),
                        title=selected.get('title', '')[:64],
                        performer="YouTube",
                        duration=int(selected.get('raw_duration', 0)),
                        parse_mode="HTML"
                    )
            except Exception as e:
                if "not enough rights to send music" in str(e):
                    await callback.message.answer(
                        "⚠️ <b>Permission issue:</b> The bot needs to be admin with message send/delete rights to send music in this group.",
                        parse_mode="HTML"
                    )
                    return
                raise

            db.add_song_download(
                user_id=user_id,
                song_title=selected['title'],
                artist="YouTube",
                source="youtube"
            )
            db.log_command_usage(
                command="music_download",
                user_id=user_id,
                group_id=callback.message.chat.id if callback.message.chat.type != "private" else None
            )
            
            try:
                await callback.message.bot.delete_message(callback.message.chat.id, user_data['search_message_id'])
            except Exception as e:
                logger.error(f"Error deleting search message (choice): {e}")

            try:
                await callback.message.bot.delete_message(callback.message.chat.id, user_data['original_message_id'])
            except Exception as e:
                logger.error(f"Error deleting original message (choice): {e}")
            
        except (IndexError, KeyError):
            await callback.message.answer("❌ <b>Invalid selection!</b>", parse_mode="HTML")
            db.log_error(
                error_message="Invalid song selection",
                user_id=user_id
            )
        except Exception as e:
            await callback.message.answer(
                f"❌ <b>Error:</b> <code>{str(e)}</code>",
                parse_mode="HTML"
            )
            db.log_error(
                error_message=str(e),
                user_id=user_id
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
        """Handle search cancellation"""
        user_id = callback.from_user.id
        if user_id not in user_searches:
            return

        user_data = user_searches[user_id]
        
        cancel_msg = await callback.message.answer("❌ <b>Search canceled.</b>", parse_mode="HTML")
        
        try:
            await callback.message.bot.delete_message(callback.message.chat.id, user_data['search_message_id'])
        except Exception as e:
            logger.error(f"Error deleting search message (cancel): {e}")

        try:
            await callback.message.bot.delete_message(callback.message.chat.id, user_data['original_message_id'])
        except Exception as e:
            logger.error(f"Error deleting original message (cancel): {e}")
        
        db.log_command_usage(
            command="music_cancel",
            user_id=user_id,
            group_id=callback.message.chat.id if callback.message.chat.type != "private" else None
        )
        
        await asyncio.sleep(5)
        try:
            await cancel_msg.delete()
        except Exception as e:
            logger.error(f"Error deleting cancel message: {e}")
        
        user_searches.pop(user_id, None)

    @dp.callback_query(F.data == "download_all")
    async def handle_download_all(callback: types.CallbackQuery):
        """Handle downloading all search results"""
        user_id = callback.from_user.id
        if user_id not in user_searches:
            return

        user_data = user_searches[user_id]
        
        try:
            username = callback.from_user.username or "Unknown"
            db.add_user(user_id, username)
            
            await callback.message.bot.edit_message_text(
                chat_id=callback.message.chat.id,
                message_id=user_data['search_message_id'],
                text="<i>⏳ Downloading all songs... This may take several minutes.</i>",
                parse_mode="HTML"
            )
            
            for result in user_data['results'][:5]:  
                file_path = await context.youtube_manager.download_track(
                    result['url'], 
                    sanitize_filename(result['title'])
                )
                
                try:
                    with open(file_path, 'rb') as f:
                        await callback.message.answer_audio(
                            audio=BufferedInputFile(f.read(), filename=os.path.basename(file_path)),
                            title=result.get('title', '')[:64],
                            performer="YouTube",
                            duration=int(result.get('raw_duration', 0)),
                            parse_mode="HTML"
                        )
                except Exception as e:
                    if "not enough rights to send music" in str(e):
                        await callback.message.answer(
                            "⚠️ <b>Permission issue:</b> The bot needs to be admin with message send/delete rights to send music in this group.",
                            parse_mode="HTML"
                        )
                        return
                    raise
                
                db.add_song_download(
                    user_id=user_id,
                    song_title=result['title'],
                    artist="YouTube",
                    source="youtube"
                )
                
                if os.path.exists(file_path):
                    try:
                        os.remove(file_path)
                    except:
                        pass
            
            db.log_command_usage(
                command="music_download_all",
                user_id=user_id,
                group_id=callback.message.chat.id if callback.message.chat.type != "private" else None
            )
            
            await callback.message.answer("✅ <b>All songs downloaded successfully!</b>", parse_mode="HTML")
        
        except Exception as e:
            await callback.message.answer(
                f"❌ <b>Error:</b> <code>{str(e)}</code>",
                parse_mode="HTML"
            )
            db.log_error(
                error_message=str(e),
                user_id=user_id
            )
        finally:
            try:
                await callback.message.bot.delete_message(callback.message.chat.id, user_data['search_message_id'])
            except Exception as e:
                logger.error(f"Error deleting search message (download_all): {e}")

            try:
                await callback.message.bot.delete_message(callback.message.chat.id, user_data['original_message_id'])
            except Exception as e:
                logger.error(f"Error deleting original message (download_all): {e}")

            user_searches.pop(user_id, None)

    @dp.message(Command("my_songs"))
    async def show_user_song_history(message: types.Message):
        """Show user's download history"""
        user_id = message.from_user.id
        songs = db.get_user_song_history(user_id)
        
        if not songs:
            await message.answer("📭 <b>You haven't downloaded any songs yet.</b>", parse_mode="HTML")
            return
        
        response = [
            "<b>📋 Your Download History:</b>",
            "<i>Last 10 downloads:</i>",
            ""
        ]
        
        for i, song in enumerate(songs[:10], 1):
            response.append(
                f"<b>{i}.</b> {song['song_title']}\n"
                f"   🎤 <i>Source:</i> {song['source']}\n"
                f"   ⏳ <i>Date:</i> {song['download_date']}\n"
            )
        
        await message.answer("\n".join(response), parse_mode="HTML")
        
        db.log_command_usage(
            command="/my_songs",
            user_id=user_id,
            group_id=message.chat.id if message.chat.type != "private" else None
        )