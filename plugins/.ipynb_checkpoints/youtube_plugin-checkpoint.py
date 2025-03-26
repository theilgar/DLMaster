from aiogram import types, F
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from handlers.utilities import sanitize_filename, format_duration
import asyncio
import os
import logging
from datetime import datetime

# Logging configuration
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

def normalize_text(text: str) -> str:
    """Normalize text: trim spaces, lowercase, standardize apostrophes"""
    return text.strip().lower().replace("'", "").replace("’", "")

def setup(context):
    dp = context.dp
    bot = context.bot
    youtube = context.youtube
    spotify = context.spotify
    youtube_playlist = context.youtube_playlist
    active_tasks = context.active_tasks
    creator_info = context.creator_info
    db = context.db

    async def track_download_handler(user_id: int, username: str, track_info: dict, source: str, chat_id=None):
        """Handle track download process and record in database"""
        try:
            # Add user to database
            db.add_user(user_id, username)
            db.increment_user_message_count(user_id)
            
            # Add song to database
            song_title = track_info.get('title', track_info.get('name', 'Unknown'))
            artist = track_info.get('artist', 
                   track_info.get('artists', [{}])[0].get('name', 'Unknown'))
            
            db.add_song_download(
                user_id=user_id,
                song_title=song_title,
                artist=artist,
                source=source
            )
            
            # Add log entry
            db.add_log(
                level="INFO",
                message=f"User {username} downloaded {source} track: {song_title}",
                user_id=user_id,
                group_id=chat_id if chat_id else None,
                command=f"/{source}_download"
            )
            
            logger.info(f"{username} successfully recorded {source} track: {song_title}")
            
        except Exception as e:
            logger.error(f"Database error: {str(e)}")
            db.log_error(
                error_message=f"Database error: {str(e)}",
                user_id=user_id
            )

    async def process_youtube_playlist(message: types.Message, url: str):
        """Process YouTube playlist"""
        user_id = message.from_user.id
        username = message.from_user.username or "Unknown"
        chat_id = message.chat.id if message.chat.type != "private" else None
        
        try:
            # Get playlist info
            playlist = youtube_playlist.get_playlist_info_playlist(url)
            
            if not playlist.get('entries'):
                await message.answer("❌ Playlist is empty or no tracks found")
                return
            
            # Create progress message with cancel button
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="⏹ Cancel", callback_data=f"cancel_{user_id}")
            
            progress_msg = await message.answer(
                f"📋 Playlist: {playlist['title']}\n"
                f"🎵 Total tracks: {playlist['total']}\n"
                f"⏱ Total duration: {playlist['duration']}\n\n"
                f"⬇️ Downloading...\n\n"
                f"{creator_info}",
                reply_markup=keyboard.as_markup()
            )
            
            if chat_id:
                await bot.pin_chat_message(chat_id=chat_id, message_id=progress_msg.message_id)

            total_tracks = len(playlist['entries'])
            downloaded_tracks = 0
            failed_tracks = 0

            for track in playlist['entries']:
                if user_id in active_tasks and active_tasks[user_id].cancelled():
                    break
                
                file_path = None
                try:
                    # Download track
                    file_path = await youtube_playlist.download_track(
                        track['url'],
                        sanitize_filename(f"{track['title']}_{track['id']}")
                    )
                    
                    if not file_path or not os.path.exists(file_path):
                        raise FileNotFoundError("Downloaded file not found")
                    
                    # Send audio
                    await message.answer_audio(
                        audio=types.FSInputFile(file_path),
                        title=track['title'][:64],
                        performer=track.get('artist', 'YouTube')[:64],
                        duration=int(track.get('duration', 0))
                    )

                    # Record download
                    await track_download_handler(
                        user_id=user_id,
                        username=username,
                        track_info=track,
                        source="youtube_playlist",
                        chat_id=chat_id
                    )

                    downloaded_tracks += 1
                    
                except Exception as e:
                    logger.error(f"Error downloading track {track['title']}: {str(e)}")
                    failed_tracks += 1
                    await message.answer(f"❌ Failed to download: {track['title']}")
                finally:
                    # Cleanup downloaded file
                    if file_path and os.path.exists(file_path):
                        try:
                            os.remove(file_path)
                        except Exception as e:
                            logger.error(f"Error deleting file {file_path}: {str(e)}")
                
                # Update progress
                if user_id not in active_tasks or not active_tasks[user_id].cancelled():
                    await progress_msg.edit_text(
                        f"📋 Playlist: {playlist['title']}\n"
                        f"🎵 Total tracks: {playlist['total']}\n"
                        f"⏱ Total duration: {playlist['duration']}\n\n"
                        f"✅ Downloaded: {downloaded_tracks}\n"
                        f"❌ Failed: {failed_tracks}\n"
                        f"📥 Remaining: {total_tracks - downloaded_tracks - failed_tracks}\n\n"
                        f"{creator_info}",
                        reply_markup=keyboard.as_markup()
                    )

            # Final status
            await progress_msg.edit_text(
                f"🎉 Playlist download complete!\n"
                f"📋 {playlist['title']}\n"
                f"✅ Success: {downloaded_tracks}\n"
                f"❌ Failed: {failed_tracks}\n\n"
                f"{creator_info}"
            )
            
        except Exception as e:
            await message.answer(f"❌ Error processing playlist: {str(e)}")
            db.log_error(
                error_message=f"Playlist processing error: {str(e)}",
                user_id=user_id
            )
        finally:
            active_tasks.pop(user_id, None)
            if chat_id and 'progress_msg' in locals():
                try:
                    await bot.unpin_chat_message(chat_id, progress_msg.message_id)
                except Exception as e:
                    logger.error(f"Error unpinning message: {str(e)}")


    # Message handlers
    @dp.message(F.text.contains("youtube.com/playlist") | F.text.contains("youtu.be/playlist"))
    async def handle_youtube_playlist(message: types.Message):
        user_id = message.from_user.id
        try:
            url = message.text.strip()
            
            if "playlist?list=" not in url and "playlist/" not in url:
                await message.answer("❌ Invalid playlist URL format!")
                return
            
            task = asyncio.create_task(process_youtube_playlist(message, url))
            active_tasks[user_id] = task
            await task
            
        except Exception as e:
            await message.answer(f"❌ Error: {str(e)}")
            db.log_error(
                error_message=f"Youtube playlist handler error: {str(e)}",
                user_id=user_id
            )


    @dp.callback_query(F.data.startswith("cancel_"))
    async def cancel_processing(callback: types.CallbackQuery):
        user_id = int(callback.data.split("_")[1])
        if task := active_tasks.get(user_id):
            task.cancel()
            await callback.answer("⏹ Download cancelled!")
            
            stop_msg = await callback.message.answer("✅ Download stopped successfully.")
            
            try:
                await bot.delete_message(callback.message.chat.id, callback.message.message_id)
            except Exception as e:
                logger.error(f"Error deleting message: {str(e)}")
            
            await asyncio.sleep(5)
            await bot.delete_message(stop_msg.chat.id, stop_msg.message_id)
            
            # Log cancellation
            db.add_log(
                level="INFO",
                message="Playlist download cancelled",
                user_id=user_id,
                group_id=callback.message.chat.id if callback.message.chat.type != "private" else None
            )