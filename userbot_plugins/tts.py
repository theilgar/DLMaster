"""🗣 .tts — mətni səs mesajı kimi göndər.  ".tts söz" → voice note.

Mühərrik: edge-tts (yüksək keyfiyyət, çox səs) varsa ondan, yoxsa gTTS. Heç biri yoxdursa quraşdırma
məsləhəti verir:  pip install edge-tts   (və ya: pip install gTTS)
Voice note üçün ffmpeg lazımdır (mp3 → ogg/opus); ffmpeg yoxdursa adi audio kimi göndərilir.

Komandalar:
  .tts <mətn>              — standart səslə (default: Azərbaycan)
  .tts az: / ru: / en: / tr: <mətn>   — dil seçimi
  .tts                      — başqa mesaja reply edib yazsan, onun mətni oxunur
  .ttsvoice                 — mövcud dil/səsləri və cari seçimi göstərir
  .ttsvoice <edge səs adı>  — standart səsi dəyiş (məs. az-AZ-BanuNeural)
"""
import asyncio
import os
import re
import shutil
import tempfile

from core.userbot_api import get_db, logger, safe_edit

MAX_LEN = 1500
VOICE_KEY = "userbot:tts_voice"

# dil → (edge-tts səsi, gTTS dil kodu)
LANGS = {
    "az": ("az-AZ-BabekNeural", "az"),
    "ru": ("ru-RU-DmitryNeural", "ru"),
    "en": ("en-US-AriaNeural", "en"),
    "tr": ("tr-TR-AhmetNeural", "tr"),
    "de": ("de-DE-KillianNeural", "de"),
    "fr": ("fr-FR-HenriNeural", "fr"),
    "es": ("es-ES-AlvaroNeural", "es"),
    "ar": ("ar-SA-HamedNeural", "ar"),
    "fa": ("fa-IR-FaridNeural", "fa"),
}
DEFAULT_LANG = "az"
_LANG_PREFIX = re.compile(r"^\s*([a-z]{2})\s*:\s*", re.I)

try:
    import edge_tts
    HAS_EDGE = True
except ImportError:
    HAS_EDGE = False
try:
    from gtts import gTTS
    HAS_GTTS = True
except ImportError:
    HAS_GTTS = False


def _edge_voice_lang(voice: str) -> str:
    """edge səs adından gTTS üçün dil kodu: 'az-AZ-BabekNeural' → 'az'."""
    return (voice.split("-", 1)[0] or DEFAULT_LANG).lower()


async def synth(text: str, voice: str, out_mp3: str):
    if HAS_EDGE:
        comm = edge_tts.Communicate(text, voice)
        await comm.save(out_mp3)
        return
    if HAS_GTTS:
        lang = _edge_voice_lang(voice)
        if lang not in {l[1] for l in LANGS.values()}:
            lang = "en"
        await asyncio.to_thread(lambda: gTTS(text=text, lang=lang).save(out_mp3))
        return
    raise RuntimeError("no-engine")


async def to_ogg(mp3: str, ogg: str) -> bool:
    """mp3 → ogg/opus (voice note). ffmpeg yoxdursa False."""
    if not shutil.which("ffmpeg"):
        return False
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y", "-i", mp3, "-c:a", "libopus", "-b:a", "48k", "-ar", "48000", "-ac", "1", ogg,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    await proc.communicate()
    return proc.returncode == 0 and os.path.exists(ogg) and os.path.getsize(ogg) > 0


async def duration_sec(path: str) -> int:
    if not shutil.which("ffprobe"):
        return 0
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of",
            "default=nw=1:nk=1", path, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        out, _ = await proc.communicate()
        return int(float(out.decode().strip() or 0))
    except Exception:
        return 0


