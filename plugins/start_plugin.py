from aiogram import types, Router, Bot, F
from aiogram.filters import Command
from aiogram.filters.chat_member_updated import (
    ChatMemberUpdatedFilter,
    JOIN_TRANSITION,
    ADMINISTRATOR,
    MEMBER,
    RESTRICTED,
)
from aiogram.types import (
    CallbackQuery,
    ChatMemberUpdated,
    ChatAdministratorRights,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from aiogram.enums import ChatMemberStatus
from datetime import datetime, timedelta, timezone
from html import escape
import asyncio
import logging
import os

from core.database import get_db, is_creator
from core.welcome import send_welcome, send_media, get_text_template, render_template

logger = logging.getLogger(__name__)
router = Router()
CALLBACK_PREFIX = "start_"
CREATOR_USERNAME = "ilgarww"

# Botun qrupda işləməsi üçün lazım olan yetkilər: (açar, göstəriləcək ad)
# "admin" — media göndərmə üçün: adminlər qrupun "üzvlər media göndərə bilməz" məhdudiyyətindən asılı olmur.
REQUIRED_RIGHTS = [
    ("admin", "🛡 Admin olmaq (media göndərmə)"),
    ("can_delete_messages", "🗑 Mesaj silmə"),
    ("can_pin_messages", "📌 Mesaj pinləmə"),
]
# t.me/<bot>?startgroup=true&admin=... linkində istənilən yetkilər (Telegram özü təsdiq dialoqu açır)
ADMIN_LINK_RIGHTS = "delete_messages+pin_messages"

# Eyni çatda eyni çatışmazlıq bildirişini təkrar göndərməmək üçün
_last_notice = {}


async def setup(context):
    context.main_router.include_router(router)

    # "Qrupa əlavə et" düyməsi ilə admin kimi əlavə edilərkən Telegram bu yetkiləri təklif edəcək
    try:
        await context.bot.set_my_default_administrator_rights(
            rights=ChatAdministratorRights(
                is_anonymous=False,
                can_manage_chat=True,
                can_delete_messages=True,
                can_manage_video_chats=False,
                can_restrict_members=False,
                can_promote_members=False,
                can_change_info=False,
                can_invite_users=False,
                can_pin_messages=True,
            ),
            for_channels=False,
        )
        logger.info("✅ Default admin yetkiləri təyin edildi (silmə, pinləmə)")
    except Exception as e:
        logger.warning(f"Default admin yetkiləri təyin edilmədi: {e}")

    logger.info("✅ Start plugin yükləndi")


# ───────────────────────── köməkçilər ─────────────────────────
def _fmt_date(ts: int) -> str:
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(os.getenv("BOT_TZ", "Asia/Baku"))
    except Exception:
        tz = timezone(timedelta(hours=4))
    return datetime.fromtimestamp(ts, tz).strftime("%d.%m.%Y")


def status_line(user_id: int) -> str:
    """İstifadəçinin roluna görə start mesajındakı status sətri."""
    if is_creator(user_id):
        return "👑 <b>Creator</b> — idarə paneli: /menu"
    try:
        cur = get_db().get_premium(user_id)
    except Exception as e:
        logger.warning(f"Premium yoxlanışı xətası: {e}")
        cur = None
    if cur:
        until = "ömürlük" if cur["until"] is None else f"{_fmt_date(cur['until'])}-dək"
        return f"💎 <b>Premium</b> ({until}) — mahnılar yazısız gəlir"
    return "💎 Premium ilə mahnılar \"via @dllmasterbot\" yazısı olmadan gəlir → /premium"


# ───────────────────────── Default mətn şablonları ─────────────────────────
# /menu → 📝 Mesajlar ilə dəyişilə bilər; {dəyişənlər} göndəriləndə əvəz olunur.
DEFAULT_TEXTS = {
    "welcome": (
        "<b>Salam {name}! 👋 Mən DLLMaster Bot 🎵</b>\n\n"
        "🎧 <b>Nə edə bilərəm?</b>\n"
        "🔎 Mahnı adını yaz və ya <code>/music ad</code> — axtarıb yükləyirəm\n"
        "🔗 YouTube / Spotify linki, playlisti və ya albomu at\n"
        "⚡ İstənilən çatda: <code>@{bot} mahnı adı</code>\n"
        "🔀 <code>/mix ad</code> və ya mahnının altındakı <b>Mix</b> — oxşar mahnılar\n"
        "⏹ Yükləməni istənilən an dayandıra bilərsən\n"
        "✏️ Mənə audio göndər — adını, artistini və şəklini dəyişim\n\n"
        "{status}\n\n"
        "Bot @{creator} tərəfindən yaradılmışdır 🚀"
    ),
    "group": (
        "<b>Salam, {group}! 👋</b>\n\n"
        "{thanks}Mən <b>DLLMaster Bot</b> 🎵\n\n"
        "🎧 <b>Qrupda necə istifadə etmək olar?</b>\n"
        "🔎 <code>/music mahnı adı</code> — axtarıb yükləyirəm\n"
        "⚡ <code>@{bot} mahnı adı</code> — bir toxunuşla göndər\n"
        "🔗 YouTube / Spotify playlist, albom və ya track linki\n"
        "🔀 <code>/mix mahnı adı</code> — oxşar mahnılar\n\n"
        "{rights}\n\n"
        "Bot @{creator} tərəfindən yaradılmışdır 🚀"
    ),
    "download": (
        "🎵 <b>Mahnı necə yüklənir?</b>\n\n"
        "1️⃣ Mahnının adını yaz, məs. <code>Miri Yusif Qarabağ</code>\n"
        "   və ya <code>/music mahnı adı</code>\n"
        "2️⃣ Çıxan siyahıdan mahnını seç\n"
        "3️⃣ Bir neçə saniyəyə mahnı gəlir 🎧\n\n"
        "🔗 <b>Linklər də işləyir:</b> YouTube, Spotify track, playlist və albom\n\n"
        "⏹ Yüklənərkən <b>Yükləməni dayandır</b> ilə ləğv edə bilərsən\n"
        "🔀 <code>/mix mahnı adı</code> — oxşar mahnılar siyahısı\n"
        "   (və ya botun göndərdiyi mahnıya cavab olaraq <code>/mix</code>)\n"
        "🙈 <b>Gizlət</b> — mahnının altındakı düymələri gizlədir"
    ),
    "inline": (
        "🔎 <b>İnline axtarış</b>\n\n"
        "Botu çata əlavə etmədən <b>istənilən çatda</b> mahnı göndərə bilərsən:\n\n"
        "1️⃣ Mesaj yerində yaz: <code>@{bot} mahnı adı</code>\n"
        "2️⃣ Çıxan siyahıdan mahnını seç\n"
        "3️⃣ Mahnı həmin çata gedir 🎧\n\n"
        "🔀 Mahnının altındakı <b>Mix</b> ilə oxşar mahnıları da eyni yolla göndərə bilərsən.\n\n"
        "👇 Sınamaq üçün düyməyə bas"
    ),
    "edit": (
        "✏️ <b>Mahnının metadatasını dəyişmək</b>\n\n"
        "1️⃣ Mənə istənilən mahnını (audio faylı) göndər\n"
        "2️⃣ Çıxan paneldən seç:\n"
        "   ✏️ <b>Ad</b> — mahnının adı\n"
        "   🎤 <b>Artist</b> — ifaçının adı\n"
        "   🖼 <b>Thumbnail</b> — üz qabığı şəkli\n"
        "3️⃣ <b>✅ Göndər</b> bas — dəyişdirilmiş mahnı geri gəlir\n\n"
        "<i>ℹ️ Fayl 20 MB-dan böyük olmamalıdır (Telegram limiti).</i>"
    ),
}


def text_template(slot: str) -> str:
    """Creator-un /menu-dan yazdığı şablon, yoxdursa default."""
    return get_text_template(slot) or DEFAULT_TEXTS[slot]


def build_private_text(first_name: str, bot_username: str, user_id: int) -> str:
    return render_template(text_template("welcome"), {
        "name": escape(first_name),
        "bot": escape(bot_username),
        "status": status_line(user_id),
        "creator": CREATOR_USERNAME,
    })


def private_keyboard(bot_username: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎵 Mahnı yüklə", callback_data="sg:download"),
         InlineKeyboardButton(text="🔎 İnline axtarış", callback_data="sg:inline")],
        [InlineKeyboardButton(text="✏️ Metadata redaktə", callback_data="sg:edit")],
        [InlineKeyboardButton(text="🤖 Botu qrupa əlavə et", url=admin_request_url(bot_username))],
        [InlineKeyboardButton(text="❌ Ləğv et", callback_data="sg:close")],
    ])


