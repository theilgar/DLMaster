from aiogram import types, F
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.exceptions import TelegramRetryAfter
from core.utilities import sanitize_filename, format_duration
import asyncio
import os
import logging

logger = logging.getLogger(__name__)

async def send_audio_with_retry(message, file_path, title, performer, duration, thumbnail_url, creator_info, max_retries=3):
    retries = 0
    while retries < max_retries:
        try:
            await message.answer_audio(
                audio=types.FSInputFile(file_path),
                title=title[:64],
                performer=performer[:64],
                duration=duration,
                thumbnail=types.URLInputFile(thumbnail_url) if thumbnail_url else None
            )
            return True
        except TelegramRetryAfter as e:
            wait_time = e.retry_after
            if retries == 0:
                try:
                    await message.answer(f"⏳ Telegram limiti: {wait_time} saniyə gözləyirəm...\n{creator_info}")
                except Exception as e:
                    logger.error(f"Gözləmə mesajı göndərilmədi: {e}")
            await asyncio.sleep(wait_time)
            retries += 1
        except Exception as e:
            logger.error(f"Mahnı göndərilmədi: {e}")
            return False
    return False

async def edit_message_with_retry(bot, chat_id, message_id, text, reply_markup=None, max_retries=3):
    retries = 0
    while retries < max_retries:
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                reply_markup=reply_markup
            )
            return True
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after)
            retries += 1
        except Exception as e:
            logger.error(f"Mesaj redaktə xətası: {e}")
            return False
    return False

async def safe_send_message(bot, chat_id, text, max_retries=3):
    retries = 0
    while retries < max_retries:
        try:
            await bot.send_message(chat_id, text)
            return True
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after)
            retries += 1
        except Exception as e:
            logger.error(f"Mesaj göndərilmədi: {e}")
            return False
    return False

