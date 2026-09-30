from aiogram import types, F
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    BufferedInputFile, FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton,
    InlineQueryResultArticle, InputTextMessageContent, InputMediaAudio,
)
from core.utilities import sanitize_filename
from core.youtube_handler import YoutubeManager, DownloadCancelled
from core.database import log_download, caption_for_user
import asyncio
import threading
import os
import logging
import re
import time
import aiohttp
from html import escape
from datetime import datetime

logger = logging.getLogger(__name__)

# ───────────────────────── Odesli / Songlink ─────────────────────────
ODESLI_API = "https://api.song.link/v1-alpha.1/links"
odesli_cache = {}
odesli_disabled_until = 0  # 401 gəlsə (açar tələb olunur) 30 dəq. sorğu göndərmə
# Ad/artist bu platformaların məlumatından götürülür (YouTube-un özününkü "Official Video" və s. ilə gəlir)
META_PLATFORMS = ("spotify", "appleMusic", "itunes", "deezer", "tidal", "amazonMusic", "napster", "pandora")


async def get_songlink(url: str):
    """Odesli-dən universal səhifə və platforma linklərini qaytarır (uğursuz olsa None)."""
    global odesli_disabled_until
    if url in odesli_cache:
        return odesli_cache[url]
    if time.time() < odesli_disabled_until and not os.getenv("ODESLI_API_KEY"):
        return None

    params = {"url": url, "songIfSingle": "true"}
    api_key = os.getenv("ODESLI_API_KEY")  # könüllü; açarsız limit ~10 sorğu/dəq
    if api_key:
        params["key"] = api_key

    try:
        timeout = aiohttp.ClientTimeout(total=8)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(ODESLI_API, params=params) as r:
                if r.status in (401, 403):
                    odesli_disabled_until = time.time() + 1800
                    logger.warning("Odesli API açar tələb edir (ODESLI_API_KEY). Birbaşa song.link/<url> linki istifadə olunacaq.")
                    return None
                if r.status != 200:
                    logger.warning(f"Odesli status {r.status} ({url})")
                    return None
                data = await r.json()
    except Exception as e:
        logger.warning(f"Odesli xətası: {e}")
        return None

    title, artist = odesli_meta(data)
    result = {
        "page": data.get("pageUrl") or f"https://song.link/{url}",
        "links": {k: v.get("url") for k, v in data.get("linksByPlatform", {}).items()},
        "title": title,
        "artist": artist,
    }
    odesli_cache[url] = result
    return result


def build_caption(url: str, sl=None, user_id=None):
    """via @dllmasterbot | song.link  (song.link — bütün platformalar səhifəsinə link). Söndürülübsə None."""
    if not caption_for_user(user_id):   # Free: həmişə · Premium: heç vaxt · Creator: /menu
        return None
    page = sl["page"] if sl else f"https://song.link/{url}"
    return f'<i>via @dllmasterbot | <a href="{escape(page, quote=True)}">song.link</a></i>'


def build_keyboard(url: str, sl=None, owner_id: int = 0):
    rows = []
    vid = video_id_from_url(url)
    first = []
    if vid:
        # Elə həmin çatda inline rejimi "mix:<id>" sorğusu ilə açır
        first.append(InlineKeyboardButton(text="🔀 Mix", switch_inline_query_current_chat=f"mix:{vid}"))
    # Düymələri gizlətmək (yalnız mahnını istəyən və ya creator)
    first.append(InlineKeyboardButton(text="🙈 Gizlət", callback_data=f"hidekb:{owner_id}"))
    rows.append(first)
    if sl:
        links = sl["links"]
        row = []
        for key, label in (("spotify", "Spotify"), ("appleMusic", "Apple Music"), ("youtubeMusic", "YT Music")):
            if links.get(key):
                row.append(InlineKeyboardButton(text=label, url=links[key]))
        if row:
            rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def stop_keyboard(callback_data: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="⏹ Yükləməni dayandır", callback_data=callback_data)
    ]])


def odesli_meta(data: dict):
    """Odesli cavabından musiqi platformasındakı rəsmi ad və artisti çıxarır."""
    entities = data.get("entitiesByUniqueId") or {}
    links = data.get("linksByPlatform") or {}
    for platform in META_PLATFORMS:
        uid = (links.get(platform) or {}).get("entityUniqueId")
        ent = entities.get(uid) if uid else None
        if ent and ent.get("title") and ent.get("artistName"):
            return ent["title"].strip(), ent["artistName"].strip()
    return None, None


