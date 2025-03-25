from aiogram import types
from aiogram.types import InlineQueryResultAudio, BufferedInputFile
import os
from handlers.utilities import YoutubeManager, sanitize_filename
from aiogram import Dispatcher

# Create the dispatcher and YoutubeManager instances
dp = Dispatcher()
youtube_manager = YoutubeManager(browser="firefox")


async def setup_inline_plugin(dp, youtube_manager):
    """
    Inline query və chosen inline result handler-larını təyin edən plugin.
    
    :param dp: Dispatcher
    :param youtube_manager: YoutubeManager instansı
    """
    @dp.inline_query()
    async def inline_query_handler(inline_query: types.InlineQuery):
        """
        İnline query üçün handler. İstifadəçinin yazdığı sorğuya uyğun mahnı nəticələrini göstərir.
        """
        search_query = inline_query.query
        if not search_query:
            return

        # YouTube-dan axtarış nəticələrini əldə et
        results = await youtube_manager.youtube_search(search_query)
        if not results:
            return

        # Inline nəticələri hazırla
        inline_results = []
        for i, result in enumerate(results[:5]):
            inline_results.append(
                InlineQueryResultAudio(
                    id=str(i),
                    audio_url=result['url'],
                    title=result['title'],
                    performer="YouTube",
                    audio_duration=int(result.get('raw_duration', 0))
                ))

        # Nəticələri istifadəçiyə göndər
        await inline_query.answer(inline_results, cache_time=1)

    @dp.chosen_inline_result()
    async def chosen_inline_result_handler(chosen_result: types.ChosenInlineResult):
        """
        İstifadəçi inline nəticələrdən birini seçdikdə işləyən handler.
        Seçilən mahnını yükləyib istifadəçiyə göndərir.
        """
        user_id = chosen_result.from_user.id
        result_id = int(chosen_result.result_id)
        results = await youtube_manager.youtube_search(chosen_result.query)

        # Nəticələri yoxla
        if not results or result_id >= len(results):
            return

        selected = results[result_id]

        # Mahnı yüklənir mesajı göndər
        loading_message = await chosen_result.bot.send_message(
            chat_id=chosen_result.from_user.id,
            text="Mahnı yüklənir..."
        )

        # Mahnını yüklə
        file_path = await youtube_manager.download_track(
            selected['url'], 
            sanitize_filename(selected['title'])
        )

        # Mahnını istifadəçiyə göndər
        with open(file_path, 'rb') as f:
            await chosen_result.bot.send_audio(
                chat_id=chosen_result.from_user.id,
                audio=BufferedInputFile(f.read(), filename=os.path.basename(file_path)),
                title=selected.get('title', '')[:64],
                performer="YouTube",
                duration=int(selected.get('raw_duration', 0))
            )

        # Yükləmə mesajını sil
        await chosen_result.bot.delete_message(chat_id=chosen_result.from_user.id, message_id=loading_message.message_id)

        # Yüklənən faylı sil
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
            except:
                pass