import logging
from aiogram import types, Router
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder

logger = logging.getLogger(__name__)
router = Router()

async def setup(context):
    router.message.register(send_profile_command, Command("profile"))
    router.callback_query.register(show_last_downloads, lambda c: c.data == "show_last_downloads")
    router.callback_query.register(back_to_profile, lambda c: c.data == "back_to_profile")
    router.callback_query.register(close_message, lambda c: c.data == "close_message")
    context.main_router.include_router(router)
    logger.info("✅ Profile plugin loaded")

async def send_profile_command(message: types.Message):
    """Show user profile information with interactive buttons"""
    try:
        context = message.bot.data.get('app_context')
        user_id = message.from_user.id
        username = message.from_user.username or message.from_user.full_name

        # Add user to database if not exists
        context.db.add_user(user_id, username)

        # Get user statistics
        cursor = context.db.stats_conn.cursor()
        cursor.execute(
            "SELECT username, message_count, song_download_count FROM users WHERE user_id = ?",
            (user_id,)
        )
        user_data = cursor.fetchone()

        if not user_data:
            await message.reply("❌ Profil tapılmadı.")
            return

        username_db, msg_count, song_count = user_data

        # Create inline keyboard
        builder = InlineKeyboardBuilder()
        builder.button(text="🎵 Son Yükləmələr", callback_data="show_last_downloads")
        builder.button(text="❌ Bağla", callback_data="close_message")
        builder.adjust(1)  # Vertical layout

        profile_text = (
            f"👤 **Profil**: @{username_db}\n"
            f"📊 **Statistikalar**:\n"
            f"• Mesaj sayı: `{msg_count}`\n"
            f"• Yüklənən mahnılar: `{song_count}`"
        )

        await message.reply(
            profile_text,
            parse_mode="Markdown",
            reply_markup=builder.as_markup()
        )

    except Exception as e:
        logger.error(f"Profil xətası: {e}", exc_info=True)
        await message.reply("❌ Profil məlumatları alına bilmədi.")

async def show_last_downloads(callback_query: types.CallbackQuery):
    """Show last downloads with back and close buttons"""
    try:
        context = callback_query.bot.data.get('app_context')
        user_id = callback_query.from_user.id
        
        # Get last 5 song downloads
        song_history = context.db.get_user_song_history(user_id)[:5]
        
        if not song_history:
            await callback_query.answer("❌ Heç bir mahnı yoxdur.", show_alert=True)
            return

        # Build history text
        history_text = "🎵 Son 5 Yükləmə:\n\n"
        for song in song_history:
            history_text += (
                f"▫️ `{song['song_title']}` - {song['artist']}\n"
                f"   📅 {song['download_date']}\n\n"
            )

        # Create back and close buttons
        builder = InlineKeyboardBuilder()
        builder.button(text="🔙 Profile qayıt", callback_data="back_to_profile")
        builder.button(text="❌ Bağla", callback_data="close_message")
        builder.adjust(1)

        await callback_query.message.edit_text(
            history_text,
            parse_mode="Markdown",
            reply_markup=builder.as_markup()
        )
        await callback_query.answer()

    except Exception as e:
        logger.error(f"Yükləmə tarixçəsi xətası: {e}", exc_info=True)
        await callback_query.answer("❌ Xəta baş verdi.", show_alert=True)

async def back_to_profile(callback_query: types.CallbackQuery):
    """Return to profile view"""
    try:
        context = callback_query.bot.data.get('app_context')
        user_id = callback_query.from_user.id
        
        # Get user statistics again
        cursor = context.db.stats_conn.cursor()
        cursor.execute(
            "SELECT username, message_count, song_download_count FROM users WHERE user_id = ?",
            (user_id,)
        )
        user_data = cursor.fetchone()
        username_db, msg_count, song_count = user_data

        # Rebuild profile buttons
        builder = InlineKeyboardBuilder()
        builder.button(text="🎵 Son Yükləmələr", callback_data="show_last_downloads")
        builder.button(text="❌ Bağla", callback_data="close_message")
        builder.adjust(1)

        profile_text = (
            f"👤 **Profil**: @{username_db}\n"
            f"📊 **Statistikalar**:\n"
            f"• Mesaj sayı: `{msg_count}`\n"
            f"• Yüklənən mahnılar: `{song_count}`"
        )

        await callback_query.message.edit_text(
            profile_text,
            parse_mode="Markdown",
            reply_markup=builder.as_markup()
        )
        await callback_query.answer()

    except Exception as e:
        logger.error(f"Geri qayıtma xətası: {e}", exc_info=True)
        await callback_query.answer("❌ Profilə qayıtmaq mümkün olmadı.", show_alert=True)

async def close_message(callback_query: types.CallbackQuery):
    """Delete the message"""
    try:
        await callback_query.message.delete()
        await callback_query.answer()
    except Exception as e:
        logger.error(f"Mesajı silmə xətası: {e}", exc_info=True)
        await callback_query.answer("❌ Mesaj silinə bilmədi.", show_alert=True)