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

COMMANDS_TEXT = """
🚀 <b>Əsas Komandalarım:</b>

1. Spotify/Youtube playlist linki göndər - Playlistdən mahnıları yüklə
2. <code>/music &lt;mahnı adı&gt;</code> - YouTubedən mahnı yüklə
3. <code>/report &lt;mesaj&gt;</code> - Təklif və xətaları bildir

📝 <i>Linklər və komandalar düzgün formatda olmalıdır!</i>
"""

GROUP_WELCOME_MESSAGE = """
<b>Salam! 👋 Mən DLLMasterBot</b>

• Youtube/Spotify playlistləri yükləyirəm
• Fərdi mahnıları endirirəm

🔧 <b>Tələb olunan yetkilər:</b>
1. Mesaj silmə
2. Media yükləmə
3. Mesaj pinləmə

Bot @ilgarrx tərəfindən yaradılmışdır 🚀
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
            [InlineKeyboardButton(text="💬 Dəstək qrupu", 
              url="https://t.me/dllmastercommunity")],
            [InlineKeyboardButton(text="📋 Komandalar", 
              callback_data=f"{CALLBACK_PREFIX}show_commands")],
            [InlineKeyboardButton(text="📢 Yeniliklər", 
              url="https://t.me/dllmasterupdates")],
            [InlineKeyboardButton(text="🎵 Playlistlər", 
              url="https://t.me/loudbaku")]
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
    db = context.db
    
    user_name = message.from_user.first_name or "Dostum"
    START_TEXT = f"""
<b>Salam {user_name}! 👋 Mən DLLMasterBotam!</b>

🎵 <b>Nələr edə bilirəm?</b>
• YouTube/Spotify-dan mahnılar endirirəm
• Playlistləri tam şəkildə yükləyirəm
• Sürətli və keyfiyyətli yükləmə

⚡ <b>Necə istifadə edim?</b>
1. Mahnı adı yaz və ya link göndər
2. Playlist linki at (YouTube/Spotify)
3. <code>/music &lt;mahnı adı&gt;</code> yaz

Bot @ilgarrx tərəfindən yaradılmışdır 🚀
"""
    if hasattr(message, "rebooted"):
        START_TEXT += "\n\n🔄 Bot yeniləndi!"

    if message.chat.type == "private":
        await send_start_message(message, START_TEXT, bot)
    else:
        welcome_msg = GROUP_WELCOME_MESSAGE
        if not await is_bot_admin(message.chat.id, bot):
            welcome_msg += "\n\n⚠️ <b>XƏBƏRDARLIQ:</b> Admin yetkiləri verilməyib!"
        await message.reply(welcome_msg, parse_mode="HTML")

    try:
        user = message.from_user
        db.add_user(user.id, user.username or user.first_name or "Anonim")
        if message.chat.type != "private":
            db.add_group(message.chat.id, message.chat.title)
            db.add_group_user(message.chat.id, user.id)
    except Exception as e:
        logger.error(f"DB xətası: {e}")

@router.callback_query(F.data == f"{CALLBACK_PREFIX}show_commands")
async def show_commands(callback: CallbackQuery):
    try:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Əsas menyu", 
              callback_data=f"{CALLBACK_PREFIX}back_to_start")]
        ])
        await callback.message.edit_text(COMMANDS_TEXT, parse_mode="HTML", reply_markup=keyboard)
        await callback.answer()
    except Exception as e:
        logger.error(f"Komandalar xətası: {e}")
        await callback.answer("❌ Xəta baş verdi!", show_alert=True)

@router.callback_query(F.data == f"{CALLBACK_PREFIX}back_to_start")
async def back_to_start(callback: CallbackQuery):
    context = callback.bot.data.get('app_context')
    user_name = callback.from_user.first_name or "Dostum"
    START_TEXT = f"""
<b>Salam {user_name}! 👋 Mən DLLMasterBotam!</b>

🎵 <b>Nələr edə bilirəm?</b>
• YouTube/Spotify-dan mahnılar endirirəm
• Playlistləri tam şəkildə yükləyirəm
• Sürətli və keyfiyyətli yükləmə

⚡ <b>Necə istifadə edim?</b>
1. Mahnı adı yaz və ya link göndər
2. Playlist linki at (YouTube/Spotify)
3. <code>/music &lt;mahnı adı&gt;</code> yaz

Bot @ilgarrx tərəfindən yaradılmışdır 🚀
"""
    await send_start_message(callback.message, START_TEXT, context.bot)
    await callback.answer()

@router.my_chat_member(ChatMemberUpdatedFilter(member_status_changed=True))
async def on_bot_added(event: ChatMemberUpdated):
    context = event.bot.data.get('app_context')
    bot = context.bot
    db = context.db
    
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

Bot @ilgarrx tərəfindən yaradılmışdır 🚀
"""
            if new_status != ChatMemberStatus.ADMINISTRATOR:
                welcome_msg += "\n\n⚠️ <b>İŞLƏMƏK ÜÇÜN ADMIN VERİN!</b>"
            
            await bot.send_message(
                chat_id=event.chat.id,
                text=welcome_msg,
                parse_mode="HTML"
            )
            logger.info(f"✅ Qrup salam mesajı göndərildi: {event.chat.title}")
            
            # Add to database
            db.add_group(event.chat.id, event.chat.title)
            logger.info(f"📥 Qrup DB-ə əlavə edildi: {event.chat.id}")

        except Exception as e:
            logger.error(f"❌ Qrup salam mesajı göndərilmədi: {str(e)}")
            logger.debug(f"Xəta detalları: Chat ID: {event.chat.id}, Status: {new_status}")

    # Bot removed from group
    elif (old_status in {ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR} 
          and new_status in {ChatMemberStatus.LEFT, ChatMemberStatus.KICKED}):
        logger.warning(f"🔴 Bot qrupdan çıxarıldı: {event.chat.title} ({event.chat.id})")
        try:
            db.remove_group(event.chat.id)
            logger.info(f"🗑️ Qrup DB-dən silindi: {event.chat.id}")
        except Exception as e:
            logger.error(f"❌ Qrup silinmə xətası: {e}")