# Mötərizədə bu sözlərdən biri varsa, həmin hissə atılır: (Official Music Video), [Lyrics], (HD) ...
_NOISE_IN_BRACKETS = re.compile(
    r"\b(official|video|audio|lyrics?|visuali[sz]er|mv|m/v|hd|hq|4k|klip|clip|rəsmi|премьера|клип)\b", re.I
)
_BRACKETS = re.compile(r"\s*[\(\[【]([^\)\]】]*)[\)\]】]")
_TRAILING_NOISE = re.compile(
    r"(?:\s*[-–—|]\s*|\s+)(?:official\s+)?(?:music\s+|lyrics?\s+)?(?:video|audio)(?:\s+clip)?\s*$"
    r"|(?:\s*[-–—|]\s*|\s+)official\s*$",
    re.I,
)


def clean_youtube_title(title: str) -> str:
    t = _BRACKETS.sub(lambda mt: "" if _NOISE_IN_BRACKETS.search(mt.group(1)) else mt.group(0), title)
    t = re.split(r"\s+(?:\||//)\s+", t, maxsplit=1)[0]      # "... | Albom adı" hissəsi
    for _ in range(2):
        t = _TRAILING_NOISE.sub("", t)
    t = re.sub(r"\s{2,}", " ", t).strip(" -–—|")
    return t or title


def parse_title(title: str):
    """YouTube adını təmizləyib (artist, ad) kimi ayırır."""
    title = clean_youtube_title(title)
    artist, track = "YouTube", title
    parts = re.split(r"\s+[-–—]\s+", title, maxsplit=1)
    if len(parts) == 2 and parts[0].strip() and parts[1].strip():
        artist, track = parts[0].strip(), parts[1].strip()
    return artist, track


def _tokens(s: str) -> set:
    return {w for w in re.findall(r"\w+", (s or "").casefold()) if len(w) >= 3}


def resolve_meta(yt_title: str, sl=None):
    """
    (artist, ad, mənbə) qaytarır.
    Əvvəl song.link (Spotify/Apple Music və s.), tapılmasa və ya YouTube adı ilə
    heç uyğun gəlmirsə — təmizlənmiş YouTube adı.
    """
    if sl and sl.get("title") and sl.get("artist"):
        yt = _tokens(yt_title)
        # Səhv uyğunlaşmaya qarşı: song.link adı/artisti YouTube adında heç keçmirsə istifadə etmə
        if not yt or (_tokens(sl["title"]) | _tokens(sl["artist"])) & yt:
            return sl["artist"], sl["title"], "songlink"
        logger.info(f"song.link uyğun gəlmədi: '{sl['artist']} - {sl['title']}' ≠ '{yt_title}'")
    artist, track = parse_title(yt_title)
    return artist, track, "youtube"


def audio_filename(artist: str, track: str, file_path: str) -> str:
    ext = os.path.splitext(file_path)[1] or ".m4a"
    name = f"{artist} - {track}" if artist and artist != "YouTube" else track
    return (sanitize_filename(name)[:100] or "audio") + ext


def video_id_from_url(url: str):
    m = re.search(r"(?:v=|youtu\.be/|shorts/)([\w-]{11})", url or "")
    return m.group(1) if m else None