def register(ub):
    def default_voice() -> str:
        try:
            v = get_db().get_setting(VOICE_KEY)
        except Exception:
            v = None
        return v or LANGS[DEFAULT_LANG][0]

    @ub.command("tts", pattern=r"^\.tts(?:\s+([\s\S]+))?$",
                help=("mətni səsə çevir",
                      "Mətni səs mesajı (voice note) kimi göndərir.\n\n"
                      "<b>İstifadə:</b>\n• <code>.tts Salam dünya</code>\n"
                      "• <code>.tts ru: Привет</code> · <code>.tts en: Hello</code> (dil: az/ru/en/tr/de/fr/es/ar/fa)\n"
                      "• başqa mesaja <b>reply</b> → <code>.tts</code> (onun mətnini oxuyur)\n\n"
                      "Standart səsi dəyişmək: <code>.ttsvoice</code>"))
    async def on_tts(event):
        arg = (event.pattern_match.group(1) or "").strip()
        text = arg
        if not text and event.is_reply:
            reply = await event.get_reply_message()
            text = (getattr(reply, "message", None) or getattr(reply, "raw_text", None) or "").strip() if reply else ""
        if not text:
            await safe_edit(event, "ℹ️ <b>.tts &lt;mətn&gt;</b> yaz və ya bir mesaja reply et.")
            return
        if not (HAS_EDGE or HAS_GTTS):
            await safe_edit(event, "❌ TTS mühərriki quraşdırılmayıb.\n<i>Serverdə:</i> "
                                   "<code>pip install edge-tts</code> <i>(və ya</i> <code>pip install gTTS</code><i>)</i>")
            return
        voice = default_voice()
        m = _LANG_PREFIX.match(text)
        if m and m.group(1).lower() in LANGS:
            voice = LANGS[m.group(1).lower()][0]
            text = text[m.end():].strip()
        if not text:
            await safe_edit(event, "ℹ️ Dil kodundan sonra mətn yaz: <code>.tts az: Salam</code>")
            return
        if len(text) > MAX_LEN:
            text = text[:MAX_LEN]
        await safe_edit(event, "🗣 <i>Səsə çevrilir...</i>")

        tmp = tempfile.mkdtemp(prefix="tts_")
        mp3, ogg = os.path.join(tmp, "v.mp3"), os.path.join(tmp, "v.ogg")
        try:
            try:
                await synth(text, voice, mp3)
            except Exception as e:
                logger.info(f".tts sintez xətası ({voice}): {e}")
                await safe_edit(event, f"❌ Səs yaradılmadı: <code>{str(e)[:150]}</code>")
                return
            if not os.path.exists(mp3) or os.path.getsize(mp3) == 0:
                await safe_edit(event, "❌ Boş səs faylı alındı.")
                return
            as_voice = await to_ogg(mp3, ogg)
            send_path = ogg if as_voice else mp3
            dur = await duration_sec(send_path)
            reply_to = event.reply_to_msg_id or None
            try:
                await event.client.send_file(
                    event.chat_id, send_path, voice_note=as_voice,
                    reply_to=reply_to, attributes=_attrs(as_voice, dur, text))
            except Exception as e:
                logger.info(f".tts göndərmə xətası: {e}")
                await safe_edit(event, f"❌ Göndərilmədi: <code>{str(e)[:150]}</code>")
                return
            try:
                await event.delete()                 # .tts komandasını təmizlə
            except Exception:
                pass
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    @ub.command("ttsvoice", pattern=r"^\.ttsvoice(?:\s+(\S+))?$",
                help=("TTS standart səsi",
                      "Standart TTS səsini göstərir / dəyişir.\n\n"
                      "<b>İstifadə:</b>\n• <code>.ttsvoice</code> — cari səs və dillər\n"
                      "• <code>.ttsvoice az-AZ-BanuNeural</code> — edge-tts səs adı ilə dəyiş\n\n"
                      "edge-tts bütün səslər: <code>edge-tts --list-voices</code> (serverdə)."))
    async def on_ttsvoice(event):
        name = (event.pattern_match.group(1) or "").strip()
        if not name:
            cur = default_voice()
            engine = "edge-tts" if HAS_EDGE else ("gTTS" if HAS_GTTS else "yoxdur ❌")
            langs = " · ".join(f"<code>{k}:</code>" for k in LANGS)
            await safe_edit(event,
                            f"🗣 <b>TTS</b>\nMühərrik: <b>{engine}</b>\nCari səs: <code>{cur}</code>\n\n"
                            f"Dil prefiksləri: {langs}\n"
                            f"<i>Səsi dəyiş:</i> <code>.ttsvoice az-AZ-BanuNeural</code>")
            return
        if not re.fullmatch(r"[A-Za-z]{2,3}-[A-Za-z]{2,4}-\w+Neural", name):
            await safe_edit(event, "❌ edge-tts səs adı belə olmalıdır: <code>az-AZ-BanuNeural</code>")
            return
        try:
            get_db().set_setting(VOICE_KEY, name)
        except Exception as e:
            await safe_edit(event, f"❌ Saxlanmadı: <code>{str(e)[:120]}</code>")
            return
        await safe_edit(event, f"✅ Standart səs: <code>{name}</code>"
                               + ("" if HAS_EDGE else "\n⚠️ <i>edge-tts quraşdırılmayıb — səs adı yalnız edge-tts "
                                  "ilə işləyir; hazırda gTTS dil kodu istifadə olunacaq.</i>"))


def _attrs(as_voice, dur, text):
    from core.userbot_api import logger as _l  # noqa
    try:
        from telethon.tl.types import DocumentAttributeAudio
    except Exception:
        return None
    if as_voice:
        return [DocumentAttributeAudio(duration=dur or 0, voice=True)]
    title = (text[:40] + "…") if len(text) > 40 else text
    return [DocumentAttributeAudio(duration=dur or 0, voice=False, title=title, performer="TTS")]
