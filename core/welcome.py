"""
Salam və bələdçi mesajlarının mediası — core/welcome.py

Hər "slot" üçün ayrıca media seçilir (/menu → 🖼 Media):
  welcome   — /start və bot qrupa əlavə olunanda salam mesajı   (default: plugins/patrick.gif)
  download  — /start → 🎵 Mahnı yüklə bələdçisi                  (default: mediasız)
  inline    — /start → 🔎 İnline axtarış bələdçisi               (default: mediasız)
  edit      — /start → ✏️ Metadata redaktə bələdçisi             (default: mediasız)

Media növləri:
  default   — yalnız welcome üçün: plugins/patrick.gif
  photo / animation (GIF) / video — Telegram file_id
  none      — mediasız, yalnız mətn

Seçim bazada (settings) saxlanılır.

Mətnlər də /menu → 📝 Mesajlar bölməsindən dəyişilir (TEXT_SLOTS). Şablonlarda {name}, {bot}
kimi dəyişənlər göndəriləndə real dəyərlə əvəz olunur. Default mətnlər plugins/start_plugin.py-dadır.
"""
import json
import logging
from pathlib import Path

from aiogram.types import FSInputFile

from core.database import get_db
from core.utilities import BASE_DIR

logger = logging.getLogger(__name__)

DEFAULT_GIF = Path(BASE_DIR) / "plugins" / "patrick.gif"
CAPTION_LIMIT = 1024          # Telegram media caption limiti

SLOTS = {
    "welcome":  {"label": "🏠 Salam mesajı (/start)", "setting": "welcome_media",       "default": "default"},
    "download": {"label": "🎵 Mahnı yükləmə",         "setting": "guide_media:download", "default": "none"},
    "inline":   {"label": "🔎 İnline axtarış",        "setting": "guide_media:inline",   "default": "none"},
    "edit":     {"label": "✏️ Metadata redaktə",      "setting": "guide_media:edit",     "default": "none"},
    "premium":  {"label": "💎 Premium",               "setting": "guide_media:premium",  "default": "none"},
    "tip":      {"label": "⭐ Bəxşiş",                "setting": "guide_media:tip",      "default": "none"},
}
TYPE_LABELS = {
    "default": "🎞 Default GIF (patrick.gif)",
    "photo": "🖼 Şəkil",
    "animation": "🎞 GIF",
    "video": "🎬 Video",
    "none": "🚫 Mediasız (yalnız mətn)",
}


def _slot(slot: str) -> dict:
    if slot not in SLOTS:
        raise ValueError(f"Naməlum slot: {slot}")
    return SLOTS[slot]


def get_media(slot: str = "welcome") -> dict:
    """{"type": ..., "file_id": ...}"""
    info = _slot(slot)
    try:
        data = json.loads(get_db().get_setting(info["setting"]) or "null")
    except Exception as e:
        logger.warning(f"Media ayarı oxunmadı ({slot}): {e}")
        data = None
    if not isinstance(data, dict) or data.get("type") not in TYPE_LABELS:
        return {"type": info["default"], "file_id": None}
    if data["type"] == "default" and slot != "welcome":
        return {"type": "none", "file_id": None}
    return data


def set_media(slot: str, media_type: str, file_id: str = None):
    info = _slot(slot)
    if media_type not in TYPE_LABELS:
        raise ValueError(f"Naməlum media növü: {media_type}")
    if media_type == "default" and slot != "welcome":
        media_type = "none"
    if media_type == info["default"]:
        value = None                     # default-a qayıt
    else:
        value = json.dumps({"type": media_type, "file_id": file_id})
    get_db().set_setting(info["setting"], value)


def media_label(slot: str = "welcome", media: dict = None) -> str:
    media = media or get_media(slot)
    label = TYPE_LABELS[media["type"]]
    if media["type"] == "default" and not DEFAULT_GIF.exists():
        label += " — fayl tapılmadı, mətn göndərilir"
    return label