def nav_row() -> list:
    """Bələdçilərin altındakı naviqasiya: salam mesajına qayıt / bağla."""
    return [InlineKeyboardButton(text="⬅️ Geri", callback_data="sg:back"),
            InlineKeyboardButton(text="❌ Ləğv et", callback_data="sg:close")]


# ───────────────────────── Bələdçilər (start düymələri) ─────────────────────────
# Hər bələdçinin mediası /menu → 🖼 Media bölməsindən dəyişilir (core/welcome.py slot-ları)
def guide_text(slot: str, bot_username: str) -> str:
    if slot not in ("download", "inline", "edit"):
        raise ValueError(slot)
    return render_template(text_template(slot), {"bot": escape(bot_username), "creator": CREATOR_USERNAME})


def guide_keyboard(slot: str) -> InlineKeyboardMarkup:
    rows = []
    if slot == "inline":
        rows += [
            [InlineKeyboardButton(text="🔎 Burada sına", switch_inline_query_current_chat="")],
            [InlineKeyboardButton(text="📤 Başqa çatda istifadə et", switch_inline_query="")],
        ]
    rows.append(nav_row())
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def can_close(callback: CallbackQuery) -> bool:
    """Şəxsi çatda hər kəs; qrupda yalnız adminlər və creator bağlaya bilər."""
    msg = callback.message
    if not msg or msg.chat.type == "private" or is_creator(callback.from_user.id):
        return True
    try:
        m = await callback.bot.get_chat_member(msg.chat.id, callback.from_user.id)
        return m.status in {ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR}
    except Exception:
        return False


