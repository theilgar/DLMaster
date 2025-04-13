import os
import logging
import asyncio
from urllib.parse import urlparse, parse_qs
from aiogram import types, F
from aiogram.types import BufferedInputFile, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.types import InlineQueryResultAudio
from core.utilities import YoutubeManager, sanitize_filename, format_duration

logger = logging.getLogger(__name__)

class YouTubeHelper:
    @staticmethod
    def extract_video_id(url: str) -> str:
        if 'youtu.be' in url:
            return url.split('/')[-1].split('?')[0]
        parsed = urlparse(url)
        if parsed.netloc in ['youtube.com', 'www.youtube.com']:
            path_segments = parsed.path.split('/')
            if len(path_segments) > 2 and path_segments[1] in ['embed', 'v']:
                return path_segments[2]
            query = parse_qs(parsed.query)
            return query.get('v', [None])[0]
        return None

async def show_progress(progress_msg: types.Message, interval: float = 5.0):
    dots = 0
    while True:
        text = f"⏳ Yüklənir{' .' * (dots % 4)}"
        try:
            await progress_msg.edit_text(text)
        except:
            break
        dots += 1
        await asyncio.sleep(interval)

async def inline_youtube_query(inline_query: types.InlineQuery):
    app_context = inline_query.bot.data.get('app_context')
    if not app_context or not hasattr(app_context, 'youtube_manager'):
        await inline_query.answer([])
        return
    
    query = inline_query.query.strip()
    if not query:
        await inline_query.answer([])
        return
    
    try:
        results = await app_context.youtube_manager.youtube_search(query)
        inline_results = []
        
        for idx, entry in enumerate(results[:10]):
            video_id = YouTubeHelper.extract_video_id(entry['url'])
            if not video_id:
                continue
                
            inline_results.append(
                InlineQueryResultAudio(
                    id=f"{inline_query.from_user.id}_{idx}",
                    audio_url=entry['url'],
                    title=entry.get('title', 'No Title')[:64],
                    performer="YouTube",
                    audio_duration=int(entry.get('raw_duration', 0)),
                    caption=f"🎵 {entry['title'][:50]}...\n⏳ Müddət: {entry['duration']}",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(
                            text="⬇️ Yüklə",
                            callback_data=f"dl_{video_id}"
                        )]
                    ])
                )
            )
        
        await inline_query.answer(inline_results, cache_time=1)
        
    except Exception as e:
        logger.error(f"Inline query error: {e}", exc_info=True)
        await inline_query.answer([{
            'type': 'article',
            'id': 'error',
            'title': 'Xəta baş verdi',
            'input_message_content': {
                'message_text': f'Axtarış zamanı xəta: {str(e)[:200]}'
            }
        }])

async def handle_youtube_url(message: types.Message):
    app_context = message.bot.data.get('app_context')
    if not app_context or not hasattr(app_context, 'youtube_manager'):
        return
    
    url = message.text.strip()
    video_id = YouTubeHelper.extract_video_id(url)
    if not video_id:
        return
    
    processing_msg = None
    try:
        processing_msg = await message.reply("⏳ Mahnı yüklənir...")
        progress_task = asyncio.create_task(show_progress(processing_msg))
        
        file_path = await app_context.youtube_manager.download_track(
            url, 
            sanitize_filename(video_id))
        
        progress_task.cancel()
        await processing_msg.edit_text("✅ Fayl yoxlanılır...")
        
        with open(file_path, 'rb') as f:
            audio_data = f.read()
            if len(audio_data) < 1024:
                raise ValueError("Qüsurlu audio faylı")
        
        audio_file = BufferedInputFile(audio_data, filename=f"{video_id}.m4a")
        await message.reply_audio(
            audio=audio_file,
            title=f"YouTube Video {video_id}",
            performer="YouTube",
            caption=f"🔗 {url}"
        )
        
        await processing_msg.delete()
        os.remove(file_path)
        
    except Exception as e:
        logger.error(f"Download error: {e}", exc_info=True)
        if processing_msg:
            await processing_msg.edit_text(f"❌ Xəta: {str(e)[:200]}")
            await asyncio.sleep(10)
            await processing_msg.delete()
        if 'file_path' in locals() and os.path.exists(file_path):
            os.remove(file_path)

async def handle_inline_download(callback: types.CallbackQuery):
    app_context = callback.bot.data.get('app_context')
    if not app_context or not hasattr(app_context, 'youtube_manager'):
        await callback.answer("Bot konfiqurasiyasında xəta", show_alert=True)
        return
    
    try:
        video_id = callback.data.split("dl_")[1]
        entry_url = f"https://youtu.be/{video_id}"
        
        processing_msg = await callback.message.answer("⏳ Mahnı yüklənir...")
        progress_task = asyncio.create_task(show_progress(processing_msg))
        
        file_path = await app_context.youtube_manager.download_track(
            entry_url, 
            sanitize_filename(video_id))
        
        progress_task.cancel()
        await processing_msg.edit_text("✅ Fayl yoxlanılır...")
        
        with open(file_path, 'rb') as f:
            audio_data = f.read()
            if len(audio_data) < 1024:
                raise ValueError("Qüsurlu audio faylı")
        
        audio_file = BufferedInputFile(audio_data, filename=f"{video_id}.m4a")
        await callback.message.answer_audio(
            audio=audio_file,
            title=f"YouTube Video {video_id}",
            performer="YouTube"
        )
        
        await processing_msg.delete()
        os.remove(file_path)
        
    except Exception as e:
        logger.error(f"Inline download error: {e}", exc_info=True)
        if processing_msg:
            await processing_msg.edit_text(f"❌ Xəta: {str(e)[:200]}")
            await asyncio.sleep(10)
            await processing_msg.delete()
        if 'file_path' in locals() and os.path.exists(file_path):
            os.remove(file_path)

def setup(context):
    dp = context.dp
    dp.inline_query.register(inline_youtube_query)
    dp.message.register(handle_youtube_url, F.text.regexp(r'(https?://)?(www\.)?(youtube|youtu)\.(com|be)'))
    dp.callback_query.register(handle_inline_download, F.data.startswith("dl_"))
    
    # YouTubeManager-i başlat
    if not hasattr(context, 'youtube_manager'):
        context.youtube_manager = YoutubeManager(browser="chrome")
    
    logger.info("✅ YouTube plugin aktiv oldu")