async def send_media(bot, chat_id: int, text: str, keyboard=None, slot: str = "welcome", reply_to: int = None):
    """Mesajı slot-un mediası ilə göndərir; alınmasa (və ya mətn çox uzundursa) mətn kimi."""
    media = get_media(slot)
    kind, file_id = media["type"], media.get("file_id")
    common = dict(chat_id=chat_id, parse_mode="HTML", reply_markup=keyboard)
    if reply_to:
        common["reply_to_message_id"] = reply_to

    if kind != "none" and len(text) <= CAPTION_LIMIT:
        try:
            if kind == "default":
                if DEFAULT_GIF.exists():
                    return await bot.send_animation(animation=FSInputFile(DEFAULT_GIF), caption=text, **common)
            elif kind == "photo":
                return await bot.send_photo(photo=file_id, caption=text, **common)
            elif kind == "animation":
                return await bot.send_animation(animation=file_id, caption=text, **common)
            elif kind == "video":
                return await bot.send_video(video=file_id, caption=text, **common)
        except Exception as e:
            logger.warning(f"Media göndərilmədi ({slot}/{kind}), mətnlə davam edilir: {e}")

    return await bot.send_message(text=text, disable_web_page_preview=True, **common)


# ───────────────────────── Mətn şablonları ─────────────────────────
TEXT_SLOTS = {
    "welcome": {
        "label": "🏠 Salam mesajı (/start)", "media": "welcome",
        "vars": {"{name}": "istifadəçinin adı", "{bot}": "botun username-i",
                 "{status}": "Free / Premium / Creator sətri", "{creator}": "sənin username-in"},
    },
    "group": {
        "label": "👥 Qrup salamı", "media": "welcome",
        "vars": {"{group}": "qrupun adı", "{thanks}": "\"əlavə etdiyin üçün təşəkkürlər, ...\" cümləsi",
                 "{adder}": "botu əlavə edənin adı", "{bot}": "botun username-i",
                 "{rights}": "yetkilər siyahısı (yazmasan sonuna avtomatik əlavə olunur)",
                 "{creator}": "sənin username-in"},
    },
    "download": {"label": "🎵 Mahnı yükləmə", "media": "download", "vars": {"{bot}": "botun username-i"}},
    "inline":   {"label": "🔎 İnline axtarış", "media": "inline",   "vars": {"{bot}": "botun username-i"}},
    "edit":     {"label": "✏️ Metadata redaktə", "media": "edit",    "vars": {"{bot}": "botun username-i"}},
    "premium": {
        "label": "💎 Premium", "media": "premium",
        "vars": {"{status}": "istifadəçinin hazırkı statusu", "{plans}": "satışdakı planlar və qiymətlər",
                 "{bot}": "botun username-i", "{creator}": "sənin username-in"},
    },
    "tip": {"label": "⭐ Bəxşiş", "media": "tip",
            "vars": {"{bot}": "botun username-i", "{creator}": "sənin username-in"}},
}
TEXT_LIMIT = 4096


def get_text_template(slot: str):
    """Creator-un yazdığı şablon; yoxdursa None (default istifadə olunur)."""
    if slot not in TEXT_SLOTS:
        raise ValueError(f"Naməlum mətn slotu: {slot}")
    try:
        return get_db().get_setting(f"text:{slot}")
    except Exception as e:
        logger.warning(f"Mətn şablonu oxunmadı ({slot}): {e}")
        return None


def set_text_template(slot: str, template):
    """template=None → default mətnə qayıt."""
    if slot not in TEXT_SLOTS:
        raise ValueError(f"Naməlum mətn slotu: {slot}")
    get_db().set_setting(f"text:{slot}", template or None)


def render_template(template: str, values: dict) -> str:
    """Yalnız tanınan {dəyişənləri} əvəz edir — mətndəki başqa fiqurlu mötərizələrə toxunmur."""
    for key, value in values.items():
        template = template.replace("{" + key + "}", value)
    return template


# ── köhnə adlar (uyğunluq üçün) ──
def get_welcome_media() -> dict:
    return get_media("welcome")


def set_welcome_media(media_type: str, file_id: str = None):
    set_media("welcome", media_type, file_id)


async def send_welcome(bot, chat_id: int, text: str, keyboard=None, reply_to: int = None):
    return await send_media(bot, chat_id, text, keyboard, "welcome", reply_to)
