from aiogram import types
from aiogram.filters import Command
from aiogram.types import ChatMemberUpdated
from aiogram.enums import ChatMemberStatus

async def register_start_handlers(dp, context):
    @dp.message(Command("start"))
    async def start_command(message: types.Message):
        # Bot haqqında məlumat
        bot_info = (
            "**Salam! 👋 Mən DLLMasterBot**\n"
            "Youtubedən və Spotifydan playlist yükləməyi bacarıram\n\n"
            "🚀 **Əsas Komandalarım:**\n"
            "1. `<spotify playlist link>` - Spotify playlistindən mahnıları yüklə.\n"
            "2. `<youtube playlist link>` - YouTube playlistindən mahnıları yüklə.\n"
            "3. `/music <mahnı adı>` - YouTubedən mahnı yüklə.\n\n"
            "📝 Playlist linkləri düzgün olmalıdır.\n\n"
            "Bot @ilgarrx tərəfindən yaradılmışdır 🚀"
        )

        # Əgər bot yenidən başladılıbsa, bu mesajı əlavə edirik
        if hasattr(message, "rebooted"):
            bot_info += "\n\n✅ Bot uğurla yenidən başladıldı."

        # İstifadəçiyə mesaj göndər
        await message.reply(bot_info, parse_mode="Markdown")

        # İstifadəçi məlumatlarını verilənlər bazasına əlavə edirik
        user_id = message.from_user.id
        username = message.from_user.username or "Naməlum"
        context.db.add_user(user_id, username)
        context.db.increment_user_message_count(user_id)

        # Əgər komanda qrupda işlədilibsə, qrup məlumatlarını da əlavə edirik
        if message.chat.type != "private":
            group_id = message.chat.id
            group_name = message.chat.title
            context.db.add_group(group_id, group_name)
            context.db.add_group_user(group_id, user_id)

        # Loglaşdırma
        context.db.add_log(
            level="INFO",
            message=f"User {username} başladı.",
            user_id=user_id,
            group_id=message.chat.id if message.chat.type != "private" else None
        )

    @dp.chat_member()
    async def on_bot_added_to_group(event: ChatMemberUpdated):
        # Yalnız bot qrupa əlavə edildikdə işlə
        if event.new_chat_member.status == ChatMemberStatus.MEMBER and event.new_chat_member.user.is_bot:
            # Qrupa mesaj göndər
            welcome_message = (
                "**Salam! 👋 Mən DLLMasterBot**\n\n"
                "Youtubedən və Spotifydan playlist yükləməyi bacarıram\n\n"
                "🚀 **Əsas Komandalarım:**\n"
                "1. `<spotify playlist link>` - Spotify playlistindən mahnıları yüklə.\n"
                "2. `<youtube playlist link>` - YouTube playlistindən mahnıları yüklə.\n"
                "3. `/music <mahnı adı>` - YouTubedən mahnı yüklə.\n\n"
                "📝 Playlist linkləri düzgün olmalıdır.\n\n"
                "Qrupda işləmək üçün aşağıdakı yetkiləri verməyiniz xahiş olunur:\n"
                "1. **Mesaj silmə** yetkisi - Mesajları idarə etmək üçün.\n"
                "2. **Mesaj pinləmə** yetkisi - Mesajların istifadəçilərə asanlıqla çıxışı təmin etmək üçün.\n\n"
                "Bu yetkilər olmadan düzgün işləyə bilmərəm. Zəhmət olmasa, yetkiləri verin!"
            )

            # Qrupa mesaj göndər
            await event.answer(welcome_message, parse_mode="Markdown")

            # Qrup məlumatlarını verilənlər bazasına əlavə edirik
            group_id = event.chat.id
            group_name = event.chat.title
            context.db.add_group(group_id, group_name)
            context.db.increment_group_bot_usage(group_id)
            context.db.add_log(level="INFO", message=f"Bot {group_name} qrupuna əlavə edildi.", group_id=group_id)