import os
from dotenv import load_dotenv
from telethon.sync import TelegramClient

# config.env faylından API məlumatlarını oxuyuruq
load_dotenv('config.env')

API_ID = int(os.getenv("TELETHON_API_ID"))
API_HASH = os.getenv("TELETHON_API_HASH")

print("📱 Telethon avtorizasiyası başlayır...")

# Müstəqil rejimdə daxil olub userbot.session faylını yaradırıq
with TelegramClient("userbot", API_ID, API_HASH) as client:
    # Bu əmr terminalda avtomatik olaraq nömrə və kod istəyəcək
    client.start()
    print("✅ Uğurla daxil oldunuz! 'userbot.session' faylı yaradıldı.")
    