def setup(context):
    dp = context.dp
    user_searches = context.user_searches
    download_jobs = {}   # (chat_id, msg_id) | ("inline", inline_message_id) -> {event, owner}
    early_stops = {}     # inline: chosen_inline_result gəlməmiş basılan "dayandır"

    if not hasattr(context, 'youtube_manager'):
        context.youtube_manager = YoutubeManager(browser="firefox")

    async def handle_music_search(message: types.Message, search_query: str, command_used: str = "music"):
        try:
            if not search_query:
                usage_msg = await message.answer(
                    f"<b>🎵 Musiqi axtarışı istifadə qaydası:</b>\n"
                    f"👉 <code>/music &lt;mahnı adı&gt;</code>\n"
                    f"<i>Nümunə:</i> <code>/music Eminem Lose Yourself</code>",
                    parse_mode="HTML"
                )
                await asyncio.sleep(20)
                try:
                    await usage_msg.delete()
                except Exception as e:
                    logger.error(f"İstifadə mesajı silinərkən xəta: {e}")
                return
            
            user_id = message.from_user.id

            search_msg = await message.answer("<i>🔍 Axtarılır...</i>", parse_mode="HTML")
            results = await context.youtube_manager.youtube_search(search_query)
            
            if not results:
                raise ValueError("Nəticə tapılmadı")
            
            # Clean titles in search results
            for result in results:
                result['original_title'] = result['title']
            
            total_pages = (len(results) + 4) // 5
            current_page = 0
            start = current_page * 5
            current_results = results[start:start+5]

            response = [
                f"<b>🎵 Tapılan Mahnılar (Səhifə {current_page +1}/{total_pages}):</b>",
                ""
            ]

            keyboard_rows = []
            for i, res in enumerate(current_results):
                original_index = start + i
                button_text = f"{res['title']} ({res['duration']})"[:64]
                keyboard_rows.append([InlineKeyboardButton(text=button_text, callback_data=f"choice_{original_index}")])

            pagination_buttons = []
            if total_pages > 1:
                if current_page > 0:
                    pagination_buttons.append(InlineKeyboardButton(text="◀️ Geri", callback_data=f"page_{current_page-1}"))
                if current_page < total_pages - 1:
                    pagination_buttons.append(InlineKeyboardButton(text="İrəli ▶️", callback_data=f"page_{current_page+1}"))

            if pagination_buttons:
                keyboard_rows.append(pagination_buttons)

            keyboard_rows.append([
                InlineKeyboardButton(text="❌ Ləğv et", callback_data="cancel")
            ])

            keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_rows)
            
            await search_msg.edit_text(
                "\n".join(response),
                reply_markup=keyboard,
                parse_mode="HTML",
                disable_web_page_preview=True
            )
            
            user_searches[message.from_user.id] = {
                'results': results,
                'search_message_id': search_msg.message_id,
                'original_message_id': message.message_id,
                'command_used': command_used,
                'current_page': current_page
            }
            
        except Exception as e:
            error_msg = await message.answer(
                f"❌ <b>Xəta:</b> <code>{str(e)}</code>",
                parse_mode="HTML"
            )
            await asyncio.sleep(5)
            try:
                await error_msg.delete()
            except Exception as del_err:
                logger.error(f"Xəta mesajı silinərkən xəta: {del_err}")

    @dp.callback_query(F.data.startswith("page_"))
    async def handle_page(callback: types.CallbackQuery):
        user_id = callback.from_user.id
        if user_id not in user_searches:
            return

        user_data = user_searches[user_id]
        new_page = int(callback.data.split("_")[1])
        user_data['current_page'] = new_page
        results = user_data['results']
        total_pages = (len(results) + 4) // 5
        start = new_page * 5
        current_results = results[start:start+5]

        response = [
            f"<b>🎵 Tapılan Mahnılar (Səhifə {new_page +1}/{total_pages}):</b>",
            ""
        ]

        keyboard_rows = []
        for i, res in enumerate(current_results):
            original_index = start + i
            button_text = f"{res['title']} ({res['duration']})"[:64]
            keyboard_rows.append([InlineKeyboardButton(text=button_text, callback_data=f"choice_{original_index}")])

        pagination_buttons = []
        if total_pages > 1:
            if new_page > 0:
                pagination_buttons.append(InlineKeyboardButton(text="◀️ Geri", callback_data=f"page_{new_page-1}"))
            if new_page < total_pages - 1:
                pagination_buttons.append(InlineKeyboardButton(text="İrəli ▶️", callback_data=f"page_{new_page+1}"))

        if pagination_buttons:
            keyboard_rows.append(pagination_buttons)

        keyboard_rows.append([
            InlineKeyboardButton(text="❌ Ləğv et", callback_data="cancel")
        ])

        keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_rows)
        
        await callback.message.edit_text(
            "\n".join(response),
            reply_markup=keyboard,
            parse_mode="HTML",
            disable_web_page_preview=True
        )
        await callback.answer()

    @dp.message(Command("music", "mahnı", "song"))
    async def music_cmd(message: types.Message, command: CommandObject):
        command_used = message.text.split()[0][1:]
        await handle_music_search(message, command.args, command_used)

    async def not_editing_audio(message: types.Message) -> bool:
        """audio_editor_plugin ad/artist gözləyərkən yazılan mətn axtarış sayılmasın."""
        s = getattr(context, "audio_edit_sessions", {}).get(message.from_user.id)
        if s and s.get("awaiting") in ("title", "artist"):
            return False
        # menu_plugin creator-dan mətn gözləyir (broadcast mətni, premium üçün ID/@username)
        waiting = getattr(context, "menu_waiting_text", None)
        if waiting and waiting(message.from_user.id):
            return False
        return True

    @dp.message(
        F.chat.type == "private",
        F.text,
        not_editing_audio,
        ~F.text.startswith("/"),
        ~(F.text.contains("youtube.com/playlist") | F.text.contains("youtu.be/playlist")),
        ~(F.text.contains("spotify.com/playlist") | F.text.contains("spotify.com/album") | F.text.contains("spotify.com/track"))
    )
    async def handle_private_music_request(message: types.Message):
        await handle_music_search(message, message.text, "music")

    @dp.callback_query(F.data.startswith("choice_"))
    async def handle_choice(callback: types.CallbackQuery):
        user_id = callback.from_user.id
        if user_id not in user_searches:
            return

        user_data = user_searches[user_id]
        file_path = None
        
        try:
            choice = int(callback.data.split("_")[1])
            selected = user_data['results'][choice]
            
            job_key = (callback.message.chat.id, user_data['search_message_id'])
            job = {"event": threading.Event(), "owner": user_id}
            download_jobs[job_key] = job

            await callback.message.bot.edit_message_text(
                chat_id=callback.message.chat.id,
                message_id=user_data['search_message_id'],
                text=f"<i>⏳ Yüklənir:</i>\n <b>{selected['title']}</b> <b>{selected['duration']}</b>",
                parse_mode="HTML",
                reply_markup=stop_keyboard(f"dlstop:{user_data['search_message_id']}"),
            )
            
            # song.link sorğusu yükləmə ilə paralel gedir
            sl_task = asyncio.create_task(get_songlink(selected['url']))
            file_path = await context.youtube_manager.download_track(
                selected['url'], 
                sanitize_filename(selected['title']),
                cancel_event=job["event"],
            )
            sl = await sl_task
            if job["event"].is_set():          # yükləmə bitən an basılıbsa
                raise DownloadCancelled("İstifadəçi yükləməni dayandırdı")

            # Ad/artist: əvvəl song.link, yoxdursa təmizlənmiş YouTube adı
            artist, track_name, meta_src = resolve_meta(selected['title'], sl)
            logger.info(f"Metadata ({meta_src}): {artist} - {track_name}")
            
            with open(file_path, 'rb') as f:
                caption = build_caption(selected['url'], sl, callback.from_user.id)

                await callback.message.answer_audio(
                    audio=BufferedInputFile(f.read(), filename=audio_filename(artist, track_name, file_path)),
                    title=track_name[:64],
                    performer=artist[:64],
                    duration=int(selected.get('raw_duration', 0)),
                    parse_mode="HTML",
                    caption=caption,
                    reply_markup=build_keyboard(selected['url'], sl, user_id)
                )

            await log_download(callback.from_user.id, f"{artist} - {track_name}", selected['url'], "music")

            try:
                await callback.message.bot.delete_message(callback.message.chat.id, user_data['search_message_id'])
            except Exception as e:
                logger.error(f"Axtarış mesajı silinərkən xəta (seçim): {e}")

            try:
                await callback.message.bot.delete_message(callback.message.chat.id, user_data['original_message_id'])
            except Exception as e:
                logger.error(f"Orijinal mesaj silinərkən xəta (seçim): {e}")
            
        except DownloadCancelled:
            chat_id = callback.message.chat.id
            try:
                await callback.message.bot.edit_message_text(
                    chat_id=chat_id, message_id=user_data['search_message_id'],
                    text=f"⏹ <b>Yükləmə dayandırıldı:</b>\n{escape(selected['title'])}", parse_mode="HTML",
                )
            except Exception:
                pass
            try:
                await callback.message.bot.delete_message(chat_id, user_data['original_message_id'])
            except Exception:
                pass

            async def _cleanup(mid=user_data['search_message_id']):
                await asyncio.sleep(5)
                try:
                    await callback.message.bot.delete_message(chat_id, mid)
                except Exception:
                    pass
            asyncio.create_task(_cleanup())

        except (IndexError, KeyError):
            await callback.message.answer("❌ <b>Yanlış seçim!</b>", parse_mode="HTML")
            
        except Exception as e:
            await callback.message.answer(
                f"❌ <b>Xəta:</b> <code>{str(e)}</code>",
                parse_mode="HTML"
            )
        finally:
            if user_data.get('search_message_id') is not None:
                download_jobs.pop((callback.message.chat.id, user_data['search_message_id']), None)
            if file_path and os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except:
                    pass
            user_searches.pop(user_id, None)

    # ── ⏹ Yükləməni dayandır (/music) ──
    @dp.callback_query(F.data.startswith("dlstop:"))
    async def handle_download_stop(callback: types.CallbackQuery):
        try:
            key = (callback.message.chat.id, int(callback.data.split(":", 1)[1]))
        except (ValueError, AttributeError):
            await callback.answer()
            return
        job = download_jobs.get(key)
        if not job:
            await callback.answer("Yükləmə artıq bitib", show_alert=True)
            return
        if callback.from_user.id not in (job["owner"], context.creator_id):
            await callback.answer("⛔ Yalnız mahnını istəyən dayandıra bilər", show_alert=True)
            return
        job["event"].set()
        await callback.answer("⏹ Dayandırılır...")
        try:
            await callback.message.edit_text("⏹ <i>Dayandırılır...</i>", parse_mode="HTML")
        except Exception:
            pass

    # ── 🙈 Düymələri gizlət (mahnının altındakı Mix və s.) ──
    @dp.callback_query(F.data.startswith("hidekb:"))
    async def handle_hide_keyboard(callback: types.CallbackQuery):
        try:
            owner = int(callback.data.split(":", 1)[1])
        except ValueError:
            owner = 0
        if owner and callback.from_user.id not in (owner, context.creator_id):
            await callback.answer("⛔ Yalnız mahnını göndərən gizlədə bilər", show_alert=True)
            return
        try:
            if callback.inline_message_id:
                await callback.bot.edit_message_reply_markup(
                    inline_message_id=callback.inline_message_id, reply_markup=None
                )
            elif callback.message:
                await callback.message.edit_reply_markup(reply_markup=None)
            await callback.answer("🙈 Gizlədildi")
        except Exception as e:
            logger.warning(f"Düymələr gizlədilmədi: {e}")
            await callback.answer("Gizlətmək alınmadı", show_alert=True)

    @dp.callback_query(F.data == "cancel")
    async def handle_cancel(callback: types.CallbackQuery):
        user_id = callback.from_user.id
        if user_id not in user_searches:
            return

        user_data = user_searches[user_id]
        
        cancel_msg = await callback.message.answer("❌ <b>Axtarış ləğv edildi.</b>", parse_mode="HTML")
        
        try:
            await callback.message.bot.delete_message(callback.message.chat.id, user_data['search_message_id'])
        except Exception as e:
            logger.error(f"Axtarış mesajı silinərkən xəta (ləğv): {e}")

        try:
            await callback.message.bot.delete_message(callback.message.chat.id, user_data['original_message_id'])
        except Exception as e:
            logger.error(f"Orijinal mesaj silinərkən xəta (ləğv): {e}")
        
        await asyncio.sleep(5)
        try:
            await cancel_msg.delete()
        except Exception as e:
            logger.error(f"Ləğv mesajı silinərkən xəta: {e}")
        
        user_searches.pop(user_id, None)


    # ───────────────────────── INLINE MODE ─────────────────────────
    STORAGE_CHAT_ID = int(os.getenv("STORAGE_CHAT_ID") or context.creator_id)
    inline_cache = {}      # video_id -> axtarış nəticəsi
    file_id_cache = {}     # video_id -> telegram file_id (təkrar seçim ani olur)
    search_cache = {}      # sorğu -> (vaxt, nəticələr)
    latest_inline = {}     # user_id -> son inline query id (debounce)
    download_sem = asyncio.Semaphore(2)

    mix_cache = {}         # video_id -> (vaxt, nəticələr)
    MIX_PAGE_SIZE = 5
    MIX_MAX_PAGES = 5

    def make_article(res):
        vid = video_id_from_url(res.get("url"))
        if not vid:
            return None
        inline_cache[vid] = res
        if len(inline_cache) > 3000:
            inline_cache.pop(next(iter(inline_cache)))
        title = res["title"]
        return InlineQueryResultArticle(
            id=vid,
            title=title[:100],
            description=f"⏱ {res.get('duration', '')}",
            thumbnail_url=res.get("thumbnail") or f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
            input_message_content=InputTextMessageContent(
                message_text=f"⏳ <b>{escape(title)}</b>\n<i>Yüklənir...</i>",
                parse_mode="HTML",
                disable_web_page_preview=True,
            ),
            # inline_message_id almaq üçün reply_markup mütləqdir
            reply_markup=stop_keyboard("inl_stop"),
        )

    @dp.inline_query()
    async def handle_inline_query(q: types.InlineQuery):
        text = (q.query or "").strip()

        # ── 🔀 Mix: "mix:<video_id>" — oxşar mahnılar, 5 səhifə × 5 mahnı ──
        if text.lower().startswith("mix:"):
            seed = text[4:].strip()
            if not re.fullmatch(r"[\w-]{11}", seed):
                return
            try:
                page = max(0, int(q.offset or 0))
            except ValueError:
                page = 0

            cached = mix_cache.get(seed)
            if cached and time.time() - cached[0] < 600:
                mix = cached[1]
            else:
                try:
                    mix = await context.youtube_manager.youtube_mix(seed, limit=MIX_PAGE_SIZE * MIX_MAX_PAGES)
                except Exception as e:
                    logger.error(f"Inline mix xətası: {e}")
                    return
                mix_cache[seed] = (time.time(), mix)
                if len(mix_cache) > 100:
                    mix_cache.pop(next(iter(mix_cache)))

            start = page * MIX_PAGE_SIZE
            chunk = mix[start:start + MIX_PAGE_SIZE]
            articles = [a for a in (make_article(r) for r in chunk) if a]
            has_more = page + 1 < MIX_MAX_PAGES and start + MIX_PAGE_SIZE < len(mix)
            try:
                await q.answer(
                    articles, cache_time=60, is_personal=False,
                    next_offset=str(page + 1) if has_more else "",
                )
            except Exception as e:
                logger.warning(f"Inline mix answer alınmadı: {e}")
            return

        # ── Adi axtarış ──
        if len(text) < 2:
            return

        # Hər hərfə axtarış getməsin deyə debounce
        latest_inline[q.from_user.id] = q.id
        await asyncio.sleep(0.8)
        if latest_inline.get(q.from_user.id) != q.id:
            return

        key = text.lower()
        cached = search_cache.get(key)
        if cached and time.time() - cached[0] < 300:
            results = cached[1]
        else:
            try:
                results = await context.youtube_manager.youtube_search(text)
            except Exception as e:
                logger.error(f"Inline axtarış xətası: {e}")
                return
            search_cache[key] = (time.time(), results)
            if len(search_cache) > 200:
                search_cache.pop(next(iter(search_cache)))

        articles = [a for a in (make_article(r) for r in (results or [])[:10]) if a]

        try:
            await q.answer(articles, cache_time=30, is_personal=False)
        except Exception as e:
            # sorğunun vaxtı keçibsə (query too old) səssizcə keç
            logger.warning(f"Inline answer alınmadı: {e}")

    @dp.callback_query(F.data == "inl_wait")
    async def handle_inline_wait(callback: types.CallbackQuery):
        await callback.answer("Yüklənir, bir az gözləyin...")

    # ── ⏹ Yükləməni dayandır (inline) ──
    @dp.callback_query(F.data == "inl_stop")
    async def handle_inline_stop(callback: types.CallbackQuery):
        iid = callback.inline_message_id
        if not iid:
            await callback.answer()
            return
        job = download_jobs.get(("inline", iid))
        if not job:
            # chosen_inline_result hələ gəlməyibsə: qeyd et, gələndə yoxlanılacaq
            early_stops[iid] = callback.from_user.id
            await callback.answer("⏹ Dayandırılır...")
            return
        if callback.from_user.id not in (job["owner"], context.creator_id):
            await callback.answer("⛔ Yalnız mahnını göndərən dayandıra bilər", show_alert=True)
            return
        job["event"].set()
        await callback.answer("⏹ Dayandırılır...")
        try:
            await callback.bot.edit_message_text(
                inline_message_id=iid, text="⏹ <i>Dayandırılır...</i>", parse_mode="HTML"
            )
        except Exception:
            pass

    @dp.chosen_inline_result()
    async def handle_chosen_inline(chosen: types.ChosenInlineResult):
        inline_id = chosen.inline_message_id
        if not inline_id:
            return

        bot = context.bot
        vid = chosen.result_id
        info = inline_cache.get(vid) or {}
        url = info.get("url") or f"https://www.youtube.com/watch?v={vid}"
        title = info.get("title") or chosen.query
        artist, track = parse_title(title)   # song.link cavabı gələnə qədər ilkin dəyər
        file_path = None
        sl = None
        owner = chosen.from_user.id
        job = {"event": threading.Event(), "owner": owner}
        download_jobs[("inline", inline_id)] = job
        early = early_stops.pop(inline_id, None)
        if early is not None and early in (owner, context.creator_id):
            job["event"].set()

        try:
            sl_task = asyncio.create_task(get_songlink(url))

            file_id = file_id_cache.get(vid)
            if not file_id:
                async with download_sem:
                    if job["event"].is_set():      # növbədə gözləyərkən dayandırılıb
                        raise DownloadCancelled("İstifadəçi yükləməni dayandırdı")
                    file_path = await context.youtube_manager.download_track(
                        url, sanitize_filename(title), cancel_event=job["event"]
                    )
            if job["event"].is_set():
                raise DownloadCancelled("İstifadəçi yükləməni dayandırdı")

            # Ad/artist: əvvəl song.link, yoxdursa təmizlənmiş YouTube adı
            sl = await sl_task
            artist, track, meta_src = resolve_meta(title, sl)
            logger.info(f"Metadata ({meta_src}): {artist} - {track}")

            if not file_id:
                # Inline mesaja yeni fayl yükləmək olmur → əvvəlcə "anbar" çata göndərib file_id alırıq
                stored = await bot.send_audio(
                    chat_id=STORAGE_CHAT_ID,
                    audio=FSInputFile(file_path, filename=audio_filename(artist, track, file_path)),
                    title=track[:64],
                    performer=artist[:64],
                    duration=int(info.get("raw_duration", 0)),
                )
                file_id = stored.audio.file_id
                file_id_cache[vid] = file_id

            await bot.edit_message_media(
                inline_message_id=inline_id,
                media=InputMediaAudio(
                    media=file_id,
                    title=track[:64],
                    performer=artist[:64],
                    duration=int(info.get("raw_duration", 0)),
                    caption=build_caption(url, sl, chosen.from_user.id),
                    parse_mode="HTML",
                ),
                reply_markup=build_keyboard(url, sl, owner),
            )
            await log_download(chosen.from_user.id, f"{artist} - {track}", url, "inline")

        except DownloadCancelled:
            try:
                await bot.edit_message_text(
                    inline_message_id=inline_id,
                    text=f"⏹ <b>Yükləmə dayandırıldı</b>\n🎵 {escape(title)}",
                    parse_mode="HTML",
                )
            except Exception as e2:
                logger.debug(f"Dayandırma mesajı yazılmadı: {e2}")
        except Exception as e:
            logger.error(f"Inline göndərmə xətası: {e}", exc_info=True)
            try:
                # Fallback: audio alınmadısa, ən azı song.link səhifəsini göndər
                fallback = (sl or {}).get("page") or f"https://song.link/{url}"
                await bot.edit_message_text(
                    inline_message_id=inline_id,
                    text=f"🎵 <b>{escape(title)}</b>\n{fallback}\n\n<i>⚠️ Audio yüklənmədi: {escape(str(e))[:150]}</i>",
                    parse_mode="HTML",
                )
            except Exception as e2:
                logger.error(f"Fallback da alınmadı: {e2}")
        finally:
            download_jobs.pop(("inline", inline_id), None)
            if file_path and os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except Exception:
                    pass
