"""
🎙 Canlı yayım asistanı — plugins/voice_assistant_plugin.py

İkinci userbot hesabı qrupların səsli yayımına (video chat) qoşulub mahnı oxudur.

Qrupda:
  /play <mahnı adı | YouTube linki>   — növbəyə əlavə et (mahnıya reply edib /play da olar)
  /mixplay [mahnı adı | link]          — 🔀 Mix: mahnı + ona oxşar mahnılar (botun /mix-inin səsli variantı);
                                         arqumentsiz — hazırda oxunan mahnının Mix-i; ♾ növbə bitəndə özü davam edir
  /pause  /resume  /skip  /stop  /queue
  "İndi oxunur" mesajındakı düymələr: ⏸ / ▶️ / ⏭ / ⏹ / 📋

Mahnı haradan gəlir (bot özü kimi):
  1) 📦 depo (adla axtarış, menyudan açılıb-söndürülür)
  2) 🎵 YouTube Music və ya ▶️ YouTube (menyudan seçilir) → video ID → 📦 depo keşi
  3) keşdə yoxdursa music_plugin-in fetch_audio-su ilə yüklənir — eyni zamanda depoya da düşür

Qrupa qoşulma: bot qrupa əlavə olunanda (və ya /play gələndə) asistan qrupda deyilsə, bot qrupda
  🔐 icazə istəyir — yalnız qrup adminləri təsdiq edə bilər; ✅-dən sonra asistan özü qoşulur və gözləyən
  /play avtomatik davam edir. Asistan menyudan söndürülübsə qrupda bildirilir + "👤 Adminlə əlaqə" düyməsi.

İdarə: /menu → 🖥 Sistem → 🎙 Canlı yayım asistanı
  🔑 hesab qoşma (telefon → kod → 2FA, hamısı bot daxilində), açıb-söndürmə, mənbə, icazələr, aktiv yayımlar.

Tələblər:
  pip install py-tgcalls        (ffmpeg də lazımdır — bot artıq istifadə edir)
  config.env: TELETHON_API_ID / TELETHON_API_HASH (birinci userbot ilə eyni ola bilər)
  Asistan hesabı qrupda olmalıdır (açıq qrupa özü qoşulur; qapalı qrupda bot admin olub dəvət linki yarada bilsə — özü qoşulur).
  Səsli yayımı admin başladır, ya da asistan admindirsə ("Manage video chats") özü başladır.

Sessiya: data/assistant.session (StringSession, 600 icazə) — bazaya yazılmır.
"""
import asyncio
import contextlib
import logging
import os
import re
import time
from collections import deque
from html import escape

from aiogram import F, types
from aiogram.filters import Command, CommandObject
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from core import audio_cache, tg_login
from core.database import get_db

logger = logging.getLogger(__name__)
PRIORITY = 20                     # music_plugin-in ümumi mətn handler-indən əvvəl (giriş kodu axtarış sayılmasın)

API_ID = os.getenv("TELETHON_API_ID")
API_HASH = os.getenv("TELETHON_API_HASH")
SESSION_PATH = os.getenv("ASSISTANT_SESSION_FILE", os.path.join("data", "assistant.session"))
PLAY_DIR = os.path.join("download", "voice")
MAX_DURATION = 30 * 60            # bir mahnı maksimum (san.)
IDLE_LEAVE = 90                   # növbə bitəndən sonra yayımdan çıxma (san.)

SOURCES = {"ytm": "🎵 YouTube Music", "yt": "▶️ YouTube"}
WHO = {"all": "👥 Hamı", "admins": "👮 Yalnız adminlər"}
S_ON, S_SRC, S_DEPO, S_WHO = "va:on", "va:source", "va:depo_first", "va:who"
S_MIXN, S_MIXAUTO = "va:mix_n", "va:mix_auto"
S_QUAL, S_NORM, S_VOL, S_THROTTLE = "va:quality", "va:norm", "va:volume", "va:throttle"
QUALITY = {"depo": "⚡ Depo (sürətli)", "best": "💎 Ən yaxşı (Opus 251)"}
VOL_STEPS = [50, 75, 100, 125, 150]
# EBU R128 — bütün mahnılar eyni səs səviyyəsində, pik -1.5 dB altında (kəsilmə / clipping olmur)
LOUDNORM = "loudnorm=I=-14:TP=-1.5:LRA=11"
MIX_STEPS = [5, 10, 15, 20, 30, 50]
MIX_REFILL_AT = 2                 # ♾ Mix: növbədə bu qədər qalanda yeni partiya gətirilir
_VID_RE = re.compile(r"(?:[?&]v=|youtu\.be/|/shorts/)([\w-]{11})")


def _get(key, default=None):
    try:
        v = get_db().get_setting(key)
        return default if v in (None, "") else v
    except Exception:
        return default


def _set(key, value):
    get_db().set_setting(key, str(value))


