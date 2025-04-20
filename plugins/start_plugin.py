from aiogram import types, Bot
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

async def setup(context):
    dp = context.dp
    db = context.db
    bot = context.bot
    
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

    async def is_bot_admin(chat_id: int) -> bool:
        try:
            bot_info = await bot.get_me()
            chat_member = await bot.get_chat_member(chat_id, bot_info.id)
            return chat_member.status in {ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR}
        except Exception as e:
            logging.error(f"Admin check error: {e}")
            return False

    async def send_start_message(message: types.Message, text: str):
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
            logging.error(f"Start message error: {e}")
            await message.answer("❌ Xəta baş verdi, zəhmət olmasa yenidən cəhd edin.")

    @dp.message(Command("start"))
    async def start_command(message: types.Message):
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
            await send_start_message(message, START_TEXT)
        else:
            welcome_msg = GROUP_WELCOME_MESSAGE
            if not await is_bot_admin(message.chat.id):
                welcome_msg += "\n\n⚠️ <b>XƏBƏRDARLIQ:</b> Admin yetkiləri verilməyib!"
            await message.reply(welcome_msg, parse_mode="HTML")

        try:
            user = message.from_user
            db.add_user(user.id, user.username or user.first_name or "Anonim")
            if message.chat.type != "private":
                db.add_group(message.chat.id, message.chat.title)
                db.add_group_user(message.chat.id, user.id)
        except Exception as e:
            logging.error(f"DB error: {e}")

    @dp.callback_query(lambda c: c.data == f"{CALLBACK_PREFIX}show_commands")
    async def show_commands(callback: CallbackQuery):
        try:
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Əsas menyu", 
                  callback_data=f"{CALLBACK_PREFIX}back_to_start")]
            ])
            await callback.message.edit_text(COMMANDS_TEXT, parse_mode="HTML", reply_markup=keyboard)
            await callback.answer()
        except Exception as e:
            logging.error(f"Commands error: {e}")
            await callback.answer("❌ Xəta baş verdi!", show_alert=True)

    @dp.callback_query(lambda c: c.data == f"{CALLBACK_PREFIX}back_to_start")
    async def back_to_start(callback: CallbackQuery):
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
        await send_start_message(callback.message, START_TEXT)
        await callback.answer()

    @dp.chat_member(ChatMemberUpdatedFilter(member_status_changed=True))
    async def on_bot_added(event: ChatMemberUpdated):
        bot_user = await bot.get_me()
        if (event.new_chat_member.user.id == bot_user.id and 
            event.new_chat_member.status == ChatMemberStatus.MEMBER):
            
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
                if not await is_bot_admin(event.chat.id):
                    welcome_msg += "\n\n⚠️ <b>İŞLƏMƏK ÜÇÜN ADMIN VERİN!</b>"
                
                await bot.send_message(event.chat.id, welcome_msg, parse_mode="HTML")
                db.add_group(event.chat.id, event.chat.title)
            except Exception as e:
                logging.error(f"Group join error: {e}")