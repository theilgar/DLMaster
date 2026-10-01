"""
⭐ Telegram Stars ilə premium satışı — core/stars.py

Planlar sabitdir, qiymətləri creator /menu → 💎 Premium → ⭐ Ulduz satışı bölməsindən təyin edir.
Qiyməti təyin olunmayan plan satışda deyil.
"""
import logging

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from core.database import get_db

logger = logging.getLogger(__name__)

# (açar, gün sayı | None = ömürlük, ad)
PLANS = [
    ("7", 7, "7 gün"),
    ("30", 30, "30 gün"),
    ("90", 90, "90 gün"),
    ("365", 365, "1 il"),
    ("life", None, "♾ Ömürlük"),
]
PLAN_MAP = {key: (days, label) for key, days, label in PLANS}
MIN_STARS, MAX_STARS = 1, 10000


def get_price(plan: str):
    """Planın qiyməti (⭐) və ya None (satışda deyil)."""
    try:
        value = get_db().get_setting(f"stars_price:{plan}")
        return int(value) if value and value.isdigit() and int(value) > 0 else None
    except Exception as e:
        logger.warning(f"Qiymət oxunmadı ({plan}): {e}")
        return None


def set_price(plan: str, stars):
    if plan not in PLAN_MAP:
        raise ValueError(plan)
    get_db().set_setting(f"stars_price:{plan}", str(int(stars)) if stars else None)


def plans_for_sale() -> list:
    """[(açar, gün, ad, qiymət)] — yalnız qiyməti olanlar."""
    out = []
    for key, days, label in PLANS:
        price = get_price(key)
        if price:
            out.append((key, days, label, price))
    return out


def per_month(days, price) -> str:
    if not days or days < 30:
        return ""
    return f" (~{price * 30 / days:.0f}⭐/ay)"


def buy_keyboard(current_until_is_lifetime: bool = False):
    """/premium altındakı alış düymələri; satışda plan yoxdursa None."""
    if current_until_is_lifetime:
        return None
    plans = plans_for_sale()
    if not plans:
        return None
    rows = [[InlineKeyboardButton(text=f"⭐ {label} — {price} ulduz", callback_data=f"buy:{key}")]
            for key, days, label, price in plans]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def make_payload(plan: str, user_id: int, price: int) -> str:
    return f"prem:{plan}:{user_id}:{price}"


def parse_payload(payload: str):
    """(plan, user_id, price) və ya None"""
    try:
        kind, plan, uid, price = (payload or "").split(":")
        if kind != "prem" or plan not in PLAN_MAP:
            return None
        return plan, int(uid), int(price)
    except ValueError:
        return None
