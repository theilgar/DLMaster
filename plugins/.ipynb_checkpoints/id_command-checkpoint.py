import logging
from datetime import datetime
from aiogram import types, F
from aiogram.filters import Command
from aiogram.utils.markdown import link

logger = logging.getLogger(__name__)

async def format_user_info(user: types.User, chat_member: types.ChatMember = None) -> str:
    """Format user information into a readable string"""
    user_info = [
        f"🆔 **ID:** `{user.id}`",
        f"👤 **Ad:** {user.first_name}",
        f"📝 **Soyad:** {user.last_name or 'Yoxdur'}",
        f"🌐 **Username:** @{user.username}" if user.username else "🔒 **Username:** Yoxdur",
        f"🤖 **Hesab Tipi:** {'Bot' if user.is_bot else 'Adi istifadəçi'}"
    ]
    
    if chat_member:
        user_info.extend([
            f"👑 **Status:** {chat_member.status}",
            f"🕒 **Qoşulma tarixi:** {datetime.fromtimestamp(chat_member.joined_date or 0).strftime('%Y-%m-%d %H:%M') if hasattr(chat_member, 'joined_date') else 'Məlumat yoxdur'}"
        ])
    
    return "\n".join(user_info)

async def info_command_handler(message: types.Message, command: Command):
    """Provides detailed information about a user account"""
    target_user = None
    chat_member = None
    
    try:
        # 1. Check if command has arguments
        if command.args:
            try:
                target_id = command.args.strip().split()[0]
                if not target_id.isdigit():
                    raise ValueError("Invalid ID format")
                target_user = types.User(id=int(target_id), is_bot=False, first_name="ID")
            except (IndexError, ValueError):
                await message.reply("⚠️ Yanlış ID formatı! Yalnız rəqəmlər istifadə edin.")
                return
        else:
            # 2. Check for replied message
            if message.reply_to_message:
                target_user = (
                    message.reply_to_message.forward_from 
                    or message.reply_to_message.from_user
                )
            # 3. Check for forwarded message
            elif message.forward_from:
                target_user = message.forward_from
            # 4. Default to current user
            else:
                target_user = message.from_user

        # Try to get chat member info if in group
        if message.chat.type != "private":
            try:
                chat_member = await message.bot.get_chat_member(
                    chat_id=message.chat.id,
                    user_id=target_user.id
                )
            except Exception as e:
                logger.debug(f"Couldn't get chat member info: {e}")

        # Format user information
        formatted_info = await format_user_info(target_user, chat_member)
        
        # Create message link
        message_link = link("📩 Mesaj göndər", f"tg://user?id={target_user.id}")
        
        # Format response
        response = (
            f"👤 **Hesab Məlumatları**:\n\n"
            f"{formatted_info}\n\n"
            f"{message_link}"
        )

        await message.reply(response, parse_mode="Markdown", disable_web_page_preview=True)

    except Exception as e:
        logger.error(f"Error in info command: {e}")
        await message.reply("⚠️ Məlumat alınarkən xəta baş verdi. Zəhmət olmasa yenidən cəhd edin.")

async def setup(context):
    """Register the plugin with the bot"""
    context.dp.message.register(
        info_command_handler,
        Command("info"),
        F.chat.type.in_({"private", "group", "supergroup"})
    )
    logger.info("✅ info_command plugin successfully loaded")