async def delete_quietly(message):
    try:
        await message.delete()
    except Exception as e:
        logger.debug(f"Mesaj silinmədi: {e}")


@router.callback_query(F.data.startswith("sg:"))
async def start_guide(callback: CallbackQuery):
    """
    Start düymələri. Hər dəfə yeni mesaj göndərilir və köhnəsi silinir — çatda bir mesaj qalır:
      sg:download / sg:inline / sg:edit → bələdçi (salam mesajının yerinə)
      sg:back  → bələdçidən salam mesajına qayıt
      sg:close → mesajı bağla
    """
    action = callback.data.split(":", 1)[1]
    bot = callback.bot
    msg = callback.message
    chat_id = msg.chat.id if msg else callback.from_user.id

    if action == "close":
        if not await can_close(callback):
            await callback.answer("⛔ Yalnız qrup adminləri bağlaya bilər", show_alert=True)
            return
        await callback.answer("❌ Bağlandı")
        if msg:
            await delete_quietly(msg)
        return

    if action == "back":
        await callback.answer()
        try:
            username = (await bot.me()).username
            text = await asyncio.to_thread(
                build_private_text, callback.from_user.first_name or "Dostum", username, callback.from_user.id
            )
            await send_welcome(bot, chat_id, text, private_keyboard(username))
        except Exception as e:
            logger.error(f"Salam mesajına qayıtmaq alınmadı: {e}")
            return
        if msg:
            await delete_quietly(msg)
        return

    if action not in ("download", "inline", "edit"):
        await callback.answer()
        return
    await callback.answer()
    try:
        username = (await bot.me()).username
        await send_media(bot, chat_id, guide_text(action, username), guide_keyboard(action), action)
    except Exception as e:
        logger.error(f"Bələdçi göndərilmədi ({action}): {e}")
        return
    if msg:
        await delete_quietly(msg)


async def is_bot_admin(chat_id: int, bot: Bot) -> bool:
    try:
        bot_info = await bot.get_me()
        chat_member = await bot.get_chat_member(chat_id, bot_info.id)
        return chat_member.status in {ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR}
    except Exception as e:
        logger.error(f"Admin yoxlanışı xətası: {e}")
        return False


