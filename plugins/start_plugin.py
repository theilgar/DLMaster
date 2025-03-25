from aiogram import types, Bot
from aiogram.filters import Command
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

async def register_start_handlers(dp, context):
    CALLBACK_PREFIX = "start_"
    
    COMMANDS_TEXT = """
🚀 <b>Əsas Komandalarım:</b>

1. <code>&lt;spotify playlist link&gt;</code> - Spotify playlistindən mahnıları yüklə
2. <code>&lt;youtube playlist link&gt;</code> - YouTube playlistindən mahnıları yüklə
3. <code>/music &lt;mahnı adı&gt;</code> - YouTubedən mahnı yüklə
4. <code>/report &lt;mesaj&gt;</code> - Təklif və xətaları adminə çatdır

📝 <i>Linklər və komandalar düzgün formatda olmalıdır!</i>
"""

    @dp.callback_query(lambda c: c.data == f"{CALLBACK_PREFIX}show_commands")
    async def show_commands_handler(callback_query: CallbackQuery):
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text="🔙 Geri qayıt", 
                callback_data=f"{CALLBACK_PREFIX}back_to_start"
            )]
        ])
        
        # Köhnə mesajı silib yenisini göndərək
        try:
            await callback_query.message.delete()
        except:
            pass
            
        await callback_query.message.answer(
            COMMANDS_TEXT,
            parse_mode="HTML",
            reply_markup=keyboard
        )
        await callback_query.answer()

    @dp.callback_query(lambda c: c.data == f"{CALLBACK_PREFIX}back_to_start")
    async def back_to_start_handler(callback_query: CallbackQuery, bot: Bot):
        START_TEXT = """
<b>Salam! 👋 Mən DLLMasterBot</b>

Youtubedən və Spotifydan playlist yükləməyi bacarıram

Bot @ilgarrx tərəfindən yaradılmışdır 🚀
"""
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
                    )
                ]
            ]
        )
        
        # Köhnə mesajı silib yenisini göndərək
        try:
            await callback_query.message.delete()
        except:
            pass
            
        # GIF göndərək
        gif_path = os.path.join(Path(__file__).parent, "patrick.gif")
        if os.path.exists(gif_path):
            gif = FSInputFile(gif_path)
            await callback_query.message.answer_animation(
                gif,
                caption=START_TEXT,
                parse_mode="HTML",
                reply_markup=keyboard
            )
        else:
            await callback_query.message.answer(
                START_TEXT,
                parse_mode="HTML",
                reply_markup=keyboard
            )
        
        await callback_query.answer()

    @dp.message(Command("start"))
    async def start_command(message: types.Message, bot: Bot):
        START_TEXT = """
<b>Salam! 👋 Mən DLLMasterBot</b>

Youtubedən və Spotifydan playlist yükləməyi bacarıram

Bot @ilgarrx tərəfindən yaradılmışdır 🚀
"""
        if hasattr(message, "rebooted"):
            START_TEXT += "\n\n✅ Bot uğurla yenidən başladıldı."

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
                    )
                ]
            ]
        )

        gif_path = os.path.join(Path(__file__).parent, "patrick.gif")
        try:
            if os.path.exists(gif_path):
                gif = FSInputFile(gif_path)
                await message.reply_animation(
                    gif,
                    caption=START_TEXT,
                    parse_mode="HTML",
                    reply_markup=keyboard
                )
            else:
                await message.reply(
                    START_TEXT,
                    parse_mode="HTML",
                    reply_markup=keyboard
                )
        except Exception as e:
            print(f"Start mesajı göndərilərkən xəta: {e}")
            await message.reply(START_TEXT, parse_mode="HTML")

        try:
            user_id = message.from_user.id
            username = message.from_user.username or "Naməlum"
            context.db.add_user(user_id, username)
            context.db.increment_user_message_count(user_id)

            if message.chat.type != "private":
                group_id = message.chat.id
                group_name = message.chat.title
                context.db.add_group(group_id, group_name)
                context.db.add_group_user(group_id, user_id)

            context.db.add_log(
                level="INFO",
                message=f"User {username} başladı.",
                user_id=user_id,
                group_id=message.chat.id if message.chat.type != "private" else None
            )
        except Exception as e:
            print(f"Verilənlər bazası xətası: {e}")

    @dp.chat_member()
    async def on_bot_added_to_group(event: ChatMemberUpdated):
        if event.new_chat_member.status == ChatMemberStatus.MEMBER and event.new_chat_member.user.is_bot:
            welcome_message = """
<b>Salam! 👋 Mən DLLMasterBot</b>

Youtubedən və Spotifydan playlist yükləməyi bacarıram

Qrupda işləmək üçün aşağıdakı yetkiləri verməyiniz xahiş olunur:
1. <b>Mesaj silmə</b> yetkisi
2. <b>Mesaj pinləmə</b> yetkisi
"""
            await event.answer(welcome_message, parse_mode="HTML")

            try:
                group_id = event.chat.id
                group_name = event.chat.title
                context.db.add_group(group_id, group_name)
                context.db.increment_group_bot_usage(group_id)
                context.db.add_log(
                    level="INFO", 
                    message=f"Bot {group_name} qrupuna əlavə edildi.", 
                    group_id=group_id
                )
            except Exception as e:
                print(f"Qrup əlavə edilərkən xəta: {e}")
