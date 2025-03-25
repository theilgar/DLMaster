from aiogram import types, F
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from handlers.utilities import sanitize_filename, format_duration
import asyncio
import os
import logging

# Loqlama konfiqurasiyası
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

def normalize_text(text: str) -> str:
    """Mətnin normalizasiyası: boşluqları sil, kiçik hərflər, apostrofları standartlaşdır."""
    return text.strip().lower().replace("’", "'")

def setup(context):
    dp = context.dp
    bot = context.bot
    youtube = context.youtube
    spotify = context.spotify
    active_tasks = context.active_tasks
    creator_info = context.creator_info

    # Downloads qovluğunu yoxlamaq və yaratmaq
    downloads_dir = "downloads"
    if not os.path.exists(downloads_dir):
        os.makedirs(downloads_dir)
        logger.info(f"'{downloads_dir}' qovluğu yaradıldı.")

    async def process_youtube_playlist(message: types.Message, url: str):
        try:
            playlist = youtube.get_playlist_info(url)
            
            if not playlist.get('entries'):
                raise ValueError("Playlist boşdur və ya mahnılar tapılmadı")
            
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="🚦 Dayandır", callback_data=f"cancel_{message.from_user.id}")
            
            msg = await message.answer(
                f"🎥 Playlist: {playlist['title']}\n"
                f"📊 Mahnı sayı: {playlist['total']}\n"
                f"⏳ Ümumi müddət: {playlist['duration']}\n\n"
                f"{creator_info}",
                reply_markup=keyboard.as_markup()
            )

            # Mesajı pin et
            await bot.pin_chat_message(chat_id=msg.chat.id, message_id=msg.message_id)

            total_tracks = len(playlist['entries'])
            sent_tracks = 0

            # Verilənlər bazasına playlist məlumatlarını əlavə edirik
            user_id = message.from_user.id
            username = message.from_user.username or "Naməlum"
            context.db.add_user(user_id, username)
            context.db.increment_user_message_count(user_id)
            context.db.add_log(
                level="INFO",
                message=f"User {username} YouTube playlist işlətdi: {playlist['title']}",
                user_id=user_id,
                group_id=message.chat.id if message.chat.type != "private" else None
            )

            for entry in playlist['entries']:
                if asyncio.current_task().cancelled():
                    break
                
                file_path = None
                try:
                    file_path = await youtube.download_track(
                        entry['url'],
                        sanitize_filename(entry['title'])
                    )
                    
                    await message.answer_audio(
                        audio=types.FSInputFile(file_path),
                        title=entry['title'][:64],
                        performer="YouTube",
                        duration=int(entry.get('duration', 0))
                    )

                    sent_tracks += 1
                    remaining_tracks = total_tracks - sent_tracks

                    await bot.edit_message_text(
                        chat_id=msg.chat.id,
                        message_id=msg.message_id,
                        text=f"🎥 Playlist: {playlist['title']}\n"
                             f"📊 Mahnı sayı: {playlist['total']}\n"
                             f"⏳ Ümumi müddət: {playlist['duration']}\n\n"
                             f"✅ Göndərilən mahnı sayı: {sent_tracks}\n"
                             f"📥 Qalan mahnı sayı: {remaining_tracks}\n\n"
                             f"{creator_info}",
                        reply_markup=keyboard.as_markup()
                    )
                    
                except Exception as e:
                    logger.error(f"Mahnı yüklənərkən xəta baş verdi: {entry['title']} - {str(e)}")
                    await message.answer(f"❌ Mahnı yüklənərkən xəta baş verdi: {entry['title']}\n{creator_info}")
                finally:
                    if file_path and os.path.exists(file_path):
                        try:
                            os.remove(file_path)
                        except:
                            pass

            await bot.delete_message(msg.chat.id, msg.message_id)
        except Exception as e:
            await message.answer(f"❌ Xəta: {str(e)}\n{creator_info}")
        finally:
            active_tasks.pop(message.from_user.id, None)

    async def process_spotify_playlist(message: types.Message, playlist_id: str):
        try:
            playlist_data = spotify.get_playlist_data(playlist_id)
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="🚦 Dayandır", callback_data=f"cancel_{message.from_user.id}")
            
            msg = await message.answer(
                f"🎧 Playlist: {playlist_data['name']}\n"
                f"📊 Mahnı sayı: {playlist_data['total']}\n"
                f"⏳ Ümumi müddət: {playlist_data['duration']}\n\n"
                f"{creator_info}",
                reply_markup=keyboard.as_markup()
            )

            # Mesajı pin et
            await bot.pin_chat_message(chat_id=msg.chat.id, message_id=msg.message_id)

            total_tracks = len(playlist_data["tracks"])
            sent_tracks = 0

            # Verilənlər bazasına playlist məlumatlarını əlavə edirik
            user_id = message.from_user.id
            username = message.from_user.username or "Naməlum"
            context.db.add_user(user_id, username)
            context.db.increment_user_message_count(user_id)
            context.db.add_log(
                level="INFO",
                message=f"User {username} Spotify playlist işlətdi: {playlist_data['name']}",
                user_id=user_id,
                group_id=message.chat.id if message.chat.type != "private" else None
            )

            for track in playlist_data["tracks"]:
                if asyncio.current_task().cancelled():
                    break

                file_path = None
                try:
                    file_path = await youtube.download_track(
                        f"ytsearch:{track['name']} {track['artists'][0]['name']}",
                        sanitize_filename(track['name'])
                    )
                    spotify.apply_metadata(file_path, track)

                    await message.answer_audio(
                        audio=types.FSInputFile(file_path),
                        title=track['name'][:64],
                        performer=track['artists'][0]['name'][:64],
                        duration=int(track['duration_ms'] // 1000)
                    )

                    sent_tracks += 1
                    remaining_tracks = total_tracks - sent_tracks

                    await bot.edit_message_text(
                        chat_id=msg.chat.id,
                        message_id=msg.message_id,
                        text=f"🎧 Playlist: {playlist_data['name']}\n"
                             f"📊 Mahnı sayı: {playlist_data['total']}\n"
                             f"⏳ Ümumi müddət: {playlist_data['duration']}\n\n"
                             f"✅ Göndərilən mahnı sayı: {sent_tracks}\n"
                             f"📥 Qalan mahnı sayı: {remaining_tracks}\n\n"
                             f"{creator_info}",
                        reply_markup=keyboard.as_markup()
                    )

                except Exception as e:
                    logger.error(f"Mahnı yüklənərkən xəta baş verdi: {track['name']} - {str(e)}")
                    await message.answer(f"❌ Mahnı yüklənərkən xəta baş verdi: {track['name']}\n{creator_info}")
                finally:
                    if file_path and os.path.exists(file_path):
                        try:
                            os.remove(file_path)
                        except:
                            pass

            await bot.delete_message(msg.chat.id, msg.message_id)
        except Exception as e:
            await message.answer(f"❌ Xəta: {str(e)}\n{creator_info}")
        finally:
            active_tasks.pop(message.from_user.id, None)

    async def process_spotify_track(message: types.Message, track_id: str):
        try:
            track_data = spotify.get_track_data(track_id)
            
            # Mahnı məlumatlarını göstər
            msg = await message.answer(
                f"🎧 Mahnı: {track_data['name']}\n"
                f"🎤 İfaçı: {track_data['artists'][0]['name']}\n"
                f"⏳ Müddət: {format_duration(track_data['duration_ms'] // 1000)}\n\n"
                f"{creator_info}"
            )

            # Mahnını yüklə
            file_path = await youtube.download_track(
                f"ytsearch:{track_data['name']} {track_data['artists'][0]['name']}",
                sanitize_filename(track_data['name'])
            )
            spotify.apply_metadata(file_path, track_data)

            # Mahnını göndər
            await message.answer_audio(
                audio=types.FSInputFile(file_path),
                title=track_data['name'][:64],
                performer=track_data['artists'][0]['name'][:64],
                duration=int(track_data['duration_ms'] // 1000)
            )

            # Verilənlər bazasına mahnı məlumatlarını əlavə edirik
            user_id = message.from_user.id
            username = message.from_user.username or "Naməlum"
            context.db.add_user(user_id, username)
            context.db.increment_user_message_count(user_id)
            context.db.add_log(
                level="INFO",
                message=f"User {username} Spotify mahnısı endirdi: {track_data['name']}",
                user_id=user_id,
                group_id=message.chat.id if message.chat.type != "private" else None
            )

            await bot.delete_message(msg.chat.id, msg.message_id)
        except Exception as e:
            await message.answer(f"❌ Xəta: {str(e)}\n{creator_info}")
        finally:
            if file_path and os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except:
                    pass

    @dp.message(F.text.contains("youtube.com/playlist") | F.text.contains("youtu.be/playlist"))
    async def handle_youtube_playlist(message: types.Message):
        try:
            url = message.text
            
            if "playlist?list=" not in url and "playlist/" not in url:
                await message.answer("❌ Yanlış playlist linki formatı!")
                return
            
            user_id = message.from_user.id
            task = asyncio.create_task(process_youtube_playlist(message, url))
            active_tasks[user_id] = task
        except Exception as e:
            await message.answer(f"❌ Xəta: {str(e)}")

    @dp.message(F.text.contains("spotify.com/playlist/"))
    async def handle_spotify_playlist(message: types.Message):
        user_id = message.from_user.id
        playlist_id = message.text.split("playlist/")[1].split("?")[0]
        task = asyncio.create_task(process_spotify_playlist(message, playlist_id))
        active_tasks[user_id] = task

    @dp.message(F.text.contains("spotify.com/track/"))
    async def handle_spotify_track(message: types.Message):
        user_id = message.from_user.id
        track_id = message.text.split("track/")[1].split("?")[0]
        task = asyncio.create_task(process_spotify_track(message, track_id))
        active_tasks[user_id] = task

    @dp.callback_query(F.data.startswith("cancel_"))
    async def cancel_processing(callback: types.CallbackQuery):
        user_id = int(callback.data.split("_")[1])
        if task := active_tasks.get(user_id):
            task.cancel()
            await callback.answer("❌ Dayandırıldı!")
            
            stop_msg = await callback.message.answer("✅ Əməliyyat uğurla dayandırıldı.")
            
            await bot.delete_message(callback.message.chat.id, callback.message.message_id)
            
            if last_status_message := active_tasks.get(f"last_status_{user_id}"):
                try:
                    await bot.delete_message(callback.message.chat.id, last_status_message.message_id)
                except Exception as e:
                    logger.error(f"last_status_message silinərkən xəta baş verdi: {str(e)}")
            
            await asyncio.sleep(5)
            await bot.delete_message(stop_msg.chat.id, stop_msg.message_id)