def fmt_dur(sec) -> str:
    sec = int(sec or 0)
    return f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}" if sec >= 3600 else f"{sec // 60}:{sec % 60:02d}"


# ───────────────────────── py-tgcalls adapteri (1.x və 2.x) ─────────────────────────
class Calls:
    def __init__(self, client, on_end, on_closed):
        from pytgcalls import PyTgCalls
        self.app = PyTgCalls(client)
        self.v2 = hasattr(self.app, "play")
        self._on_end, self._on_closed = on_end, on_closed
        self._register()

    def _register(self):
        if self.v2:
            @self.app.on_update()
            async def _upd(_, update):
                name = type(update).__name__
                chat_id = getattr(update, "chat_id", None)
                if chat_id is None:
                    return
                if name == "StreamEnded":
                    # yalnız audio axını (video IGNORE-dur, amma bəzi versiyalar ikisini də göndərir)
                    st = str(getattr(update, "stream_type", "audio")).lower()
                    if "video" not in st:
                        await self._on_end(chat_id)
                elif name == "ChatUpdate":
                    status = str(getattr(update, "status", "")).upper()
                    if any(x in status for x in ("KICKED", "LEFT", "CLOSED", "DISCARDED")):
                        await self._on_closed(chat_id, status)
        else:
            @self.app.on_stream_end()
            async def _end(_, update):
                await self._on_end(update.chat_id)

            for ev in ("on_kicked", "on_closed_voice_chat", "on_left"):
                deco = getattr(self.app, ev, None)
                if deco:
                    def make(evname):
                        async def _closed(_, chat_id):
                            await self._on_closed(chat_id, evname)
                        return _closed
                    deco()(make(ev))

    async def start(self):
        await self.app.start()

    async def play(self, chat_id: int, path: str):
        if self.v2:
            from pytgcalls.types import AudioQuality, MediaStream
            # 48 kHz stereo — Telegram-ın Opus axını üçün maksimum (STUDIO onsuz da 48k-ya endirilir)
            await self.app.play(chat_id, MediaStream(path, audio_parameters=AudioQuality.HIGH,
                                                     video_flags=MediaStream.Flags.IGNORE))
            return
        try:
            from pytgcalls.types import AudioPiped
        except ImportError:
            from pytgcalls.types.input_stream import AudioPiped
        try:
            await self.app.change_stream(chat_id, AudioPiped(path))
        except Exception:
            await self.app.join_group_call(chat_id, AudioPiped(path))

    async def volume(self, chat_id, value: int):
        fn = getattr(self.app, "change_volume_call", None)
        if fn is not None:
            with contextlib.suppress(Exception):
                await fn(chat_id, int(value))

    async def pause(self, chat_id):
        await (self.app.pause(chat_id) if self.v2 else self.app.pause_stream(chat_id))

    async def resume(self, chat_id):
        await (self.app.resume(chat_id) if self.v2 else self.app.resume_stream(chat_id))

    async def leave(self, chat_id):
        with contextlib.suppress(Exception):
            await (self.app.leave_call(chat_id) if self.v2 else self.app.leave_group_call(chat_id))


def pytgcalls_version() -> str:
    try:
        import pytgcalls
        return getattr(pytgcalls, "__version__", "?")
    except Exception:
        return ""


# ───────────────────────── vəziyyət ─────────────────────────
class Track:
    __slots__ = ("title", "vid", "url", "duration", "file_id", "by", "path", "prep", "depo", "src", "fmt")

    def __init__(self, title, vid=None, url=None, duration=0, file_id=None, by="", depo=False, src=""):
        self.title, self.vid, self.url, self.duration = title, vid, url, int(duration or 0)
        self.file_id, self.by, self.depo, self.src = file_id, by, depo, src
        self.path, self.prep, self.fmt = None, None, ""


class ChatState:
    def __init__(self, chat_id, title=""):
        self.chat_id, self.title = chat_id, title
        self.queue = deque()
        self.current = None
        self.started = 0.0
        self.paused = False
        self.np_msg = None                   # "indi oxunur" mesajı (message_id)
        self.idle_task = None
        self.lock = asyncio.Lock()
        self.radio = None                    # 🔀 Mix rejimi: {"seed": vid, "seen": set()}
        self.refilling = False


class NotMember(Exception):
    """Asistan qrupda deyil — qoşulmaq üçün admin icazəsi lazımdır."""


class Assistant:
    def __init__(self, context):
        self.ctx = context
        self.client = None
        self.calls = None
        self.me = None
        self.status = "söndürülüb"
        self.error = ""
        self.chats = {}                      # chat_id → ChatState
        self._lock = asyncio.Lock()

    # ── ayarlar ──
    @property
    def enabled(self) -> bool:
        return _get(S_ON, "0") == "1"

    @property
    def source(self) -> str:
        v = _get(S_SRC, "ytm")
        return v if v in SOURCES else "ytm"

    @property
    def depo_first(self) -> bool:
        return _get(S_DEPO, "1") == "1"

    @property
    def who(self) -> str:
        v = _get(S_WHO, "all")
        return v if v in WHO else "all"

    @property
    def mix_n(self) -> int:
        try:
            v = int(_get(S_MIXN, 15))
        except (TypeError, ValueError):
            v = 15
        return v if v in MIX_STEPS else 15

    @property
    def quality(self) -> str:
        v = _get(S_QUAL, "best")
        return v if v in QUALITY else "best"

    @property
    def normalize(self) -> bool:
        return _get(S_NORM, "1") == "1"

    @property
    def volume(self) -> int:
        try:
            v = int(_get(S_VOL, 100))
        except (TypeError, ValueError):
            v = 100
        return v if v in VOL_STEPS else 100

    @property
    def throttle(self) -> bool:
        return _get(S_THROTTLE, "1") == "1"

    def apply_throttle(self):
        """Yayım gedərkən 📦 depo yükləmə proseslərini yarıya endir — CPU boş qalsın, səs kəsilməsin."""
        try:
            from core import cpu_pool
        except Exception:
            return
        live = any(s.current for s in self.chats.values())
        full = cpu_pool.depo_procs()
        want = max(1, full // 2) if (live and self.throttle) else full
        lane = cpu_pool.LANES["depo"]
        if lane.size != want:
            lane.size = want
            logger.info(f"🎙 📦 Depo prosesləri: {want}/{full}" + (" (yayım gedir)" if want < full else ""))

    @property
    def mix_auto(self) -> bool:
        return _get(S_MIXAUTO, "1") == "1"

    # ── 🔀 Mix ──
    async def mix_tracks(self, vid: str, limit: int, seen: set) -> list:
        """Verilən mahnının YouTube Mix-i → Track siyahısı (depoda olanlar 📦 file_id ilə, təkrarlar süzülür)."""
        ym = getattr(self.ctx, "youtube_manager", None)
        if ym is None or not vid:
            return []
        res = await ym.youtube_mix(vid, limit=limit + 10)
        vids = [m.group(1) for r in res or [] if (m := _VID_RE.search(r.get("url") or ""))]
        cached = await asyncio.to_thread(audio_cache.cached_ids, vids) if vids else set()
        out = []
        for r in res or []:
            m = _VID_RE.search(r.get("url") or "")
            if not m:
                continue
            v, dur = m.group(1), int(r.get("raw_duration") or 0)
            if v in seen or dur > MAX_DURATION:
                continue
            seen.add(v)
            c = await asyncio.to_thread(audio_cache.get_cached, v) if v in cached else None
            title = f"{c['performer']} - {c['title']}" if c and c.get("performer") and c["performer"] != "YouTube" \
                else r.get("title") or v
            out.append(Track(title, v, f"https://www.youtube.com/watch?v={v}", dur or (c or {}).get("duration") or 0,
                             (c or {}).get("file_id"), "", depo=bool(c), src="mix"))
            if len(out) >= limit:
                break
        return out

    async def start_mix(self, chat_id: int, title: str, seed: Track, by: str) -> tuple:
        """Seed mahnını (oxunmursa) oxudur, ardınca Mix-i növbəyə qoyur, ♾ rejimi açır. → (oxunur?, əlavə sayı)"""
        st = self.state(chat_id, title)
        seen = {t.vid for t in [st.current, *st.queue] if t and t.vid}
        if seed.vid:
            seen.add(seed.vid)
        tracks = await self.mix_tracks(seed.vid, self.mix_n, seen)
        if not tracks:
            raise RuntimeError("bu mahnı üçün Mix tapılmadı")
        st.radio = {"seed": seed.vid, "seen": seen}
        started = False
        if st.current is None or (st.current.vid != seed.vid):
            kind, _ = await self.enqueue(chat_id, title, seed)
            started = kind == "playing"
        st.queue.extend(tracks)
        if st.queue:
            self.prefetch(st.queue[0])
        await self.refresh_np(st)
        return started, len(tracks)

    async def refill(self, st: ChatState, force: bool = False):
        """♾ Mix: növbə azalanda son mahnının Mix-indən yeni partiya."""
        if not st.radio or st.refilling or not (self.mix_auto or force):
            return 0
        st.refilling = True
        try:
            base = (st.queue[-1].vid if st.queue and st.queue[-1].vid else
                    st.current.vid if st.current and st.current.vid else st.radio["seed"])
            tracks = await self.mix_tracks(base, self.mix_n, st.radio["seen"])
            if not tracks and base != st.radio["seed"]:
                tracks = await self.mix_tracks(st.radio["seed"], self.mix_n, st.radio["seen"])
            st.queue.extend(tracks)
            if tracks:
                self.prefetch(st.queue[0])
                logger.info(f"🎙 ♾ Mix ({st.chat_id}): +{len(tracks)} mahnı")
            return len(tracks)
        except Exception as e:
            logger.warning(f"🎙 Mix davamı alınmadı: {e}")
            return 0
        finally:
            st.refilling = False

    @property
    def running(self) -> bool:
        return self.calls is not None and self.client is not None and self.client.is_connected()

    @staticmethod
    def has_session() -> bool:
        return os.path.isfile(SESSION_PATH) and os.path.getsize(SESSION_PATH) > 10

    @staticmethod
    def _read_session() -> str:
        with open(SESSION_PATH, encoding="utf-8") as f:
            return f.read().strip()

    @staticmethod
    def _write_session(s: str):
        tg_login.write_secret(SESSION_PATH, s)

    # ── başlat / dayandır ──
    async def start(self) -> str:
        async with self._lock:
            if self.running:
                return "artıq işləyir"
            if not API_ID or not API_HASH:
                self.status, self.error = "xəta", "TELETHON_API_ID / TELETHON_API_HASH yoxdur"
                return self.error
            if not pytgcalls_version():
                self.status, self.error = "xəta", "py-tgcalls quraşdırılmayıb: pip install py-tgcalls"
                return self.error
            if not self.has_session():
                self.status, self.error = "hesab yoxdur", "🔑 Hesab qoşulmayıb"
                return self.error
            try:
                from telethon import TelegramClient
                from telethon.sessions import StringSession
                self.client = TelegramClient(StringSession(self._read_session()), int(API_ID), API_HASH)
                self.client.flood_sleep_threshold = 60
                await self.client.connect()
                if not await self.client.is_user_authorized():
                    raise RuntimeError("sessiya etibarsızdır — hesabı yenidən qoş")
                self.me = await self.client.get_me()
                self.calls = Calls(self.client, self._on_end, self._on_closed)
                await self.calls.start()
                asyncio.ensure_future(self._warm())       # qrupların entity keşi (qoşulma sürətli olsun)
                self.status, self.error = "işləyir", ""
                logger.info(f"🎙 Canlı yayım asistanı: {self.me.first_name} (@{self.me.username or '—'}) · "
                            f"py-tgcalls {pytgcalls_version()}")
                return ""
            except Exception as e:
                logger.error(f"🎙 Asistan başlamadı: {e}", exc_info=True)
                self.status, self.error = "xəta", str(e)[:200]
                await self._close_client()
                return self.error

    async def _warm(self):
        with contextlib.suppress(Exception):
            await self.client.get_dialogs()

    async def _close_client(self):
        self.calls = None
        if self.client is not None:
            with contextlib.suppress(Exception):
                await self.client.disconnect()
        self.client = None

    async def stop(self, kb=None):
        async with self._lock:
            for chat_id in list(self.chats):
                await self._end_chat(chat_id, announce=True, reason="asistan söndürüldü", kb=kb)
            await self._close_client()
            self.status = "söndürülüb"

    # ── qrupa qoşulma ──
    async def member_status(self, chat_id: int) -> str:
        """Bot API ilə: 'member' | 'left' | 'kicked' | 'unknown'."""
        if not self.me:
            return "unknown"
        try:
            m = await self.ctx.bot.get_chat_member(chat_id, self.me.id)
        except Exception as e:
            logger.debug(f"🎙 get_chat_member: {e}")
            return "unknown"
        st = str(getattr(m.status, "value", m.status)).lower()
        if st in ("member", "administrator", "creator") or (st == "restricted" and getattr(m, "is_member", True)):
            return "member"
        return "kicked" if st == "kicked" else "left"

    async def _cache_entity(self, chat_id: int) -> bool:
        for attempt in (1, 2):
            try:
                await self.client.get_input_entity(chat_id)
                return True
            except Exception:
                if attempt == 1:                          # StringSession-da entity keşi boşdur — dialoqları oxu
                    with contextlib.suppress(Exception):
                        await self.client.get_dialogs()
        return False

    async def ensure_member(self, chat_id: int):
        """Asistan qrupdadırsa entity hazırlanır; deyilsə NotMember (özü icazəsiz qoşulmur)."""
        status = await self.member_status(chat_id)
        if status in ("left", "kicked"):
            raise NotMember(status)
        if not await self._cache_entity(chat_id):
            raise NotMember("unknown")

    async def join(self, chat_id: int):
        """Admin icazəsindən sonra: asistan qrupa qoşulur (açıq qrup — username, qapalı — botun dəvət linki)."""
        from telethon.errors import UserAlreadyParticipantError
        from telethon.tl.functions.channels import JoinChannelRequest
        from telethon.tl.functions.messages import ImportChatInviteRequest
        bot = self.ctx.bot
        status = await self.member_status(chat_id)
        if status == "member" and await self._cache_entity(chat_id):
            return
        if status == "kicked":
            try:
                await bot.unban_chat_member(chat_id, self.me.id, only_if_banned=True)
            except Exception:
                raise RuntimeError("Asistan bu qrupdan qadağan olunub (ban). Qadağanı götürün və ya botu "
                                   "\"İstifadəçiləri bloklamaq\" icazəli admin edin, sonra yenidən ✅ basın.")
        chat = await bot.get_chat(chat_id)
        try:
            if chat.username:
                await self.client(JoinChannelRequest(chat.username))
            else:
                try:
                    link = await bot.create_chat_invite_link(chat_id, member_limit=1, name="🎙 asistan")
                except Exception:
                    raise RuntimeError("Qapalı qrupdur — botu <b>admin</b> edin (\"Dəvət linki ilə istifadəçi əlavə "
                                       "etmək\" icazəsi), sonra yenidən ✅ basın.")
                await self.client(ImportChatInviteRequest(link.invite_link.rsplit("/", 1)[-1].lstrip("+")))
        except UserAlreadyParticipantError:
            pass
        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError(f"Asistan qoşula bilmədi: {escape(str(e)[:120])}")
        await asyncio.sleep(1)
        if not await self._cache_entity(chat_id):
            raise RuntimeError("Asistan qoşuldu, amma qrup hələ görünmür — bir az sonra yenidən sınayın.")
        logger.info(f"🎙 Asistan qrupa qoşuldu: {chat.title} ({chat_id})")

    # ── mahnı tapmaq ──
    async def resolve(self, query: str, by: str) -> Track:
        query = query.strip()
        m = _VID_RE.search(query)
        if m:
            vid = m.group(1)
            c = await asyncio.to_thread(audio_cache.get_cached, vid)
            title = f"{c['performer']} - {c['title']}" if c and c.get("performer") else (c or {}).get("title") or vid
            return Track(title, vid, f"https://www.youtube.com/watch?v={vid}", (c or {}).get("duration") or 0,
                         (c or {}).get("file_id"), by, depo=bool(c), src="link")
        if self.depo_first:
            hits = await asyncio.to_thread(audio_cache.search_depo, query, 1)
            if hits:
                h = hits[0]
                return Track(f"{h.get('performer')} - {h.get('title')}" if h.get("performer") else h.get("title"),
                             h.get("video_id"), f"https://www.youtube.com/watch?v={h.get('video_id')}",
                             h.get("duration") or 0, h.get("file_id"), by, depo=True, src="depo")
        ym = getattr(self.ctx, "youtube_manager", None)
        if ym is None:
            raise RuntimeError("youtube_manager yoxdur")
        results = await ym.youtube_search(query, limit=6, source=self.source)
        pick = next((r for r in results or [] if _VID_RE.search(r.get("url") or "")
                     and (r.get("raw_duration") or 0) <= MAX_DURATION), None)
        if not pick:
            raise RuntimeError("tapılmadı")
        vid = _VID_RE.search(pick["url"]).group(1)
        c = await asyncio.to_thread(audio_cache.get_cached, vid)
        title = f"{c['performer']} - {c['title']}" if c and c.get("performer") and c["performer"] != "YouTube" \
            else pick.get("title")
        return Track(title, vid, pick["url"], pick.get("raw_duration") or (c or {}).get("duration") or 0,
                     (c or {}).get("file_id"), by, depo=bool(c), src=self.source)

    # ── faylı hazırlamaq: əvvəl depo / keş, yoxdursa yüklə (depoya da düşür) ──
    async def obtain(self, t: Track) -> str:
        if t.path and os.path.exists(t.path):
            return t.path
        path = None
        if self.quality == "best" and t.vid:
            try:
                path = await self._obtain_best(t)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.info(f"🎙 💎 Opus alınmadı ({str(e)[:120]}) — depo / m4a ilə davam")
        if path is None:
            path = await self._obtain_depo(t)
        if self.normalize:
            path = await self._normalize(t, path)
        return path

    async def _obtain_best(self, t: Track) -> str:
        """YouTube-un ən yaxşı audio axını (Opus 251, ~160 kbps) — çevirmə yoxdur. Depo fonda dolur."""
        from core import cpu_pool
        from core import youtube_handler as yh
        os.makedirs(PLAY_DIR, exist_ok=True)
        opts = {
            "format": "bestaudio[acodec=opus]/bestaudio[ext=webm]/bestaudio",
            "outtmpl": os.path.abspath(os.path.join(PLAY_DIR, f"{t.vid}_{id(t)}_best.%(ext)s")),
            "noplaylist": True, "nopart": True, "quiet": True, "retries": 3, "fragment_retries": 5,
            **yh.JS_OPTS, **yh.cookie_opts(),
        }
        res = await cpu_pool.ytdl_download(t.url, opts)
        if not os.path.exists(res["path"]):
            raise RuntimeError("fayl yoxdur")
        t.path, t.fmt = res["path"], "💎 Opus"
        if not t.file_id:                                  # depoda yoxdursa — orada da olsun (bir dəfə)
            asyncio.ensure_future(self._fill_depo(t))
        return t.path

    async def _fill_depo(self, t: Track):
        fetch = getattr(self.ctx, "music_fetch_audio", None)
        if fetch is None:
            return
        import threading
        try:
            res = await fetch(t.url, t.title, t.duration, threading.Event())
            if res.get("path"):
                with contextlib.suppress(OSError):
                    os.remove(res["path"])
        except Exception as e:
            logger.debug(f"🎙 depoya yazılmadı: {e}")

    async def _normalize(self, t: Track, src: str) -> str:
        """Səs səviyyəsini bərabərləşdirir (loudnorm). Nəticə FLAC — itkisiz, əlavə keyfiyyət itkisi olmur."""
        from core import cpu_pool
        dst = os.path.splitext(src)[0] + "_norm.flac"
        proc = await asyncio.create_subprocess_exec(
            *cpu_pool.ffmpeg_prefix(), "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", src,
            "-vn", "-af", LOUDNORM, "-ar", "48000", "-ac", "2", "-c:a", "flac", "-compression_level", "0", dst,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        _, err = await proc.communicate()
        if proc.returncode != 0 or not os.path.exists(dst):
            logger.info(f"🎙 Normallaşdırma alınmadı: {err.decode(errors='ignore')[-150:]}")
            with contextlib.suppress(OSError):
                os.remove(dst)
            return src
        with contextlib.suppress(OSError):
            os.remove(src)
        t.path = dst
        return dst

    async def _obtain_depo(self, t: Track) -> str:
        os.makedirs(PLAY_DIR, exist_ok=True)
        dst = os.path.abspath(os.path.join(PLAY_DIR, f"{t.vid or int(time.time() * 1000)}_{id(t)}.m4a"))
        if not t.file_id and t.url:
            fetch = getattr(self.ctx, "music_fetch_audio", None)
            if fetch is None:
                raise RuntimeError("music_plugin yüklənməyib")
            import threading
            res = await fetch(t.url, t.title, t.duration, threading.Event())
            if res.get("path") and os.path.exists(res["path"]):
                os.replace(res["path"], dst)
                t.path, t.fmt = dst, "AAC"
                return dst
            t.file_id = res.get("file_id")
        if t.file_id:
            try:
                await self.ctx.bot.download(t.file_id, destination=dst)     # 📦 depodan (Bot API, ≤20 MB)
                t.path, t.fmt = dst, "📦 AAC"
                return dst
            except Exception as e:
                logger.info(f"🎙 Bot API ilə endirilmədi ({e}) — asistan depodan götürür")
            c = await asyncio.to_thread(audio_cache.get_cached, t.vid) if t.vid else None
            if c and c.get("chat_id") and c.get("message_id"):
                ref = audio_cache.depo_setting()
                try:
                    ent = await self.client.get_entity(ref if not ref.lstrip("-").isdigit() else int(ref))
                except Exception:
                    ent = await self.client.get_entity(c["chat_id"])
                msg = await self.client.get_messages(ent, ids=c["message_id"])
                if msg and msg.media:
                    await self.client.download_media(msg, file=dst)
                    t.path = dst
                    return dst
        raise RuntimeError("mahnı faylı alınmadı")

    def prefetch(self, t: Track):
        if t.prep is None:
            t.prep = asyncio.ensure_future(self.obtain(t))
            t.prep.add_done_callback(lambda f: f.exception() if not f.cancelled() else None)

    # ── növbə və oxutma ──
    def state(self, chat_id, title="") -> ChatState:
        st = self.chats.get(chat_id)
        if st is None:
            st = self.chats[chat_id] = ChatState(chat_id, title)
        elif title:
            st.title = title
        return st

    async def enqueue(self, chat_id: int, title: str, t: Track) -> tuple:
        st = self.state(chat_id, title)
        if st.idle_task:
            st.idle_task.cancel()
            st.idle_task = None
        async with st.lock:
            if st.current is None:
                try:
                    await self._play(st, t)
                except Exception:
                    self._drop_file(t)
                    raise
                return "playing", 0
            st.queue.append(t)
            self.prefetch(st.queue[0])
            return "queued", len(st.queue)

    async def _play(self, st: ChatState, t: Track):
        if not self.running:
            raise RuntimeError("asistan işləmir")
        await self.ensure_member(st.chat_id)
        path = await (t.prep if t.prep is not None else self.obtain(t))
        await self.calls.play(st.chat_id, path)
        if self.volume != 100:
            await self.calls.volume(st.chat_id, self.volume)
        prev = st.current
        st.current, st.started, st.paused = t, time.time(), False
        self.apply_throttle()
        if prev and prev is not t:
            self._drop_file(prev)
        if st.queue:
            self.prefetch(st.queue[0])
        if st.radio and len(st.queue) <= MIX_REFILL_AT:
            asyncio.ensure_future(self._refill_and_refresh(st))
        await self.announce(st)

    async def _refill_and_refresh(self, st: ChatState):
        if await self.refill(st):
            await self.refresh_np(st)

    async def next(self, chat_id: int, skipped=False):
        st = self.chats.get(chat_id)
        if not st:
            return
        async with st.lock:
            if not st.queue and st.radio:
                await self.refill(st)                     # ♾ Mix — növbə bitib, yenisini gətir
            while st.queue:
                t = st.queue.popleft()
                try:
                    await self._play(st, t)
                    return
                except Exception as e:
                    logger.warning(f"🎙 Oxudulmadı ({t.title}): {e}")
                    with contextlib.suppress(Exception):
                        await self.ctx.bot.send_message(chat_id, f"⚠️ Oxudulmadı: <b>{escape(t.title)}</b>\n"
                                                                 f"<code>{escape(str(e)[:150])}</code>",
                                                        parse_mode="HTML")
                    self._drop_file(t)
            if skipped:                                   # ⏭ son mahnıda — səsi dərhal kəs, yayımdan çıx
                await self._end_chat(chat_id, announce=True, reason="növbə bitdi")
                return
            if st.current:
                self._drop_file(st.current)
            st.current = None
            await self.announce(st, ended=True)
            st.idle_task = asyncio.ensure_future(self._idle_leave(chat_id))

    async def _idle_leave(self, chat_id):
        await asyncio.sleep(IDLE_LEAVE)
        st = self.chats.get(chat_id)
        if st and st.current is None and not st.queue:
            await self._end_chat(chat_id, announce=False)

    async def _on_end(self, chat_id):
        st = self.chats.get(chat_id)
        if st and st.current and time.time() - st.started > 2:     # təkrar hadisələri süz
            await self.next(chat_id)

    async def _on_closed(self, chat_id, status):
        if chat_id in self.chats:
            logger.info(f"🎙 Yayım bağlandı ({status}): {chat_id}")
            await self._end_chat(chat_id, announce=True, reason="səsli yayım bağlandı", leave=False)

    async def _end_chat(self, chat_id, announce=True, reason="", leave=True, kb=None):
        st = self.chats.pop(chat_id, None)
        self.apply_throttle()
        if leave and self.calls is not None:
            await self.calls.leave(chat_id)
        if not st:
            return
        if st.idle_task:
            st.idle_task.cancel()
        for t in [st.current, *st.queue]:
            if t:
                if t.prep and not t.prep.done():
                    t.prep.cancel()
                self._drop_file(t)
        if announce and st.np_msg:
            with contextlib.suppress(Exception):
                await self.ctx.bot.edit_message_text(
                    chat_id=chat_id, message_id=st.np_msg, parse_mode="HTML",
                    text=f"⏹ <b>Yayım bitdi</b>" + (f" — {escape(reason)}" if reason else ""), reply_markup=kb)

    @staticmethod
    def _drop_file(t: Track):
        if t and t.path:
            with contextlib.suppress(OSError):
                os.remove(t.path)
            t.path = None

    # ── "indi oxunur" mesajı ──
    def np_view(self, st: ChatState, ended=False):
        if ended or st.current is None:
            return "✅ <b>Növbə bitdi</b> — /play ilə yeni mahnı əlavə et", None
        t = st.current
        src = {"depo": "📦 depo", "link": "🔗 link", "ytm": "🎵 YT Music", "yt": "▶️ YouTube",
               "mix": "🔀 Mix"}.get(t.src, "")
        q = " · ".join(x for x in (t.fmt, "🔊 norm" if self.normalize and (t.path or "").endswith("_norm.flac") else "") if x)
        lines = [f"{'⏸' if st.paused else '🎙'} <b>{'Fasilə' if st.paused else 'İndi oxunur'}</b>"
                 + (f"  <i>{q}</i>" if q else ""),
                 f"🎵 <b>{escape(t.title[:80])}</b>",
                 f"⏱ {fmt_dur(t.duration)}" + (f" · {src}" if src else "") + ("  📦" if t.depo and t.src != "depo" else "")
                 + (f" · 👤 {escape(t.by)}" if t.by else "")]
        if st.radio:
            lines.append("🔀 <b>Mix rejimi</b>" + (" · ♾ növbə bitəndə davam edir" if self.mix_auto else ""))
        if st.queue:
            lines.append(f"\n📋 <b>Növbədə: {len(st.queue)}</b>")
            for i, q in enumerate(list(st.queue)[:5], 1):
                lines.append(f"{i}. {escape(q.title[:50])} <i>({fmt_dur(q.duration)})</i>")
            if len(st.queue) > 5:
                lines.append(f"<i>... və daha {len(st.queue) - 5}</i>")
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="▶️" if st.paused else "⏸", callback_data="vp:toggle"),
            InlineKeyboardButton(text="⏭", callback_data="vp:skip"),
            InlineKeyboardButton(text="⏹", callback_data="vp:stop"),
            InlineKeyboardButton(text="🔀✅" if st.radio else "🔀", callback_data="vp:radio"),
            InlineKeyboardButton(text="🔄", callback_data="vp:refresh"),
        ]])
        return "\n".join(lines), kb

    async def announce(self, st: ChatState, ended=False):
        text, kb = self.np_view(st, ended)
        bot = self.ctx.bot
        if st.np_msg and not ended and st.current is not None:
            # yeni mahnı — köhnə mesaj silinir, yenisi aşağıda görünsün
            with contextlib.suppress(Exception):
                await bot.delete_message(st.chat_id, st.np_msg)
            st.np_msg = None
        if st.np_msg:
            with contextlib.suppress(Exception):
                await bot.edit_message_text(chat_id=st.chat_id, message_id=st.np_msg, text=text,
                                            parse_mode="HTML", reply_markup=kb)
                return
        with contextlib.suppress(Exception):
            msg = await bot.send_message(st.chat_id, text, parse_mode="HTML", reply_markup=kb,
                                         disable_notification=True)
            st.np_msg = msg.message_id

    async def refresh_np(self, st: ChatState):
        if not st.np_msg:
            return
        text, kb = self.np_view(st)
        with contextlib.suppress(Exception):
            await self.ctx.bot.edit_message_text(chat_id=st.chat_id, message_id=st.np_msg, text=text,
                                                 parse_mode="HTML", reply_markup=kb)

    # ── menyu ──
    def label(self) -> str:
        if not self.enabled:
            return "🎙 Canlı yayım · 🔴"
        if not self.running:
            return "🎙 Canlı yayım · ⚠️"
        n = sum(1 for s in self.chats.values() if s.current)
        return "🎙 Canlı yayım · 🟢" + (f" {n} yayım" if n else "")


