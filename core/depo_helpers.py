"""
🤖 Köməkçi botlar — depo kanalına yükləməni bir neçə bota paylayır.

Telegram-ın qrup / kanal limiti (~20 mesaj/dəq.) hər bot üçün ayrıdır: N köməkçi ≈ N dəfə çox yükləmə (say limiti yoxdur).

Necə işləyir (music_plugin-ə toxunmadan):
  1. Əsas botun aiogram sessiyasına request middleware qoşulur.
  2. Depoya gedən SendAudio sorğusu tutulur və növbəti boş köməkçi bota ötürülür
     (hər köməkçi öz tempində — dəqiqədə N mahnı, flood alsa digərinə keçilir).
  3. file_id hər bot üçün fərqlidir — köməkçinin file_id-si ilə əsas bot mahnı göndərə bilməz.
     Çevirmə: əsas bot depo postunu sink çatına (default: creator-in şəxsi çatı) forward edib
     ÖZ file_id-sini alır, forward dərhal silinir. İki rejim:
       🐢 lazy (default) — keşə köməkçinin file_id-si yazılır + "köməkçi file_id → depo postu" xəritəsi.
          Əsas bot həmin file_id ilə nəsə göndərəndə (answer_audio, inline nəticə, edit_message_media)
          middleware sorğunu tutur, BİR DƏFƏ çevirir, keşi yeniləyir. Heç kim istəməyən mahnı üçün
          forward heç olmur → tutum = köməkçilərin tam limiti. Fon çevirici boş vaxtda ən çox
          istənilənləri əvvəlcədən çevirir. İnline-da çevrilə bilməyən nəticə (2.5 san.) o dəfəlik
          göstərilmir, fonda çevrilir.
       ⚡ eager — yükləmə anında çevrilir (tutum sink limitinə bağlıdır, ~60/dəq. hər sink çatı)
  4. music_plugin-ə depo postunun özü (chat_id / message_id) qaytarılır — keş və depo indeksi işləyir.
  Nəsə alınmasa (köməkçi yoxdur / forward olmadı) — əsas bot özü yükləyir, heç nə itmir.

Rejim (/depo → 🤖 Köməkçi botlar):
  filler — yalnız 📦 doldurucunun mahnıları (default) · all — bütün depo yükləmələri · off — söndürülüb

config.env:
  DEPO_HELPER_TOKENS=123:AAA...,456:BBB...   # və ya /depo helper add <token>
  DEPO_SINK_CHATS=<creator_id>,<başqa_id>     # file_id çevirmə çatları (botu /start etmiş şəxslər)
  DEPO_SINK_INTERVAL=1.0                      # bir sink çatına forward-lar arası (san.)

👋 Köməkçi botlara kimsə yazsa (/start və s.) — "bu köməkçi botdur" + əsas botun linki göndərilir.
  Bunun üçün köməkçilər ayrıca, yüngül polling ilə dinlənir (yalnız şəxsi mesajlar).

Tələb: köməkçi botlar depo kanalında admin olmalıdır ("Post messages"),
depo kanalında "Restrict saving content" (protect content) söndürülmüş olmalıdır — yoxsa forward olmur.
"""
import asyncio
import contextvars
import json
import logging
import os
import re
import time

from aiogram import Bot, Dispatcher, F
from aiogram.client.session.middlewares.base import BaseRequestMiddleware
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.methods import SendAudio
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, InputFile, Message

logger = logging.getLogger(__name__)

# (filler, işçi nömrəsi) — doldurucunun öz yükləməsidir (rejim "filler" üçün) + monitorda faza
UPLOAD_CTX = contextvars.ContextVar("depo_upload_ctx", default=None)

