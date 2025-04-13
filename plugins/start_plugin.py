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
import os
from pathlib import Path

async def setup(context):
    """Plugin-in async qurulum funksiyası"""
    
    dp = context.dp
    db = context.db
    bot = context.bot
    
    CALLBACK_PREFIX = "start_"
    
    COMMANDS_TEXT = """
🚀 <b>Əsas Komandalarım:</b>

1. <code>&lt;spotify playlist link&gt;</code> - Spotify playlistindən mahnıları yüklə
2. <code>&lt;youtube playlist link&gt;</code> - YouTube playlistindən mahnıları yüklə
3. <code>/music &lt;mahnı adı&gt;</code> - YouTubedən mahnı yüklə
4. <code>/report &lt;mesaj&gt;</code> - Təklif və xətaları adminə çatdır

📝 <i>Linklər və komandalar düzgün formatda olmalıdır!</i>
"""

    GROUP_WELCOME_MESSAGE = """
<b>Salam! 👋 Mən DLLMasterBot</b>

Youtubedən və Spotifydan playlist yükləməyi bacarıram

Qrupda işləmək üçün aşağıdakı yetkiləri verməyiniz xahiş olunur:
1. <b>Mesaj silmə</b> yetkisi
2. <b>Mesaj pinləmə</b> yetkisi
3. <b>Media yükləmə</b> yetkisi

Bot @ilgarrx tərəfindən yaradılmışdır 🚀
"""

    @dp.message(Command("start"))
    async def start_command(message: types.Message):
        """Əsas start komandası handleri"""
        START_TEXT = """
<b>Salam! 👋 Mən DLLMasterBot</b>

Youtubedən və Spotifydan playlist yükləməyi bacarıram

Bot @ilgarrx tərəfindən yaradılmışdır 🚀
"""
        if hasattr(message, "rebooted"):
            START_TEXT += "\n\n✅ Bot uğurla yenidən başladıldı."

        if message.chat.type == "private":
            await send_start_message(message, START_TEXT)
        else:
            is_admin = await is_bot_admin(message.chat.id)
            welcome_msg = GROUP_WELCOME_MESSAGE
            
            if not is_admin:
                welcome_msg += "\n\n⚠️ <b>XƏBƏRDARLIQ:</b> Mənə admin yetkiləri verilməyib! Yuxarıdakı yetkiləri verməyiniz xahiş olunur."
            
            await message.reply(welcome_msg, parse_mode="HTML")

        try:
            user_id = message.from_user.id
            username = message.from_user.username or "Naməlum"
            db.add_user(user_id, username)
            db.increment_user_message_count(user_id)

            if message.chat.type != "private":
                group_id = message.chat.id
                group_name = message.chat.title
                db.add_group(group_id, group_name)
                db.add_group_user(group_id, user_id)
                db.increment_group_bot_usage(group_id)
        except Exception:
            pass

    async def send_start_message(message: types.Message, text: str):
        """Start mesajını göndərən köməkçi funksiya"""
        bot_username = (await bot.me()).username
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🤖 Botu qrupa əlavə et",
                        url=f"https://t.me/{bot_username}?startgroup=true"
                    ),
                    InlineKeyboardButton(
                        text="💬 Dəstək qrupu",
                        url="https://t.me/dllmastercommunity"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="📋 Komandaları göstər",
                        callback_data=f"{CALLBACK_PREFIX}show_commands"
                    ),
                    InlineKeyboardButton(
                        text="❤️ Donate et",
                        url="https://kofe.al/@ilgarrrx"
                    )
                ]
            ]
        )
        
        try:
            await message.delete()
        except Exception:
            pass
            
        gif_path = os.path.join(Path(__file__).parent, "patrick.gif")
        if os.path.exists(gif_path):
            try:
                gif = FSInputFile(gif_path)
                await message.answer_animation(
                    gif,
                    caption=text,
                    parse_mode="HTML",
                    reply_markup=keyboard
                )
                return
            except Exception:
                pass
        
        await message.answer(
            text,
            parse_mode="HTML",
            reply_markup=keyboard
        )

    @dp.callback_query(lambda c: c.data == f"{CALLBACK_PREFIX}show_commands")
    async def show_commands_handler(callback_query: CallbackQuery):
        """Komandalar siyahısını göstərən handler"""
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text="🔙 Geri qayıt", 
                callback_data=f"{CALLBACK_PREFIX}back_to_start"
            )]
        ])
        
        try:
            await callback_query.message.edit_text(
                COMMANDS_TEXT,
                parse_mode="HTML",
                reply_markup=keyboard
            )
        except Exception:
            await callback_query.message.answer(
                COMMANDS_TEXT,
                parse_mode="HTML",
                reply_markup=keyboard
            )
        
        try:
            db.log_command_usage(
                command="show_commands",
                user_id=callback_query.from_user.id
            )
        except Exception:
            pass
        
        await callback_query.answer()

    @dp.callback_query(lambda c: c.data == f"{CALLBACK_PREFIX}back_to_start")
    async def back_to_start_handler(callback_query: CallbackQuery):
        """Əsas səhifəyə qayıtma handleri"""
        START_TEXT = """
<b>Salam! 👋 Mən DLLMasterBot</b>

Youtubedən və Spotifydan playlist yükləməyi bacarıram

Bot @ilgarrx tərəfindən yaradılmışdır 🚀
"""
        await send_start_message(callback_query.message, START_TEXT)
        await callback_query.answer()

    @dp.chat_member(ChatMemberUpdatedFilter(member_status_changed=True))
    async def on_bot_added_to_group(event: ChatMemberUpdated):
        """Bot qrupa əlavə edildikdə işə düşən handler"""
        bot_user = await bot.me()
        if (event.new_chat_member.status == ChatMemberStatus.MEMBER and 
            event.new_chat_member.user.is_bot and 
            event.new_chat_member.user.id == bot_user.id):
            
            try:
                is_admin = await is_bot_admin(event.chat.id)
                
                welcome_msg = f"""
<b>Salam {event.chat.title}! 👋 Mən DLLMasterBot</b>

Youtubedən və Spotifydan playlist yükləməyi bacarıram
"""
                if not is_admin:
                    welcome_msg += """
⚠️ <b>XƏBƏRDARLIQ:</b>
Mənə admin yetkiləri verilməyib! Aşağıdakı yetkiləri verməyiniz xahiş olunur:
1. Mesaj silmə
2. Media yükləmə
3. Mesaj pinləmə

Əks halda düzgün işləyə bilmərəm 😔
"""
                
                welcome_msg += "\n\nBot @ilgarrx tərəfindən yaradılmışdır 🚀"
                
                # Fixed: Using bot.send_message instead of event.answer
                await bot.send_message(
                    chat_id=event.chat.id,
                    text=welcome_msg,
                    parse_mode="HTML"
                )

                # Log to database
                group_id = event.chat.id
                group_name = event.chat.title
                db.add_group(group_id, group_name)
                db.increment_group_bot_usage(group_id)

            except Exception:
                pass