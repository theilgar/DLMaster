import os
import glob
import logging
from aiogram import types
from aiogram.filters import Command
from aiogram import Router

logger = logging.getLogger(__name__)
router = Router()

async def setup(context):
    router.message.register(send_log_command, Command("log"))
    context.dp.include_router(router)

async def send_log_command(message: types.Message):
    """Son log faylını göndər"""
    try:
        context = message.bot.data.get('app_context')
        # Yalnız creator və sudo istifadəçilər üçün icazə
        if message.from_user.id not in [context.creator_id] + context.sudo_users:
            await message.reply("⚠️ Bu əmr yalnız adminlər üçündür!")
            return

        # Ən son log faylını tap
        log_dir = "bot_logs"
        log_files = glob.glob(os.path.join(log_dir, "bot(*).log*"))
        
        if not log_files:
            await message.reply("📭 Heç bir log faylı tapılmadı!")
            return

        # Ən son dəyişdirilmiş faylı seç
        latest_file = max(log_files, key=os.path.getmtime)
        
        # Sənədi göndər
        with open(latest_file, 'rb') as log_file:
            await message.reply_document(
                document=types.input_file.BufferedInputFile(log_file.read(), filename=os.path.basename(latest_file)),
                caption=f"📅 Log faylı: {os.path.basename(latest_file)}"
            )
            
    except Exception as e:
        logger.error(f"Log göndərilmədi: {str(e)}", exc_info=True)
        await message.reply(f"❌ Xəta: {str(e)}")