def setup(context):
    dp = context.dp
    bot = context.bot
    youtube = context.youtube
    spotify = context.spotify
    active_tasks = context.active_tasks
    creator_info = context.creator_info
    db = context.db

    async def process_spotify_playlist(message: types.Message, playlist_id: str, status_msg: types.Message = None, delay_task = None):
        user_id = message.from_user.id
        username = message.from_user.username or "Naməlum"
        chat_id = message.chat.id if message.chat.type != "private" else None
        
        try:
            playlist_data = spotify.get_playlist_data(playlist_id)
            
            if delay_task and not delay_task.done():
                delay_task.cancel()
            
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="🚦 Dayandır", callback_data=f"cancel_{user_id}")
            
            if status_msg:
                await edit_message_with_retry(
                    bot,
                    status_msg.chat.id,
                    status_msg.message_id,
                    f"🎧 Playlist: {playlist_data['name']}\n"
                    f"📊 Mahnı sayı: {playlist_data['total']}\n"
                    f"⏳ Ümumi müddət: {playlist_data['duration']}\n\n"
                    f"{creator_info}",
                    keyboard.as_markup()
                )
            else:
                status_msg = await message.answer(
                    f"🎧 Playlist: {playlist_data['name']}\n"
                    f"📊 Mahnı sayı: {playlist_data['total']}\n"
                    f"⏳ Ümumi müddət: {playlist_data['duration']}\n\n"
                    f"{creator_info}",
                    reply_markup=keyboard.as_markup()
                )

            if chat_id:
                try:
                    await bot.pin_chat_message(chat_id=chat_id, message_id=status_msg.message_id)
                except Exception as e:
                    logger.error(f"Mesaj sabitlənmədi: {e}")

            total_tracks = len(playlist_data["tracks"])
            sent_tracks = 0

            for track in playlist_data["tracks"]:
                if asyncio.current_task().cancelled():
                    break

                file_path = None
                try:
                    await asyncio.sleep(1.5)
                    file_path = await youtube.download_track(
                        f"ytsearch:{track['name']} {track['artists'][0]['name']}",
                        os.path.join("download", sanitize_filename(track['name'])),
                    )
                    spotify.apply_metadata(file_path, track)

                    thumbnail_url = None
                    if track['album'].get('images'):
                        thumbnail_url = track['album']['images'][0]['url'].split('?')[0]

                    success = await send_audio_with_retry(
                        message,
                        file_path,
                        track['name'],
                        track['artists'][0]['name'],
                        int(track['duration_ms'] // 1000),
                        thumbnail_url,
                        creator_info
                    )

                    if not success:
                        raise Exception(f"{track['name']} mahnısı göndərilmədi")

                    sent_tracks += 1
                    remaining_tracks = total_tracks - sent_tracks

                    await edit_message_with_retry(
                        bot,
                        status_msg.chat.id,
                        status_msg.message_id,
                        f"🎧 Playlist: {playlist_data['name']}\n"
                        f"📊 Mahnı sayı: {playlist_data['total']}\n"
                        f"⏳ Ümumi müddət: {playlist_data['duration']}\n\n"
                        f"✅ Göndərilən mahnı sayı: {sent_tracks}\n"
                        f"📥 Qalan mahnı sayı: {remaining_tracks}\n\n"
                        f"{creator_info}",
                        keyboard.as_markup()
                    )

                except Exception as e:
                    logger.error(f"Mahnı yüklənərkən xəta: {track['name']} - {str(e)}")
                    await safe_send_message(bot, message.chat.id, f"❌ Mahnı yüklənərkən xəta: {track['name']}\n{creator_info}")
                finally:
                    if file_path and os.path.exists(file_path):
                        try:
                            os.remove(file_path)
                        except:
                            pass

            await bot.delete_message(status_msg.chat.id, status_msg.message_id)
        except Exception as e:
            await safe_send_message(bot, message.chat.id, f"❌ Xəta: {str(e)[:300]}\n{creator_info}")
            logger.error(f"Spotify playlist error: {str(e)}")
        finally:
            active_tasks.pop(user_id, None)

    async def process_spotify_album(message: types.Message, album_id: str):
        user_id = message.from_user.id
        username = message.from_user.username or "Naməlum"
        chat_id = message.chat.id if message.chat.type != "private" else None
        
        try:
            album_data = spotify.get_album_data(album_id)
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="🚦 Dayandır", callback_data=f"cancel_{user_id}")
            
            msg = await message.answer(
                f"💿 Albüm: {album_data['name']}\n"
                f"🎤 İfaçı: {album_data['artist']}\n"
                f"📊 Mahnı sayı: {album_data['total']}\n"
                f"⏳ Ümumi müddət: {album_data['duration']}\n\n"
                f"{creator_info}",
                reply_markup=keyboard.as_markup()
            )

            total_tracks = len(album_data["tracks"])
            sent_tracks = 0

            for track in album_data["tracks"]:
                if asyncio.current_task().cancelled():
                    break

                file_path = None
                try:
                    await asyncio.sleep(1.5)
                    file_path = await youtube.download_track(
                        f"ytsearch:{track['name']} {track['artists'][0]['name']}",
                        os.path.join("download", sanitize_filename(track['name'])),
                    )
                    
                    track['album'] = {
                        "name": album_data['name'],
                        "images": album_data['images']
                    }
                    spotify.apply_metadata(file_path, track)

                    thumbnail_url = None
                    if album_data.get('images'):
                        thumbnail_url = album_data['images'][0]['url'].split('?')[0]

                    success = await send_audio_with_retry(
                        message,
                        file_path,
                        track['name'],
                        track['artists'][0]['name'],
                        int(track['duration_ms'] // 1000),
                        thumbnail_url,
                        creator_info
                    )

                    if not success:
                        raise Exception(f"{track['name']} mahnısı göndərilmədi")

                    sent_tracks += 1
                    remaining_tracks = total_tracks - sent_tracks

                    await edit_message_with_retry(
                        bot,
                        msg.chat.id,
                        msg.message_id,
                        f"💿 Albüm: {album_data['name']}\n"
                        f"🎤 İfaçı: {album_data['artist']}\n"
                        f"📊 Mahnı sayı: {album_data['total']}\n"
                        f"⏳ Ümumi müddət: {album_data['duration']}\n\n"
                        f"✅ Göndərilən: {sent_tracks}\n"
                        f"📥 Qalan: {remaining_tracks}\n\n"
                        f"{creator_info}",
                        keyboard.as_markup()
                    )

                except Exception as e:
                    logger.error(f"Mahnı yüklənərkən xəta: {track['name']} - {str(e)}")
                    await safe_send_message(bot, message.chat.id, f"❌ Mahnı yüklənərkən xəta: {track['name']}\n{creator_info}")
                finally:
                    if file_path and os.path.exists(file_path):
                        try:
                            os.remove(file_path)
                        except:
                            pass

            await bot.delete_message(msg.chat.id, msg.message_id)
        except Exception as e:
            await safe_send_message(bot, message.chat.id, f"❌ Xəta: {str(e)[:300]}\n{creator_info}")
            logger.error(f"Spotify album error: {str(e)}")
        finally:
            active_tasks.pop(user_id, None)

    async def process_spotify_track(message: types.Message, track_id: str):
        user_id = message.from_user.id
        username = message.from_user.username or "Naməlum"
        chat_id = message.chat.id if message.chat.type != "private" else None
        file_path = None
        
        try:
            track_data = spotify.get_track_data(track_id)
            
            msg = await message.answer(
                f"🎧 Mahnı: {track_data['name']}\n"
                f"🎤 İfaçı: {track_data['artists'][0]['name']}\n"
                f"⏳ Müddət: {format_duration(track_data['duration_ms'] // 1000)}\n\n"
                f"{creator_info}"
            )

            file_path = await youtube.download_track(
                f"ytsearch:{track_data['name']} {track_data['artists'][0]['name']}",
                os.path.join("download", sanitize_filename(track_data['name'])),
            )
            spotify.apply_metadata(file_path, track_data)

            thumbnail_url = None
            if track_data['album'].get('images'):
                thumbnail_url = track_data['album']['images'][0]['url'].split('?')[0]

            success = await send_audio_with_retry(
                message,
                file_path,
                track_data['name'],
                track_data['artists'][0]['name'],
                int(track_data['duration_ms'] // 1000),
                thumbnail_url,
                creator_info
            )

            if not success:
                raise Exception("Mahnı göndərilmədi")

            await bot.delete_message(msg.chat.id, msg.message_id)
        except Exception as e:
            await safe_send_message(bot, message.chat.id, f"❌ Xəta: {str(e)[:300]}\n{creator_info}")
            logger.error(f"Spotify track error: {str(e)}")
        finally:
            if file_path and os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except:
                    pass

    @dp.message(F.text.contains("spotify.com/playlist/"))
    async def handle_spotify_playlist(message: types.Message):
        user_id = message.from_user.id
        playlist_id = message.text.split("playlist/")[1].split("?")[0]
        
        status_msg = await message.answer("♻️ Link qəbul edildi, playlist emal edilir...")
        
        async def delay_check():
            await asyncio.sleep(5)
            try:
                await status_msg.edit_text("⏳ Zəhmət olmasa gözləyin, emal davam edir...")
            except Exception as e:
                logger.error(f"Gözləmə mesajı redaktə edilmədi: {e}")

        delay_task = asyncio.create_task(delay_check())
        
        main_task = asyncio.create_task(
            process_spotify_playlist(message, playlist_id, status_msg, delay_task))
        active_tasks[user_id] = main_task

    @dp.message(F.text.contains("spotify.com/album/"))
    async def handle_spotify_album(message: types.Message):
        user_id = message.from_user.id
        album_id = message.text.split("album/")[1].split("?")[0]
        task = asyncio.create_task(process_spotify_album(message, album_id))
        active_tasks[user_id] = task
    
    @dp.message(F.text.contains("spotify.com/track/"))
    async def handle_spotify_track(message: types.Message):
        user_id = message.from_user.id
        track_id = message.text.split("track/")[1].split("?")[0]
        task = asyncio.create_task(process_spotify_track(message, track_id))
        active_tasks[user_id] = task