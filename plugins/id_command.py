import logging
from aiogram import types, F
from aiogram.filters import Command

logger = logging.getLogger(__name__)

async def id_command_handler(message: types.Message, command: Command):
    """Hesaba mesaj göndərmək üçün link yaradır"""
    # Check if command has arguments
    if not command.args:
        await message.reply("⚠️ İstifadə: `/id <hesab_id>` (Nümunə: `/id 123456789`)")
        return

    # İstifadəçi ID-ni əldə et
    try:
        target_id = command.args.strip().split()[0]  # Get first argument
        if not target_id.isdigit():
            raise ValueError("Yanlış ID formatı")
    except (IndexError, ValueError):
        await message.reply("⚠️ Yanlış ID formatı! Yalnız rəqəmlər istifadə edin.")
        return

    # Telegram deep link yarat
    deep_link = f"tg://user?id={target_id}"
    response = (
        f"🔗 Bu link ilə ID **{target_id}** hesabına mesaj yaza bilərsən:\n"
        f"👉 {deep_link}\n\n"
        "📱 **Diqqət:** Linki açmaq üçün Telegram app istifadə edin!"
    )

    await message.reply(response, parse_mode="Markdown")

async def setup(context):
    """Plugin-i bot-a yüklə"""
    # Handler-ı əlavə et: /id <hesab_id>
    context.dp.message.register(
        id_command_handler,
        Command("id"),
        F.chat.type.in_({"private", "group", "supergroup"})
    )
    logger.info("✅ id_command plugin-i yükləndi")