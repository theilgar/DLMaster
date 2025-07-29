from aiogram import types, Router, Bot, F
from aiogram.filters import Command, ChatMemberUpdatedFilter
from aiogram.types import (
    ChatMemberUpdated, 
    FSInputFile, 
    InlineKeyboardMarkup, 
    InlineKeyboardButton,
    CallbackQuery
)
from aiogram.enums import ChatMemberStatus
from pathlib import Path
import logging

logger = logging.getLogger(__name__)
router = Router()
CALLBACK_PREFIX = "start_"

GROUP_WELCOME_MESSAGE = """
<b>Salam! 👋 Mən DLLMaster Bot</b>

• Youtube/Spotify playlistləri yükləyirəm

🔧 <b>Tələb olunan yetkilər:</b>
1. Mesaj silmə
2. Media yükləmə
3. Mesaj pinləmə

Bot @illgaarr tərəfindən yaradılmışdır 🚀
"""

async def setup(context):
    context.main_router.include_router(router)
    logger.info("✅ Start plugin yükləndi")

async def is_bot_admin(chat_id: int, bot: Bot) -> bool:
    try:
        bot_info = await bot.get_me()
        chat_member = await bot.get_chat_member(chat_id, bot_info.id)
        return chat_member.status in {ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR}
    except Exception as e:
        logger.error(f"Admin yoxlanışı xətası: {e}")
        return False

async def send_start_message(message: types.Message, text: str, bot: Bot):
    try:
        bot_username = (await bot.me()).username
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🤖 Botu qrupa əlavə et", 
              url=f"https://t.me/{bot_username}?startgroup=true")],
        ])
        
        gif_path = Path(__file__).parent/"patrick.gif"
        if gif_path.exists():
            await message.answer_animation(
                FSInputFile(gif_path),
                caption=text,
                parse_mode="HTML",
                reply_markup=keyboard
            )
        else:
            await message.answer(text, parse_mode="HTML", reply_markup=keyboard)
            
    except Exception as e:
        logger.error(f"Start mesajı xətası: {e}")
        await message.answer("❌ Xəta baş verdi, zəhmət olmasa yenidən cəhd edin.")

@router.message(Command("start"))
async def start_command(message: types.Message):
    context = message.bot.data.get('app_context')
    bot = context.bot
    
    user_name = message.from_user.first_name or "Dostum"
    START_TEXT = f"""
<b>Salam {user_name}! 👋 Mən DLLMaster Bot!</b>

🎵 <b>Nələr edə bilirəm?</b>
• YouTube/Spotify-dan mahnılar endirirəm

⚡ <b>Necə istifadə edim?</b>
1. Mahnı adı yaz və ya link göndər
2. Playlist linki at (YouTube/Spotify)
3. <code>/music &lt;mahnı adı&gt;</code> yaz

Bot @illgaarr tərəfindən yaradılmışdır 🚀
"""
    if message.chat.type == "private":
        await send_start_message(message, START_TEXT, bot)
    else:
        welcome_msg = GROUP_WELCOME_MESSAGE
        if not await is_bot_admin(message.chat.id, bot):
            welcome_msg += "\n\n⚠️ <b>XƏBƏRDARLIQ:</b> Admin yetkiləri verilməyib!"
        await message.reply(welcome_msg, parse_mode="HTML")

@router.my_chat_member(ChatMemberUpdatedFilter(member_status_changed=True))
async def on_bot_added(event: ChatMemberUpdated):
    context = event.bot.data.get('app_context')
    bot = context.bot
    
    # Only handle group/supergroup events
    if event.chat.type not in ["group", "supergroup"]:
        return

    new_status = event.new_chat_member.status
    old_status = event.old_chat_member.status

    logger.info(f"🔄 Bot status dəyişikliyi: {old_status} -> {new_status} | Chat: {event.chat.title} ({event.chat.id})")

    # Bot added to group
    if (old_status in {ChatMemberStatus.LEFT, ChatMemberStatus.KICKED} 
        and new_status in {ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR}):
        
        try:
            welcome_msg = f"""
<b>Salam {event.chat.title}! 👋</b>

Mən DLLMasterBot, qrupunuzda:
• Mahnı və playlistlər yükləyə bilərəm
• Sürətli və asan istifadə

🔧 <b>Zəhmət olmasa bu yetkiləri verin:</b>
1. Mesaj silmə
2. Media yükləmə
3. Mesaj pinləmə

Bot @illgaarr tərəfindən yaradılmışdır 🚀
"""
            if new_status != ChatMemberStatus.ADMINISTRATOR:
                welcome_msg += "\n\n⚠️ <b>İŞLƏMƏK ÜÇÜN ADMIN VERİN!</b>"
            
            await bot.send_message(
                chat_id=event.chat.id,
                text=welcome_msg,
                parse_mode="HTML"
            )
            logger.info(f"✅ Qrup salam mesajı göndərildi: {event.chat.title}")

        except Exception as e:
            logger.error(f"❌ Qrup salam mesajı göndərilmədi: {str(e)}")
            logger.debug(f"Xəta detalları: Chat ID: {event.chat.id}, Status: {new_status}")