SCOPES = {"filler": "📦 Yalnız doldurucu", "all": "🌐 Bütün depo yükləmələri", "off": "🔴 Söndürülüb"}
DEFAULT_RATE = 18                 # hər köməkçi — dəqiqədə neçə mahnı (Telegram limiti ~20)
RATE_STEPS = [6, 10, 12, 15, 18, 20, 25, 30]
S_TOKENS, S_SCOPE, S_RATE = "depo_helpers:tokens", "depo_helpers:scope", "depo_helpers:rate"
S_CONV = "depo_helpers:conv"
CONV_MODES = {"lazy": "🐢 Lazy — yalnız istənəndə", "eager": "⚡ Dərhal — yükləmə anında"}
INLINE_BUDGET = 2.5               # inline cavab üçün çevirməyə ayrılan vaxt (san.)
SEND_BUDGET = 30.0                # adi göndərmə üçün
WARM_INTERVAL = float(os.getenv("DEPO_WARM_INTERVAL") or 3.0)   # fon çevirici: hər N san. bir mahnı
# file_id daşıya bilən sorğular (music_plugin-in istifadə etdikləri + ehtiyat)
FID_METHODS = {"SendAudio", "SendDocument", "SendMediaGroup", "AnswerInlineQuery", "EditMessageMedia",
               "SendVoice", "AnswerWebAppQuery", "SavePreparedInlineMessage",
               "GetFile"}                  # fix_artist: depo indeksindəki yer tutucu file_id də çevrilsin
_TOKEN_RE = re.compile(r"^\d{6,12}:[A-Za-z0-9_-]{30,}$")
_DEAD_ERRORS = ("chat not found", "not enough rights", "have no rights", "bot was kicked", "not a member",
                "need administrator rights", "chat_write_forbidden", "unauthorized")


def _db():
    from core.database import get_db
    return get_db()


class Helper:
    def __init__(self, token: str, from_env: bool):
        self.token = token
        self.from_env = from_env
        self.bot = None
        self.me = None
        self.ok = False
        self.status = "yoxlanılır..."
        self.next_at = 0.0                # tempə görə növbəti boş an (monotonic)
        self.cool_until = 0.0             # flood (RetryAfter) bitənə qədər
        self.sent = 0
        self.floods = 0
        self.fails = 0

    @property
    def name(self) -> str:
        if self.me:
            return f"@{self.me.username}"
        return f"bot{self.token.split(':', 1)[0]}"