# ───────────────────────── plugin ─────────────────────────
def setup(context):
    dp, bot = context.dp, context.bot
    va = Assistant(context)
    old = getattr(context, "voice_assistant", None)
    context.voice_assistant = va

    def is_creator(uid) -> bool:
        return bool(context.creator_id) and uid == context.creator_id

    # music_plugin-in ümumi mətn handler-i giriş məlumatını axtarış saymasın (hər iki hesabın girişi)
    context.va_waiting = tg_login.any_waiting

    async def can_control(chat_id, user_id) -> bool:
        if is_creator(user_id) or va.who == "all":
            return True
        with contextlib.suppress(Exception):
            m = await bot.get_chat_member(chat_id, user_id)
            return m.status in ("administrator", "creator")
        return False

    # ── menyu paneli ──
    def panel_view(note: str = ""):
        ver = pytgcalls_version()
        if not ver:
            state = "❌ py-tgcalls quraşdırılmayıb — <code>pip install py-tgcalls</code>"
        elif not va.has_session():
            state = "⚠️ Hesab qoşulmayıb — 🔑 düyməsi ilə qoş"
        elif not va.enabled:
            state = "🔴 Söndürülüb"
        elif va.running:
            state = "🟢 İşləyir"
        else:
            state = f"⚠️ İşləmir: {escape(va.error or va.status)}"
        me = va.me
        acc = (f"{escape(me.first_name or '')} (@{escape(me.username)})" if me and me.username
               else escape(me.first_name or "") if me else ("qoşulub (aktiv deyil)" if va.has_session() else "—"))
        lines = ["🎙 <b>2️⃣ Canlı yayım asistanı</b> — <i>yalnız səs</i>\n━━━━━━━━━━━━━━━━━━",
                 f"<b>Vəziyyət:</b> {state}",
                 f"👤 <b>Hesab:</b> {acc}",
                 f"🔎 <b>Axtarış:</b> {SOURCES[va.source]}",
                 f"📦 <b>Əvvəl depo (adla):</b> {'✅' if va.depo_first else '❌'}",
                 f"🎛 <b>İdarə edə bilər:</b> {WHO[va.who]}",
                 f"🧩 <b>py-tgcalls:</b> {ver or '—'}"]
        active = [s for s in va.chats.values() if s.current or s.queue]
        lines.append("━━━━━━━━━━━━━━━━━━")
        if active:
            lines.append(f"🔊 <b>Aktiv yayımlar: {len(active)}</b>")
            for s in active[:8]:
                cur = s.current.title[:40] if s.current else "—"
                lines.append(f"• {escape(s.title or str(s.chat_id))[:30]} — {'⏸' if s.paused else '▶️'} "
                             f"{escape(cur)}" + (f" <i>(+{len(s.queue)})</i>" if s.queue else ""))
        else:
            lines.append("🔇 <i>Aktiv yayım yoxdur</i>")
        lines.insert(7, f"🔀 <b>/mixplay:</b> {va.mix_n} mahnı · ♾ davam: {'✅' if va.mix_auto else '❌'}")
        lines.insert(8, f"🎧 <b>Keyfiyyət:</b> {QUALITY[va.quality]} · 🔊 normallaşdırma: "
                        f"{'✅' if va.normalize else '❌'} · səviyyə {va.volume}%"
                        + (" ⚠️" if va.volume > 100 else "")
                        + f"\n⚙️ <b>Yayımda deponu yavaşlat:</b> {'✅' if va.throttle else '❌'}")
        lines.append("\n<i>Qrupda: /play mahnı adı · /mixplay · /pause · /resume · /skip · /stop · /queue</i>")
        if note:
            lines.append(f"\n{note}")
        rows = [[InlineKeyboardButton(text=("🟢 Asistan: Açıq" if va.enabled else "🔴 Asistan: Söndürülüb"),
                                      callback_data="va:toggle")],
                [InlineKeyboardButton(text=("✅ " if va.source == k else "") + v, callback_data=f"va:src:{k}")
                 for k, v in SOURCES.items()],
                [InlineKeyboardButton(text=f"📦 Depo əvvəl: {'✅' if va.depo_first else '❌'}", callback_data="va:depo"),
                 InlineKeyboardButton(text=WHO[va.who], callback_data="va:who")],
                [InlineKeyboardButton(text=f"🔀 Mix: {va.mix_n} mahnı", callback_data="va:mixn"),
                 InlineKeyboardButton(text=f"♾ Davam: {'✅' if va.mix_auto else '❌'}", callback_data="va:mixauto")],
                [InlineKeyboardButton(text=("✅ " if va.quality == k else "") + v.split(" (")[0], callback_data=f"va:q:{k}")
                 for k, v in QUALITY.items()],
                [InlineKeyboardButton(text=f"🔊 Norm: {'✅' if va.normalize else '❌'}", callback_data="va:norm"),
                 InlineKeyboardButton(text=f"🔉 {va.volume}%", callback_data="va:vol"),
                 InlineKeyboardButton(text=f"⚙️ Depo: {'🐢' if va.throttle else '🚀'}", callback_data="va:throttle")]]
        if va.has_session():
            rows.append([InlineKeyboardButton(text="🔄 Yenidən qoşul", callback_data="va:reconnect"),
                         InlineKeyboardButton(text="🔁 Hesabı dəyiş", callback_data="va:login")])
            rows.append([InlineKeyboardButton(text="🚪 Hesabdan çıx", callback_data="va:logout")])
        else:
            rows.append([InlineKeyboardButton(text="🔑 Hesab qoş", callback_data="va:login")])
        if active:
            rows.append([InlineKeyboardButton(text="⏹ Bütün yayımları bitir", callback_data="va:stopall")])
        rows.append([InlineKeyboardButton(text="🔄 Yenilə", callback_data="va:panel"),
                     InlineKeyboardButton(text="⬅️ Userbotlar", callback_data="ubacc:panel")])
        return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)

    async def on_login_done(session_str, me, client):
        main_me = getattr(context, "telethon_me", None)
        if main_me and main_me.id == me.id:
            with contextlib.suppress(Exception):
                await client.log_out()
            return panel_view("❌ <b>Bu hesab 1️⃣ əsas userbotdur.</b> Asistan üçün ayrıca hesab qoş — eyni sessiyanı "
                              "iki client açanda Telegram birini atır.")
        await va.stop()
        va._write_session(session_str)
        va.me = me
        _set(S_ON, "1")
        err = await va.start()
        return panel_view(f"✅ <b>2️⃣ Asistan qoşuldu:</b> {escape(me.first_name or '')}"
                          + (f" (@{escape(me.username)})" if me.username else "")
                          + (f"\n⚠️ {escape(err)}" if err else ""))

    flow = tg_login.LoginFlow(
        "assistant", context, "2️⃣ Canlı yayım asistanı hesabını qoş",
        "Ayrıca hesab olmalıdır (1️⃣ əsas userbot yox). Bu hesab yalnız səsli yayımda mahnı oxudacaq.",
        on_login_done, "va:logincancel")

    async def show(cb, text, kb):
        with contextlib.suppress(Exception):
            await cb.message.edit_text(text, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True)

    @dp.callback_query(F.data.startswith("va:"))
    async def va_cb(cb: types.CallbackQuery):
        if not is_creator(cb.from_user.id):
            await cb.answer("⛔ Yalnız bot sahibi", show_alert=True)
            return
        parts = cb.data.split(":")
        act = parts[1] if len(parts) > 1 else "panel"
        note = ""
        if act == "toggle":
            if va.enabled:
                _set(S_ON, "0")
                await cb.answer("🔴 Söndürülür...")
                await va.stop(kb=await contact_kb())     # aktiv qruplarda "adminlə əlaqə" düyməsi
            else:
                _set(S_ON, "1")
                await cb.answer("🟢 Başladılır...")
                err = await va.start()
                note = f"⚠️ {escape(err)}" if err else "🟢 Asistan işləyir"
        elif act == "src" and len(parts) > 2 and parts[2] in SOURCES:
            _set(S_SRC, parts[2])
            await cb.answer(SOURCES[parts[2]])
        elif act == "depo":
            _set(S_DEPO, "0" if va.depo_first else "1")
            await cb.answer("📦 Depo əvvəl: " + ("✅" if va.depo_first else "❌"))
        elif act == "who":
            _set(S_WHO, "admins" if va.who == "all" else "all")
            await cb.answer(WHO[va.who])
        elif act == "mixn":
            nxt = MIX_STEPS[(MIX_STEPS.index(va.mix_n) + 1) % len(MIX_STEPS)]
            _set(S_MIXN, nxt)
            await cb.answer(f"🔀 /mixplay: {nxt} mahnı (bir partiya)")
        elif act == "q" and len(parts) > 2 and parts[2] in QUALITY:
            _set(S_QUAL, parts[2])
            await cb.answer(QUALITY[parts[2]] + (" — növbəti mahnıdan" if va.chats else ""))
        elif act == "norm":
            _set(S_NORM, "0" if va.normalize else "1")
            await cb.answer("🔊 Normallaşdırma " + ("açıq — bütün mahnılar eyni səviyyədə" if va.normalize else "söndürüldü"))
        elif act == "vol":
            nxt = VOL_STEPS[(VOL_STEPS.index(va.volume) + 1) % len(VOL_STEPS)]
            _set(S_VOL, nxt)
            for cid, stt in va.chats.items():
                if stt.current:
                    await va.calls.volume(cid, nxt)
            await cb.answer(f"🔉 {nxt}%" + (" — 100%-dən yuxarı səs təhrif ola bilər" if nxt > 100 else ""),
                            show_alert=nxt > 100)
        elif act == "throttle":
            _set(S_THROTTLE, "0" if va.throttle else "1")
            va.apply_throttle()
            await cb.answer("⚙️ Yayımda depo prosesləri yarıya enir" if va.throttle else "⚙️ Depo tam sürətdə")
        elif act == "mixauto":
            _set(S_MIXAUTO, "0" if va.mix_auto else "1")
            await cb.answer("♾ Növbə bitəndə Mix davam edir" if va.mix_auto else "♾ Davam söndürüldü")
        elif act == "reconnect":
            await cb.answer("🔄 Yenidən qoşulur...")
            await va.stop()
            if va.enabled:
                err = await va.start()
                note = f"⚠️ {escape(err)}" if err else "✅ Yenidən qoşuldu"
            else:
                note = "ℹ️ Asistan söndürülüb — 🟢 ilə aç"
        elif act == "logout":
            await cb.answer()
            await show(cb, "🚪 <b>Asistan hesabından çıxılsın?</b>\n\nSessiya silinir, hesab Telegram-dan da "
                           "çıxış edir (Cihazlar siyahısından itir).",
                       InlineKeyboardMarkup(inline_keyboard=[[
                           InlineKeyboardButton(text="✅ Bəli, çıx", callback_data="va:logoutok"),
                           InlineKeyboardButton(text="↩️ Xeyr", callback_data="va:panel")]]))
            return
        elif act == "logoutok":
            await cb.answer("🚪 Çıxılır...")
            client = va.client
            await va.stop()
            try:
                if client is None and va.has_session():
                    from telethon import TelegramClient
                    from telethon.sessions import StringSession
                    client = TelegramClient(StringSession(va._read_session()), int(API_ID), API_HASH)
                if client is not None:
                    if not client.is_connected():
                        await client.connect()
                    await client.log_out()
            except Exception as e:
                logger.warning(f"🎙 log_out: {e}")
            with contextlib.suppress(OSError):
                os.remove(SESSION_PATH)
            va.me = None
            _set(S_ON, "0")
            note = "🚪 Hesabdan çıxıldı"
        elif act == "stopall":
            for cid in list(va.chats):
                await va._end_chat(cid, announce=True, reason="bot sahibi dayandırdı")
            await cb.answer("⏹ Hamısı bitdi")
        elif act == "login":
            try:
                text, kb = await flow.begin(cb.message.chat.id, cb.message.message_id)
            except Exception as e:
                await cb.answer(str(e)[:190], show_alert=True)
                return
            await cb.answer()
            await show(cb, text, kb)
            return
        elif act == "logincancel":
            await flow.cancel()
            await cb.answer("❌ Ləğv edildi")
        else:
            await cb.answer()
        await show(cb, *panel_view(note))

    # ── giriş: telefon → kod → 2FA (core/tg_login.py) ──
    @dp.message(F.chat.type == "private", F.text, F.from_user.func(lambda u: flow.waiting(u.id)))
    async def va_login_input(message: types.Message):
        await flow.handle(message)

    # ── qrup komandaları ──
    # ── 👤 adminlə əlaqə ──
    contact_cache = {}

    async def contact_url() -> str:
        v = (os.getenv("ADMIN_CONTACT") or "").strip()
        if v:
            return v if v.startswith(("http://", "https://", "tg://")) else f"https://t.me/{v.lstrip('@')}"
        if "url" not in contact_cache:
            url = f"tg://user?id={context.creator_id}"
            with contextlib.suppress(Exception):
                ch = await bot.get_chat(context.creator_id)
                if ch.username:
                    url = f"https://t.me/{ch.username}"
            contact_cache["url"] = url
        return contact_cache["url"]

    async def contact_kb():
        return InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="👤 Adminlə əlaqə", url=await contact_url())]])

    async def send_with_contact(chat_id, text, reply_to=None):
        """tg://user düyməsini məxfilik ayarı bloklasa — düyməsiz, mətndə link ilə."""
        try:
            return await bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=await contact_kb(),
                                          reply_to_message_id=reply_to, allow_sending_without_reply=True)
        except Exception:
            url = await contact_url()
            return await bot.send_message(chat_id, text + f'\n\n👤 <a href="{escape(url, quote=True)}">Admin</a>',
                                          parse_mode="HTML", reply_to_message_id=reply_to,
                                          allow_sending_without_reply=True)

    async def notify_disabled(chat_id, reply_to=None):
        await send_with_contact(
            chat_id, "🔴 <b>Canlı yayım asistanı hazırda söndürülüb.</b>\n\n"
                     "Səsli yayımda mahnı oxutmaq üçün bot admini onu aktivləşdirməlidir — "
                     "aşağıdakı düymə ilə əlaqə saxlayın.\n<i>Botun digər funksiyaları (/music, inline) işləyir.</i>",
            reply_to)

    # ── 🔐 asistanı qrupa əlavə etmək üçün icazə ──
    join_asks = {}                       # chat_id → {"msg": id, "pending": [coroutine factory], "ts"}

    def asst_name() -> str:
        me = va.me
        if not me:
            return "asistan"
        return f"@{me.username}" if me.username else escape(me.first_name or "asistan")

    async def ask_join(chat, retry=None):
        """Qrupda icazə sorğusu (təkrar sorğu göndərmir — gözləyən əməliyyat əlavə olunur)."""
        ask = join_asks.get(chat.id)
        if ask and time.time() - ask["ts"] < 900:
            if retry:
                ask["pending"].append(retry)
            return False
        status = await va.member_status(chat.id)
        text = (f"🎙 <b>Canlı yayım asistanı</b> ({asst_name()}) bu qrupda deyil.\n\n"
                "Səsli yayımda mahnı oxutması üçün onu qrupa əlavə edimmi?\n"
                "<i>👮 Yalnız qrup adminləri təsdiq edə bilər. Asistan yalnız səs oxudur — mesaj yazmır, "
                "heç nəyi idarə etmir.</i>"
                + ("\n\n⚠️ <i>Asistan əvvəllər bu qrupdan qadağan olunub — təsdiqdən sonra qadağa götürüləcək.</i>"
                   if status == "kicked" else ""))
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ Əlavə et", callback_data="vj:yes"),
            InlineKeyboardButton(text="❌ Xeyr", callback_data="vj:no")]])
        msg = await bot.send_message(chat.id, text, parse_mode="HTML", reply_markup=kb)
        join_asks[chat.id] = {"msg": msg.message_id, "pending": [retry] if retry else [], "ts": time.time()}
        return True

    async def need_member(message: types.Message, retry) -> bool:
        """Asistan qrupdadırsa True; deyilsə icazə sorğusu göndərilir, əməliyyat təsdiqdən sonra təkrarlanır."""
        try:
            await va.ensure_member(message.chat.id)
            return True
        except NotMember:
            sent = await ask_join(message.chat, retry)
            if not sent:
                with contextlib.suppress(Exception):
                    await message.reply("🔐 Asistanın qrupa əlavə olunması üçün admin təsdiqi gözlənilir — "
                                        "təsdiqdən sonra avtomatik davam edəcək.")
            return False

    @dp.callback_query(F.data.startswith("vj:"))
    async def vj_cb(cb: types.CallbackQuery):
        chat = cb.message.chat
        uid = cb.from_user.id
        is_admin = uid == context.creator_id
        if not is_admin:
            with contextlib.suppress(Exception):
                m = await bot.get_chat_member(chat.id, uid)
                is_admin = str(getattr(m.status, "value", m.status)) in ("administrator", "creator")
        if not is_admin:
            await cb.answer("👮 Yalnız qrup adminləri təsdiq edə bilər", show_alert=True)
            return
        ask = join_asks.get(chat.id)
        if cb.data == "vj:no":
            join_asks.pop(chat.id, None)
            await cb.answer("❌ İmtina edildi")
            with contextlib.suppress(Exception):
                await cb.message.edit_text("❌ Asistan qrupa əlavə edilmədi. Lazım olsa /play ilə yenidən soruşulacaq.")
            return
        if not va.enabled:
            await cb.answer()
            join_asks.pop(chat.id, None)
            with contextlib.suppress(Exception):
                await cb.message.delete()
            await notify_disabled(chat.id)
            return
        if not va.running:
            err = await va.start()
            if err:
                await cb.answer(f"⚠️ Asistan işləmir: {err[:150]}", show_alert=True)
                return
        await cb.answer("⏳ Qoşulur...")
        with contextlib.suppress(Exception):
            await cb.message.edit_text("⏳ <i>Asistan qrupa qoşulur...</i>", parse_mode="HTML")
        try:
            await va.join(chat.id)
        except Exception as e:
            with contextlib.suppress(Exception):
                await cb.message.edit_text(f"⚠️ {e}", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[[InlineKeyboardButton(text="🔄 Yenidən sına", callback_data="vj:yes"),
                                      InlineKeyboardButton(text="❌ Ləğv et", callback_data="vj:no")]]))
            return
        join_asks.pop(chat.id, None)
        by = escape(cb.from_user.first_name or "admin")
        with contextlib.suppress(Exception):
            await cb.message.edit_text(
                f"✅ <b>Asistan qrupa əlavə olundu</b> ({asst_name()}) — təsdiq: {by}\n\n"
                "🎙 İndi səsli yayımda: <code>/play mahnı adı</code> · <code>/mixplay</code>\n"
                "<i>💡 Asistanı \"Video chatları idarə etmək\" icazəli admin etsəniz, səsli yayımı özü başlada bilər.</i>",
                parse_mode="HTML")
        for retry in (ask or {}).get("pending", []):     # gözləyən /play, /mixplay — indi davam
            with contextlib.suppress(Exception):
                await retry()

    # ── bot qrupa əlavə olunanda ──
    # Handler yox, outer middleware: hadisəni udmur, start_plugin-in salam handler-inə də ötürür.
    # Asistan işi fonda gedir ki, (sleep, va.start) qrup salamını gecikdirməsin.
    _bg_tasks = set()

    async def _membership_mw(handler, event, data):
        task = asyncio.create_task(on_bot_membership(event))
        _bg_tasks.add(task)
        task.add_done_callback(_bg_tasks.discard)
        return await handler(event, data)

    dp.my_chat_member.outer_middleware(_membership_mw)

    async def on_bot_membership(upd: types.ChatMemberUpdated):
        try:
            await _on_bot_membership(upd)
        except Exception as e:
            logger.warning(f"🎙 Asistan qrupa qoşulma yoxlanışı xətası: {e}")

    async def _on_bot_membership(upd: types.ChatMemberUpdated):
        if upd.chat.type not in ("group", "supergroup"):
            return
        new = str(getattr(upd.new_chat_member.status, "value", upd.new_chat_member.status))
        old = str(getattr(upd.old_chat_member.status, "value", upd.old_chat_member.status))
        if new not in ("member", "administrator") or old in ("member", "administrator", "creator", "restricted"):
            return                                    # yalnız yeni əlavə olunma (yüksəltmə yox)
        if not va.enabled or not va.has_session():
            return                                    # söndürülübsə — /play yazılanda bildiriləcək
        if not va.running and await va.start():
            return
        await asyncio.sleep(2)                        # qrup yaradılırkən əlavə olunubsa — asistan da ola bilər
        if await va.member_status(upd.chat.id) != "member":
            with contextlib.suppress(Exception):
                await ask_join(upd.chat)

    async def guard(message: types.Message) -> bool:
        if message.chat.type not in ("group", "supergroup"):
            await message.reply("🎙 Bu komanda qruplarda, səsli yayımda işləyir.")
            return False
        if not va.enabled:
            await notify_disabled(message.chat.id, reply_to=message.message_id)
            return False
        if not va.running:
            err = await va.start()
            if err:
                await message.reply(f"⚠️ Asistan işləmir: {escape(err)}", parse_mode="HTML")
                return False
        if not await can_control(message.chat.id, message.from_user.id):
            await message.reply("👮 Yalnız qrup adminləri idarə edə bilər.")
            return False
        return True

    @dp.message(Command("play", "p", "oynat", ignore_mention=True))
    async def play_cmd(message: types.Message, command: CommandObject):
        if not await guard(message):
            return
        if not await need_member(message, lambda: play_cmd(message, command)):
            return
        query = (command.args or "").strip()
        reply = message.reply_to_message
        by = message.from_user.first_name or ""
        status = await message.reply("🔎 <i>Axtarılır...</i>", parse_mode="HTML")
        try:
            if not query and reply and (reply.audio or reply.voice):
                a = reply.audio or reply.voice
                title = (f"{a.performer} - {a.title}" if getattr(a, "performer", None) and getattr(a, "title", None)
                         else getattr(a, "title", None) or getattr(a, "file_name", None) or "Səs faylı")
                t = Track(title, None, None, a.duration or 0, a.file_id, by, src="reply")
            elif not query:
                await status.edit_text("🎙 İstifadə: <code>/play mahnı adı</code> və ya mahnıya reply edib "
                                       "<code>/play</code>", parse_mode="HTML")
                return
            else:
                t = await va.resolve(query, by)
            if t.duration and t.duration > MAX_DURATION:
                await status.edit_text(f"⏭ Çox uzundur ({fmt_dur(t.duration)}), maksimum {fmt_dur(MAX_DURATION)}")
                return
            await status.edit_text(f"⏳ <b>{escape(t.title[:80])}</b>\n<i>"
                                   + ("📦 depodan götürülür..." if t.file_id else "⬇️ yüklənir...") + "</i>",
                                   parse_mode="HTML")
            kind, pos = await va.enqueue(message.chat.id, message.chat.title or "", t)
            if kind == "playing":
                with contextlib.suppress(Exception):
                    await status.delete()
            else:
                await status.edit_text(f"📋 Növbəyə əlavə olundu (#{pos}): <b>{escape(t.title[:80])}</b>",
                                       parse_mode="HTML")
                st = va.chats.get(message.chat.id)
                if st:
                    await va.refresh_np(st)
        except Exception as e:
            msg = str(e)
            low = msg.lower()
            if "groupcall" in low.replace("_", "") or "no active" in low or "not in a call" in low:
                msg = "Səsli yayım başlamayıb — qrupda video chat başladın (və ya asistanı \"Manage video chats\" admini edin)"
            logger.warning(f"🎙 /play xətası ({message.chat.id}): {e}")
            with contextlib.suppress(Exception):
                await status.edit_text(f"❌ {escape(msg[:300])}", parse_mode="HTML")

    async def simple(message: types.Message, action: str):
        if not await guard(message):
            return
        st = va.chats.get(message.chat.id)
        if not st or not st.current:
            await message.reply("🔇 Hazırda heç nə oxunmur.")
            return
        await do_action(st, action)
        with contextlib.suppress(Exception):
            await message.delete()

    async def do_action(st: ChatState, action: str) -> str:
        if action == "pause" and not st.paused:
            await va.calls.pause(st.chat_id)
            st.paused = True
            await va.refresh_np(st)
            return "⏸ Fasilə"
        if action == "resume" and st.paused:
            await va.calls.resume(st.chat_id)
            st.paused = False
            await va.refresh_np(st)
            return "▶️ Davam"
        if action == "toggle":
            return await do_action(st, "resume" if st.paused else "pause")
        if action == "skip":
            await va.next(st.chat_id, skipped=True)
            return "⏭ Növbəti"
        if action == "stop":
            await va._end_chat(st.chat_id, announce=True, reason="dayandırıldı")
            return "⏹ Dayandırıldı"
        if action == "radio":
            if st.radio:
                st.radio = None
                await va.refresh_np(st)
                return "🔀 Mix rejimi söndürüldü"
            seed = st.current.vid if st.current else None
            if not seed:
                return "Bu mahnı üçün Mix yoxdur"
            st.radio = {"seed": seed, "seen": {t.vid for t in [st.current, *st.queue] if t and t.vid}}
            n = await va.refill(st, force=True)
            await va.refresh_np(st)
            return f"🔀 Mix: +{n} mahnı"
        return ""

    @dp.message(Command("mixplay", "mp", "radio", ignore_mention=True))
    async def mixplay_cmd(message: types.Message, command: CommandObject):
        if not await guard(message):
            return
        if not await need_member(message, lambda: mixplay_cmd(message, command)):
            return
        query = (command.args or "").strip()
        reply = message.reply_to_message
        by = message.from_user.first_name or ""
        st = va.chats.get(message.chat.id)
        status = await message.reply("🔀 <i>Oxşar mahnılar axtarılır...</i>", parse_mode="HTML")
        try:
            if query:
                seed = await va.resolve(query, by)
            elif reply and reply.audio:
                a = reply.audio
                q = f"{a.performer} - {a.title}" if a.performer and a.title else (a.title or a.file_name or "")
                m = _VID_RE.search(" ".join(filter(None, [reply.caption or "",
                                                         *(e.url or "" for e in (reply.caption_entities or []))])))
                seed = await va.resolve(f"https://youtu.be/{m.group(1)}" if m else q, by)
            elif st and st.current and st.current.vid:
                seed = st.current                          # hazırda oxunanın Mix-i
            else:
                await status.edit_text("🔀 İstifadə: <code>/mixplay mahnı adı</code>, <code>/mixplay link</code>, "
                                       "mahnıya reply edib <code>/mixplay</code>, və ya bir mahnı oxunarkən sadəcə "
                                       "<code>/mixplay</code>", parse_mode="HTML")
                return
            if not seed.vid:
                raise RuntimeError("bu mahnının YouTube ID-si yoxdur — Mix qurula bilmir")
            await status.edit_text(f"🔀 <b>Mix:</b> {escape(seed.title[:70])}\n<i>"
                                   + ("📦 depodan götürülür..." if seed.file_id else "⬇️ hazırlanır...") + "</i>",
                                   parse_mode="HTML")
            started, n = await va.start_mix(message.chat.id, message.chat.title or "", seed, by)
            await status.edit_text(f"🔀 <b>Mix başladı:</b> {escape(seed.title[:70])}\n"
                                   f"📋 +{n} oxşar mahnı növbəyə əlavə olundu"
                                   + (" · ♾ bitəndə özü davam edəcək" if va.mix_auto else ""), parse_mode="HTML")
            asyncio.ensure_future(_autodelete(status, 20))
        except Exception as e:
            msg = str(e)
            low = msg.lower()
            if "groupcall" in low.replace("_", "") or "no active" in low or "not in a call" in low:
                msg = "Səsli yayım başlamayıb — qrupda video chat başladın"
            logger.warning(f"🎙 /mixplay xətası ({message.chat.id}): {e}")
            with contextlib.suppress(Exception):
                await status.edit_text(f"❌ {escape(msg[:300])}", parse_mode="HTML")

    async def _autodelete(msg, delay):
        await asyncio.sleep(delay)
        with contextlib.suppress(Exception):
            await msg.delete()

    @dp.message(Command("pause", ignore_mention=True))
    async def pause_cmd(message: types.Message):
        await simple(message, "pause")

    @dp.message(Command("resume", ignore_mention=True))
    async def resume_cmd(message: types.Message):
        await simple(message, "resume")

    @dp.message(Command("skip", "next", ignore_mention=True))
    async def skip_cmd(message: types.Message):
        await simple(message, "skip")

    @dp.message(Command("stop", "end", ignore_mention=True))
    async def stop_cmd(message: types.Message):
        if message.chat.type not in ("group", "supergroup"):
            return                                   # şəxsi çatda /stop başqa plugin-lərə qalsın
        await simple(message, "stop")

    @dp.message(Command("queue", "q", "np", ignore_mention=True))
    async def queue_cmd(message: types.Message):
        if message.chat.type not in ("group", "supergroup"):
            return
        st = va.chats.get(message.chat.id)
        if not st or not st.current:
            await message.reply("🔇 Hazırda heç nə oxunmur — /play mahnı adı")
            return
        if st.np_msg:
            with contextlib.suppress(Exception):
                await bot.delete_message(st.chat_id, st.np_msg)
            st.np_msg = None
        await va.announce(st)

    @dp.callback_query(F.data.startswith("vp:"))
    async def vp_cb(cb: types.CallbackQuery):
        st = va.chats.get(cb.message.chat.id)
        if not st or not st.current:
            await cb.answer("Yayım artıq bitib")
            return
        if not await can_control(cb.message.chat.id, cb.from_user.id):
            await cb.answer("👮 Yalnız adminlər", show_alert=True)
            return
        action = cb.data.split(":", 1)[1]
        if action == "refresh":
            await va.refresh_np(st)
            await cb.answer()
            return
        try:
            await cb.answer(await do_action(st, action) or "")
        except Exception as e:
            await cb.answer(f"❌ {str(e)[:150]}", show_alert=True)

    # ── başlanğıc ──
    async def _boot():
        if old is not None:
            with contextlib.suppress(Exception):
                await old.stop()                         # plugin reload — köhnə client bağlansın
        if va.enabled and va.has_session():
            await va.start()

    try:
        asyncio.get_running_loop().create_task(_boot())
    except RuntimeError:
        dp.startup.register(_boot)

    async def _shutdown():
        await va.stop()
    dp.shutdown.register(_shutdown)


async def teardown(context):
    va = getattr(context, "voice_assistant", None)
    if va is not None:
        await va.stop()
