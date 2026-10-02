"""
📦 Depo doldurucu + 📱 Telethon userbot — yalnız creator.

Fonda işləyir: populyar və istifadəçilərin sevdiyi mahnıları əvvəlcədən depo kanalına yükləyir ki,
kimsə istəyəndə dərhal (yükləməsiz) göndərilsin.

Mənbələr (növbə bu sıra ilə qurulur):
  1. 👥 İstifadəçilərin son 7 gündə ən çox yüklədiyi ifaçılar → Spotify-da onların top mahnıları
  2. 🔀 Ən çox yüklənən mahnıların YouTube Mix-i (oxşar mahnılar)
  3. 📋 Sənin əlavə etdiyin mənbələr: Spotify playlist / albom / ifaçı, YouTube playlist,
     ✈️ Telegram kanal / qrup:
        • userbot (Telethon) aktivdirsə — public, gizli (t.me/+...), qrup, t.me/c/..., -100... ID;
          bütün tarixçə skan edilir, yeni audiolar canlı növbəyə düşür
        • userbot yoxdursa — yalnız public kanal (t.me/s/kanal veb səhifəsindən)

Telegram-dan gələn mahnı adları təmizlənir (@kanal, linklər, emoji, (Official Video), 320kbps,
trek nömrəsi, fayl uzantısı, "audio_2024-..." kimi fayl adları və s.), sonra:
  Spotify axtarışı → (tapılmasa) iTunes axtarışı → song.link ilə dəqiq YouTube → (alınmasa) YouTube axtarışı.
Tapılan rəsmi ad/ifaçı metadata kimi depoya yazılır. Nəticələr bazada yadda saxlanır (təkrar axtarış yoxdur).

  4. 🎵 YouTube Music kataloqu (ytmusicapi, panel düyməsi və ya /depo ytm on):
        bütün ölkələrin chartları + janrlar + istifadəçilərin ifaçıları → hər ifaçının BÜTÜN mahnıları,
        albomları, sinqlları → oxşar və feat. ifaçılar → ... (bitməyən BFS, vəziyyət bazada — restartda davam edir).
        Əsas mənbələr həmişə öndədir; YT Music növbəsi onlar boş olanda işlənir.

Sürət / paralellik (paneldə ➖ ➕):
  ⏱ fasilə — hər işçinin mahnılar arası gözləməsi (0 … 300 san.; /depo delay N ilə istənilən)
  🧵 işçi sayı — 1 = tək-tək, 2…32 = eyni anda neçə mahnı yüklənsin (dərhal tətbiq olunur)

💾 Restartdan sonra qaldığı yerdən davam edir:
  • növbə + YT Music növbəsi + yarımçıq qalan (işçilərin əlindəki) mahnılar hər 20 san.-dən bir və
    bot dayananda depo_state.json-a yazılır, açılanda geri yüklənir
  • Telegram mənbələri artımlı skan olunur: skan olunmuş mesajlar yadda qalır, yarımçıq skan
    qaldığı mesajdan davam edir, sonrakı qurulmalarda yalnız yeni audiolar oxunur
  • 🔁 Növbəni yenidən qur — hər şeyi sıfırlayır (Telegram tarixçəsi də tam yenidən oxunur)

📊 Canlı monitor (/depotop və ya paneldə 📊 düyməsi) — htop kimi, hər 1 saniyədən bir:
  hər işçinin vəziyyəti (DL / SRCH / CHK / SLP / WAIT) və müddəti, sürət qrafiki (mahnı/dəq.),
  növbələr, cəmi göstəricilər, botun CPU / RAM / thread sayı, son hadisələr jurnalı.

Komandalar:
  /depo                              — panel (status, fasilə, paralellik, mənbələr)
  /depo on | /depo off               — işə sal / söndür
  /depo delay <san.>                 — mahnılar arası fasilə
  /depo workers <1-32>               — paralel yükləmə sayı (1 = tək-tək)
  /depotop                           — 📊 canlı monitor (1 san.)
  /depo ytm on | off                 — YouTube Music kataloqunu yüklə
  /depo add <link> [mesaj_sayı]      — mənbə əlavə et (Telegram üçün dərhal skan da başlayır)
  /scan_chat <link/ID> [mesaj_sayı]  — çatı birdəfəlik skan et (mənbə kimi saxlamadan)
  /stop_scan                         — cari skanı dayandır
  /userbot                           — userbot statusu
  .alive / .info / .fastfetch / .clear — userbot_tools_plugin.py
"""
import asyncio
import json
import logging
import os
import re
import threading
import time
import unicodedata
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from html import escape, unescape

import aiohttp

from aiogram import F, types
from aiogram.filters import Command, CommandObject
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from core import audio_cache
from core.database import get_db
from core.links import parse_link, yt_list_url

try:
    from telethon import TelegramClient, events, utils as tl_utils
    from telethon.errors import InviteHashExpiredError, InviteHashInvalidError, UserAlreadyParticipantError
    from telethon.tl.functions.messages import (CheckChatInviteRequest, ImportChatInviteRequest,
                                                SendReactionRequest)
    from telethon.tl.types import (ChatInviteAlready, ChatInvitePeek, DocumentAttributeAudio,
                                   DocumentAttributeFilename, InputMessagesFilterMusic, ReactionEmoji)
    try:
        from telethon.errors import InviteRequestSentError
    except ImportError:                       # köhnə Telethon
        InviteRequestSentError = None
    HAS_TELETHON = True
except ImportError:
    HAS_TELETHON = False

try:
    from ytmusicapi import YTMusic
    HAS_YTM = True
except ImportError:
    HAS_YTM = False

logger = logging.getLogger(__name__)
PRIORITY = 60

# 🎵 YouTube Music kataloqu: ifaçı qrafı üzrə BFS (chart → ifaçı → mahnılar/albomlar/sinqllar → oxşar ifaçılar ...)
YTM_COUNTRIES = ["ZZ", "AZ", "TR", "RU", "US", "GB", "DE", "UA", "KZ", "UZ", "GE", "IR", "IN", "BR", "FR", "KR"]
YTM_LOW_WATER = 150              # YT Music növbəsi bundan az olanda növbəti ifaçı skan edilir
YTM_PAUSE = 1.0                  # ytmusicapi sorğuları arası (san.) — IP bloklanmasın
YTM_MAX_ALBUMS = 60              # bir ifaçının ən çox neçə albom / sinqlı açılsın (hər bölmə üçün)
YTM_MOOD_PLAYLISTS = 2           # seed: hər janr / əhval-ruhiyyədən neçə playlist
YTM_RECRAWL = 14 * 86400         # skan olunmuş ifaçını 14 gündən sonra yeni relizlər üçün yenidən yoxla
YTM_FAIL_RETRY = 3 * 86400
YTM_SEEN_CAP = 300_000

DEFAULT_SOURCES = [
    {"kind": "sp_playlist", "id": "37i9dQZF1DXcBWIGoYBM5M", "label": "Today's Top Hits"},
    {"kind": "sp_playlist", "id": "37i9dQZEVXbMDoHDwVN2tF", "label": "Top 50 Global"},
]
# ⏱ Mahnılar arası fasilə (san.) — panelde ➖ / ➕ bu pillələrlə dəyişir, /depo delay N istənilən dəyər
DELAY_STEPS = [0, 1, 2, 3, 5, 8, 10, 15, 20, 25, 30, 45, 60, 90, 120, 180, 300]
DEFAULT_DELAY = 25
MAX_DELAY = 3600
OLD_SPEEDS = {"slow": 60, "normal": 25, "fast": 8}     # köhnə ayarı köçürmək üçün
# 🧵 Paralel yükləmə: 1 = tək-tək, 2..MAX_WORKERS = eyni anda neçə mahnı
DEFAULT_WORKERS = 1
MAX_WORKERS = 32
WORKER_STEPS = [1, 2, 3, 4, 5, 6, 8, 10, 12, 16, 20, 24, 28, 32]   # panel ➖ / ➕ pillələri
MAX_DURATION = 15 * 60
STATE_FILE = os.getenv("DEPO_STATE_FILE", "depo_state.json")   # 💾 növbənin restart arası saxlanması
STATE_SAVE_EVERY = 20                                  # san. — dəyişiklik varsa
TG_CHECKPOINT = 500                                    # Telegram skanında hər N mesajdan bir vəziyyət yazılır
IDLE_SLEEP = 30 * 60                                   # növbə bitəndə yenidən qurmağa qədər
NOTFOUND_RETRY = 3 * 86400                             # tapılmayan mahnını 3 gün sonra yenidən axtar

API_ID = os.getenv("TELETHON_API_ID")
API_HASH = os.getenv("TELETHON_API_HASH")
SESSION_FILE = "userbot.session"
SOURCE_KIND_LABELS = {"sp_playlist": "Spotify playlist", "sp_album": "Spotify albom", "sp_artist": "Spotify ifaçı",
                      "yt_list": "YouTube playlist", "tg_channel": "Telegram (veb)", "tg_chat": "Telegram (userbot)"}


# ───────────────────────── 🧹 Mahnı adının təmizlənməsi ─────────────────────────
_AUDIO_EXT = re.compile(r"\.(mp3|m4a|flac|ogg|oga|opus|wav|aac|wma|alac|mp4)$", re.I)
_URL_RE = re.compile(
    r"(?:https?://|www\.)\S+"
    r"|\b[\w-]+\.(?:com|net|org|ru|az|tr|uz|kz|info|fm|me|biz|io|xyz|cc|tv|ws|su|pro|club|top|site|online|"
    r"music|mobi|live)\b(?:/\S*)?", re.I)
_MENTION_RE = re.compile(r"(?<![^\W_])[@#][\w.]+")
_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\u2190-\u21FF\u2500-\u25FF\u2660-\u266F"
    "\u2700-\u27BF\uFE0F\u200B-\u200F\u2060\u00A9\u00AE\u2122]+")
_BRACKETS = re.compile(r"\s*[\(\[【{<«]([^\)\]】}>»]*)[\)\]】}>»]")
_BRACKET_NOISE = re.compile(
    r"\b(official|video|audio|lyrics?|visuali[sz]er|mv|m/v|hd|hq|4k|klip|clip|rəsmi|premyera|премьера|клип|"
    r"mp3|m4a|flac|\d{2,3}\s*kbps|kbps|bass\s*boost(ed)?|tiktok|trend|hit|yeni|new|full|prod\.?|"
    r"music|muzik|musiqi|музыка|скачать|download)\b|^\s*(?:19|20)\d\d\s*$|^\s*\d+\s*$|https?://|@", re.I)
_TRAILING_NOISE = re.compile(
    r"(?:\s*[-|/]\s*|\s+)(?:official|video|audio|lyrics?|visuali[sz]er|klip|clip|rəsmi|mp3|hd|hq|4k|"
    r"\d{2,3}\s*kbps)\s*$", re.I)
_TRACKNO = re.compile(r"^\s*(?:track\s*)?\d{1,3}\s*(?:[.)_:]|-\s)\s*(?=\S)", re.I)
_JUNK_NAME = re.compile(
    r"^(?:audio|aud|voice|track|trek|file|fayl|document|doc|record|rec|ses|unknown|untitled|"
    r"naməlum|неизвестен|без названия)?[\s_\-.]*[\d_\-\s.:]*$|^[0-9a-f]{16,}$|^track\s*\d+$"
    r"|^(?:aud|ptt|vid|rec|voice|audio)[\s_\-]*\d{6,}.*$", re.I)
_JUNK_PERFORMER = re.compile(
    r"^(?:unknown|unknown artist|<unknown>|naməlum|неизвестен|various artists?|va|artist|ifaçı|"
    r"music|muzik|musiqi|mp3|audio|track)$|(?:mp3|muzik|musiqi|music|kanal|channel|chat|group|qrup|"
    r"download|yüklə|скачать)\b", re.I)


def _norm_dashes(s: str) -> str:
    s = re.sub(r"\s*[–—―‒−~]\s*", " - ", s)
    s = re.sub(r"(?<=\S)\s+-\s*(?=\S)|(?<=\S)\s*-\s+(?=\S)", " - ", s)
    return s


def _clean_part(s: str) -> str:
    if not s:
        return ""
    s = unescape(str(s)).replace("%20", " ").replace("\u00a0", " ")
    s = _AUDIO_EXT.sub("", s.strip())
    s = _BRACKETS.sub(lambda m: "" if (not m.group(1).strip() or _BRACKET_NOISE.search(m.group(1))
                                       or _URL_RE.search(m.group(1))) else m.group(0), s)
    s = _URL_RE.sub(" ", s)
    s = _MENTION_RE.sub(" ", s)                     # @kanal_adi — "_" əvəzlənməzdən əvvəl
    s = s.replace("_", " ")
    s = _EMOJI_RE.sub(" ", s)
    s = re.split(r"\s+(?:\||//|•|·)\s+", s, maxsplit=1)[0] if re.search(r"\s(?:\||//|•|·)\s", s) else s
    s = _TRACKNO.sub("", s)
    s = _norm_dashes(s)
    for _ in range(3):
        s = _TRAILING_NOISE.sub("", s)
    s = re.sub(r"\s{2,}", " ", s)
    return s.strip(" -–|.,:;*•·'\"`")


def _fold(s: str) -> str:
    s = (s or "").casefold().replace("ə", "e").replace("ı", "i")
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


_STOP = {"feat", "ft", "featuring", "prod", "with", "x", "vs", "the", "and", "a"}


def _toks(s: str) -> set:
    return {w for w in re.findall(r"\w+", _fold(s)) if len(w) >= 2 and w not in _STOP}


def _same(a: str, b: str) -> bool:
    ta, tb = _toks(a), _toks(b)
    return bool(ta) and bool(tb) and (ta <= tb or tb <= ta)


