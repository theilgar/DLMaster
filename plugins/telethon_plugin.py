"""
📱 Telethon Userbot İnteqrasiyası — depo üçün (şəxsi hesab)

Bu plugin şəxsi Telegram hesabınızı (userbot) işə salır və 
gizli/açıq kanallardan, qruplardan audioları tutub depo kanalına yönləndirir.

Tələblər:
  - pip install telethon
  - config.env faylında TELETHON_API_ID və TELETHON_API_HASH
"""
import os
import asyncio
import logging
from telethon import TelegramClient, events
from telethon.errors import SessionPasswordNeededError
from aiogram import F, types
from aiogram.filters import Command

from core.audio_cache import depo_chat_id #[span_2](start_span)[span_2](end_span)
from core.database import get_db #[span_3](start_span)[span_3](end_span)

logger = logging.getLogger(__name__)

# Qeyd: Bu məlumatları my.telegram.org saytından alıb config.env faylına əlavə edin[span_4](start_span)[span_4](end_span)
API_ID = os.getenv("TELETHON_API_ID")
API_HASH = os.getenv("TELETHON_API_HASH")
SESSION_FILE = "userbot.session"

def setup(context):
    dp = context.dp
    bot = context.bot
    
    if not API_ID or not API_HASH:
        logger.error("❌ TELETHON_API_ID və ya TELETHON_API_HASH tapılmadı. Telethon işə düşməyəcək.")
        return

    # Telethon client-in yaradılması
    client = TelegramClient(SESSION_FILE, int(API_ID), API_HASH)
    context.telethon_client = client

    def is_creator(uid: int) -> bool:
        return bool(context.creator_id) and uid == context.creator_id #[span_5](start_span)[span_5](end_span)

    async def start_telethon():
        """Telethon müştərisini arxa fonda işə salır"""
        try:
            logger.info("📱 Telethon userbot qoşulur...")
            await client.connect()
            
            if not await client.is_user_authorized():
                logger.warning("⚠️ Userbot avtorizasiya olunmayıb! Zəhmət olmasa terminalda və ya xüsusi skriptlə giriş edin.")
                # Bot vasitəsilə OTP qəbul etmək üçün əlavə məntiq yazıla bilər
                return
                
            logger.info("✅ Telethon userbot uğurla qoşuldu!")
        except Exception as e:
            logger.error(f"❌ Telethon xətası: {e}")

    # Plugin yüklənəndə Telethon-u aiogram ilə eyni loop-da işə salırıq[span_6](start_span)[span_6](end_span)
    context.telethon_task = asyncio.create_task(start_telethon())

    # ── 1) Yeni audiolara qulaq asmaq və depoya göndərmək ──
    @client.on(events.NewMessage(func=lambda e: e.audio))
    async def on_new_audio(event):
        """Userbot-un olduğu hər hansı bir yerdə yeni audio gələndə onu depoya yönləndirir."""
        
        # Yalnız müəyyən kanalları izləmək istəyirsinizsə, burada ID-ləri yoxlayın:
        # if event.chat_id not in [-100xxxxxxxxx, -100yyyyyyyyy]: return
        
        audio = event.audio
        title = next((attr.title for attr in audio.attributes if hasattr(attr, 'title')), "Naməlum")
        performer = next((attr.performer for attr in audio.attributes if hasattr(attr, 'performer')), "")
        
        target_depo_id = await depo_chat_id(bot) # Botun depo kanalının ID-si[span_7](start_span)[span_7](end_span)
        
        if not target_depo_id:
            logger.warning("📦 Depo kanalı tapılmadı, audio ötürüldü.")
            return

        try:
            # Userbot faylı birbaşa depo kanalına yönləndirir (əgər admin/yazma icazəsi varsa)
            # Və ya botun özünə göndərə bilər ki, bot onu emal etsin.
            # Burada faylı bota yönləndiririk ki, `audio_editor_plugin.py` və ya `depo_filler` onu tutsun[span_8](start_span)[span_8](end_span)[span_9](start_span)[span_9](end_span)
            
            bot_info = await bot.me()
            await client.forward_messages(bot_info.username, messages=event.message)
            logger.info(f"📥 {performer} - {title} depolanmaq üçün bota yönləndirildi.")
            
        except Exception as e:
            logger.error(f"Userbot-dan yönləndirmə xətası: {e}")

    # ── 2) Nəzarət komandaları (Yalnız Creator üçün) ──
    @dp.message(Command("userbot"), F.from_user.id == context.creator_id)
    async def userbot_status(message: types.Message):
        if not await client.is_user_authorized():
            await message.answer("🔴 <b>Userbot aktiv deyil!</b>\n<i>Əvvəlcə serverdə sessiya yaradın.</i>", parse_mode="HTML")
            return
            
        me = await client.get_me()
        await message.answer(
            f"🟢 <b>Userbot Aktivdir!</b>\n"
            f"👤 <b>Hesab:</b> {me.first_name} (@{me.username or 'yoxdur'})\n"
            f"📦 <i>Userbot vasitəsilə gizli qruplardan audiolara çıxış təmin edilir.</i>",
            parse_mode="HTML"
        )

async def teardown(context):
    """Bot dayandırılanda və ya plugin yenidən yüklənəndə userbot-u təhlükəsiz söndürür[span_10](start_span)[span_10](end_span)[span_11](start_span)[span_11](end_span)[span_12](start_span)[span_12](end_span)."""
    client = getattr(context, "telethon_client", None)
    if client and client.is_connected():
        await client.disconnect()
        logger.info("🛑 Telethon userbot dayandırıldı.")
        