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

Komandalar:
  /depo                              — panel (status, sürət, mənbələr)
  /depo on | /depo off               — işə sal / söndür
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

logger = logging.getLogger(__name__)
PRIORITY = 60

DEFAULT_SOURCES = [
    {"kind": "sp_playlist", "id": "37i9dQZF1DXcBWIGoYBM5M", "label": "Today's Top Hits"},
    {"kind": "sp_playlist", "id": "37i9dQZEVXbMDoHDwVN2tF", "label": "Top 50 Global"},
]
SPEEDS = {"slow": 60, "normal": 25, "fast": 8}          # mahnılar arası fasilə (san.)
SPEED_LABELS = {"slow": "🐢 Yavaş", "normal": "🚶 Normal", "fast": "🏃 Sürətli"}
MAX_DURATION = 15 * 60
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


# ───────────────────────── 📦 Doldurucu ─────────────────────────
class Filler:
    def __init__(self, context):
        self.ctx = context
        self.task = None
        self.stop_event = threading.Event()
        self.queue = deque()
        self.seen = set()
        self.tg = None                          # TgUser (userbot) — setup-da verilir
        self.stats = {"done": 0, "cached": 0, "failed": 0, "started": None, "current": None,
                      "last_build": None, "queue_built": 0, "note": ""}
        self._init_resolve_table()

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
    def speed(self) -> str:
        s = self._get("speed") or "normal"
        return s if s in SPEEDS else "normal"

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
        self.task = asyncio.create_task(self.loop())
        logger.info("📦 Depo doldurucu işə düşdü")

    async def stop(self, persist=True):
        if persist:
            self._set("on", "0")
        self.stop_event.set()
        if self.task and not self.task.done():
            self.task.cancel()
            try:
                await self.task
            except (asyncio.CancelledError, Exception):
                pass
        self.task = None
        self.stats["current"] = None
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
                        await self.tg.scan(ent, None, self._add, self.stop_event.is_set)
                    else:
                        data = await tg_channel_items(src["id"])
                        for it in data["items"]:
                            self._add(it)
                elif src["kind"] == "tg_chat":
                    if not (self.tg and await self.tg.ok()):
                        logger.info(f"Doldurucu: {src.get('label')}: userbot aktiv deyil — ötürüldü")
                        continue
                    ent = await self.tg.entity_for_source(src)
                    await self.tg.scan(ent, src.get("limit"), self._add, self.stop_event.is_set)
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

    async def loop(self):
        await asyncio.sleep(5)
        fetch = getattr(self.ctx, "music_fetch_audio", None)
        while not self.stop_event.is_set():
            if not fetch:
                self.stats["note"] = "music_plugin yüklənməyib"
                await asyncio.sleep(60)
                fetch = getattr(self.ctx, "music_fetch_audio", None)
                continue
            if not await audio_cache.depo_chat_id(self.ctx.bot):
                self.stats["note"] = "⚠️ depo kanalı əlçatan deyil — 10 dəq. sonra yenidən"
                await asyncio.sleep(600)
                continue
            if not self.queue:
                # növbə ən çox 30 dəqiqədə bir qurulur — hamısı depodadırsa API-ləri boş yerə yükləməsin
                since = time.time() - (self.stats["last_build"] or 0)
                if self.stats["last_build"] and since < IDLE_SLEEP:
                    self.stats["note"] = "növbə bitdi — yeni mahnılar üçün gözləyir"
                    await asyncio.sleep(min(60, IDLE_SLEEP - since))     # skan yeni mahnı atarsa tez başlasın
                    continue
                self.seen.clear()
                await self.build_queue()
                if not self.queue:
                    self.stats["note"] = "növbə boşdur — 30 dəq. sonra yenidən"
                    self.stats["last_build"] = time.time()
                    continue
            self.stats["note"] = ""
            item = self.queue.popleft()
            self.stats["current"] = item["title"]
            try:
                url, from_cache = await self.resolve(item)
                vid = re.search(r"v=([\w-]{11})", url or "")
                if not vid:
                    self.stats["failed"] += 1
                    if not from_cache:
                        logger.info(f"Doldurucu: {item['title']}: tapılmadı")
                        await asyncio.sleep(min(2, SPEEDS[self.speed]))
                    continue
                if await asyncio.to_thread(audio_cache.get_cached, vid.group(1)):
                    self.stats["cached"] += 1
                    if not from_cache:                # axtarış edildi — API-ləri yormasın
                        await asyncio.sleep(min(2, SPEEDS[self.speed]))
                    continue
                res = await fetch(url, item["title"], int(item.get("raw_duration") or 0), self.stop_event,
                                  meta=item.get("meta"), thumb_url=item.get("thumb"))
                if res.get("path"):
                    try:
                        os.remove(res["path"])
                    except OSError:
                        pass
                if res.get("file_id") and not res.get("cached"):
                    self.stats["done"] += 1
                else:
                    self.stats["cached"] += 1
            except asyncio.CancelledError:
                raise
            except Exception as e:
                if self.stop_event.is_set():
                    break
                self.stats["failed"] += 1
                logger.info(f"Doldurucu: {item['title']}: {e}")
            finally:
                self.stats["current"] = None
            # istifadəçilər yükləyirsə onlara mane olmasın deyə fasilə
            await asyncio.sleep(SPEEDS[self.speed])


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

    async def scan(self, ent, limit, on_item, should_stop, progress=None) -> dict:
        """Çatdakı audioları (yeni → köhnə) təmizlənmiş adla on_item-ə ötürür."""
        _, title, _ = self.describe(ent)
        st = {"messages": 0, "items": 0, "added": 0, "unreadable": 0}
        async for msg in self.client.iter_messages(ent, limit=limit, filter=InputMessagesFilterMusic):
            if should_stop():
                break
            st["messages"] += 1
            it = tl_message_item(msg, title)
            if not it:
                st["unreadable"] += 1
            else:
                st["items"] += 1
                if on_item(it):
                    st["added"] += 1
            if progress and st["messages"] % 100 == 0:
                await progress(st)
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
    async def run_scan(chat_id: int, ent, limit, header: str):
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

    def launch_scan(chat_id, ent, limit, header) -> bool:
        if scan_state["running"]:
            return False
        scan_state["running"] = True
        scan_state["task"] = asyncio.create_task(run_scan(chat_id, ent, limit, header))
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
                        if launch_scan(chat_id, ent, limit, "Yeni mənbə skan edilir"):
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
            f"⚡ Sürət: {SPEED_LABELS[filler.speed]} (mahnılar arası {SPEEDS[filler.speed]} san.)",
            f"📦 Depo: <b>{cs['songs']}</b> mahnı · {cs['size'] / 1048576:.0f} MB · {escape(depo['ref'])}"
            + ("" if depo["id"] or not on else " ⚠️"),
            userbot_line(),
            "",
            f"⬆️ Bu sessiyada yükləndi: <b>{st['done']}</b> · ⏭ artıq depoda idi: {st['cached']} · ❌ {st['failed']}",
            f"📋 Növbədə: <b>{len(filler.queue)}</b>"
            + (f" / {st['queue_built']}" if st["queue_built"] else ""),
        ]
        if st["current"]:
            lines.append(f"⏳ İndi: <i>{escape(st['current'][:60])}</i>")
        if st["note"]:
            lines.append(f"ℹ️ {escape(st['note'])}")
        srcs = filler.sources()
        lines.append(f"\n📋 <b>Mənbələr</b> ({len(srcs)}) + 👥 istifadəçilərin sevdikləri + 🔀 Mix")
        for s in srcs[:8]:
            lines.append(f"   • {escape(s.get('label') or s['id'])}")
        lines.append("\n<i>Mənbə əlavə et:</i> <code>/depo add &lt;Spotify / YouTube / t.me linki&gt; [say]</code>")
        rows = [
            [btn("⏸ Söndür", "df:off") if on else btn("▶️ İşə sal", "df:on"), btn("🔄 Yenilə", "df:panel")],
            [btn(("• " if filler.speed == k else "") + v, f"df:speed:{k}") for k, v in SPEED_LABELS.items()],
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
        elif action == "speed" and len(parts) > 2 and parts[2] in SPEEDS:
            filler._set("speed", parts[2])
            await cb.answer(SPEED_LABELS[parts[2]])
        elif action == "rebuild":
            filler.queue.clear()
            filler.seen.clear()
            filler.stats["queue_built"] = 0
            filler.stats["last_build"] = None
            await cb.answer("Növbə növbəti addımda yenidən qurulacaq")
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

    # Restartdan sonra: əvvəl açıq idisə davam et
    if filler.enabled:
        try:
            filler.start()
        except RuntimeError:              # event loop hələ işləmirsə
            pass

    logger.info("✅ Depo doldurucu + userbot plugin-i yükləndi (/depo, /scan_chat, /userbot)")


async def teardown(context):
    """plugin_manager söndürəndə / yenidən yükləyəndə fon işini dayandırır (vəziyyət saxlanılır)."""
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