def clean_song(performer=None, title=None, file_name=None, caption=None, chat_title=None):
    """Telegram audio məlumatından (artist, ad) — oxunmursa None."""
    p, t = _clean_part(performer), _clean_part(title)
    if performer and re.search(r"@|https?://|t\.me/|www\.", str(performer), re.I):
        p = ""                                         # ifaçı yerində kanal / bot adı
    if p and (_JUNK_PERFORMER.search(p) or (chat_title and _same(p, chat_title))):
        p = ""
    if t and (_JUNK_NAME.match(t) or (chat_title and _fold(t) == _fold(chat_title))):
        t = ""
    if not t and file_name:
        f = _clean_part(os.path.splitext(file_name)[0] if _AUDIO_EXT.search(file_name) else file_name)
        if f and not _JUNK_NAME.match(f):
            t = f
    if not t and caption:
        for line in str(caption).splitlines():
            line = _clean_part(line)
            if line and " - " in line and len(line) <= 120:
                t = line
                break
    if not t or not re.search(r"\w", t):
        return None
    if " - " in t:
        a, b = (x.strip() for x in t.split(" - ", 1))
        if a and b:
            if not p:
                p, t = a, b
            elif _same(a, p):
                t = b                                  # "Eminem - Lose Yourself" + ifaçı Eminem
            elif _same(b, p):
                t = a                                  # "Lose Yourself - Eminem" + ifaçı Eminem
    if p and _fold(t).startswith(_fold(p) + " "):     # "Eminem Lose Yourself"
        rest = t[len(p):].strip(" -")
        t = rest or t
    t = t.strip(" -")
    return (p.strip(" -"), t) if t else None


def tg_item(artist: str, title: str, duration: int = 0) -> dict:
    artist, title = (artist or "").strip(), (title or "").strip()
    full = f"{artist} - {title}" if artist else title
    base = _BRACKETS.sub("", title).strip() or title          # axtarış üçün (feat. ...) olmadan
    return {"title": full, "query": f"{artist} {base}".strip(), "url": None, "raw_duration": int(duration or 0),
            "meta": None, "thumb": None, "sp_id": None, "lookup": True,
            "clean": {"artist": artist, "track": title}}


def tl_message_item(msg, chat_title: str = ""):
    """Telethon mesajından növbə elementi (səs mesajı / adsız fayl → None)."""
    doc = getattr(msg, "audio", None) or getattr(msg, "document", None)
    if not doc:
        return None
    title = performer = fname = None
    duration = 0
    is_audio = False
    for a in getattr(doc, "attributes", None) or []:
        if isinstance(a, DocumentAttributeAudio):
            if a.voice:
                return None
            is_audio = True
            title, performer, duration = a.title, a.performer, a.duration or 0
        elif isinstance(a, DocumentAttributeFilename):
            fname = a.file_name
    if not is_audio and not (fname and _AUDIO_EXT.search(fname)):
        return None
    res = clean_song(performer, title, fname, getattr(msg, "message", None), chat_title)
    if not res:
        return None
    return tg_item(res[0], res[1], duration)


# ───────────────────────── ✈️ Telegram linkləri ─────────────────────────
TG_PAGES = 15                  # t.me/s/kanal — hər səhifə ~20 post
_RESERVED = {"s", "c", "joinchat", "share", "addstickers", "addemoji", "proxy", "socks", "addlist", "iv", "login"}
_TG_LINK = re.compile(r"^(?:https?://)?(?:t\.me|telegram\.me|telegram\.dog)/(\S+)$", re.I)
_SIZE_RE = re.compile(r"^\s*[\d.,]+\s*(?:B|KB|MB|GB)\s*$", re.I)
_TAG_RE = re.compile(r"<[^>]+>")


def parse_tg_target(text: str):
    """→ {"type": user|invite|id, ...}, mesaj sayı (limit) və ya (None, None)."""
    words = (text or "").split()
    target, tw = None, None
    for w in words:
        w = w.strip().rstrip(",")
        if re.fullmatch(r"-?\d{7,}", w):
            target = {"type": "id", "id": int(w)}
        elif re.fullmatch(r"@[A-Za-z][A-Za-z0-9_]{3,31}", w):
            target = {"type": "user", "name": w[1:]}
        else:
            m = _TG_LINK.match(w)
            if not m:
                continue
            parts = m.group(1).split("?")[0].strip("/").split("/")
            if parts[0].startswith("+"):
                target = {"type": "invite", "hash": parts[0][1:]}
            elif parts[0].lower() == "joinchat" and len(parts) > 1:
                target = {"type": "invite", "hash": parts[1]}
            elif parts[0].lower() == "c" and len(parts) > 1 and parts[1].isdigit():
                target = {"type": "id", "id": int("-100" + parts[1])}
            else:
                name = parts[1] if parts[0].lower() == "s" and len(parts) > 1 else parts[0]
                if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{3,31}", name) and name.lower() not in _RESERVED:
                    target = {"type": "user", "name": name}
        if target:
            tw = w
            break
    limit = next((int(w) for w in words if w != tw and w.isdigit() and len(w) <= 6), None)
    return target, limit


def parse_tg_channel(text: str):
    t, _ = parse_tg_target(text)
    return t["name"] if t and t["type"] == "user" else None


def _clean_html(html_part: str) -> str:
    return unescape(_TAG_RE.sub("", html_part or "")).strip()


def parse_tg_page(html: str) -> list:
    """t.me/s/... səhifəsindən mahnılar: audio postları (ad + ifaçı) və "Artist - Ad" mətnləri."""
    items = []
    for block in re.split(r'<div class="tgme_widget_message_wrap', html)[1:]:
        found = False
        for m in re.finditer(
                r'tgme_widget_message_document_title[^>]*>(.*?)</div>\s*'
                r'<div class="tgme_widget_message_document_extra[^>]*>(.*?)</div>', block, re.S):
            title, extra = _clean_html(m.group(1)), _clean_html(m.group(2))
            if not title:
                continue
            performer = "" if (not extra or _SIZE_RE.match(extra)) else extra    # audio: ifaçı "extra"-dadır
            res = clean_song(performer, title, title)
            if res:
                items.append(tg_item(*res))
            found = True
        if not found:
            tm = re.search(r'tgme_widget_message_text[^>]*>(.*?)</div>', block, re.S)
            text = _clean_html((tm.group(1) if tm else "").replace("<br/>", "\n").replace("<br>", "\n"))
            first = text.split("\n")[0].strip() if text else ""
            if " - " in _norm_dashes(first) and len(first) <= 120 and "http" not in first:
                res = clean_song("", first)
                if res and res[0]:
                    items.append(tg_item(*res))
    return items


async def tg_channel_items(name: str, pages: int = TG_PAGES) -> dict:
    """Public kanalın son postlarından mahnı siyahısı (yeni → köhnə) — userbot olmayanda."""
    items, title, before = [], name, None
    timeout = aiohttp.ClientTimeout(total=20)
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0 Safari/537.36"}
    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        for _ in range(pages):
            url = f"https://t.me/s/{name}" + (f"?before={before}" if before else "")
            async with session.get(url) as r:
                if r.status != 200:
                    break
                html = await r.text()
            if "tgme_channel_info" not in html and "tgme_widget_message" not in html:
                raise RuntimeError("Kanal tapılmadı və ya public deyil")
            t = re.search(r'tgme_channel_info_header_title[^>]*>(.*?)</div>', html, re.S)
            if t:
                title = _clean_html(t.group(1)) or title
            items += parse_tg_page(html)[::-1]        # səhifədə köhnə → yeni; bizə yeni → köhnə lazımdır
            m = re.search(r'data-before="(\d+)"', html) or re.search(r'\?before=(\d+)', html)
            nb = m.group(1) if m else None
            if not nb or nb == before:
                break
            before = nb
    seen, uniq = set(), []
    for it in items:
        k = it["query"].lower()
        if k and k not in seen:
            seen.add(k)
            uniq.append(it)
    return {"name": title, "items": uniq}


# ───────────────────────── 🔎 Ad → rəsmi mahnı (Spotify / iTunes) ─────────────────────────
def match_score(artist: str, track: str, c_artist: str, c_track: str, dur: int = 0, c_dur: int = 0) -> float:
    qt, ct = _toks(track), _toks(c_track)
    if not qt or not ct:
        return 0.0
    common = len(qt & ct)
    title_sim = (common / len(qt) + common / len(ct)) / 2
    if artist:
        qa = _toks(artist)
        ca = _toks(c_artist) | ct                     # "feat. X" bəzən adda olur
        artist_sim = len(qa & ca) / len(qa) if qa else 0.0
        score = 0.55 * title_sim + 0.45 * artist_sim
        if artist_sim == 0:
            score = min(score, 0.45)                  # ifaçı tamam fərqlidir — qəbul etmə
    else:
        score = title_sim * 0.8 if title_sim >= 0.9 else title_sim * 0.5
    if dur and c_dur:
        diff = abs(int(dur) - int(c_dur))
        score -= 0 if diff <= 10 else (0.1 if diff <= 30 else 0.3)
    return score


ACCEPT_SCORE = 0.6