class HelperPool:
    def __init__(self, context):
        self.ctx = context
        self.helpers = []
        self.lock = asyncio.Lock()
        self.stats = {"helper": 0, "fallback": 0, "converted": 0, "conv_fail": 0}
        self._depo = {"id": None, "ts": 0.0}
        self._sinks = {}                  # chat_id → növbəti boş an
        self._sink_lock = asyncio.Lock()
        self.last_error = ""
        # 👋 köməkçilərə yazanlara cavab (ayrıca Dispatcher)
        self._poll_dp = None
        self._poll_task = None
        self._main_username = None
        self._replied = {}                # (bot_id, user_id) → son cavab vaxtı (spam qarşısı)
        self.redirects = 0
        # 🐢 lazy çevirmə
        self.foreign = {}                 # köməkçi file_id → (chat_id, message_id, unique_id)
        self.converted = {}               # köməkçi file_id → əsas botun file_id-si (yaddaşda, son çevrilənlər)
        self._conv_locks = {}
        self._dead = set()                # depo postu silinib — çevrilə bilmir
        self._warm_task = None
        self._warm_fails = {}             # fid → uğursuz cəhd sayı (fon çevirici ilişib qalmasın)
        self.cstats = {"lazy": 0, "warm": 0, "inline_skip": 0, "fail": 0}
        self._init_table()

    # ── ayarlar ──
    def _get(self, key, default=None):
        try:
            v = _db().get_setting(key)
        except Exception:
            v = None
        return default if v in (None, "") else v

    def _set(self, key, value):
        _db().set_setting(key, value)

    @property
    def scope(self) -> str:
        v = self._get(S_SCOPE, "filler")
        return v if v in SCOPES else "filler"

    def set_scope(self, v: str):
        if v in SCOPES:
            self._set(S_SCOPE, v)

    @property
    def rate(self) -> int:
        try:
            return max(1, min(60, int(self._get(S_RATE, DEFAULT_RATE))))
        except (TypeError, ValueError):
            return DEFAULT_RATE

    def step_rate(self, direction: int) -> int:
        cur = self.rate
        if direction > 0:
            nxt = next((x for x in RATE_STEPS if x > cur), RATE_STEPS[-1])
        else:
            nxt = next((x for x in reversed(RATE_STEPS) if x < cur), RATE_STEPS[0])
        self._set(S_RATE, str(nxt))
        return nxt

    @property
    def conv_mode(self) -> str:
        v = self._get(S_CONV, "lazy")
        return v if v in CONV_MODES else "lazy"

    def toggle_conv(self) -> str:
        nxt = "eager" if self.conv_mode == "lazy" else "lazy"
        self._set(S_CONV, nxt)
        return nxt

    def _db_tokens(self) -> list:
        try:
            data = json.loads(self._get(S_TOKENS) or "[]")
        except ValueError:
            data = []
        return [t for t in data if isinstance(t, str)]

    def tokens(self) -> list:
        """[(token, env-dəndir?)] — təkrarsız, sıra qorunur."""
        out, seen = [], set()
        main = getattr(self.ctx.bot, "token", None)
        env = [t.strip() for t in (os.getenv("DEPO_HELPER_TOKENS") or "").replace(";", ",").split(",")]
        for t, env_flag in [(t, True) for t in env] + [(t, False) for t in self._db_tokens()]:
            if t and t not in seen and t != main:
                seen.add(t)
                out.append((t, env_flag))
        return out

    def add_token(self, token: str) -> str:
        token = token.strip()
        if not _TOKEN_RE.match(token):
            return "format"
        if token == getattr(self.ctx.bot, "token", None):
            return "main"
        if any(t == token for t, _ in self.tokens()):
            return "dup"
        self._set(S_TOKENS, json.dumps(self._db_tokens() + [token]))
        return "ok"

    def remove(self, index: int) -> str:
        if not 0 <= index < len(self.helpers):
            return "range"
        h = self.helpers[index]
        if h.from_env:
            return "env"
        self._set(S_TOKENS, json.dumps([t for t in self._db_tokens() if t != h.token]))
        return "ok"

    # ── sink (file_id çevirmə) çatları ──
    def sinks(self) -> list:
        raw = os.getenv("DEPO_SINK_CHATS") or ""
        ids = [int(x) for x in re.findall(r"-?\d+", raw)]
        if not ids and getattr(self.ctx, "creator_id", 0):
            ids = [int(self.ctx.creator_id)]
        return ids

    @staticmethod
    def sink_interval() -> float:
        try:
            return max(0.2, float(os.getenv("DEPO_SINK_INTERVAL") or 1.0))
        except ValueError:
            return 1.0

    # ── vəziyyət ──
    def active(self) -> list:
        return [h for h in self.helpers if h.ok]

    def flooded(self) -> int:
        now = time.monotonic()
        return sum(1 for h in self.active() if h.cool_until > now)

    def capacity(self) -> int:
        """Nəzəri sürət (mahnı/dəq.) — köməkçilər və sink çevirmə limitindən kiçiyi."""
        up = len(self.active()) * self.rate
        if self.conv_mode == "lazy":
            return up                     # forward yükləmə anında olmur — sink limiti təsir etmir
        conv = int(len(self.sinks()) * 60 / self.sink_interval())
        return min(up, conv) if up else 0

    def applies(self) -> bool:
        sc = self.scope
        if sc == "off" or not self.active() or not self.sinks():
            return False
        return sc == "all" or UPLOAD_CTX.get() is not None

    async def depo_id(self, bot):
        if self._depo["id"] is None or time.monotonic() - self._depo["ts"] > 600:
            try:
                from core import audio_cache
                self._depo["id"] = await audio_cache.depo_chat_id(bot)
            except Exception:
                self._depo["id"] = None
            self._depo["ts"] = time.monotonic()
        return self._depo["id"]

    async def is_depo(self, chat_id, bot) -> bool:
        depo = await self.depo_id(bot)
        if depo is None:
            return False
        if str(chat_id) == str(depo):
            return True
        try:
            from core import audio_cache
            ref = (audio_cache.depo_status() or {}).get("ref") or ""
            return bool(ref) and str(chat_id).lstrip("@").lower() == str(ref).lstrip("@").lower()
        except Exception:
            return False

    # ── başlatma / yoxlama ──
    async def reload(self):
        await self.stop_polling()
        async with self.lock:
            await self._close_all()
            self.helpers = [Helper(t, env) for t, env in self.tokens()]
        if self.helpers:
            await asyncio.gather(*(self._check(h) for h in self.helpers), return_exceptions=True)
            logger.info(f"🤖 Köməkçi botlar: {len(self.active())}/{len(self.helpers)} hazır "
                        f"({', '.join(h.name + ('✅' if h.ok else '❌') for h in self.helpers)})")
            await self.start_polling()
        self.start_warm()

    # ── 👋 köməkçiyə yazana əsas botun linki ──
    async def main_username(self):
        if not self._main_username:
            try:
                self._main_username = (await self.ctx.bot.get_me()).username
            except Exception:
                return None
        return self._main_username

    @property
    def polling(self) -> bool:
        return bool(self._poll_task and not self._poll_task.done())

    async def start_polling(self):
        bots = [h.bot for h in self.helpers if h.bot and h.me]   # admin olmasa da — cavab versin
        if not bots:
            return
        dp = Dispatcher()

        @dp.message(F.chat.type == "private")
        async def _redirect(message: Message, bot: Bot):
            key = (bot.id, message.from_user.id if message.from_user else 0)
            now = time.monotonic()
            if now - self._replied.get(key, 0) < 10:              # eyni adama 10 san.-də bir cavab
                return
            self._replied[key] = now
            if len(self._replied) > 5000:
                self._replied.clear()
            user = await self.main_username()
            if not user:
                return
            self.redirects += 1
            try:
                await message.answer(
                    "🤖 <b>Bu köməkçi botdur</b> — yalnız mahnı deposuna xidmət edir.\n\n"
                    f"🎵 Musiqi axtarmaq və yükləmək üçün əsas bota keç: @{user}",
                    parse_mode="HTML",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                        InlineKeyboardButton(text="🎵 Əsas bota keç", url=f"https://t.me/{user}?start=helper")]]),
                )
            except Exception as e:
                logger.debug(f"Köməkçi cavab vermədi: {e}")

        for b in bots:
            try:      # köhnə yığılmış mesajlara (bot söndürülü ikən yazılanlar) cavab verilməsin
                await b.delete_webhook(drop_pending_updates=True)
            except Exception:
                pass
        self._poll_dp = dp
        self._poll_task = asyncio.create_task(dp.start_polling(
            *bots, handle_signals=False, close_bot_session=False, allowed_updates=["message"]))
        logger.info(f"👋 Köməkçi botlar dinlənilir ({len(bots)}) — yazanlara əsas botun linki göndərilir")

    async def stop_polling(self):
        dp, task = self._poll_dp, self._poll_task
        self._poll_dp = self._poll_task = None
        if not task or task.done():
            return
        try:
            await dp.stop_polling()
            await asyncio.wait_for(asyncio.shield(task), 15)
        except Exception:
            task.cancel()
            try:
                await task
            except BaseException:
                pass

    async def _check(self, h: Helper):
        h.ok = False
        try:
            if h.bot is None:
                h.bot = Bot(token=h.token)
            h.me = await h.bot.get_me()
            depo = await self.depo_id(self.ctx.bot)
            if depo is None:
                h.status = "⚠️ depo kanalı tapılmadı"
                return
            cm = await h.bot.get_chat_member(depo, h.me.id)
            chat = await h.bot.get_chat(depo)
            st = getattr(cm, "status", "")
            st = getattr(st, "value", st)
            if st == "creator":
                h.ok = True
            elif st == "administrator":
                h.ok = chat.type != "channel" or bool(getattr(cm, "can_post_messages", False))
                if not h.ok:
                    h.status = "⚠️ admindir, amma \"Post messages\" icazəsi yoxdur"
            elif st == "member" and chat.type != "channel":
                h.ok = True
            else:
                h.status = "⚠️ depoda admin deyil"
            if h.ok:
                h.status = "🟢 hazır"
                if getattr(chat, "has_protected_content", False):
                    h.status = "🟢 hazır · ⚠️ depoda protect content açıqdır — forward olmayacaq"
        except TelegramForbiddenError:
            h.status = "⚠️ depoya əlavə olunmayıb"
        except TelegramBadRequest as e:
            h.status = "⚠️ " + ("depoya əlavə olunmayıb" if "not found" in str(e).lower() else str(e)[:80])
        except Exception as e:
            h.status = f"❌ {type(e).__name__}: {str(e)[:80]}"

    async def recheck(self):
        self._depo["ts"] = 0
        await asyncio.gather(*(self._check(h) for h in self.helpers), return_exceptions=True)

    async def _close_all(self):
        for h in self.helpers:
            if h.bot:
                try:
                    await h.bot.session.close()
                except Exception:
                    pass

    async def close(self):
        if self._warm_task:
            self._warm_task.cancel()
        await self.stop_polling()
        await self._close_all()
        self.helpers = []

    # ── yükləmə ──
    async def _reserve(self):
        """Ən tez boşalan köməkçini seçir (flood alanlar cooldown-larına görə avtomatik arxaya düşür)
        və ona yer ayırır → (helper, gözləmə san.)"""
        interval = 60.0 / self.rate
        async with self.lock:
            now = time.monotonic()
            cands = self.active()
            if not cands:
                return None, 0.0
            h = min(cands, key=lambda x: max(x.next_at, x.cool_until))
            start = max(now, h.next_at, h.cool_until)
            h.next_at = start + interval
            return h, start - now

    @staticmethod
    def _phase(name: str):
        ctx = UPLOAD_CTX.get()
        if ctx:
            filler, idx = ctx
            try:
                filler._phase(idx, name)
            except Exception:
                pass

    async def upload(self, method, bot, make_request):
        attempts = max(2, len(self.active()) * 2)
        for _ in range(attempts):
            h, wait = await self._reserve()
            if h is None:
                break
            if wait > 0:
                self._phase("SLOT")
                await asyncio.sleep(wait)
            self._phase("UP")
            try:
                msg = await h.bot(method)
            except TelegramRetryAfter as e:
                h.cool_until = time.monotonic() + e.retry_after + 1
                h.floods += 1
                logger.info(f"🤖 {h.name}: flood {e.retry_after} san. — digər köməkçiyə keçilir")
                continue
            except (TelegramForbiddenError, TelegramBadRequest) as e:
                err = str(e).lower()
                if isinstance(e, TelegramForbiddenError) or any(x in err for x in _DEAD_ERRORS):
                    h.ok = False
                    h.status = f"⚠️ {str(e)[:80]}"
                    logger.warning(f"🤖 {h.name} söndürüldü: {e}")
                    continue
                raise                                  # faylın özü ilə bağlı xəta — əsas bot da alacaqdı
            except Exception as e:
                h.fails += 1
                self.last_error = f"{h.name}: {type(e).__name__}: {e}"[:150]
                h.cool_until = time.monotonic() + 15        # şəbəkə xətası — qısa fasilə, digərləri işləsin
                continue
            h.sent += 1
            if self.conv_mode == "lazy" and getattr(msg, "audio", None):
                await self.register_foreign(msg)
                self.stats["helper"] += 1
                return msg.model_copy().as_(bot)       # köməkçinin file_id-si — göndəriləndə çevriləcək
            try:
                res = await self._as_main(msg, bot)
                self.stats["helper"] += 1
                return res
            except Exception as e:
                # çevirmə alınmadı — köməkçinin postunu silib əsas botla yükləyirik (depoda dublikat qalmasın)
                self.stats["conv_fail"] += 1
                self.last_error = f"file_id çevirmə: {type(e).__name__}: {e}"[:150]
                logger.warning(f"🤖 file_id çevrilmədi, əsas bot yükləyir: {e}")
                try:
                    await h.bot.delete_message(msg.chat.id, msg.message_id)
                except Exception:
                    pass
                break
        self.stats["fallback"] += 1
        self._phase("UP")
        return await make_request(bot, method)

    async def _sink_slot(self):
        sinks = self.sinks()
        interval = self.sink_interval()
        async with self._sink_lock:
            now = time.monotonic()
            chat = min(sinks, key=lambda c: self._sinks.get(c, 0.0))
            start = max(now, self._sinks.get(chat, 0.0))
            self._sinks[chat] = start + interval
            return chat, start - now

    async def _forward_audio(self, bot, chat_id, message_id, unique_id=None):
        """Depo postunu sink çatına forward edir → əsas botun Audio obyekti; forward silinir."""
        fwd = None
        for _ in range(4):
            chat, wait = await self._sink_slot()
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                fwd = await bot.forward_message(chat_id=chat, from_chat_id=chat_id,
                                                message_id=message_id, disable_notification=True)
                break
            except TelegramRetryAfter as e:
                async with self._sink_lock:
                    self._sinks[chat] = time.monotonic() + e.retry_after + 1
        if fwd is None:
            raise RuntimeError("sink çatına forward alınmadı (flood)")
        asyncio.create_task(self._delete(bot, fwd.chat.id, fwd.message_id))
        if not fwd.audio or (unique_id and fwd.audio.file_unique_id != unique_id):
            raise RuntimeError("forward-da eyni audio tapılmadı")
        self.stats["converted"] += 1
        return fwd.audio

    async def _as_main(self, msg, bot):
        """⚡ eager: depo postunun özü (chat / message_id köməkçinindir), audio isə əsas botun file_id-si ilə."""
        if not getattr(msg, "audio", None):
            raise RuntimeError("köməkçinin cavabında audio yoxdur")
        audio = await self._forward_audio(bot, msg.chat.id, msg.message_id, msg.audio.file_unique_id)
        return msg.model_copy(update={"audio": audio}).as_(bot)

    # ───────────── 🐢 lazy çevirmə ─────────────
    def _init_table(self):
        try:
            db = _db()
            with db._lock:
                db._conn.execute("""CREATE TABLE IF NOT EXISTS depo_foreign (
                    file_id TEXT PRIMARY KEY, unique_id TEXT, chat_id INTEGER, message_id INTEGER,
                    main_file_id TEXT, created INTEGER, converted INTEGER)""")
                db._conn.execute("CREATE INDEX IF NOT EXISTS idx_foreign_pending ON depo_foreign(main_file_id)")
                # restartdan əvvəl çevrilib, amma keşə yazılmamış qalan sətirləri düzəlt
                db._conn.execute("""UPDATE audio_cache SET file_id = (
                        SELECT main_file_id FROM depo_foreign f WHERE f.file_id = audio_cache.file_id)
                    WHERE file_id IN (SELECT file_id FROM depo_foreign WHERE main_file_id IS NOT NULL)""")
                rows = db._conn.execute("""SELECT file_id, unique_id, chat_id, message_id, main_file_id
                    FROM depo_foreign WHERE main_file_id IS NULL OR converted >= ?""",
                                        (int(time.time()) - 2 * 86400,)).fetchall()
                db._conn.commit()
            for r in rows:
                if r[4]:
                    self.converted[r[0]] = r[4]
                else:
                    self.foreign[r[0]] = (r[2], r[3], r[1])
            if self.foreign:
                logger.info(f"🐢 Çevrilməmiş köməkçi file_id-ləri: {len(self.foreign)}")
        except Exception as e:
            logger.warning(f"depo_foreign cədvəli hazırlanmadı: {e}")

    def pending(self) -> int:
        return len(self.foreign)

    async def register_foreign(self, msg):
        a = msg.audio
        self.foreign[a.file_id] = (msg.chat.id, msg.message_id, a.file_unique_id)

        def put():
            db = _db()
            with db._lock:
                db._conn.execute("""INSERT OR REPLACE INTO depo_foreign
                    (file_id, unique_id, chat_id, message_id, main_file_id, created) VALUES (?,?,?,?,NULL,?)""",
                                 (a.file_id, a.file_unique_id, msg.chat.id, msg.message_id, int(time.time())))
                db._conn.commit()
        try:
            await asyncio.to_thread(put)
        except Exception as e:
            logger.warning(f"depo_foreign yazılmadı: {e}")

    def _save_converted(self, fid: str, main_fid: str):
        db = _db()
        with db._lock:
            db._conn.execute("UPDATE depo_foreign SET main_file_id=?, converted=? WHERE file_id=?",
                             (main_fid, int(time.time()), fid))
            db._conn.execute("UPDATE audio_cache SET file_id=? WHERE file_id=?", (main_fid, fid))
            db._conn.commit()
        try:
            from core import audio_cache
            audio_cache.mark_dirty()              # depo axtarış indeksi köhnə file_id saxlamasın
        except Exception:
            pass

    def _drop_dead(self, fid: str):
        db = _db()
        with db._lock:
            db._conn.execute("DELETE FROM depo_foreign WHERE file_id=?", (fid,))
            db._conn.commit()

    async def resolve(self, fid: str, bot, source: str = "lazy"):
        """Köməkçinin file_id-si → əsas botunku (lazım olsa forward ilə). Alınmasa None."""
        if fid in self.converted:
            return self.converted[fid]
        if fid not in self.foreign or fid in self._dead:
            return None
        lock = self._conv_locks.setdefault(fid, asyncio.Lock())
        async with lock:
            if fid in self.converted:                 # paralel sorğu artıq çevirib
                return self.converted[fid]
            entry = self.foreign.get(fid)
            if not entry:
                return None
            chat_id, message_id, uid = entry
            try:
                audio = await self._forward_audio(bot, chat_id, message_id, uid)
            except TelegramBadRequest as e:
                err = str(e).lower()
                if "not found" in err or "can't be forwarded" in err or "protected" in err:
                    # depo postu silinib / forward qadağandır — music_plugin köhnə file_id ilə xəta alıb
                    # keşi özü təmizləyəcək və mahnını yenidən yükləyəcək
                    self._dead.add(fid)
                    self.foreign.pop(fid, None)
                    await asyncio.to_thread(self._drop_dead, fid)
                self.cstats["fail"] += 1
                self.last_error = f"çevirmə: {str(e)[:120]}"
                return None
            except Exception as e:
                self.cstats["fail"] += 1
                self.last_error = f"çevirmə: {type(e).__name__}: {str(e)[:110]}"
                return None
            finally:
                self._conv_locks.pop(fid, None)
            main_fid = audio.file_id
            self.converted[fid] = main_fid
            self.foreign.pop(fid, None)
            self.cstats[source] += 1
            if len(self.converted) > 50_000:
                for k in list(self.converted)[:20_000]:
                    self.converted.pop(k, None)
            try:
                await asyncio.to_thread(self._save_converted, fid, main_fid)
            except Exception as e:
                logger.warning(f"Çevrilmiş file_id yazılmadı: {e}")
            return main_fid

    # ── sorğudakı köməkçi file_id-lərini tapıb əvəz etmək ──
    def _collect(self, obj, out: set, depth: int = 0):
        if depth > 5 or obj is None:
            return
        if isinstance(obj, str):
            if obj in self.foreign or obj in self.converted:
                out.add(obj)
        elif isinstance(obj, (list, tuple)):
            for x in obj:
                self._collect(x, out, depth + 1)
        elif hasattr(type(obj), "model_fields"):
            for name in type(obj).model_fields:
                self._collect(getattr(obj, name, None), out, depth + 1)

    def _replace(self, obj, mp: dict, depth: int = 0):
        if depth > 5 or obj is None:
            return obj
        if isinstance(obj, str):
            return mp.get(obj, obj)
        if isinstance(obj, (list, tuple)):
            new = [self._replace(x, mp, depth + 1) for x in obj]
            return type(obj)(new) if any(a is not b for a, b in zip(new, obj)) else obj
        if hasattr(type(obj), "model_fields"):
            upd = {}
            for name in type(obj).model_fields:
                v = getattr(obj, name, None)
                nv = self._replace(v, mp, depth + 1)
                if nv is not v:
                    upd[name] = nv
            return obj.model_copy(update=upd) if upd else obj
        return obj

    async def substitute(self, method, bot):
        """Əsas botun sorğusunda köməkçi file_id-si varsa — əsas botunku ilə əvəz edir."""
        if type(method).__name__ not in FID_METHODS or not (self.foreign or self.converted):
            return method
        found = set()
        self._collect(method, found)
        if not found:
            return method
        inline = type(method).__name__ == "AnswerInlineQuery"
        tasks = {fid: asyncio.ensure_future(self.resolve(fid, bot)) for fid in found}
        done, _ = await asyncio.wait(tasks.values(), timeout=INLINE_BUDGET if inline else SEND_BUDGET)
        mp = {fid: t.result() for fid, t in tasks.items()
              if t in done and not t.cancelled() and t.exception() is None and t.result()}
        # vaxtında bitməyənlər fonda davam edir (tapşırıq ləğv olunmur) — növbəti dəfə hazır olacaq
        if inline:
            left = found - set(mp)
            if left:
                self.cstats["inline_skip"] += len(left)
                kept = [r for r in method.results if getattr(r, "audio_file_id", None) not in left]
                method = method.model_copy(update={"results": kept})
        return self._replace(method, mp) if mp else method

    # ── 🔥 fon çevirici: boş vaxtda ən çox istənilən mahnıları əvvəlcədən çevirir ──
    def _next_warm(self):
        db = _db()
        with db._lock:
            rows = db._conn.execute("""SELECT f.file_id FROM depo_foreign f
                    LEFT JOIN audio_cache a ON a.file_id = f.file_id
                    WHERE f.main_file_id IS NULL
                    ORDER BY COALESCE(a.hits, 0) DESC, f.created DESC LIMIT 50""").fetchall()
        for r in rows:
            if self._warm_fails.get(r[0], 0) < 3:          # 3 dəfə alınmayanı ötür (istənəndə yenə sınanır)
                return r[0]
        return None

    async def warm_loop(self):
        while True:
            await asyncio.sleep(WARM_INTERVAL)
            if self.conv_mode != "lazy" or not self.foreign or WARM_INTERVAL <= 0:
                continue
            try:
                fid = await asyncio.to_thread(self._next_warm)
                if fid and fid not in self.foreign:            # DB-də var, yaddaşda yox (köhnə restart)
                    self._dead.add(fid)
                    await asyncio.to_thread(self._drop_dead, fid)
                    continue
                if fid and not await self.resolve(fid, self.ctx.bot, source="warm"):
                    self._warm_fails[fid] = self._warm_fails.get(fid, 0) + 1
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.debug(f"Fon çevirici: {e}")

    def start_warm(self):
        if WARM_INTERVAL > 0 and not (self._warm_task and not self._warm_task.done()):
            self._warm_task = asyncio.create_task(self.warm_loop())

    @staticmethod
    async def _delete(bot, chat_id, message_id):
        for _ in range(3):
            try:
                await bot.delete_message(chat_id, message_id)
                return
            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after + 1)
            except Exception:
                return


