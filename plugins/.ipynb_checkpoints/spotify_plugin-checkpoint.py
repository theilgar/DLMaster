from aiogram import types, F
from aiogram.utils.keyboard import InlineKeyboardBuilder
from handlers.utilities import sanitize_filename, format_duration
import asyncio
import os
import logging

logger = logging.getLogger(__name__)

def setup(context):
    dp = context.dp
    bot = context.bot
    youtube = context.youtube
    spotify = context.spotify
    active_tasks = context.active_tasks
    creator_info = context.creator_info
    db = context.db

    async def process_spotify_playlist(message: types.Message, playlist_id: str):
        """Spotify playlist emalı"""
        user_id = message.from_user.id
        username = message.from_user.username or "Naməlum"
        chat_id = message.chat.id if message.chat.type != "private" else None
        
        try:
            playlist_data = spotify.get_playlist_data(playlist_id)
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="🚦 Dayandır", callback_data=f"cancel_{user_id}")
            
            msg = await message.answer(
                f"🎧 Playlist: {playlist_data['name']}\n"
                f"📊 Mahnı sayı: {playlist_data['total']}\n"
                f"⏳ Ümumi müddət: {playlist_data['duration']}\n\n"
                f"{creator_info}",
                reply_markup=keyboard.as_markup()
            )

            await bot.pin_chat_message(chat_id=msg.chat.id, message_id=msg.message_id)

            total_tracks = len(playlist_data["tracks"])
            sent_tracks = 0

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

                    # Mahnı məlumatlarını qeyd et
                    await track_download_handler(
                        user_id=user_id,
                        username=username,
                        track_info=track,
                        source="spotify_playlist",
                        chat_id=chat_id
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
                    logger.error(f"Mahnı yüklənərkən xəta: {track['name']} - {str(e)}")
                    await message.answer(f"❌ Mahnı yüklənərkən xəta: {track['name']}\n{creator_info}")
                finally:
                    if file_path and os.path.exists(file_path):
                        try:
                            os.remove(file_path)
                        except:
                            pass

            await bot.delete_message(msg.chat.id, msg.message_id)
        except Exception as e:
            await message.answer(f"❌ Xəta: {str(e)}\n{creator_info}")
            db.log_error(
                error_message=f"Spotify playlist error: {str(e)}",
                user_id=user_id
            )
        finally:
            active_tasks.pop(user_id, None)

    async def process_spotify_track(message: types.Message, track_id: str):
        """Spotify mahnı emalı"""
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
                sanitize_filename(track_data['name'])
            )
            spotify.apply_metadata(file_path, track_data)

            await message.answer_audio(
                audio=types.FSInputFile(file_path),
                title=track_data['name'][:64],
                performer=track_data['artists'][0]['name'][:64],
                duration=int(track_data['duration_ms'] // 1000)
            )

            # Mahnı məlumatlarını qeyd et
            await track_download_handler(
                user_id=user_id,
                username=username,
                track_info=track_data,
                source="spotify",
                chat_id=chat_id
            )

            await bot.delete_message(msg.chat.id, msg.message_id)
        except Exception as e:
            await message.answer(f"❌ Xəta: {str(e)}\n{creator_info}")
            db.log_error(
                error_message=f"Spotify track error: {str(e)}",
                user_id=user_id
            )
        finally:
            if file_path and os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except:
                    pass

    # Message handlers
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

    async def track_download_handler(user_id: int, username: str, track_info: dict, source: str, chat_id=None):
        """Mahnı yükləmə prosesini idarə edir və verilənlər bazasına qeyd edir"""
        try:
            # İstifadəçini verilənlər bazasına əlavə et
            db.add_user(user_id, username)
            db.increment_user_message_count(user_id)
            
            # Mahnı məlumatlarını verilənlər bazasına əlavə et
            db.add_song_download(
                user_id=user_id,
                song_title=track_info.get('title', track_info.get('name', 'Naməlum')),
                artist=track_info.get('artist', track_info.get('artists', [{}])[0].get('name', 'Naməlum')),
                source=source
            )
            
            # Log qeydi
            db.add_log(
                level="INFO",
                message=f"User {username} {source} mahnısı endirdi: {track_info.get('title', track_info.get('name', 'Naməlum'))}",
                user_id=user_id,
                group_id=chat_id if chat_id else None,
                command=f"/{source}_download"
            )
            
            logger.info(f"{username} tərəfindən {source} mahnısı uğurla qeyd edildi")
            
        except Exception as e:
            logger.error(f"Verilənlər bazasına yazılmada xəta: {str(e)}")
            db.log_error(
                error_message=f"Database error: {str(e)}",
                user_id=user_id
            )