async def spotify_lookup(spotify, artist: str, track: str, dur: int):
    if not spotify or not getattr(spotify, "sp", None):
        return None
    q = f"{artist} {track}".strip()
    res = await asyncio.to_thread(spotify.sp.search, q=q, type="track", limit=6)
    best, best_s = None, 0.0
    for t in (res.get("tracks") or {}).get("items") or []:
        names = [a["name"] for a in t.get("artists") or [] if a.get("name")]
        s = match_score(artist, track, " ".join(names), t.get("name", ""), dur, (t.get("duration_ms") or 0) // 1000)
        if s > best_s:
            best, best_s = t, s
    if not best or best_s < ACCEPT_SCORE:
        return None
    imgs = sorted((best.get("album") or {}).get("images") or [], key=lambda i: i.get("width") or 0)
    fit = [i for i in imgs if (i.get("width") or 0) <= 320]
    names = [a["name"] for a in best["artists"] if a.get("name")]
    return {"artist": ", ".join(names[:3]), "first": names[0] if names else "", "track": best["name"],
            "duration": (best.get("duration_ms") or 0) // 1000, "sp_id": best.get("id"),
            "link": f"https://open.spotify.com/track/{best['id']}" if best.get("id") else None,
            "thumb": ((fit[-1] if fit else imgs[0])["url"] if imgs else None), "src": "spotify"}


async def itunes_lookup(artist: str, track: str, dur: int):
    q = f"{artist} {track}".strip()
    timeout = aiohttp.ClientTimeout(total=10)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get("https://itunes.apple.com/search",
                               params={"term": q, "entity": "song", "limit": "6"}) as r:
            if r.status != 200:
                return None
            data = json.loads(await r.text())
    best, best_s = None, 0.0
    for t in data.get("results") or []:
        s = match_score(artist, track, t.get("artistName", ""), t.get("trackName", ""), dur,
                        (t.get("trackTimeMillis") or 0) // 1000)
        if s > best_s:
            best, best_s = t, s
    if not best or best_s < ACCEPT_SCORE:
        return None
    return {"artist": best.get("artistName", ""), "first": best.get("artistName", ""),
            "track": best.get("trackName", ""), "duration": (best.get("trackTimeMillis") or 0) // 1000,
            "sp_id": None, "link": best.get("trackViewUrl"),
            "thumb": (best.get("artworkUrl100") or "").replace("100x100bb", "300x300bb") or None, "src": "itunes"}


def btn(text, data):
    return InlineKeyboardButton(text=text, callback_data=data)


# ───────────────────────── 🎵 YouTube Music kataloq skaneri ─────────────────────────
def _ytm_duration(t) -> int:
    if t.get("duration_seconds"):
        return int(t["duration_seconds"])
    d = str(t.get("duration") or "")
    if re.fullmatch(r"\d{1,2}(?::\d{2}){1,2}", d):
        sec = 0
        for part in d.split(":"):
            sec = sec * 60 + int(part)
        return sec
    return 0


def _ytm_thumb(thumbs):
    thumbs = sorted([x for x in thumbs or [] if x.get("url")], key=lambda x: x.get("width") or 0)
    if not thumbs:
        return None
    fit = [x for x in thumbs if (x.get("width") or 0) <= 320]
    return (fit[-1] if fit else thumbs[0])["url"]


def ytm_item(t: dict, fallback_artists=None, fallback_thumbs=None):
    """ytmusicapi trek obyekti → növbə elementi (rəsmi ad + ifaçı metadata kimi)."""
    vid = t.get("videoId")
    if not vid or t.get("isAvailable") is False:
        return None
    title = (t.get("title") or "").strip()
    arts = [a["name"] for a in (t.get("artists") or fallback_artists or []) if a.get("name")]
    if not title:
        return None
    artist = ", ".join(arts[:3])
    dur = _ytm_duration(t)
    if dur > MAX_DURATION:
        return None
    return {"title": f"{artist} - {title}" if artist else title,
            "query": f"{arts[0] if arts else ''} {title}".strip(),
            "url": f"https://www.youtube.com/watch?v={vid}", "vid": vid, "raw_duration": dur,
            "meta": {"artist": artist, "track": title} if artist else None,
            "thumb": _ytm_thumb(t.get("thumbnails") or fallback_thumbs), "sp_id": None, "src": "ytm"}


class YtmCrawler:
    """
    YouTube Music-dəki mahnıları ifaçı qrafı üzrə gəzir (bitməyən BFS):
      seed: hər ölkənin chart ifaçıları + janr/əhval-ruhiyyə playlistləri + istifadəçilərin sevdiyi ifaçılar
      ifaçı: bütün mahnılar playlisti + bütün albomlar + sinqllar → növbə
      yeni ifaçılar: «oxşar ifaçılar» + mahnılardakı feat. ifaçılar → bazaya (pending)
    Vəziyyət bazada saxlanır (depo_ytm) — restartdan sonra qaldığı yerdən davam edir.
    """

    def __init__(self):
        self.yt = None
        self.stats = {"artist": None, "found": 0, "in_depo": 0, "note": ""}
        try:
            conn = get_db()._conn
            conn.execute("""CREATE TABLE IF NOT EXISTS depo_ytm (
                              aid TEXT PRIMARY KEY, name TEXT, state INTEGER DEFAULT 0,
                              songs INTEGER DEFAULT 0, ts INTEGER DEFAULT 0)""")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_depo_ytm_state ON depo_ytm(state, ts)")
            conn.commit()
        except Exception as e:
            logger.warning(f"depo_ytm cədvəli yaradılmadı: {e}")

    def client(self):
        if self.yt is None:
            try:
                self.yt = YTMusic(language="en", location="AZ")
            except TypeError:                          # köhnə ytmusicapi (location yoxdur)
                self.yt = YTMusic()
        return self.yt

    # ── baza ──
    def counts(self) -> dict:
        try:
            rows = get_db()._conn.execute("SELECT state, COUNT(*), SUM(songs) FROM depo_ytm GROUP BY state").fetchall()
        except Exception:
            return {"pending": 0, "done": 0, "failed": 0, "songs": 0}
        c = {"pending": 0, "done": 0, "failed": 0, "songs": 0}
        for st, n, songs in rows:
            c[{0: "pending", 1: "done", 2: "failed"}.get(st, "pending")] += n
            c["songs"] += songs or 0
        return c

    def add_artists(self, pairs) -> int:
        pairs = [(a, n) for a, n in pairs if a and str(a).startswith("UC")]
        if not pairs:
            return 0
        conn = get_db()._conn
        before = conn.total_changes
        conn.executemany("INSERT OR IGNORE INTO depo_ytm (aid, name, state, ts) VALUES (?, ?, 0, 0)", pairs)
        conn.commit()
        return conn.total_changes - before

    def _next_pending(self):
        return get_db()._conn.execute(
            "SELECT aid, name FROM depo_ytm WHERE state = 0 ORDER BY rowid LIMIT 1").fetchone()

    def _mark(self, aid, state, songs=0):
        conn = get_db()._conn
        conn.execute("UPDATE depo_ytm SET state = ?, songs = ?, ts = ? WHERE aid = ?",
                     (state, songs, int(time.time()), aid))
        conn.commit()

    def _requeue_old(self) -> int:
        now = int(time.time())
        conn = get_db()._conn
        before = conn.total_changes
        conn.execute("""UPDATE depo_ytm SET state = 0 WHERE aid IN (
                          SELECT aid FROM depo_ytm WHERE (state = 1 AND ts < ?) OR (state = 2 AND ts < ?)
                          ORDER BY ts LIMIT 2000)""", (now - YTM_RECRAWL, now - YTM_FAIL_RETRY))
        conn.commit()
        return conn.total_changes - before

    # ── ytmusicapi (sinxron — to_thread-də işləyir) ──
    def _seed_sync(self, user_artists, stop) -> list:
        yt, found = self.client(), {}
        for country in YTM_COUNTRIES:
            if stop.is_set():
                break
            try:
                charts = yt.get_charts(country=country) or {}
                for a in (charts.get("artists") or {}).get("items") or []:
                    if a.get("browseId"):
                        found[a["browseId"]] = a.get("title") or ""
            except Exception as e:
                logger.debug(f"YTM chart {country}: {e}")
            time.sleep(YTM_PAUSE)
        for name in user_artists:
            if stop.is_set():
                break
            try:
                res = yt.search(name, filter="artists", limit=1) or []
                if res and res[0].get("browseId"):
                    found[res[0]["browseId"]] = res[0].get("artist") or name
            except Exception as e:
                logger.debug(f"YTM ifaçı axtarışı {name}: {e}")
            time.sleep(YTM_PAUSE)
        try:
            cats = yt.get_mood_categories() or {}
        except Exception as e:
            logger.debug(f"YTM janrlar: {e}")
            cats = {}
        for section in cats.values():
            for mood in section or []:
                if stop.is_set():
                    break
                try:
                    pls = yt.get_mood_playlists(mood["params"]) or []
                except Exception:
                    continue
                for pl in pls[:YTM_MOOD_PLAYLISTS]:
                    try:
                        tracks = (yt.get_playlist(pl["playlistId"], limit=100) or {}).get("tracks") or []
                    except Exception:
                        continue
                    for t in tracks:
                        for a in t.get("artists") or []:
                            if a.get("id"):
                                found[a["id"]] = a.get("name") or ""
                    time.sleep(YTM_PAUSE)
        return list(found.items())

    def _artist_sync(self, aid, stop):
        """→ (ad, [trek], [(oxşar_aid, ad)])"""
        yt = self.client()
        a = yt.get_artist(aid) or {}
        name = a.get("name") or ""
        tracks = []
        songs = a.get("songs") or {}
        if songs.get("browseId"):
            try:
                tracks += (yt.get_playlist(songs["browseId"], limit=None) or {}).get("tracks") or []
            except Exception as e:
                logger.debug(f"YTM {name}: mahnılar playlisti: {e}")
                tracks += songs.get("results") or []
            time.sleep(YTM_PAUSE)
        else:
            tracks += songs.get("results") or []
        me = [{"name": name, "id": aid}]
        for key in ("albums", "singles"):
            sec = a.get(key) or {}
            albums = sec.get("results") or []
            if sec.get("browseId") and sec.get("params"):
                try:
                    albums = yt.get_artist_albums(sec["browseId"], sec["params"], limit=None) or albums
                except TypeError:
                    try:
                        albums = yt.get_artist_albums(sec["browseId"], sec["params"]) or albums
                    except Exception:
                        pass
                except Exception as e:
                    logger.debug(f"YTM {name}: {key}: {e}")
                time.sleep(YTM_PAUSE)
            for al in albums[:YTM_MAX_ALBUMS]:
                if stop.is_set():
                    break
                bid = al.get("browseId")
                if not bid:
                    continue
                try:
                    alb = yt.get_album(bid) or {}
                except Exception:
                    continue
                for t in alb.get("tracks") or []:
                    t["_fa"] = alb.get("artists") or me
                    t["_ft"] = alb.get("thumbnails")
                    tracks.append(t)
                time.sleep(YTM_PAUSE)
        related = [(r["browseId"], r.get("title") or "") for r in (a.get("related") or {}).get("results") or []
                   if r.get("browseId")]
        for t in tracks:                                     # feat. ifaçılar da qrafa düşsün
            for x in t.get("artists") or []:
                if x.get("id"):
                    related.append((x["id"], x.get("name") or ""))
        return name, tracks, related

    # ── bir addım: bir ifaçı → mahnılar ──
    async def step(self, add_items, user_artists_fn, stop) -> bool:
        """Növbəyə mahnı əlavə edilə bildisə True; heç nə yoxdursa False (çağıran gözləsin)."""
        row = self._next_pending()
        if not row:
            n = self._requeue_old()
            if not n:
                self.stats["note"] = "seed: chartlar və janrlar oxunur..."
                pairs = await asyncio.to_thread(self._seed_sync, user_artists_fn(), stop)
                n = self.add_artists(pairs)
                logger.info(f"🎵 YT Music seed: {len(pairs)} ifaçı ({n} yeni)")
            self.stats["note"] = ""
            row = self._next_pending()
            if not row:
                self.stats["note"] = "yeni ifaçı yoxdur — 1 saat sonra yenidən"
                return False
        aid, name = row[0], row[1]
        self.stats["artist"] = name or aid
        try:
            name, tracks, related = await asyncio.to_thread(self._artist_sync, aid, stop)
        except Exception as e:
            logger.info(f"🎵 YTM ifaçı {name or aid}: {e}")
            self._mark(aid, 2)
            self.stats["artist"] = None
            return True
        items, vids = [], set()
        for t in tracks:
            it = ytm_item(t, t.get("_fa"), t.get("_ft"))
            if it and it["vid"] not in vids:
                vids.add(it["vid"])
                items.append(it)
        new_artists = self.add_artists(related)
        added, in_depo = await add_items(items)
        self.stats["found"] += added
        self.stats["in_depo"] += in_depo
        self._mark(aid, 1, len(items))
        self.stats.update(artist=None, note="")
        logger.info(f"🎵 YTM {name}: {len(items)} mahnı (+{added} növbəyə, {in_depo} artıq depoda), "
                    f"+{new_artists} yeni ifaçı")
        return True


def ensure_thread_pool(workers: int):
    """asyncio.to_thread hovuzunu işçi sayına görə böyüdür.

    Default hovuz min(32, CPU+4) thread-dir; 16-32 işçi (hər biri yt-dlp / Spotify / baza üçün thread tutur)
    onu doldurub istifadəçilərin öz yükləmələrini də gözlədərdi. Hovuz yalnız böyüyür, kiçilmir."""
    need = max(32, workers * 3 + 16)
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    ex = getattr(loop, "_default_executor", None)
    cur = getattr(ex, "_max_workers", 0) if ex else min(32, (os.cpu_count() or 1) + 4)
    if cur >= need:
        return
    loop.set_default_executor(ThreadPoolExecutor(max_workers=need, thread_name_prefix="asyncio"))
    logger.info(f"🧵 Thread hovuzu böyüdüldü: {cur} → {need} ({workers} işçi üçün)")


# ───────────────────────── 📦 Doldurucu ─────────────────────────
class Filler:
    def __init__(self, context):
        self.ctx = context
        self.task = None
        self.stop_event = threading.Event()
        self.queue = deque()
        self.seen = set()
        self.tg = None                          # TgUser (userbot) — setup-da verilir
        self.stats = {"done": 0, "cached": 0, "failed": 0, "started": None,
                      "last_build": None, "queue_built": 0, "note": ""}
        self.current = {}                       # işçi nömrəsi → hazırda yüklənən mahnı
        self.workers_tasks = {}
        # 📊 canlı monitor üçün
        self.wstate = {}                        # işçi → {"phase", "title", "since"}
        self.events = deque(maxlen=80)          # son hadisələr: (ts, işçi, növ, ad, san., bayt)
        self.done_ts = deque(maxlen=20000)      # yüklənən hər mahnının vaxtı (sürət qrafiki)
        self.durations = deque(maxlen=200)      # bir mahnının tam müddəti (axtarış + yükləmə + göndərmə)
        self.bytes = 0
        # 💾 restart arası davamlılıq
        self.inflight = {}                      # işçi → hazırda işlənən element (restartda növbənin önünə qayıdır)
        self._save_lock = asyncio.Lock()
        self._saved_sig = None
        self.restored = 0
        self.build_lock = asyncio.Lock()
        # 🎵 YouTube Music kataloqu — ayrıca növbə (əsas mənbələr həmişə öndədir)
        self.ytm_queue = deque()
        self.ytm_seen = set()
        self.ytm = YtmCrawler() if HAS_YTM else None
        self._init_resolve_table()
        self.load_state()

    # ── ayarlar (bazada) ──
    def _get(self, key, default=None):
        try:
            return get_db().get_setting(f"depo_fill:{key}", default)
        except Exception:
            return default

    def _set(self, key, value):
        get_db().set_setting(f"depo_fill:{key}", value)

    @property
    def enabled(self) -> bool:
        return self._get("on") == "1"

    @property
    def delay(self) -> int:
        """Hər işçinin mahnılar arası fasiləsi (san.)."""
        v = self._get("delay")
        if v is None:                                   # köhnə 🐢/🚶/🏃 ayarından köçür
            return OLD_SPEEDS.get(self._get("speed") or "", DEFAULT_DELAY)
        try:
            return max(0, min(MAX_DELAY, int(v)))
        except (TypeError, ValueError):
            return DEFAULT_DELAY

    def set_delay(self, value: int) -> int:
        value = max(0, min(MAX_DELAY, int(value)))
        self._set("delay", str(value))
        return value

    def step_delay(self, direction: int) -> int:
        cur = self.delay
        if direction > 0:
            nxt = next((x for x in DELAY_STEPS if x > cur), min(MAX_DELAY, cur + 60))
        else:
            nxt = next((x for x in reversed(DELAY_STEPS) if x < cur), 0)
        return self.set_delay(nxt)

    @property
    def workers(self) -> int:
        """Eyni anda neçə mahnı yüklənsin (1 = tək-tək)."""
        try:
            return max(1, min(MAX_WORKERS, int(self._get("workers") or DEFAULT_WORKERS)))
        except (TypeError, ValueError):
            return DEFAULT_WORKERS

    def set_workers(self, value: int) -> int:
        value = max(1, min(MAX_WORKERS, int(value)))
        self._set("workers", str(value))
        ensure_thread_pool(value)
        return value

    def step_workers(self, direction: int) -> int:
        cur = self.workers
        if direction > 0:
            nxt = next((x for x in WORKER_STEPS if x > cur), MAX_WORKERS)
        else:
            nxt = next((x for x in reversed(WORKER_STEPS) if x < cur), 1)
        return self.set_workers(nxt)

    # ── 📊 monitor köməkçiləri ──
    def _phase(self, idx: int, phase: str, title: str = None):
        st = self.wstate.setdefault(idx, {"phase": "", "title": "", "since": time.time()})
        if title is not None:
            st["title"] = title
        if st["phase"] != phase:
            st["phase"], st["since"] = phase, time.time()

    def _event(self, idx: int, kind: str, title: str, secs: float = 0.0, size: int = 0):
        now = time.time()
        self.events.append((now, idx, kind, title, secs, size))
        if kind == "done":
            self.done_ts.append(now)
            self.durations.append(secs)
            self.bytes += size

    @property
    def ytm_on(self) -> bool:
        return HAS_YTM and self._get("ytm_all") == "1"

    def set_ytm(self, on: bool):
        self._set("ytm_all", "1" if on else "0")

    def sources(self) -> list:
        try:
            data = json.loads(self._get("sources") or "null")
        except ValueError:
            data = None
        return data if isinstance(data, list) else list(DEFAULT_SOURCES)

    def save_sources(self, sources):
        self._set("sources", json.dumps(sources))

    def tg_source_ids(self) -> set:
        return {int(s["id"]) for s in self.sources() if s.get("kind") == "tg_chat" and str(s["id"]).lstrip("-").isdigit()}

    @property
    def running(self) -> bool:
        return bool(self.task and not self.task.done())

    # ── 💾 növbənin saxlanması (restartdan sonra davam) ──
    def _snapshot(self) -> dict:
        """Event loop thread-ində çağırılır — siyahılar kopyalanır, JSON isə ayrıca thread-də yazılır."""
        fly_main, fly_ytm = [], []
        for _, it in sorted(self.inflight.items()):
            (fly_ytm if it.get("src") == "ytm" else fly_main).append(dict(it))
        st = self.stats
        return {
            "v": 1, "saved": time.time(),
            "queue": fly_main + list(self.queue),
            "ytm_queue": fly_ytm + list(self.ytm_queue),
            "last_build": st.get("last_build"), "queue_built": st.get("queue_built", 0),
            "build_pending": self.build_lock.locked(),
            "counters": {k: st.get(k, 0) for k in ("done", "cached", "failed")},
            "bytes": self.bytes,
        }

    def _sig(self):
        q, y = self.queue, self.ytm_queue
        return (len(q), len(y), self._key(q[0]) if q else None, y[0].get("vid") if y else None,
                tuple(sorted(self.inflight)), self.stats["done"] + self.stats["cached"] + self.stats["failed"])

    @staticmethod
    def _write_state(data: dict):
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"), default=str)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, STATE_FILE)                    # atomik — yarımçıq fayl qalmır

    def save_state_now(self):
        """Sinxron yazı (bot dayananda / plugin söndürüləndə)."""
        try:
            self._write_state(self._snapshot())
            self._saved_sig = self._sig()
        except Exception as e:
            logger.warning(f"💾 Depo növbəsi yazılmadı: {e}")

    async def save_state(self, force: bool = False):
        sig = self._sig()
        if not force and sig == self._saved_sig:
            return
        if self._save_lock.locked() and not force:
            return
        async with self._save_lock:             # force — gedən yazını gözləyib təzə snapshot yazır
            data = self._snapshot()
            try:
                await asyncio.to_thread(self._write_state, data)
                self._saved_sig = sig
            except Exception as e:
                logger.warning(f"💾 Depo növbəsi yazılmadı: {e}")

    async def state_saver(self):
        while not self.stop_event.is_set():
            await asyncio.sleep(STATE_SAVE_EVERY)
            await self.save_state()

    def load_state(self):
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            return
        except Exception as e:
            logger.warning(f"💾 {STATE_FILE} oxunmadı ({e}) — növbə sıfırdan qurulacaq")
            return
        for it in data.get("queue") or []:
            if isinstance(it, dict) and self._key(it) and self._key(it) not in self.seen:
                self.seen.add(self._key(it))
                self.queue.append(it)
        for it in data.get("ytm_queue") or []:
            if isinstance(it, dict) and it.get("vid") and it["vid"] not in self.ytm_seen:
                self.ytm_seen.add(it["vid"])
                self.ytm_queue.append(it)
        st = self.stats
        # qurulma yarımçıq qalmışdısa — bərpa olunan növbə bitən kimi yenidən qurulsun (Telegram qaldığı yerdən)
        st["last_build"] = None if data.get("build_pending") else data.get("last_build")
        st["queue_built"] = data.get("queue_built") or 0
        for k, v in (data.get("counters") or {}).items():
            if k in st and isinstance(v, int):
                st[k] = v
        self.bytes = int(data.get("bytes") or 0)
        self.restored = len(self.queue) + len(self.ytm_queue)
        self._saved_sig = self._sig()
        if self.restored:
            ago = int((time.time() - (data.get("saved") or time.time())) / 60)
            logger.info(f"💾 Depo növbəsi bərpa olundu: {len(self.queue)} əsas + {len(self.ytm_queue)} YT Music "
                        f"({ago} dəq. əvvəl yazılıb)")

    def reset_state(self):
        """🔁 Növbəni yenidən qur: növbə + Telegram skan mövqeləri sıfırlanır."""
        self.queue.clear()
        self.seen.clear()
        self.stats["queue_built"] = 0
        self.stats["last_build"] = None
        self._set("tgscan", "{}")

    # ── Telegram artımlı skan mövqeləri ──
    def tg_state(self, peer_id) -> dict:
        try:
            all_ = json.loads(self._get("tgscan") or "{}")
        except ValueError:
            all_ = {}
        st = all_.get(str(peer_id))
        return st if isinstance(st, dict) else {}

    async def tg_checkpoint(self, peer_id, state: dict):
        """Əvvəl növbə (skan olunanlar itməsin), sonra skan mövqeyi yazılır."""
        await self.save_state(force=True)
        try:
            all_ = json.loads(self._get("tgscan") or "{}")
        except ValueError:
            all_ = {}
        all_[str(peer_id)] = state
        self._set("tgscan", json.dumps(all_))

    async def tg_scan_source(self, ent, limit, on_item, should_stop, progress=None) -> dict:
        peer_id = self.tg.describe(ent)[0]
        state = dict(self.tg_state(peer_id))

        async def checkpoint(stt):
            await self.tg_checkpoint(peer_id, stt)

        return await self.tg.scan(ent, limit, on_item, should_stop, progress, state=state, checkpoint=checkpoint)

    # ── axtarış nəticələrinin keşi (bazada): eyni ad üçün API-lər təkrar yorulmasın ──
    def _init_resolve_table(self):
        try:
            conn = get_db()._conn
            conn.execute("""CREATE TABLE IF NOT EXISTS depo_resolve (
                              qkey TEXT PRIMARY KEY, vid TEXT, meta TEXT, ts INTEGER)""")
            conn.commit()
        except Exception as e:
            logger.warning(f"depo_resolve cədvəli yaradılmadı: {e}")

    @staticmethod
    def _qkey(item) -> str:
        if item.get("sp_id"):
            return f"sp:{item['sp_id']}"
        return "q:" + " ".join(sorted(_toks(item.get("query") or item.get("title") or "")))

    def _rc_get(self, key):
        try:
            row = get_db()._conn.execute("SELECT vid, meta, ts FROM depo_resolve WHERE qkey = ?", (key,)).fetchone()
        except Exception:
            return None
        if not row:
            return None
        vid, meta, ts = row[0], row[1], row[2] or 0
        if not vid and time.time() - ts > NOTFOUND_RETRY:
            return None                              # köhnə "tapılmadı" — yenidən sına
        try:
            meta = json.loads(meta) if meta else None
        except ValueError:
            meta = None
        return {"vid": vid or None, "meta": meta}

    def _rc_put(self, key, vid, meta=None):
        try:
            conn = get_db()._conn
            conn.execute("INSERT OR REPLACE INTO depo_resolve (qkey, vid, meta, ts) VALUES (?, ?, ?, ?)",
                         (key, vid or "", json.dumps(meta) if meta else None, int(time.time())))
            conn.commit()
        except Exception as e:
            logger.debug(f"depo_resolve yazılmadı: {e}")

    # ── idarə ──
    def start(self):
        self._set("on", "1")
        if self.running:
            return
        self.stop_event = threading.Event()
        self.stats.update(started=time.time(), note="")
        # əvvəlki dayandırmada yarımçıq qalan mahnılar növbənin önünə
        for _, it in sorted(self.inflight.items(), reverse=True):
            (self.ytm_queue if it.get("src") == "ytm" else self.queue).appendleft(it)
        self.inflight.clear()
        ensure_thread_pool(self.workers)
        self.task = asyncio.create_task(self.loop())
        logger.info("📦 Depo doldurucu işə düşdü")

    async def stop(self, persist=True):
        if persist:
            self._set("on", "0")
        self.save_state_now()                  # işçilərin əlindəki mahnılar da (ləğvdən əvvəl)
        self.stop_event.set()
        if self.task and not self.task.done():
            self.task.cancel()
            try:
                await self.task
            except (asyncio.CancelledError, Exception):
                pass
        self.task = None
        self.current.clear()
        self.wstate.clear()
        logger.info("📦 Depo doldurucu dayandı")

    # ── növbə ──
    def _key(self, item):
        return item.get("url") or (item.get("query") or "").lower()

    def _ok(self, item) -> bool:
        key = self._key(item)
        return bool(key) and key not in self.seen and (item.get("raw_duration") or 0) <= MAX_DURATION

    def _add(self, item):
        if not self._ok(item):
            return False
        self.seen.add(self._key(item))
        self.queue.append(item)
        return True

    def add_front(self, items) -> int:
        """Siyahını növbənin əvvəlinə (sırası qorunur) — yeni əlavə olunan mənbə gözləməsin."""
        fresh = [it for it in items if self._ok(it)]
        uniq, keys = [], set()
        for it in fresh:
            k = self._key(it)
            if k not in keys:
                keys.add(k)
                uniq.append(it)
        for it in reversed(uniq):
            self.seen.add(self._key(it))
            self.queue.appendleft(it)
        return len(uniq)

    async def build_queue(self):
        ctx = self.ctx
        ym = ctx.youtube_manager
        spotify_data = getattr(ctx, "links_spotify_data", None)
        spotify = getattr(ctx, "spotify", None)
        now = int(time.time())
        db = get_db()

        # 1) istifadəçilərin sevdiyi ifaçılar
        try:
            rows = db._conn.execute(
                """SELECT title, url, COUNT(*) AS n FROM downloads
                   WHERE ts >= ? AND title IS NOT NULL GROUP BY COALESCE(url, title) ORDER BY n DESC LIMIT 200""",
                (now - 7 * 86400,)).fetchall()
        except Exception:
            rows = []
        artists = {}
        for r in rows:
            artist = (r["title"] or "").split(" - ", 1)[0].split(",")[0].strip()
            if artist and artist != "YouTube":
                artists[artist] = artists.get(artist, 0) + r["n"]
        top_artists = sorted(artists, key=artists.get, reverse=True)[:12]
        if spotify and spotify_data:
            for name in top_artists:
                if self.stop_event.is_set():
                    return
                try:
                    res = await asyncio.to_thread(spotify.sp.search, q=f"artist:{name}", type="artist", limit=1)
                    found = (res.get("artists") or {}).get("items") or []
                    if not found:
                        continue
                    data = await spotify_data("artist", found[0]["id"])
                    for it in (data.get("top") or data.get("items") or [])[:10]:
                        self._add(it)
                except Exception as e:
                    logger.debug(f"Doldurucu: ifaçı {name}: {e}")

        # 2) ən çox yüklənən mahnıların Mix-i
        for r in rows[:8]:
            if self.stop_event.is_set():
                return
            m = re.search(r"v=([\w-]{11})", r["url"] or "")
            if not m:
                continue
            try:
                for it in await ym.youtube_mix(m.group(1), limit=15):
                    self._add(it)
            except Exception as e:
                logger.debug(f"Doldurucu: mix {m.group(1)}: {e}")

        # 3) mənbələr
        for src in self.sources():
            if self.stop_event.is_set():
                return
            try:
                if src["kind"].startswith("sp_") and spotify_data:
                    data = await spotify_data(src["kind"][3:], src["id"])
                    for it in data.get("items") or []:
                        self._add(it)
                elif src["kind"] == "yt_list":
                    data = await ym.playlist_entries(yt_list_url(src["id"]), limit=200)
                    for it in data["entries"]:
                        self._add(it)
                elif src["kind"] == "tg_channel":
                    if self.tg and await self.tg.ok():
                        ent = await self.tg.entity({"type": "user", "name": src["id"]})
                        await self.tg_scan_source(ent, None, self._add, self.stop_event.is_set)
                    else:
                        data = await tg_channel_items(src["id"])
                        for it in data["items"]:
                            self._add(it)
                elif src["kind"] == "tg_chat":
                    if not (self.tg and await self.tg.ok()):
                        logger.info(f"Doldurucu: {src.get('label')}: userbot aktiv deyil — ötürüldü")
                        continue
                    ent = await self.tg.entity_for_source(src)
                    await self.tg_scan_source(ent, src.get("limit"), self._add, self.stop_event.is_set)
            except Exception as e:
                logger.warning(f"Doldurucu: mənbə {src.get('label') or src['id']}: {e}")

        self.stats["last_build"] = time.time()
        self.stats["queue_built"] = len(self.queue)
        logger.info(f"📦 Doldurucu növbəsi: {len(self.queue)} mahnı")

    async def lookup(self, item):
        """Telegram adı → Spotify / iTunes-da rəsmi mahnı. Tapılsa item-i yeniləyir, platforma linkini qaytarır."""
        c = item.get("clean") or {}
        artist, track = c.get("artist", ""), c.get("track") or item.get("title", "")
        dur = int(item.get("raw_duration") or 0)
        found = None
        try:
            found = await spotify_lookup(getattr(self.ctx, "spotify", None), artist, track, dur)
        except Exception as e:
            logger.debug(f"Spotify axtarışı ({artist} - {track}): {e}")
        if not found:
            try:
                found = await itunes_lookup(artist, track, dur)
            except Exception as e:
                logger.debug(f"iTunes axtarışı ({artist} - {track}): {e}")
        if not found:
            return None
        item["meta"] = {"artist": found["artist"], "track": found["track"]}
        item["title"] = f"{found['artist']} - {found['track']}"
        item["query"] = f"{found['first']} {found['track']}".strip()
        item["thumb"] = item.get("thumb") or found["thumb"]
        if found["duration"]:
            item["raw_duration"] = found["duration"]
        if found["sp_id"]:
            item["sp_id"] = found["sp_id"]
        logger.info(f"🔎 {artist} - {track} → {item['title']} ({found['src']})")
        return found["link"]

    async def _songlink_youtube(self, link: str):
        songlink = getattr(self.ctx, "music_songlink", None)
        if not (link and songlink):
            return None
        try:
            sl = await songlink(link)
            links = (sl or {}).get("links") or {}
            url = links.get("youtube") or links.get("youtubeMusic")
            vid = re.search(r"(?:v=|youtu\.be/)([\w-]{11})", url or "")
            return f"https://www.youtube.com/watch?v={vid.group(1)}" if vid else None
        except Exception as e:
            logger.debug(f"song.link alınmadı: {e}")
            return None

    async def resolve(self, item):
        """(YouTube linki, keşdən?) — keş → [Telegram adı: Spotify/iTunes axtarışı] → song.link → YouTube axtarışı."""
        if item.get("url"):
            return item["url"], False
        key = self._qkey(item)
        hit = self._rc_get(key)
        if hit:
            if hit["meta"] and not item.get("meta"):
                item["meta"] = hit["meta"]
                item["title"] = f"{hit['meta']['artist']} - {hit['meta']['track']}"
            return (f"https://www.youtube.com/watch?v={hit['vid']}" if hit["vid"] else None), True

        link = None
        if item.get("sp_id"):
            link = f"https://open.spotify.com/track/{item['sp_id']}"
        elif item.get("lookup"):
            link = await self.lookup(item)
        url = await self._songlink_youtube(link)
        if not url:
            resolve = getattr(self.ctx, "music_resolve_youtube", None)
            url = await resolve(item.get("query") or item["title"], item.get("raw_duration") or 0) if resolve else None
        vid = re.search(r"v=([\w-]{11})", url or "")
        self._rc_put(key, vid.group(1) if vid else None, item.get("meta"))
        return url, False

    # ── 🎵 YouTube Music növbəsi ──
    async def ytm_add_items(self, items) -> tuple:
        """(növbəyə əlavə, artıq depoda) — depoda olanlar növbəyə heç düşmür."""
        fresh = [it for it in items if it["vid"] not in self.ytm_seen]
        if len(self.ytm_seen) > YTM_SEEN_CAP:
            self.ytm_seen.clear()

        def check():
            return [bool(audio_cache.get_cached(it["vid"])) for it in fresh]

        flags = await asyncio.to_thread(check) if fresh else []
        added = in_depo = 0
        for it, cached in zip(fresh, flags):
            self.ytm_seen.add(it["vid"])
            if cached:
                in_depo += 1
            else:
                self.ytm_queue.append(it)
                added += 1
        return added, in_depo

    def _user_artists(self) -> list:
        try:
            rows = get_db()._conn.execute(
                """SELECT title, COUNT(*) AS n FROM downloads WHERE ts >= ? AND title IS NOT NULL
                   GROUP BY title ORDER BY n DESC LIMIT 300""", (int(time.time()) - 30 * 86400,)).fetchall()
        except Exception:
            return []
        names = {}
        for r in rows:
            a = (r[0] or "").split(" - ", 1)[0].split(",")[0].strip()
            if a and a not in ("YouTube", "Naməlum"):
                names[a] = names.get(a, 0) + r[1]
        return sorted(names, key=names.get, reverse=True)[:40]

    async def ytm_feeder(self):
        """YT Music kataloqunu fonda gəzir — növbə azaldıqca növbəti ifaçını açır."""
        while not self.stop_event.is_set():
            if not self.ytm_on or not self.ytm:
                await asyncio.sleep(10)
                continue
            if len(self.ytm_queue) >= YTM_LOW_WATER:
                await asyncio.sleep(5)
                continue
            try:
                ok = await self.ytm.step(self.ytm_add_items, self._user_artists, self.stop_event)
                if not ok:
                    await asyncio.sleep(3600)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"🎵 YT Music skaneri: {e}")
                self.ytm.stats["note"] = f"xəta: {str(e)[:60]} — 1 dəq. sonra"
                self.ytm.yt = None                     # sessiyanı yenilə
                await asyncio.sleep(60)

    # ── 🧵 İşçilər ──
    async def loop(self):
        """Nəzarətçi: istənilən sayda işçi saxlayır (➕/➖ dərhal tətbiq olunur) + YT Music skaneri."""
        await asyncio.sleep(5)
        feeder = asyncio.create_task(self.ytm_feeder())
        saver = asyncio.create_task(self.state_saver())
        try:
            while not self.stop_event.is_set():
                want = self.workers
                for i in range(want):
                    t = self.workers_tasks.get(i)
                    if t is None or t.done():
                        self.workers_tasks[i] = asyncio.create_task(self.worker(i))
                for i in [k for k, t in self.workers_tasks.items() if t.done()]:
                    self.workers_tasks.pop(i, None)
                await asyncio.sleep(2)
        finally:
            tasks = [feeder, saver, *self.workers_tasks.values()]
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self.workers_tasks.clear()

    async def next_item(self, idx: int):
        """Növbəti mahnı: əvvəl əsas növbə (mənbələr), boşdursa YT Music kataloqu. Yoxdursa None (gözləyib)."""
        fetch = getattr(self.ctx, "music_fetch_audio", None)
        if not fetch:
            self.stats["note"] = "music_plugin yüklənməyib"
            await asyncio.sleep(60)
            return None
        if not await audio_cache.depo_chat_id(self.ctx.bot):
            self.stats["note"] = "⚠️ depo kanalı əlçatan deyil — 10 dəq. sonra yenidən"
            await asyncio.sleep(600)
            return None
        if self.queue:
            self.stats["note"] = ""
            return self.queue.popleft()
        since = time.time() - (self.stats["last_build"] or 0)
        need_build = not self.stats["last_build"] or since >= IDLE_SLEEP
        if need_build and not self.build_lock.locked():
            async with self.build_lock:
                if not self.queue:
                    self.stats["note"] = "növbə qurulur..."
                    self.seen.clear()
                    await self.build_queue()
                    if not self.stop_event.is_set():
                        self.stats["last_build"] = time.time()
            await self.save_state(force=True)
            if self.queue:
                self.stats["note"] = ""
                return self.queue.popleft()
        if self.ytm_queue:
            self.stats["note"] = ""
            return self.ytm_queue.popleft()
        if not self.build_lock.locked():
            self.stats["note"] = ("növbə bitdi — YT Music kataloqu oxunur" if self.ytm_on
                                  else "növbə bitdi — yeni mahnılar üçün gözləyir")
        await asyncio.sleep(5 if self.ytm_on else min(60, max(5, IDLE_SLEEP - since)))
        return None

    async def worker(self, idx: int):
        try:
            while not self.stop_event.is_set():
                if idx >= self.workers:                 # ➖ basıldı — artıq işçi dayanır
                    return
                self._phase(idx, "WAIT", "")
                item = await self.next_item(idx)
                if item is not None:
                    await self.process(idx, item)
        finally:
            self.wstate.pop(idx, None)

    async def process(self, idx: int, item: dict):
        fetch = getattr(self.ctx, "music_fetch_audio", None)
        had_url = bool(item.get("url"))
        t0 = time.time()
        self.current[idx] = item["title"]
        self.inflight[idx] = item
        self._phase(idx, "SRCH", item["title"])
        try:
            url, from_cache = await self.resolve(item)
            searched = not had_url and not from_cache       # API-lərdə axtarış edildi
            vid = re.search(r"v=([\w-]{11})", url or "")
            if not vid:
                self.stats["failed"] += 1
                self._event(idx, "notfound", item["title"], time.time() - t0)
                if searched:
                    logger.info(f"Doldurucu: {item['title']}: tapılmadı")
                    self._phase(idx, "SLP", item["title"])
                    await asyncio.sleep(min(2, self.delay))
                return
            self._phase(idx, "CHK", item["title"])
            if await asyncio.to_thread(audio_cache.get_cached, vid.group(1)):
                self.stats["cached"] += 1
                self._event(idx, "cached", item["title"], time.time() - t0)
                if searched:
                    self._phase(idx, "SLP", item["title"])
                    await asyncio.sleep(min(2, self.delay))
                return
            self._phase(idx, "DL", item["title"])
            res = await fetch(url, item["title"], int(item.get("raw_duration") or 0), self.stop_event,
                              meta=item.get("meta"), thumb_url=item.get("thumb"))
            size = 0
            if res.get("path"):
                try:
                    size = os.path.getsize(res["path"])
                    os.remove(res["path"])
                except OSError:
                    pass
            if res.get("file_id") and not res.get("cached"):
                self.stats["done"] += 1
                self._event(idx, "done", item.get("title") or "", time.time() - t0, size)
            else:
                self.stats["cached"] += 1
                self._event(idx, "cached", item.get("title") or "", time.time() - t0)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            if self.stop_event.is_set():
                return
            self.stats["failed"] += 1
            self._event(idx, "failed", item["title"], time.time() - t0)
            logger.info(f"Doldurucu: {item['title']}: {e}")
        finally:
            self.current.pop(idx, None)
            if not self.stop_event.is_set():       # dayandırılıbsa — element növbəyə qayıtmaq üçün qalır
                self.inflight.pop(idx, None)
        # istifadəçilər yükləyirsə onlara mane olmasın deyə fasilə
        if self.delay:
            self._phase(idx, "SLP", "")
            await asyncio.sleep(self.delay)



# ───────────────────────── 📊 Canlı monitor (depo top) ─────────────────────────
TOP_INTERVAL = 1.0           # yenilənmə (san.)
TOP_MAX_RUNTIME = 600        # avtomatik dayanma (san.) — ▶️ Davam ilə uzanır
TOP_SPARK_MIN = 15           # sürət qrafiki: son neçə dəqiqə
TOP_TITLE = 27               # cədvəldə mahnı adının eni
PHASE_ORDER = {"DL": 0, "CHK": 1, "SRCH": 2, "SLP": 3, "WAIT": 4}
EVENT_TAGS = {"done": "OK  ", "cached": "SKIP", "failed": "ERR ", "notfound": "404 "}
_SPARK = "▁▂▃▄▅▆▇█"


class ProcMeter:
    """Botun öz prosesi: CPU% (os.times fərqi, uşaq proseslər daxil), RSS, thread sayı, load."""

    def __init__(self):
        self.prev = None

    @staticmethod
    def _rss() -> int:
        try:
            with open("/proc/self/statm") as f:
                return int(f.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")
        except Exception:
            pass
        try:
            import psutil
            return psutil.Process().memory_info().rss
        except Exception:
            return 0

    def sample(self) -> dict:
        t, now = os.times(), time.monotonic()
        cpu_t = t.user + t.system + t.children_user + t.children_system
        pct = 0.0
        if self.prev:
            pct = max(0.0, (cpu_t - self.prev[0]) / max(now - self.prev[1], 1e-6) * 100)
        self.prev = (cpu_t, now)
        try:
            load = " ".join(f"{x:.2f}" for x in os.getloadavg())
        except (AttributeError, OSError):
            load = "-"
        return {"cpu": pct, "rss": self._rss(), "threads": threading.active_count(), "load": load}


def _human(n: float) -> str:
    for unit in ("B", "K", "M", "G", "T"):
        if abs(n) < 1024 or unit == "T":
            return f"{n:.1f}{unit}" if unit in ("G", "T") else f"{n:.0f}{unit}"
        n /= 1024
    return f"{n:.1f}T"


def _mmss(sec: float) -> str:
    sec = max(0, int(sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _bar(frac: float, width: int = 14) -> str:
    frac = max(0.0, min(1.0, frac))
    n = round(frac * width)
    return "|" * n + " " * (width - n)


def _spark(values) -> str:
    top = max(values) if values else 0
    if not top:
        return _SPARK[0] * len(values)
    return "".join(_SPARK[min(len(_SPARK) - 1, round(v / top * (len(_SPARK) - 1)))] for v in values)


def _short(title: str, width: int) -> str:
    t = _EMOJI_RE.sub("", title or "").replace("\n", " ").strip()
    return t if len(t) <= width else t[:width - 1] + "…"


def bot_tz():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(os.getenv("BOT_TZ", "Asia/Baku"))
    except Exception:
        from datetime import timedelta, timezone
        return timezone(timedelta(hours=4))


def render_top(filler, sess: dict, proc: dict, now_str: str) -> str:
    now = time.time()
    st = filler.stats
    on = filler.running
    want = filler.workers
    ws = dict(filler.wstate)
    active = sum(1 for w in ws.values() if w["phase"] in ("DL", "CHK", "SRCH"))
    # sürət: son 15 dəqiqə, hər dəqiqə üçün yüklənən mahnı sayı
    buckets = [0] * TOP_SPARK_MIN
    for ts in reversed(filler.done_ts):
        age = now - ts
        if age >= TOP_SPARK_MIN * 60:
            break
        buckets[TOP_SPARK_MIN - 1 - int(age // 60)] += 1
    last5 = sum(buckets[-5:]) / 5
    avg = (sum(filler.durations) / len(filler.durations)) if filler.durations else 0
    uptime = _mmss(now - st["started"]) if on and st["started"] else "-"

    lines = [
        f"Wrk [{_bar(active / max(want, 1))}] {active}/{want} aktiv  fas {filler.delay}s",
        f"Que esas {len(filler.queue)}" + (f"/{st['queue_built']}" if st["queue_built"] else "")
        + f"  ytm {len(filler.ytm_queue)}",
        f"Spd {_spark(buckets)} {last5:.1f}/dq",
        f"Avg {avg:.0f}s/mahni  cemi {_human(filler.bytes)}",
        f"Tot up {st['done']}  skip {st['cached']}  err {st['failed']}",
        f"Bot cpu {proc['cpu']:.0f}%  ram {_human(proc['rss'])}  thr {proc['threads']}",
        f"Up  {uptime}  load {proc['load']}",
        "",
    ]
    if sess["view"] == "log":
        lines.append(f"{'VAXT':<8} {'#':>2} {'NOV':<4} {'SAN':>4} {'OLCU':>5} MAHNI")
        evs = list(filler.events)[-24:][::-1]
        for ts, idx, kind, title, secs, size in evs:
            t = datetime.fromtimestamp(ts, bot_tz()).strftime("%H:%M:%S")
            lines.append(f"{t:<8} {idx + 1:>2} {EVENT_TAGS.get(kind, kind[:4]):<4} {secs:>4.0f} "
                         f"{(_human(size) if size else '-'):>5} {_short(title, 22)}")
        if not evs:
            lines.append("  (hələ hadisə yoxdur)")
    else:
        lines.append(f"{'#':>2} {'ST':<4} {'VAXT':>6} MAHNI")
        rows = []
        for i in range(max(want, max(ws) + 1 if ws else 0)):
            w = ws.get(i)
            if w is None:
                rows.append((i, "-", 0.0, "(başlayır)" if on and i < want else "(dayanıb)"))
                continue
            ph = w["phase"] or "WAIT"
            title = w["title"] or {"SLP": "(fasilə)", "WAIT": "(növbə gözləyir)"}.get(ph, "")
            rows.append((i, ph, now - w["since"], title))
        if sess["sort"] == "time":
            rows.sort(key=lambda r: (PHASE_ORDER.get(r[1], 9), -r[2]))
        for i, ph, el, title in rows:
            lines.append(f"{i + 1:>2} {ph:<4} {_mmss(el) if ph != '-' else '':>6} {_short(title, TOP_TITLE)}")

    if sess["paused"]:
        state = "⏸ fasilə"
    else:
        left = int(sess["until"] - now)
        state = f"🔄 hər {TOP_INTERVAL:g} san. · {left} san. qalıb"
    head = (f"📦 <b>depo top</b> · {'🟢 işləyir' if on else '🔴 söndürülüb'} · "
            f"⬆️ <b>{last5:.1f}</b>/dəq\n<i>{now_str} · {state}</i>")
    if st["note"]:
        head += f"\nℹ️ <i>{escape(st['note'][:120])}</i>"
    legend = ("<i>DL yükləyir · SRCH axtarır · CHK depo yoxlanır · SLP fasilə · WAIT növbə</i>"
              if sess["view"] != "log" else "<i>OK yükləndi · SKIP artıq depoda · ERR xəta · 404 tapılmadı</i>")
    return f"{head}\n<pre>{escape(chr(10).join(lines))}</pre>{legend}"


# ───────────────────────── 📱 Userbot (Telethon) ─────────────────────────
class TgUser:
    def __init__(self, context):
        self.ctx = context
        self.own_client = False
        self.client = None
        self.task = None
        self.me = None
        if not HAS_TELETHON:
            logger.warning("⚠️ telethon quraşdırılmayıb — Telegram mənbələri yalnız veb (public) rejimdə işləyəcək")
            return
        existing = getattr(context, "telethon_client", None)
        if existing:                                  # köhnə telethon_plugin hələ yüklənibsə — eyni sessiya
            self.client = existing
            return
        if not API_ID or not API_HASH:
            logger.warning("⚠️ TELETHON_API_ID / TELETHON_API_HASH yoxdur — userbot işə düşməyəcək")
            return
        self.client = TelegramClient(SESSION_FILE, int(API_ID), API_HASH)
        self.client.flood_sleep_threshold = 120
        self.own_client = True
        context.telethon_client = self.client

    @property
    def available(self) -> bool:
        return self.client is not None

    def start(self):
        if not self.own_client:
            return
        try:
            self.task = asyncio.create_task(self._connect())
        except RuntimeError:
            self.task = None
        self.ctx.telethon_task = self.task

    async def _connect(self):
        try:
            logger.info("📱 Telethon userbot qoşulur...")
            await self.client.connect()
            if not await self.client.is_user_authorized():
                logger.warning("⚠️ Userbot avtorizasiya olunmayıb! Əvvəlcə login.py ilə giriş edin.")
                return
            self.me = await self.client.get_me()
            logger.info(f"✅ Telethon userbot qoşuldu: {self.me.first_name} (@{self.me.username or 'yoxdur'})")
        except Exception as e:
            logger.error(f"❌ Telethon qoşulma xətası: {e}", exc_info=True)

    async def ok(self) -> bool:
        if not self.client:
            return False
        try:
            if not self.client.is_connected():
                if self.own_client and (not self.task or self.task.done()):
                    await self._connect()
                if not self.client.is_connected():
                    return False
            return await self.client.is_user_authorized()
        except Exception:
            return False

    async def entity(self, target: dict):
        """(entity, qoşuldu?) deyil — sadəcə entity; invite üçün lazım gələrsə qoşulur."""
        c = self.client
        if target["type"] == "user":
            return await c.get_entity(target["name"])
        if target["type"] == "id":
            try:
                return await c.get_entity(target["id"])
            except (ValueError, TypeError):
                await c.get_dialogs()                 # entity keşdə yoxdur — dialoqlardan doldur
                return await c.get_entity(target["id"])
        if target["type"] == "invite":
            h = target["hash"]
            try:
                info = await c(CheckChatInviteRequest(h))
            except (InviteHashExpiredError, InviteHashInvalidError):
                raise RuntimeError("Dəvət linki etibarsızdır və ya vaxtı keçib")
            if isinstance(info, (ChatInviteAlready, ChatInvitePeek)):
                return info.chat
            try:
                upd = await c(ImportChatInviteRequest(h))
            except UserAlreadyParticipantError:
                info = await c(CheckChatInviteRequest(h))
                return info.chat
            except Exception as e:
                if InviteRequestSentError and isinstance(e, InviteRequestSentError):
                    raise RuntimeError("Qoşulma sorğusu göndərildi — admin təsdiqləyəndən sonra yenidən əlavə et")
                raise
            logger.info("📱 Userbot dəvət linki ilə çata qoşuldu")
            return upd.chats[0]
        raise RuntimeError("Naməlum hədəf")

    async def entity_for_source(self, src: dict):
        if src.get("username"):
            try:
                return await self.client.get_entity(src["username"])
            except Exception:
                pass
        return await self.entity({"type": "id", "id": int(src["id"])})

    @staticmethod
    def describe(ent):
        title = getattr(ent, "title", None) or " ".join(
            x for x in (getattr(ent, "first_name", None), getattr(ent, "last_name", None)) if x) or "Çat"
        return tl_utils.get_peer_id(ent), title, getattr(ent, "username", None)

    async def scan(self, ent, limit, on_item, should_stop, progress=None, state=None, checkpoint=None) -> dict:
        """Çatdakı audioları (yeni → köhnə) təmizlənmiş adla on_item-ə ötürür.

        state verilərsə (artımlı skan): {"top": ən yeni oxunan id, "bottom": ən köhnə oxunan id,
        "count": oxunan audio sayı, "complete": tarixçə bitib?}
          1) top-dan yeni mesajlar oxunur
          2) tarixçə bitməyibsə — bottom-dan köhnəyə doğru davam edilir (limit varsa count-a qədər)
        Vəziyyət checkpoint(state) ilə hər TG_CHECKPOINT mesajdan bir və sonda yazılır."""
        _, title, _ = self.describe(ent)
        st = {"messages": 0, "items": 0, "added": 0, "unreadable": 0, "new": 0, "resumed": False}

        def handle(msg):
            st["messages"] += 1
            it = tl_message_item(msg, title)
            if not it:
                st["unreadable"] += 1
            else:
                st["items"] += 1
                if on_item(it):
                    st["added"] += 1

        if state is None:
            async for msg in self.client.iter_messages(ent, limit=limit, filter=InputMessagesFilterMusic):
                if should_stop():
                    break
                handle(msg)
                if progress and st["messages"] % 100 == 0:
                    await progress(st)
            return st

        top = int(state.get("top") or 0)
        newest = top
        stopped = False
        # 1) son skandan sonra gələn yeni audiolar
        if top:
            async for msg in self.client.iter_messages(ent, min_id=top, filter=InputMessagesFilterMusic):
                if should_stop():
                    stopped = True
                    break
                newest = max(newest, msg.id)
                handle(msg)
                st["new"] += 1
                if progress and st["messages"] % 100 == 0:
                    await progress(st)
        if not stopped:
            state["top"] = newest
        # 2) yarımçıq tarixçə — qaldığı yerdən köhnəyə doğru
        if not stopped and not state.get("complete"):
            count = int(state.get("count") or 0)
            remaining = (limit - count) if limit else None
            st["resumed"] = bool(state.get("bottom"))
            if remaining is None or remaining > 0:
                async for msg in self.client.iter_messages(ent, offset_id=int(state.get("bottom") or 0),
                                                           limit=remaining, filter=InputMessagesFilterMusic):
                    if should_stop():
                        stopped = True
                        break
                    handle(msg)
                    if not state.get("top") or msg.id > state["top"]:
                        state["top"] = msg.id
                    state["bottom"] = msg.id
                    state["count"] = int(state.get("count") or 0) + 1
                    if checkpoint and state["count"] % TG_CHECKPOINT == 0:
                        await checkpoint(dict(state))
                    if progress and st["messages"] % 100 == 0:
                        await progress(st)
            if not stopped:
                state["complete"] = True
        if checkpoint:
            await checkpoint(dict(state))
        return st


# ───────────────────────── setup ─────────────────────────
def setup(context):
    dp = context.dp
    bot = context.bot
    filler = Filler(context)
    context.depo_filler = filler
    tg = TgUser(context)
    filler.tg = tg
    context.depo_userbot = tg
    tg.start()

    def is_creator(uid) -> bool:
        return bool(context.creator_id) and uid == context.creator_id

    # ➕ Mənbə əlavə et: creator-dan link gözlənilir (5 dəq.) — links_plugin bu linki yükləməyə götürməsin
    add_wait = {"until": 0, "chat_id": None, "msg_id": None}
    scan_state = {"running": False, "stop": False, "task": None}
    context.depo_scan_state = scan_state

    def depo_waiting(uid) -> bool:
        return is_creator(uid) and time.time() < add_wait["until"]

    context.depo_waiting = depo_waiting

    stop_kb = InlineKeyboardMarkup(inline_keyboard=[[btn("⏹ Skanı dayandır", "scan:stop")]])

    # ── 🔎 Skan (həm /depo add, həm /scan_chat) ──
    async def run_scan(chat_id: int, ent, limit, header: str, incremental: bool = False):
        _, title, _ = tg.describe(ent)
        scan_state.update(running=True, stop=False)
        limit_label = f"son {limit} audio" if limit else "bütün tarixçə"
        status = await bot.send_message(
            chat_id, f"🔎 <b>{escape(header)}</b>\n<i>{escape(title)} skan edilir ({limit_label})...</i>",
            parse_mode="HTML", reply_markup=stop_kb)
        batch, last_edit, added = [], [0.0], [0]

        def collect(it):
            batch.append(it)
            return True

        async def flush():
            if batch:
                added[0] += filler.add_front(batch)
                batch.clear()

        async def progress(st):
            await flush()                                 # doldurucu gözləmədən işə başlasın
            if time.time() - last_edit[0] < 4:
                return
            last_edit[0] = time.time()
            try:
                await status.edit_text(
                    f"🔎 <b>{escape(header)}</b>\n<i>{escape(title)}</i>\n\n"
                    f"📨 Audio: <b>{st['messages']}</b> · 🎶 adı oxundu: {st['items']} · ➕ növbəyə: {added[0]}",
                    parse_mode="HTML", reply_markup=stop_kb)
            except Exception:
                pass

        try:
            if incremental:      # daimi mənbə — mövqe yadda qalır, növbəti qurulma təkrar oxumur
                st = await filler.tg_scan_source(ent, limit, collect, lambda: scan_state["stop"], progress)
            else:
                st = await tg.scan(ent, limit, collect, lambda: scan_state["stop"], progress)
            await flush()
            head = "⏹ <b>Skan dayandırıldı</b>" if scan_state["stop"] else "✅ <b>Skan tamamlandı</b>"
            tail = ("" if filler.running else
                    "\n\n⚠️ Doldurucu söndürülüb — yükləmə üçün: /depo on")
            await status.edit_text(
                f"{head} — {escape(title)}\n\n"
                f"📨 Audio mesaj: <b>{st['messages']}</b>\n"
                f"🎶 Adı təmizlənib oxundu: <b>{st['items']}</b>\n"
                f"➕ Növbəyə əlavə olundu: <b>{added[0]}</b> <i>(qalanı artıq növbədə idi)</i>\n"
                f"❔ Adı oxunmayan: {st['unreadable']}\n\n"
                f"<i>Hər mahnı Spotify / iTunes-da axtarılıb song.link ilə YouTube-dan yüklənəcək. "
                f"Vəziyyət:</i> /depo{tail}",
                parse_mode="HTML")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Çat skan xətası ({title}): {e}", exc_info=True)
            try:
                await status.edit_text(f"❌ <b>Skan xətası:</b> <code>{escape(str(e))[:300]}</code>",
                                       parse_mode="HTML")
            except Exception:
                pass
        finally:
            scan_state.update(running=False, stop=False, task=None)

    def launch_scan(chat_id, ent, limit, header, incremental=False) -> bool:
        if scan_state["running"]:
            return False
        scan_state["running"] = True
        scan_state["task"] = asyncio.create_task(run_scan(chat_id, ent, limit, header, incremental))
        return True

    async def add_source(link_text: str, chat_id: int = None):
        """(ok, mesaj) — linki mənbə kimi yoxlayıb əlavə edir; Telegram üçün skanı da başladır."""
        target, limit = parse_tg_target(link_text)
        if target:
            if await tg.ok():
                try:
                    ent = await tg.entity(target)
                except Exception as e:
                    if target["type"] != "user":
                        return False, f"❌ Çat açılmadı: <code>{escape(str(e))[:200]}</code>"
                    ent = None
                    logger.info(f"Userbot @{target['name']} açmadı, veb sınanır: {e}")
                if ent is not None:
                    peer_id, title, uname = tg.describe(ent)
                    src = {"kind": "tg_chat", "id": str(peer_id), "username": uname, "limit": limit,
                           "label": f"✈️ {title}" + (f" (@{uname})" if uname else "")}
                    srcs = [s for s in filler.sources()
                            if not (s["id"] == src["id"] or (uname and s["kind"] == "tg_channel"
                                                             and s["id"].lower() == uname.lower()))]
                    filler.save_sources(srcs + [src])
                    note = f"✅ Telegram mənbəyi əlavə olundu: <b>{escape(title)}</b>"
                    if chat_id:
                        if launch_scan(chat_id, ent, limit, "Yeni mənbə skan edilir", incremental=True):
                            note += "\n<i>Skan başladı — nəticə ayrıca mesajda.</i>"
                        else:
                            note += "\n<i>Başqa skan gedir — bu çat növbəti qurulmada oxunacaq.</i>"
                    return True, note
            elif target["type"] != "user":
                return False, ("❌ Gizli kanal / qrup / ID üçün userbot lazımdır, amma aktiv deyil.\n"
                               "<i>Serverdə login.py ilə sessiya yarat və TELETHON_API_ID/HASH-i yoxla.</i> /userbot")
            # userbot yoxdur — public kanal veb səhifəsindən
            name = target["name"]
            try:
                data = await tg_channel_items(name, pages=3)
            except Exception as e:
                return False, f"❌ Telegram kanalı açılmadı: <code>{escape(str(e))[:200]}</code>"
            if not data["items"]:
                return False, ("❌ Bu kanalın son postlarında mahnı tapılmadı.\n"
                               "<i>Userbot olmadan kanal public olmalıdır (t.me/kanal) və orada audio və ya "
                               "\"Artist - Ad\" formatlı postlar olmalıdır.</i>")
            src = {"kind": "tg_channel", "id": name, "label": f"✈️ {data['name']} (@{name})"}
            filler.save_sources([s for s in filler.sources() if s["id"] != name] + [src])
            n = filler.add_front(data["items"])
            return True, (f"✅ Telegram kanalı əlavə olundu (veb rejim): <b>{escape(data['name'])}</b>\n"
                          f"<i>{n} mahnı dərhal növbəyə əlavə olundu — qalanı növbəti qurulmada.</i>")
        parsed = parse_link(link_text)
        if not parsed or parsed["kind"] not in ("sp_playlist", "sp_album", "sp_artist", "yt_list", "yt_video_list"):
            return False, ("❌ Spotify playlist / albom / ifaçı, YouTube playlist və ya Telegram kanal / qrup "
                           "(t.me/kanal, @kanal, t.me/+dəvət, -100...) göndər.\n<i>Mahnı linki mənbə ola bilməz.</i>")
        if parsed["kind"] == "yt_video_list":
            parsed = {"kind": "yt_list", "list": parsed["list"]}
        src = {"kind": parsed["kind"], "id": parsed.get("id") or parsed.get("list")}
        label = src["id"]
        try:
            if src["kind"].startswith("sp_") and getattr(context, "links_spotify_data", None):
                data = await context.links_spotify_data(src["kind"][3:], src["id"])
                label = f"{data['name']} ({len(data.get('items') or [])})"
            elif src["kind"] == "yt_list":
                data = await context.youtube_manager.playlist_entries(yt_list_url(src["id"]), limit=200)
                label = f"{data['title']} ({len(data['entries'])})"
        except Exception as e:
            return False, f"❌ Mənbə açılmadı: <code>{escape(str(e))[:200]}</code>"
        src["label"] = label
        srcs = [s for s in filler.sources() if s["id"] != src["id"]] + [src]
        filler.save_sources(srcs)
        return True, f"✅ Mənbə əlavə olundu: <b>{escape(label)}</b>"

    async def waiting_link(message: types.Message) -> bool:
        return bool(message.from_user) and depo_waiting(message.from_user.id)

    @dp.message(F.chat.type == "private", F.text, ~F.text.startswith("/"), waiting_link)
    async def depo_link_input(message: types.Message):
        ok, note = await add_source(message.text, message.chat.id)
        with_panel = add_wait["msg_id"]
        if ok:
            add_wait["until"] = 0
        try:
            await message.delete()
        except Exception:
            pass
        if with_panel:
            text, kb = sources_view(note + ("" if ok else "\n\n✍️ Yenidən link göndər:"))
            try:
                await bot.edit_message_text(chat_id=add_wait["chat_id"], message_id=with_panel,
                                            text=text, parse_mode="HTML", reply_markup=kb)
                return
            except Exception:
                pass
        await message.answer(note, parse_mode="HTML")

    def userbot_line() -> str:
        if not tg.available:
            return "📱 Userbot: <i>qoşulmayıb (yalnız public kanallar, veb)</i>"
        if tg.client.is_connected() and tg.me:
            line = f"📱 Userbot: 🟢 @{escape(tg.me.username or str(tg.me.id))}"
        elif tg.client.is_connected():
            line = "📱 Userbot: 🟡 qoşulub, avtorizasiya yoxlanılır"
        else:
            line = "📱 Userbot: 🔴 bağlıdır"
        return line + (" · 🔎 skan gedir" if scan_state["running"] else "")

    def workers_label(n: int) -> str:
        return "tək-tək (1)" if n == 1 else f"paralel × {n}"

    def ytm_line() -> str:
        if not HAS_YTM:
            return "🎵 YT Music kataloqu: <i>ytmusicapi yoxdur —</i> <code>pip install ytmusicapi</code>"
        if not filler.ytm_on:
            return "🎵 YT Music kataloqu: 🔴 söndürülüb"
        c = filler.ytm.counts()
        ys = filler.ytm.stats
        line = (f"🎵 YT Music: 🟢 ifaçı ✅ {c['done']} · ⏳ {c['pending']}"
                + (f" · ❌ {c['failed']}" if c["failed"] else "")
                + f" · 🎶 tapıldı {c['songs']} · 📋 növbədə {len(filler.ytm_queue)}")
        if ys["artist"]:
            line += f"\n   🔎 <i>{escape(str(ys['artist'])[:40])}</i> oxunur"
        if ys["note"]:
            line += f"\n   ℹ️ <i>{escape(ys['note'])}</i>"
        return line

    def panel():
        st = filler.stats
        depo = audio_cache.depo_status()
        try:
            cs = get_db().cache_stats(depo["id"])
        except Exception:
            cs = {"songs": 0, "hits": 0, "size": 0}
        on = filler.running
        fix_count = getattr(context, "fix_artist_count", None)      # fix_artist_plugin
        try:
            bad = fix_count() if fix_count else 0
        except Exception:
            bad = 0
        uptime = ""
        if on and st["started"]:
            m = int((time.time() - st["started"]) / 60)
            uptime = f" · {m // 60} saat {m % 60} dəq."
        lines = [
            "📦 <b>Depo doldurucu</b>\n",
            f"📶 Vəziyyət: <b>{'🟢 işləyir' if on else '🔴 söndürülüb'}</b>{uptime}",
            f"⏱ Fasilə: <b>{filler.delay} san.</b> (hər işçi mahnılar arası)",
            f"🧵 Rejim: <b>{workers_label(filler.workers)}</b>",
            f"📦 Depo: <b>{cs['songs']}</b> mahnı · {cs['size'] / 1048576:.0f} MB · {escape(depo['ref'])}"
            + ("" if depo["id"] or not on else " ⚠️"),
            userbot_line(),
            "",
            f"⬆️ Yükləndi: <b>{st['done']}</b> · ⏭ artıq depoda idi: {st['cached']} · ❌ {st['failed']}",
            f"📋 Növbədə: <b>{len(filler.queue)}</b>"
            + (f" / {st['queue_built']}" if st["queue_built"] else ""),
        ]
        cur = sorted(filler.current.items())
        for i, title in cur[:4]:
            lines.append(f"⏳ {'İndi' if len(cur) == 1 else f'#{i + 1}'}: <i>{escape(title[:55])}</i>")
        if len(cur) > 4:
            lines.append(f"⏳ <i>+{len(cur) - 4} işçi daha işləyir — hamısı 📊 Canlı monitorda</i>")
        if st["note"]:
            lines.append(f"ℹ️ {escape(st['note'])}")
        if filler.restored:
            lines.append(f"💾 <i>Restartdan sonra {filler.restored} mahnı növbədən bərpa olundu</i>")
        lines.append(ytm_line())
        srcs = filler.sources()
        lines.append(f"\n📋 <b>Mənbələr</b> ({len(srcs)}) + 👥 istifadəçilərin sevdikləri + 🔀 Mix")
        for s in srcs[:8]:
            lines.append(f"   • {escape(s.get('label') or s['id'])}")
        lines.append("\n<i>Mənbə əlavə et:</i> <code>/depo add &lt;Spotify / YouTube / t.me linki&gt; [say]</code>")
        rows = [
            [btn("⏸ Söndür", "df:off") if on else btn("▶️ İşə sal", "df:on"), btn("🔄 Yenilə", "df:panel")],
            [btn("📊 Canlı monitor (1 san.)", "dt:start")],
            [btn("➖", "df:d:-"), btn(f"⏱ {filler.delay} san.", "df:noop"), btn("➕", "df:d:+")],
            [btn("➖", "df:w:-"), btn(f"🧵 {workers_label(filler.workers)}", "df:noop"), btn("➕", "df:w:+")],
            [btn(("🎵 YT Music kataloqu: 🟢" if filler.ytm_on else "🎵 YT Music kataloqu: 🔴"), "df:ytm")],
            [btn("🔁 Növbəni yenidən qur", "df:rebuild"), btn("📋 Mənbələr", "df:sources")],
            *([[btn(f"🩹 Artist düzəlişi ({bad})" if bad else "🩹 Artist düzəlişi", "fx:panel")]]
              if fix_count else []),
            *([[btn("⏹ Skanı dayandır", "scan:stop")]] if scan_state["running"] else []),
            [btn("⬅️ Menyu", "menu:main"), btn("❌ Bağla", "df:close")],
        ]
        return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)

    def sources_view(note: str = ""):
        srcs = filler.sources()
        text = "📋 <b>Doldurucu mənbələri</b>\n\n" + ("\n".join(
            f"{i}. {escape(s.get('label') or s['id'])} <i>({SOURCE_KIND_LABELS.get(s['kind'], s['kind'])})</i>"
            for i, s in enumerate(srcs, 1))
            or "<i>Mənbə yoxdur — yalnız istifadəçilərin sevdikləri və Mix işlənir.</i>") \
            + (f"\n\n{note}" if note else "")
        rows = [[btn(f"🗑 {(s.get('label') or s['id'])[:30]}", f"df:rm:{i}")] for i, s in enumerate(srcs)]
        rows.append([btn("➕ Mənbə əlavə et", "df:addsrc")])
        rows.append([btn("↩️ Default mənbələr", "df:defsrc")])
        rows.append([btn("⬅️ Geri", "df:panel"), btn("❌ Bağla", "df:close")])
        return text, InlineKeyboardMarkup(inline_keyboard=rows)

    @dp.message(Command("depo"))
    async def depo_cmd(message: types.Message, command: CommandObject):
        if not message.from_user or not is_creator(message.from_user.id):
            return
        args = (command.args or "").strip()
        low = args.lower()
        if low in ("on", "aç", "start"):
            filler.start()
            await message.answer("🟢 <b>Depo doldurucu işə salındı.</b>\n<i>Söndürmək üçün:</i> /depo off",
                                 parse_mode="HTML")
            return
        if low in ("off", "bağla", "stop"):
            await filler.stop()
            await message.answer("🔴 <b>Depo doldurucu söndürüldü.</b>", parse_mode="HTML")
            return
        if low.startswith(("delay", "fasilə", "fasile")):
            num = re.search(r"\d+", low)
            if not num:
                await message.answer(f"⏱ Fasilə: <b>{filler.delay} san.</b>\n<i>Dəyiş:</i> <code>/depo delay 5</code>",
                                     parse_mode="HTML")
                return
            v = filler.set_delay(int(num.group()))
            await message.answer(f"⏱ Mahnılar arası fasilə: <b>{v} san.</b>", parse_mode="HTML")
            return
        if low.startswith(("workers", "paralel", "parallel")):
            num = re.search(r"\d+", low)
            if not num:
                await message.answer(f"🧵 Rejim: <b>{workers_label(filler.workers)}</b>\n"
                                     f"<i>Dəyiş:</i> <code>/depo workers 16</code> (1 = tək-tək, max {MAX_WORKERS})",
                                     parse_mode="HTML")
                return
            v = filler.set_workers(int(num.group()))
            warn = ("\n⚠️ <i>Çox işçi: Telegram kanala göndərmə limiti (flood) və YouTube 429 / bot-yoxlaması "
                    "ehtimalı artır. Fasiləni 0 etmə.</i>" if v > 8 else "")
            await message.answer(f"🧵 Rejim: <b>{workers_label(v)}</b>{warn}", parse_mode="HTML")
            return
        if low.startswith("ytm"):
            if not HAS_YTM:
                await message.answer("❌ <code>pip install ytmusicapi</code> lazımdır.", parse_mode="HTML")
                return
            arg = low[3:].strip()
            if arg in ("on", "off"):
                filler.set_ytm(arg == "on")
                if arg == "off":
                    filler.ytm_queue.clear()
                    filler.ytm_seen.clear()
            await message.answer(ytm_line(), parse_mode="HTML")
            return
        if low.startswith("add"):
            wait = await message.answer("⏳ <i>Mənbə yoxlanılır...</i>", parse_mode="HTML")
            ok, note = await add_source(args[3:], message.chat.id)
            try:
                await wait.edit_text(note, parse_mode="HTML")
            except Exception:
                await message.answer(note, parse_mode="HTML")
            return
        text, kb = panel()
        await message.answer(text, parse_mode="HTML", reply_markup=kb)

    @dp.callback_query(F.data.startswith("df:"))
    async def depo_callback(cb: types.CallbackQuery):
        if not is_creator(cb.from_user.id):
            await cb.answer("⛔ İcazə yoxdur", show_alert=True)
            return
        parts = cb.data.split(":")
        action = parts[1]
        if action != "addsrc":
            add_wait["until"] = 0                  # başqa düyməyə basıldı — link gözləmə bitir

        async def show(text, kb):
            try:
                await cb.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
            except Exception as e:
                if "not modified" not in str(e):
                    logger.warning(f"Depo paneli yenilənmədi: {e}")

        if action == "close":
            await cb.answer()
            try:
                await cb.message.delete()
            except Exception:
                pass
            return
        if action == "on":
            filler.start()
            await cb.answer("🟢 İşə salındı")
        elif action == "off":
            await filler.stop()
            await cb.answer("🔴 Söndürüldü")
        elif action == "noop":
            await cb.answer("➖ / ➕ ilə dəyiş")
            return
        elif action == "d" and len(parts) > 2:
            v = filler.step_delay(1 if parts[2] == "+" else -1)
            await cb.answer(f"⏱ Fasilə: {v} san.")
        elif action == "w" and len(parts) > 2:
            cur = filler.workers
            v = filler.step_workers(1 if parts[2] == "+" else -1)
            if v == cur:
                await cb.answer("Tək-tək rejimdir (minimum)" if v == 1 else f"Maksimum {MAX_WORKERS} paralel",
                                show_alert=False)
            else:
                await cb.answer(f"🧵 {workers_label(v)}")
        elif action == "ytm":
            if not HAS_YTM:
                await cb.answer("ytmusicapi quraşdırılmayıb: pip install ytmusicapi", show_alert=True)
                return
            filler.set_ytm(not filler.ytm_on)
            if not filler.ytm_on:
                filler.ytm_queue.clear()
                filler.ytm_seen.clear()
            await cb.answer("🎵 YT Music kataloqu " + ("açıldı" if filler.ytm_on else "söndürüldü"))
        elif action == "rebuild":
            filler.reset_state()
            await filler.save_state(force=True)
            await cb.answer("Növbə sıfırlandı — Telegram tarixçəsi də tam yenidən oxunacaq", show_alert=True)
        elif action == "sources":
            await cb.answer()
            await show(*sources_view())
            return
        elif action == "addsrc":
            await cb.answer()
            add_wait.update(until=time.time() + 300, chat_id=cb.message.chat.id, msg_id=cb.message.message_id)
            await show(
                "➕ <b>Mənbə əlavə et</b>\n\n"
                "Linki göndər (adi link kimi, komandasız):\n"
                "• Spotify playlist / albom / ifaçı\n"
                "• YouTube playlist\n"
                "• ✈️ Telegram kanal / qrup: <code>https://t.me/kanal</code>, <code>@kanal</code>, "
                "<code>https://t.me/+dəvət</code>, <code>-100...</code>\n"
                "  <i>(sonuna say yaza bilərsən: </i><code>@kanal 500</code><i> — son 500 audio)</i>\n\n"
                "<i>məs.</i> <code>https://open.spotify.com/playlist/...</code>",
                InlineKeyboardMarkup(inline_keyboard=[[btn("⬅️ Geri", "df:sources"), btn("❌ Bağla", "df:close")]]),
            )
            return
        elif action == "rm" and len(parts) > 2 and parts[2].isdigit():
            srcs = filler.sources()
            i = int(parts[2])
            if 0 <= i < len(srcs):
                srcs.pop(i)
                filler.save_sources(srcs)
            await cb.answer("Silindi")
            await show(*sources_view())
            return
        elif action == "defsrc":
            filler.save_sources(list(DEFAULT_SOURCES))
            await cb.answer("Default mənbələr")
            await show(*sources_view())
            return
        else:
            await cb.answer()
        await show(*panel())

    @dp.channel_post(F.audio)
    async def tg_source_post(post: types.Message):
        """Mənbə kanalına (bot orada admin olanda) yeni mahnı düşdü → dərhal növbəyə."""
        name = (post.chat.username or "").lower()
        srcs = filler.sources()
        if not any((s["kind"] == "tg_channel" and s["id"].lower() == name) or
                   (s["kind"] == "tg_chat" and s["id"] == str(post.chat.id)) for s in srcs):
            return
        a = post.audio
        res = clean_song(a.performer, a.title, a.file_name, post.caption, post.chat.title)
        if not res:
            return
        item = tg_item(res[0], res[1], a.duration or 0)
        if filler.add_front([item]):
            logger.info(f"📦 Kanal postu növbəyə: {item['title']}")

    # ── 📊 Canlı monitor (depo top) ──
    top_sessions = {}          # (chat_id, msg_id) -> {"paused", "view", "sort", "stop", "until", ...}
    context.depo_top_sessions = top_sessions

    def top_kb(sess, running=True):
        if not running:
            return InlineKeyboardMarkup(inline_keyboard=[
                [btn("▶️ Yenidən başlat", "dt:start")],
                [btn("⬅️ Panel", "df:panel"), btn("❌ Bağla", "dt:close")],
            ])
        log = sess["view"] == "log"
        row1 = [btn("▶️ Davam" if sess["paused"] else "⏸ Fasilə", "dt:pause"),
                btn("🧵 İşçilər" if log else "📜 Jurnal", "dt:view")]
        if not log:
            row1.append(btn("↕️ #-yə görə" if sess["sort"] == "time" else "↕️ Vəziyyətə görə", "dt:sort"))
        return InlineKeyboardMarkup(inline_keyboard=[
            row1,
            [btn("➖", "dt:w:-"), btn(f"🧵 {filler.workers}", "dt:noop"), btn("➕", "dt:w:+")],
            [btn("➖", "dt:d:-"), btn(f"⏱ {filler.delay} san.", "dt:noop"), btn("➕", "dt:d:+")],
            [btn("⏸ Doldurucunu söndür", "dt:off") if filler.running else btn("▶️ Doldurucunu işə sal", "dt:on"),
             btn("⏹ Monitoru dayandır", "dt:stop")],
            [btn("⬅️ Panel", "df:panel"), btn("❌ Bağla", "dt:close")],
        ])

    def kb_sig(kb) -> str:
        return "|".join(b.text for row in kb.inline_keyboard for b in row)

    async def top_run(key, sess):
        chat_id, msg_id = key
        meter = ProcMeter()
        meter.sample()
        tz = bot_tz()
        last_text = last_kb = None
        next_tick = time.monotonic()
        try:
            while not sess["stop"]:
                if not sess["paused"] and time.time() >= sess["until"]:
                    break
                kb = top_kb(sess)
                sig = kb_sig(kb)
                text = None
                if not sess["paused"] or sess.pop("dirty", False):
                    proc = meter.sample() if not sess["paused"] else (sess.get("proc") or meter.sample())
                    sess["proc"] = proc
                    text = render_top(filler, sess, proc, datetime.now(tz).strftime("%H:%M:%S"))
                try:
                    if text and text != last_text:
                        await bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=text,
                                                    parse_mode="HTML", reply_markup=kb)
                        last_text, last_kb = text, sig
                    elif sig != last_kb:
                        await bot.edit_message_reply_markup(chat_id=chat_id, message_id=msg_id, reply_markup=kb)
                        last_kb = sig
                except Exception as e:
                    err = str(e).lower()
                    wait = getattr(e, "retry_after", None)
                    if wait:
                        logger.info(f"depo top: flood limit, {wait} san. gözlənilir")
                        await asyncio.sleep(wait)
                        next_tick = time.monotonic()
                    elif "not modified" in err:
                        last_text, last_kb = text or last_text, sig
                    elif "not found" in err or "can't be edited" in err:
                        break                                   # mesaj silinib
                    else:
                        logger.warning(f"depo top yenilənmədi: {e}")
                # dəqiq 1 san. addım (edit-in özü ~0.1–0.3 san. çəkir — sürüşmə olmasın)
                next_tick += TOP_INTERVAL
                delay = next_tick - time.monotonic()
                if delay < 0:
                    next_tick = time.monotonic()
                    delay = 0
                await asyncio.sleep(delay)
        except asyncio.CancelledError:
            sess["closed"] = True
            raise
        finally:
            top_sessions.pop(key, None)
            if not sess.get("closed"):
                proc = sess.get("proc") or meter.sample()
                text = render_top(filler, {**sess, "paused": True}, proc,
                                  datetime.now(tz).strftime("%H:%M:%S")).replace("⏸ fasilə", "⏹ dayandı")
                try:
                    await bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=text,
                                                parse_mode="HTML", reply_markup=top_kb(sess, running=False))
                except Exception:
                    pass

    def top_start(key):
        for k, old in list(top_sessions.items()):     # bir çatda bir monitor
            if k[0] == key[0]:
                old["stop"] = True
                if k == key:
                    old["closed"] = True
        sess = {"paused": False, "view": "workers", "sort": "time", "stop": False,
                "until": time.time() + TOP_MAX_RUNTIME}
        top_sessions[key] = sess
        sess["task"] = asyncio.create_task(top_run(key, sess))
        return sess

    @dp.message(Command("depotop"))
    async def depotop_cmd(message: types.Message):
        if not message.from_user or not is_creator(message.from_user.id):
            return
        msg = await message.answer("📊 <i>depo top başlayır...</i>", parse_mode="HTML")
        top_start((msg.chat.id, msg.message_id))

    @dp.callback_query(F.data.startswith("dt:"))
    async def depotop_callback(cb: types.CallbackQuery):
        if not is_creator(cb.from_user.id):
            await cb.answer("⛔ İcazə yoxdur", show_alert=True)
            return
        parts = cb.data.split(":")
        action = parts[1]
        key = (cb.message.chat.id, cb.message.message_id)
        sess = top_sessions.get(key)

        if action == "start":
            add_wait["until"] = 0
            await cb.answer("📊 Canlı monitor")
            top_start(key)
            return
        if action == "close":
            if sess:
                sess["closed"] = sess["stop"] = True
            await cb.answer()
            try:
                await cb.message.delete()
            except Exception:
                pass
            return
        if action == "noop":
            await cb.answer("➖ / ➕ ilə dəyiş")
            return
        # bu düymələr monitor dayansa da işləyir
        if action == "w" and len(parts) > 2:
            cur = filler.workers
            v = filler.step_workers(1 if parts[2] == "+" else -1)
            if v == cur:
                await cb.answer("Minimum 1 işçi" if v == 1 else f"Maksimum {MAX_WORKERS} işçi")
            else:
                await cb.answer(f"🧵 {workers_label(v)}" + (" · ⚠️ flood riski" if v > 8 else ""))
        elif action == "d" and len(parts) > 2:
            v = filler.step_delay(1 if parts[2] == "+" else -1)
            await cb.answer(f"⏱ Fasilə: {v} san.")
        elif action == "on":
            filler.start()
            await cb.answer("🟢 Doldurucu işə salındı")
        elif action == "off":
            await filler.stop()
            await cb.answer("🔴 Doldurucu söndürüldü")
        elif not sess:
            await cb.answer("Monitor dayanıb — ▶️ ilə yenidən başlat")
            return
        elif action == "pause":
            sess["paused"] = not sess["paused"]
            if not sess["paused"]:
                sess["until"] = max(sess["until"], time.time() + 300)
            await cb.answer("⏸ Fasilə" if sess["paused"] else "▶️ Davam")
        elif action == "view":
            sess["view"] = "workers" if sess["view"] == "log" else "log"
            sess["dirty"] = True
            await cb.answer("📜 Son hadisələr" if sess["view"] == "log" else "🧵 İşçilər")
        elif action == "sort":
            sess["sort"] = "idx" if sess["sort"] == "time" else "time"
            sess["dirty"] = True
            await cb.answer("Vəziyyətə görə" if sess["sort"] == "time" else "İşçi nömrəsinə görə")
        elif action == "stop":
            sess["stop"] = True
            await cb.answer("⏹ Monitor dayandırıldı")
            return
        else:
            await cb.answer()
            return
        if sess:
            sess["dirty"] = True

    class StopTopOnLeave:
        """Monitor mesajında 📦 panel / menyu düyməsi basılanda canlı yenilənmə dayansın (paneli üstələməsin)."""
        async def __call__(self, handler, event, data):
            d = getattr(event, "data", None) or ""
            if d.startswith(("df:", "menu:", "fx:")) and event.message:
                ts = top_sessions.get((event.message.chat.id, event.message.message_id))
                if ts:
                    ts["closed"] = ts["stop"] = True
                    t = ts.get("task")
                    if t and not t.done():
                        t.cancel()
            return await handler(event, data)

    dp.callback_query.outer_middleware(StopTopOnLeave())

    # ── 📱 Userbot komandaları ──
    @dp.message(Command("userbot"), F.from_user.id.func(is_creator))
    async def userbot_status(message: types.Message):
        if not await tg.ok():
            await message.answer("🔴 <b>Userbot aktiv deyil!</b>\n"
                                 "<i>TELETHON_API_ID/HASH və serverdə login.py ilə yaradılmış sessiyanı yoxla.</i>",
                                 parse_mode="HTML")
            return
        me = tg.me = await tg.client.get_me()
        n = len(filler.tg_source_ids())
        await message.answer(
            f"🟢 <b>Userbot aktivdir!</b>\n"
            f"👤 <b>Hesab:</b> {escape(me.first_name or '')} (@{escape(me.username or 'yoxdur')})\n"
            f"🆔 <b>ID:</b> <code>{me.id}</code>\n"
            f"✈️ <b>Telegram mənbələri:</b> {n}\n\n"
            f"<i>Qrup / gizli kanal əlavə et:</i> <code>/depo add https://t.me/+...</code>\n"
            f"<i>Birdəfəlik skan:</i> <code>/scan_chat &lt;link/ID&gt; [say]</code>\n"
            f"⚡ <i>İstənilən çatda</i> <code>.alive</code> <i>yazaraq yoxla.</i>",
            parse_mode="HTML",
        )

    @dp.message(Command("scan_chat"), F.from_user.id.func(is_creator))
    async def scan_chat_cmd(message: types.Message, command: CommandObject):
        if not await tg.ok():
            await message.answer("🔴 Userbot aktiv deyil. /userbot", parse_mode="HTML")
            return
        target, limit = parse_tg_target(command.args or "")
        if not target:
            await message.answer(
                "ℹ️ <b>İstifadə:</b> <code>/scan_chat &lt;link_və_ya_id&gt; [audio_sayı]</code>\n\n"
                "• <code>/scan_chat https://t.me/+AbCdEfGhIjK</code> <i>(bütün tarixçə)</i>\n"
                "• <code>/scan_chat https://t.me/kanal_adi 300</code> <i>(son 300 audio)</i>\n"
                "• <code>/scan_chat -1001234567890 500</code>\n\n"
                "<i>Daimi mənbə kimi saxlamaq üçün:</i> <code>/depo add &lt;link&gt;</code>",
                parse_mode="HTML")
            return
        if scan_state["running"]:
            await message.answer("⚠️ Artıq skan gedir. Dayandırmaq: /stop_scan", parse_mode="HTML")
            return
        try:
            ent = await tg.entity(target)
        except Exception as e:
            await message.answer(f"❌ Çat açılmadı: <code>{escape(str(e))[:300]}</code>", parse_mode="HTML")
            return
        launch_scan(message.chat.id, ent, limit, "Birdəfəlik skan")

    @dp.callback_query(F.data == "scan:stop")
    async def cb_stop_scan(cb: types.CallbackQuery):
        if not is_creator(cb.from_user.id):
            await cb.answer("⛔ İcazəniz yoxdur.", show_alert=True)
            return
        if scan_state["running"]:
            scan_state["stop"] = True
            await cb.answer("⏹ Skan dayandırılır...")
        else:
            await cb.answer("Hazırda aktiv skan yoxdur.", show_alert=True)

    @dp.message(Command("stop_scan"), F.from_user.id.func(is_creator))
    async def cmd_stop_scan(message: types.Message):
        if scan_state["running"]:
            scan_state["stop"] = True
            await message.answer("⏹ <b>Skan dayandırılır...</b>", parse_mode="HTML")
        else:
            await message.answer("ℹ️ Hazırda aktiv skan yoxdur.", parse_mode="HTML")

    if tg.own_client:
        client = tg.client

        # .alive / .info / .fastfetch / .clear — userbot_tools_plugin.py-dadır

        @client.on(events.NewMessage())
        async def on_new_audio(event):
            """Yalnız mənbə kimi əlavə olunmuş çatlardakı yeni audiolar → növbənin əvvəlinə."""
            if not getattr(event.message, "media", None) or event.chat_id not in filler.tg_source_ids():
                return
            title = ""
            try:
                chat = await event.get_chat()
                title = getattr(chat, "title", "") or ""
            except Exception:
                pass
            item = tl_message_item(event.message, title)
            if item and filler.add_front([item]):
                logger.info(f"📥 Userbot: yeni audio növbəyə: {item['title']}")

    # 💾 Bot dayananda (Ctrl+C / systemctl stop — aiogram siqnalı tutur) növbə diskə yazılır
    if not getattr(context, "_depo_shutdown_hooked", False):
        context._depo_shutdown_hooked = True

        async def _depo_on_shutdown():
            f = getattr(context, "depo_filler", None)     # plugin yenidən yüklənibsə — ən təzəsi
            if f:
                f.save_state_now()
                logger.info(f"💾 Depo növbəsi yazıldı: {len(f.queue)} əsas + {len(f.ytm_queue)} YT Music"
                            f" + {len(f.inflight)} yarımçıq")

        dp.shutdown.register(_depo_on_shutdown)

    # Restartdan sonra: əvvəl açıq idisə davam et
    if filler.enabled:
        try:
            filler.start()
        except RuntimeError:              # event loop hələ işləmirsə
            pass

    logger.info("✅ Depo doldurucu + userbot plugin-i yükləndi (/depo, /depotop, /scan_chat, /userbot)")


async def teardown(context):
    """plugin_manager söndürəndə / yenidən yükləyəndə fon işini dayandırır (vəziyyət saxlanılır)."""
    for ts in list(getattr(context, "depo_top_sessions", {}).values()):
        ts["stop"] = True
    scan = getattr(context, "depo_scan_state", None)
    if scan and scan.get("task") and not scan["task"].done():
        scan["stop"] = True
        scan["task"].cancel()
    filler = getattr(context, "depo_filler", None)
    if filler:
        await filler.stop(persist=False)
    tg = getattr(context, "depo_userbot", None)
    if tg and tg.own_client:
        if tg.task and not tg.task.done():
            tg.task.cancel()
        if tg.client.is_connected():
            await tg.client.disconnect()
            logger.info("🛑 Telethon userbot bağlantısı kəsildi.")
        if getattr(context, "telethon_client", None) is tg.client:
            context.telethon_client = None