class DepoUploadMiddleware(BaseRequestMiddleware):
    """Əsas botun depoya SendAudio sorğularını köməkçi botlara yönəldir."""

    def __init__(self, context):
        self.ctx = context

    async def __call__(self, make_request, bot, method):
        pool = getattr(self.ctx, "depo_helpers", None)
        if pool is not None:
            try:
                method = await pool.substitute(method, bot)      # 🐢 lazy: köməkçi file_id → əsas botunku
            except Exception as e:
                logger.warning(f"file_id əvəzlənmədi: {e}")
        # yalnız real fayl yükləməsi: string (əsas botun file_id-si / URL) köməkçi üçün keçərsizdir
        if (pool is not None and isinstance(method, SendAudio) and isinstance(method.audio, InputFile)
                and pool.applies() and await pool.is_depo(method.chat_id, bot)):
            return await pool.upload(method, bot, make_request)
        return await make_request(bot, method)


def install(context) -> HelperPool:
    """Hovuzu yaradır (plugin yenidən yüklənəndə köhnəsini əvəz edir), middleware bir dəfə qoşulur."""
    pool = HelperPool(context)
    context.depo_helpers = pool
    if not getattr(context, "_depo_upload_mw", False):
        context.bot.session.middleware(DepoUploadMiddleware(context))
        context._depo_upload_mw = True
    return pool
