# plugins/logs_profile.py
import os
import glob
import logging
from aiogram import types, Router
from aiogram.filters import Command

logger = logging.getLogger(__name__)
router = Router()

async def setup(context):
    """Plugin setup function"""
    router.message.register(send_log_command, Command("log"))
    context.main_router.include_router(router)
    logger.info("✅ Logs plugin loaded")

async def send_log_command(message: types.Message):
    """Send the latest log file"""
    try:
        context = message.bot.data.get('app_context')
        # Only for creator and sudo users
        if message.from_user.id not in [context.creator_id] + context.sudo_users:
            await message.reply("⚠️ Bu əmr yalnız adminlər üçündür!")
            return

        # Find the latest log file
        log_dir = "bot_logs"
        log_files = glob.glob(os.path.join(log_dir, "bot(*).log*"))
        
        if not log_files:
            await message.reply("📭 Heç bir log faylı tapılmadı!")
            return

        # Select the most recently modified file
        latest_file = max(log_files, key=os.path.getmtime)
        
        # Send the document
        with open(latest_file, 'rb') as log_file:
            await message.reply_document(
                document=types.input_file.BufferedInputFile(
                    log_file.read(), 
                    filename=os.path.basename(latest_file)
                ),
                caption=f"📅 Log faylı: {os.path.basename(latest_file)}"
            )
            
    except Exception as e:
        logger.error(f"Log göndərilmədi: {str(e)}", exc_info=True)
        await message.reply(f"❌ Xəta: {str(e)}")