def rights_status(member) -> dict:
    """Botun üzv obyektinə görə hansı yetkilərin olduğunu qaytarır."""
    if member.status == ChatMemberStatus.CREATOR:
        return {key: True for key, _ in REQUIRED_RIGHTS}
    is_admin = member.status == ChatMemberStatus.ADMINISTRATOR
    return {
        "admin": is_admin,
        "can_delete_messages": is_admin and bool(getattr(member, "can_delete_messages", False)),
        "can_pin_messages": is_admin and bool(getattr(member, "can_pin_messages", False)),
    }


def missing_keys(rights: dict) -> tuple:
    return tuple(key for key, _ in REQUIRED_RIGHTS if not rights.get(key))


def admin_request_url(bot_username: str) -> str:
    return f"https://t.me/{bot_username}?startgroup=true&admin={ADMIN_LINK_RIGHTS}"


def rights_keyboard(bot_username: str, rights: dict):
    rows = [[InlineKeyboardButton(text="🔎 İnline axtarış", switch_inline_query_current_chat="")]]
    if missing_keys(rights):
        rows.insert(0, [InlineKeyboardButton(text="🛡 Yetkiləri ver", url=admin_request_url(bot_username))])
    rows.append([InlineKeyboardButton(text="❌ Bağla", callback_data="sg:close")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def rights_lines(rights: dict) -> str:
    return "\n".join(f"{'✅' if rights.get(key) else '❌'} {label}" for key, label in REQUIRED_RIGHTS)


def rights_block(bot_username: str, rights: dict) -> str:
    """Yetki siyahısı + çatışmırsa təlimat (qrup salamında {rights})."""
    text = f"🔧 <b>Lazımi yetkilər:</b>\n{rights_lines(rights)}\n\n"
    if missing_keys(rights):
        text += (
            "⚠️ <b>Tam işləməyim üçün aşağıdakı düyməyə basıb mənə admin yetkiləri verin.</b>\n"
            f"<i>Və ya: Qrup → Adminlər → Admin əlavə et → @{escape(bot_username)}</i>"
        )
    else:
        text += "✅ <b>Bütün yetkilər verilib, hazıram!</b>"
    return text


def build_group_text(title: str, bot_username: str, rights: dict, adder_html: str = None) -> str:
    template = text_template("group")
    if "{rights}" not in template:
        template = template.rstrip() + "\n\n{rights}"      # yetki siyahısı həmişə görünsün
    return render_template(template, {
        "group": escape(title),
        "thanks": f"Məni qrupa əlavə etdiyin üçün təşəkkürlər, {adder_html}! " if adder_html else "",
        "adder": adder_html or "",
        "bot": escape(bot_username),
        "rights": rights_block(bot_username, rights),
        "creator": CREATOR_USERNAME,
    })


def sample_message(slot: str, first_name: str, user_id: int, bot_username: str):
    """/menu önizləməsi üçün: (mətn, klaviatura) — istifadəçilərin görəcəyi kimi."""
    if slot == "welcome":
        return build_private_text(first_name, bot_username, user_id), private_keyboard(bot_username)
    if slot == "group":
        rights = {"admin": True, "can_delete_messages": True, "can_pin_messages": False}
        adder = f'<a href="tg://user?id={user_id}">{escape(first_name)}</a>'
        return build_group_text("Test qrupu", bot_username, rights, adder), rights_keyboard(bot_username, rights)
    return guide_text(slot, bot_username), guide_keyboard(slot)


async def get_bot_member(bot: Bot, chat_id: int):
    me = await bot.me()
    member = await bot.get_chat_member(chat_id, me.id)
    return member, me.username


async def send_group_welcome(bot: Bot, chat_id: int, text: str, keyboard):
    """/menu-da seçilmiş media ilə (alınmasa mətn kimi) göndərir."""
    await send_welcome(bot, chat_id, text, keyboard)


async def send_start_message(message: types.Message, text: str, bot: Bot):
    try:
        bot_username = (await bot.me()).username
        await send_welcome(bot, message.chat.id, text, private_keyboard(bot_username))
    except Exception as e:
        logger.error(f"Start mesajı xətası: {e}")
        await message.answer("❌ Xəta baş verdi, zəhmət olmasa yenidən cəhd edin.")


@router.message(Command("start"))
async def start_command(message: types.Message):
    context = message.bot.data.get('app_context')
    bot = context.bot

    if message.chat.type == "private":
        username = (await bot.me()).username
        text = await asyncio.to_thread(
            build_private_text, message.from_user.first_name or "Dostum", username, message.from_user.id
        )
        await send_start_message(message, text, bot)
    else:
        try:
            member, username = await get_bot_member(bot, message.chat.id)
            rights = rights_status(member)
        except Exception as e:
            logger.error(f"Yetki yoxlanışı xətası: {e}")
            username = (await bot.me()).username
            rights = {key: False for key, _ in REQUIRED_RIGHTS}
        await message.reply(
            build_group_text(message.chat.title or "qrup", username, rights),
            parse_mode="HTML",
            reply_markup=rights_keyboard(username, rights),
        )


@router.my_chat_member(ChatMemberUpdatedFilter(JOIN_TRANSITION))
async def on_bot_added(event: ChatMemberUpdated, bot: Bot):
    # Yalnız qrup/supergroup hadisələri
    if event.chat.type not in ["group", "supergroup"]:
        return

    old_status = event.old_chat_member.status
    new_status = event.new_chat_member.status
    logger.info(f"🔄 Bot status dəyişikliyi: {old_status} -> {new_status} | Chat: {event.chat.title} ({event.chat.id})")

    try:
        username = (await bot.me()).username
        rights = rights_status(event.new_chat_member)
        adder = event.from_user.mention_html() if event.from_user else None

        await send_group_welcome(
            bot, event.chat.id,
            build_group_text(event.chat.title or "qrup", username, rights, adder_html=adder),
            rights_keyboard(username, rights),
        )
        _last_notice[event.chat.id] = missing_keys(rights)
        logger.info(f"✅ Qrup salam mesajı göndərildi: {event.chat.title}")

    except Exception as e:
        logger.error(f"❌ Qrup salam mesajı göndərilmədi: {str(e)}")
        logger.debug(f"Xəta detalları: Chat ID: {event.chat.id}, Status: {new_status}")


@router.my_chat_member(ChatMemberUpdatedFilter((MEMBER | RESTRICTED) >> ADMINISTRATOR))
@router.my_chat_member(ChatMemberUpdatedFilter(ADMINISTRATOR >> ADMINISTRATOR))
async def on_bot_rights_changed(event: ChatMemberUpdated, bot: Bot):
    """Bot admin edildikdə və ya yetkiləri dəyişdikdə: çatışmayanları bildirir / təşəkkür edir."""
    if event.chat.type not in ["group", "supergroup"]:
        return

    try:
        username = (await bot.me()).username
        rights = rights_status(event.new_chat_member)
        missing = missing_keys(rights)

        # Eyni vəziyyəti təkrar bildirmə
        if _last_notice.get(event.chat.id) == missing:
            return
        _last_notice[event.chat.id] = missing

        if not missing:
            text = (
                "✅ <b>Təşəkkürlər!</b> Bütün yetkilər verildi, hazıram 🚀\n\n"
                "🔎 <code>/music mahnı adı</code> yazaraq başlaya bilərsiniz."
            )
            keyboard = None
        else:
            text = (
                "⚠️ <b>Hələ çatışmayan yetkilər var:</b>\n"
                f"{rights_lines(rights)}\n\n"
                "<i>Aşağıdakı düyməyə basıb tamamlayın.</i>"
            )
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🛡 Yetkiləri ver", url=admin_request_url(username))],
            ])

        await bot.send_message(chat_id=event.chat.id, text=text, parse_mode="HTML", reply_markup=keyboard)
        logger.info(f"🔐 Yetki bildirişi göndərildi: {event.chat.title} | çatışmayan: {missing}")

    except Exception as e:
        logger.error(f"❌ Yetki bildirişi göndərilmədi: {e}")
