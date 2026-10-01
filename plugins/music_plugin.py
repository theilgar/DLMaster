from aiogram import types, F
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    BufferedInputFile, FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton,
    InlineQueryResultArticle, InputTextMessageContent, InputMediaAudio,
)
from core.utilities import sanitize_filename
from core.youtube_handler import YoutubeManager, DownloadCancelled
from core import audio_cache
from core.database import log_download, caption_for_user, is_premium
import asyncio
import contextlib
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


BATCH_MAX_DURATION = 15 * 60      # toplu yükləmədə bundan uzun videolar (mix, albom) ötürülür


def download_all_row(count: int) -> list:
    """Axtarış / mix siyahısının altında: 💎 premium üçün hamısını yüklə."""
    return [InlineKeyboardButton(text=f"⬇️ Hamısını yüklə ({count}) 💎", callback_data="dlall")]


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

            keyboard_rows.append(download_all_row(len(results)))
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
            f"<b>{user_data.get('header', '🎵 Tapılan Mahnılar')} (Səhifə {new_page +1}/{total_pages}):</b>",
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

        keyboard_rows.append(download_all_row(len(results)))
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

    # ═════════════ /mix — oxşar mahnılar (inline Mix-in çat versiyası) ═════════════
    MIX_PAGE, MIX_PAGES = 5, 5

    def mix_seed_from_reply(message: types.Message):
        """
        Cavab verilən mesajdan Mix toxumu → (video_id | None, axtarış/başlıq | None):
          • botun göndərdiyi mahnı (🔀 Mix düyməsi varsa — dəqiq video)
          • mətndə / caption-da YouTube linki
          • audio (artist + ad, yoxdursa fayl adı), audio-fayl sənəd, video
          • adi mətn — mahnı adı kimi axtarılır
        """
        r = message.reply_to_message
        if not r:
            return None, None

        audio_title = None
        if r.audio:
            audio_title = " ".join(filter(None, [r.audio.performer, r.audio.title])) or \
                          os.path.splitext(r.audio.file_name or "")[0] or None

        kb = getattr(r, "reply_markup", None)
        for row in (kb.inline_keyboard if kb else []):
            for b in row:
                q = getattr(b, "switch_inline_query_current_chat", None) or ""
                if q.startswith("mix:"):
                    return q[4:], (r.audio.title if r.audio and r.audio.title else audio_title)

        text = r.text or r.caption or ""
        vid = video_id_from_url(text)
        if vid:
            return vid, None
        if audio_title:
            return None, audio_title
        doc = getattr(r, "document", None)
        if doc and (doc.mime_type or "").startswith("audio/") and doc.file_name:
            return None, os.path.splitext(doc.file_name)[0]
        video = getattr(r, "video", None)
        if video and getattr(video, "file_name", None):
            return None, os.path.splitext(video.file_name)[0]
        if text and not text.startswith("/"):
            return None, text[:100]
        return None, None

    def mix_page_kb(results, page: int):
        total_pages = max(1, (len(results) + MIX_PAGE - 1) // MIX_PAGE)
        start = page * MIX_PAGE
        rows = [
            [InlineKeyboardButton(text=f"{r['title']} ({r['duration']})"[:64], callback_data=f"choice_{start + i}")]
            for i, r in enumerate(results[start:start + MIX_PAGE])
        ]
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton(text="◀️ Geri", callback_data=f"page_{page - 1}"))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton(text="İrəli ▶️", callback_data=f"page_{page + 1}"))
        if nav:
            rows.append(nav)
        rows.append(download_all_row(len(results)))
        rows.append([InlineKeyboardButton(text="❌ Ləğv et", callback_data="cancel")])
        return InlineKeyboardMarkup(inline_keyboard=rows), total_pages

    @dp.message(Command("mix"))
    async def mix_cmd(message: types.Message, command: CommandObject):
        await run_mix(message, (command.args or "").strip())

    # Mahnıya cavab olaraq sadəcə "mix" yazmaq da işləyir (slash-sız)
    MIX_WORDS = {"mix", "miks", "🔀", "mix et", "oxşar"}

    @dp.message(F.reply_to_message, F.text.func(lambda t: t.strip().lower() in MIX_WORDS))
    async def mix_reply_word(message: types.Message):
        await run_mix(message, "")

    async def run_mix(message: types.Message, query: str):
        vid, seed_title = video_id_from_url(query), None
        if not query:
            vid, reply_query = mix_seed_from_reply(message)
            if vid:
                seed_title = reply_query
            else:
                query = reply_query or ""

        if not vid and not query:
            usage = await message.answer(
                "<b>🔀 Mix — oxşar mahnılar</b>\n\n"
                "👉 <code>/mix mahnı adı</code>\n"
                "👉 <code>/mix YouTube linki</code>\n"
                "👉 və ya istənilən mahnıya <b>cavab (reply)</b> olaraq <code>/mix</code> və ya sadəcə <code>mix</code> yaz\n\n"
                "<i>Nümunə:</i> <code>/mix Eminem Lose Yourself</code>",
                parse_mode="HTML",
            )
            await asyncio.sleep(20)
            try:
                await usage.delete()
            except Exception:
                pass
            return

        status = await message.answer("<i>🔀 Oxşar mahnılar axtarılır...</i>", parse_mode="HTML")
        try:
            if not vid:
                found = await context.youtube_manager.youtube_search(query)
                seed = next((r for r in found or [] if video_id_from_url(r.get("url"))), None)
                if not seed:
                    raise ValueError("Mahnı tapılmadı")
                vid, seed_title = video_id_from_url(seed["url"]), seed["title"]

            results = await context.youtube_manager.youtube_mix(vid, limit=MIX_PAGE * MIX_PAGES)
            if not results:
                raise ValueError("Bu mahnı üçün Mix tapılmadı")

            label = escape(clean_youtube_title(seed_title)[:60]) if seed_title else "bu mahnı"
            header = f"🔀 Mix: {label}"
            kb, total_pages = mix_page_kb(results, 0)
            await status.edit_text(
                f"<b>{header} (Səhifə 1/{total_pages}):</b>\n"
                f"<i>{len(results)} oxşar mahnı tapıldı — yükləmək üçün seç</i>",
                parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True,
            )
            # /music ilə eyni axın: choice_ / page_ / cancel handler-ləri bunu istifadə edir
            user_searches[message.from_user.id] = {
                'results': results,
                'search_message_id': status.message_id,
                'original_message_id': message.message_id,
                'command_used': "mix",
                'current_page': 0,
                'header': header,
            }
        except Exception as e:
            logger.warning(f"/mix xətası: {e}")
            try:
                await status.edit_text(f"❌ <b>Xəta:</b> <code>{escape(str(e))[:300]}</code>", parse_mode="HTML")
                await asyncio.sleep(5)
                await status.delete()
            except Exception:
                pass

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

    # ═════════════ ✏️ Inline ilə BOTUN ÖZÜNƏ göndərilən mahnı → meta redaktə təklifi ═════════════
    def inline_vid(message: types.Message):
        """Botun inline mesajından video ID: placeholder (inl_stop:<id>) və ya hazır mahnı (mix:<id>)."""
        kb = getattr(message, "reply_markup", None)
        for row in (kb.inline_keyboard if kb else []):
            for b in row:
                data = getattr(b, "callback_data", None) or ""
                if data.startswith("inl_stop:"):
                    return data.split(":", 1)[1]
                q = getattr(b, "switch_inline_query_current_chat", None) or ""
                if q.startswith("mix:"):
                    return q[4:]
        return None

    async def is_own_inline(message: types.Message) -> bool:
        vb = getattr(message, "via_bot", None)
        return bool(vb) and vb.id == context.bot.id

    @dp.message(F.chat.type == "private", F.via_bot, is_own_inline)
    async def own_inline_in_private(message: types.Message):
        vid = inline_vid(message)
        if not vid or not getattr(context, "audio_editor_start", None):
            return                       # axtarışa getmir, sadəcə susur
        info = inline_cache.get(vid) or {}
        title = clean_youtube_title(info.get("title") or "") or \
            (message.text or "").replace("⏳", "").split("\n")[0].strip() or "bu mahnı"
        await message.reply(
            f"✏️ <b>{escape(title[:80])}</b>\n\n"
            "Bu mahnının meta məlumatlarını dəyişmək istəyirsən?\n"
            "<i>🎵 ad · 🎤 artist · 🖼 üz qabığı şəkli</i>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="✅ Bəli", callback_data=f"meta:yes:{vid}:{message.message_id}"),
                InlineKeyboardButton(text="❌ Ləğv et", callback_data="meta:no"),
            ]]),
        )

    @dp.callback_query(F.data.startswith("meta:"))
    async def meta_offer(callback: types.CallbackQuery):
        parts = callback.data.split(":")
        if parts[1] == "no" or len(parts) < 4:
            await callback.answer("Ləğv edildi")
            with contextlib.suppress(Exception):
                await callback.message.delete()
            return

        vid, reply_to = parts[2], int(parts[3]) if parts[3].isdigit() else None
        await callback.answer()
        with contextlib.suppress(Exception):
            await callback.message.edit_text("⏳ <i>Mahnı hazırlanır...</i>", parse_mode="HTML")

        # Inline mahnı hələ yüklənirsə — hazır olana qədər gözlə (depo/keşə düşən an)
        cached = None
        for _ in range(120):
            cached = await asyncio.to_thread(audio_cache.get_cached, vid)
            if cached:
                break
            await asyncio.sleep(1)
        if not cached:
            with contextlib.suppress(Exception):
                await callback.message.edit_text(
                    "❌ Mahnı hazır olmadı (yükləmə dayandırılıb və ya alınmayıb).\n"
                    "<i>Mahnı gələndən sonra onu mənə göndər — redaktə paneli açılacaq.</i>",
                    parse_mode="HTML",
                )
            return

        start = getattr(context, "audio_editor_start", None)
        performer, title = cached.get("performer") or "", cached.get("title") or "audio"
        await start(
            chat_id=callback.message.chat.id, uid=callback.from_user.id, file_id=cached["file_id"],
            file_name=f"{performer + ' - ' if performer else ''}{title}.m4a", title=title, artist=performer,
            duration=cached.get("duration"), mime="audio/mp4", size=cached.get("size"), reply_to=reply_to,
        )
        with contextlib.suppress(Exception):
            await callback.message.delete()

    @dp.message(
        F.chat.type == "private",
        F.text,
        ~F.via_bot,                      # botun öz inline mesajı axtarış sayılmasın
        not_editing_audio,
        ~F.text.startswith("/"),
        ~(F.text.contains("youtube.com/playlist") | F.text.contains("youtu.be/playlist")),
        ~(F.text.contains("spotify.com/playlist") | F.text.contains("spotify.com/album") | F.text.contains("spotify.com/track"))
    )
    async def handle_private_music_request(message: types.Message):
        await handle_music_search(message, message.text, "music")

    # ═════════════ 📦 Keş + depo kanalı ═════════════
    async def fetch_audio(url: str, yt_title: str, duration: int, cancel_event,
                          need_file_id: bool = False, fallback_chat=None, sem=None) -> dict:
        """
        Mahnını əvvəl keşdən (depo kanalı) götürür; yoxdursa YouTube-dan yükləyir, depoya atır, keşə yazır.
        Nəticə: {file_id, path, fname, artist, track, sl, cached, vid}
        """
        bot = context.bot
        vid = video_id_from_url(url)
        sl_task = asyncio.create_task(get_songlink(url))
        async with (audio_cache.lock_for(vid) if vid else contextlib.nullcontext()):
            cached = await asyncio.to_thread(audio_cache.get_cached, vid)
            if cached:
                try:
                    sl = await asyncio.wait_for(asyncio.shield(sl_task), 2)   # keşdən göndərməni ləngitməsin
                except (asyncio.TimeoutError, Exception):
                    sl = None
                artist, track, _ = resolve_meta(yt_title, sl)
                logger.info(f"📦 Keşdən: {vid}")
                return {"file_id": cached["file_id"], "path": None, "fname": None, "vid": vid, "sl": sl,
                        "artist": cached.get("performer") or artist, "track": cached.get("title") or track,
                        "cached": True}

            async with (sem or contextlib.nullcontext()):
                if cancel_event.is_set():
                    raise DownloadCancelled("İstifadəçi yükləməni dayandırdı")
                path = await context.youtube_manager.download_track(
                    url, sanitize_filename(yt_title), cancel_event=cancel_event
                )
            sl = await sl_task
            if cancel_event.is_set():
                with contextlib.suppress(OSError):
                    os.remove(path)
                raise DownloadCancelled("İstifadəçi yükləməni dayandırdı")

            artist, track, meta_src = resolve_meta(yt_title, sl)
            logger.info(f"Metadata ({meta_src}): {artist} - {track}")
            fname = audio_filename(artist, track, path)
            file_id = await audio_cache.upload_to_depo(bot, path, fname, track, artist, duration, url, vid)
            if not file_id and need_file_id and fallback_chat:
                # depo əlçatan deyil — inline üçün file_id mütləq lazımdır
                msg = await bot.send_audio(
                    chat_id=fallback_chat, audio=FSInputFile(path, filename=fname),
                    title=track[:64], performer=artist[:64], duration=duration or None,
                )
                file_id = await asyncio.to_thread(audio_cache.save_from_message, vid, msg, track, artist, duration)
            return {"file_id": file_id, "path": path, "fname": fname, "vid": vid, "sl": sl,
                    "artist": artist, "track": track, "cached": False}

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
            
            duration = int(selected.get('raw_duration', 0) or 0)
            for attempt in (1, 2):
                # 📦 əvvəl depo/keş, yoxdursa yüklə (song.link paralel)
                res = await fetch_audio(selected['url'], selected['title'], duration, job["event"])
                file_path = res["path"]
                sl, artist, track_name = res["sl"], res["artist"], res["track"]
                caption = build_caption(selected['url'], sl, callback.from_user.id)
                kb = build_keyboard(selected['url'], sl, user_id)
                if job["event"].is_set():          # yükləmə bitən an basılıbsa
                    raise DownloadCancelled("İstifadəçi yükləməni dayandırdı")
                try:
                    if res["file_id"]:
                        await callback.message.answer_audio(
                            audio=res["file_id"], caption=caption, parse_mode="HTML", reply_markup=kb,
                        )
                    else:
                        # depo əlçatan deyil — birbaşa göndər, file_id-ni yenə də keşə yaz
                        sent = await callback.message.answer_audio(
                            audio=FSInputFile(file_path, filename=res["fname"]),
                            title=track_name[:64], performer=artist[:64], duration=duration or None,
                            parse_mode="HTML", caption=caption, reply_markup=kb,
                        )
                        await asyncio.to_thread(audio_cache.save_from_message, res["vid"], sent,
                                                track_name, artist, duration)
                    break
                except Exception as e:
                    if attempt == 1 and res["cached"] and audio_cache.is_bad_file_id(e):
                        await asyncio.to_thread(audio_cache.invalidate, res["vid"])
                        continue                       # yenidən: bu dəfə YouTube-dan
                    raise
            if res["cached"]:
                await asyncio.to_thread(audio_cache.mark_hit, res["vid"])

            await log_download(callback.from_user.id, f"{artist} - {track_name}", selected['url'],
                               "mix" if user_data.get('command_used') == "mix" else "music",
                               chat_id=callback.message.chat.id)

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


    # ═════════════ ⬇️ Hamısını yüklə (💎 premium) ═════════════
    batch_jobs = {}      # user_id -> {"event", "msg_id", "chat_id"}

    async def send_track(target: types.Message, selected: dict, user_id: int, event) -> tuple:
        """Bir mahnını keş/depo və ya yükləmə ilə göndərir → (artist, ad). handle_choice ilə eyni məntiq."""
        duration = int(selected.get('raw_duration', 0) or 0)
        for attempt in (1, 2):
            res = await fetch_audio(selected['url'], selected['title'], duration, event)
            try:
                if event.is_set():
                    raise DownloadCancelled("İstifadəçi dayandırdı")
                caption = build_caption(selected['url'], res["sl"], user_id)
                kb = build_keyboard(selected['url'], res["sl"], user_id)
                for flood_try in range(3):
                    try:
                        if res["file_id"]:
                            await target.answer_audio(audio=res["file_id"], caption=caption,
                                                      parse_mode="HTML", reply_markup=kb)
                        else:
                            sent = await target.answer_audio(
                                audio=FSInputFile(res["path"], filename=res["fname"]),
                                title=res["track"][:64], performer=res["artist"][:64], duration=duration or None,
                                caption=caption, parse_mode="HTML", reply_markup=kb,
                            )
                            await asyncio.to_thread(audio_cache.save_from_message, res["vid"], sent,
                                                    res["track"], res["artist"], duration)
                        break
                    except Exception as e:
                        wait = getattr(e, "retry_after", None)       # Telegram flood limiti
                        if wait and flood_try < 2:
                            await asyncio.sleep(wait + 1)
                            continue
                        raise
                if res["cached"]:
                    await asyncio.to_thread(audio_cache.mark_hit, res["vid"])
                return res["artist"], res["track"]
            except Exception as e:
                if attempt == 1 and res["cached"] and audio_cache.is_bad_file_id(e):
                    await asyncio.to_thread(audio_cache.invalidate, res["vid"])
                    continue
                raise
            finally:
                if res.get("path"):
                    with contextlib.suppress(OSError):
                        os.remove(res["path"])

    def batch_stop_kb():
        return InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="⏹ Dayandır", callback_data="dlall_stop")
        ]])

    @dp.callback_query(F.data == "dlall")
    async def handle_download_all(callback: types.CallbackQuery):
        user_id = callback.from_user.id
        user_data = user_searches.get(user_id)
        if not user_data or callback.message.message_id != user_data.get('search_message_id'):
            await callback.answer("Bu siyahı köhnəlib — yenidən axtar", show_alert=True)
            return
        if not await asyncio.to_thread(is_premium, user_id):
            await callback.answer(
                "💎 Hamısını birdən yükləmək yalnız Premium istifadəçilər üçündür.\n\n"
                "Premium almaq üçün: /premium",
                show_alert=True,
            )
            return
        if user_id in batch_jobs:
            await callback.answer("Artıq bir toplu yükləmə gedir", show_alert=True)
            return

        # dublikatları və çox uzun videoları çıxar
        seen, items, skipped = set(), [], 0
        for r in user_data['results']:
            vid = video_id_from_url(r.get('url'))
            if not vid or vid in seen:
                continue
            seen.add(vid)
            if (r.get('raw_duration') or 0) > BATCH_MAX_DURATION:
                skipped += 1
                continue
            items.append(r)
        if not items:
            await callback.answer("Yüklənəcək mahnı yoxdur", show_alert=True)
            return

        await callback.answer(f"⬇️ {len(items)} mahnı yüklənir...")
        source = "mix" if user_data.get('command_used') == "mix" else "batch"
        header = user_data.get('header', "🎵 Axtarış")
        user_searches.pop(user_id, None)          # siyahı artıq bu iş üçündür
        event = threading.Event()
        chat_id = callback.message.chat.id
        batch_jobs[user_id] = {"event": event, "msg_id": callback.message.message_id, "chat_id": chat_id}
        delay = 1.0 if callback.message.chat.type == "private" else 3.2   # qrupda ~20 mesaj/dəq limiti

        ok = failed = 0
        note = f"\n<i>⏭ {skipped} uzun video (15 dəq.+) ötürüldü</i>" if skipped else ""

        async def progress(i, current=""):
            with contextlib.suppress(Exception):
                await callback.message.edit_text(
                    f"⬇️ <b>Hamısı yüklənir</b> — {escape(header)}\n\n"
                    f"📊 {i}/{len(items)} · ✅ {ok} · ❌ {failed}"
                    + (f"\n⏳ <i>{escape(current[:60])}</i>" if current else "") + note,
                    parse_mode="HTML", reply_markup=batch_stop_kb(),
                )

        try:
            for i, item in enumerate(items):
                if event.is_set():
                    break
                await progress(i, item['title'])
                try:
                    artist, track = await send_track(callback.message, item, user_id, event)
                    ok += 1
                    await log_download(user_id, f"{artist} - {track}", item['url'], source, chat_id=chat_id)
                except DownloadCancelled:
                    break
                except Exception as e:
                    failed += 1
                    logger.warning(f"Toplu yükləmə xətası ({item.get('url')}): {e}")
                await asyncio.sleep(delay)
        finally:
            batch_jobs.pop(user_id, None)

        stopped = event.is_set()
        with contextlib.suppress(Exception):
            await callback.message.edit_text(
                ("⏹ <b>Dayandırıldı</b>" if stopped else "✅ <b>Hamısı göndərildi</b>")
                + f" — {escape(header)}\n\n📊 ✅ {ok} · ❌ {failed} · cəmi {len(items)}" + note,
                parse_mode="HTML",
            )
        with contextlib.suppress(Exception):
            await callback.message.bot.delete_message(chat_id, user_data['original_message_id'])

        async def _cleanup():
            await asyncio.sleep(15)
            with contextlib.suppress(Exception):
                await callback.message.delete()
        asyncio.create_task(_cleanup())

    @dp.callback_query(F.data == "dlall_stop")
    async def handle_download_all_stop(callback: types.CallbackQuery):
        job = batch_jobs.get(callback.from_user.id)
        if not job or job["msg_id"] != callback.message.message_id:
            owner = next((u for u, j in batch_jobs.items() if j["msg_id"] == callback.message.message_id), None)
            if owner is not None and callback.from_user.id == context.creator_id:
                job = batch_jobs[owner]
            elif owner is not None:
                await callback.answer("⛔ Yalnız yükləməni başladan dayandıra bilər", show_alert=True)
                return
            else:
                await callback.answer("Yükləmə artıq bitib")
                return
        job["event"].set()
        await callback.answer("⏹ Dayandırılır...")

    # ───────────────────────── INLINE MODE ─────────────────────────
    STORAGE_CHAT_ID = int(os.getenv("STORAGE_CHAT_ID") or context.creator_id)
    inline_cache = {}      # video_id -> axtarış nəticəsi
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
            reply_markup=stop_keyboard(f"inl_stop:{vid}"),
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
    @dp.callback_query(F.data.startswith("inl_stop"))
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
            duration = int(info.get("raw_duration", 0) or 0)
            for attempt in (1, 2):
                # 📦 əvvəl depo/keş; yoxdursa yüklə. İnline mesaja yeni fayl yükləmək olmur →
                # depo (və ya STORAGE_CHAT_ID) üzərindən file_id alınır
                res = await fetch_audio(url, title, duration, job["event"], need_file_id=True,
                                        fallback_chat=STORAGE_CHAT_ID, sem=download_sem)
                file_path = res["path"]
                sl, artist, track = res["sl"], res["artist"], res["track"]
                if job["event"].is_set():
                    raise DownloadCancelled("İstifadəçi yükləməni dayandırdı")
                try:
                    await bot.edit_message_media(
                        inline_message_id=inline_id,
                        media=InputMediaAudio(
                            media=res["file_id"],
                            title=track[:64],
                            performer=artist[:64],
                            duration=duration or None,
                            caption=build_caption(url, sl, chosen.from_user.id),
                            parse_mode="HTML",
                        ),
                        reply_markup=build_keyboard(url, sl, owner),
                    )
                    break
                except Exception as e:
                    if attempt == 1 and res["cached"] and audio_cache.is_bad_file_id(e):
                        await asyncio.to_thread(audio_cache.invalidate, res["vid"])
                        continue
                    raise
            if res["cached"]:
                await asyncio.to_thread(audio_cache.mark_hit, res["vid"])
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
