"""
⭐ Premium-un Telegram Stars ilə satışı.

  /premium → ⭐ plan düyməsi → Telegram ödəniş pəncərəsi (XTR) → premium avtomatik verilir
  /paysupport — ödənişlə bağlı dəstək (Telegram Stars qaydaları tələb edir)

Qiymətlər /menu → 💎 Premium → ⭐ Ulduz satışı bölməsindən təyin olunur (core/stars.py).
Geri qaytarma (refund) də oradan edilir.
"""
import asyncio
import logging
from html import escape

from aiogram import F, types
from aiogram.filters import Command
from aiogram.types import CallbackQuery, LabeledPrice, Message, PreCheckoutQuery

from core.database import get_db, is_creator
from core.stars import (
    PLAN_MAP, get_price, make_payload, parse_payload,
    TIP_PLAN, MIN_STARS, MAX_STARS, make_tip_payload, parse_tip_payload,
)

logger = logging.getLogger(__name__)


def fmt_until(until) -> str:
    if until is None:
        return "♾ Ömürlük"
    from datetime import datetime, timedelta, timezone
    try:
        from zoneinfo import ZoneInfo
        import os
        tz = ZoneInfo(os.getenv("BOT_TZ", "Asia/Baku"))
    except Exception:
        tz = timezone(timedelta(hours=4))
    return datetime.fromtimestamp(until, tz).strftime("%d.%m.%Y") + "-dək"


def setup(context):
    dp = context.dp
    bot = context.bot
    # "✏️ Öz məbləğim" — istifadəçidən rəqəm gözlənilir (music_plugin bunu axtarış saymır)
    tip_waiting = set()
    context.tip_waiting = tip_waiting

    # ═════════════ ⭐ Bəxşiş ═════════════
    async def send_tip_invoice(uid: int, amount: int):
        await bot.send_invoice(
            chat_id=uid,
            title="Bəxşiş — DLLMaster 💙",
            description=f"Əməyə dəstək üçün {amount}⭐ bəxşiş. Təşəkkürlər!",
            payload=make_tip_payload(uid, amount),
            currency="XTR",
            provider_token="",
            prices=[LabeledPrice(label="Bəxşiş", amount=amount)],
        )

    @dp.callback_query(F.data.startswith("tip:"))
    async def tip_choice(cb: CallbackQuery):
        uid = cb.from_user.id
        value = cb.data.split(":", 1)[1]
        if is_creator(uid):
            await cb.answer("🙂 Özünə bəxşiş göndərə bilməzsən", show_alert=True)
            return
        if value == "cancel":
            tip_waiting.discard(uid)
            await cb.answer("Ləğv edildi")
            try:
                await cb.message.delete()
            except Exception:
                pass
            return
        if value == "custom":
            tip_waiting.add(uid)
            await cb.answer()
            await bot.send_message(
                uid,
                f"✏️ <b>Neçə ulduz göndərmək istəyirsən?</b>\n<i>{MIN_STARS}–{MAX_STARS} arası rəqəm yaz, məs.</i> <code>75</code>",
                parse_mode="HTML",
                reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[
                    types.InlineKeyboardButton(text="❌ Ləğv et", callback_data="tip:cancel")
                ]]),
            )
            return
        if not value.isdigit():
            await cb.answer()
            return
        await cb.answer()
        try:
            await send_tip_invoice(uid, int(value))
        except Exception as e:
            logger.error(f"Bəxşiş invoice-u göndərilmədi ({uid}): {e}")
            await bot.send_message(uid, "❌ Ödəniş pəncərəsi açılmadı, bir az sonra yenidən cəhd et.")

    async def waiting_tip(message: Message) -> bool:
        return bool(message.from_user) and message.from_user.id in tip_waiting

    @dp.message(F.chat.type == "private", F.text, ~F.text.startswith("/"), waiting_tip)
    async def tip_amount_input(message: Message):
        raw = message.text.strip().replace("⭐", "").replace(" ", "")
        if not raw.isdigit() or not (MIN_STARS <= int(raw) <= MAX_STARS):
            await message.reply(f"❌ {MIN_STARS}–{MAX_STARS} arası rəqəm yaz, məs. <code>75</code>", parse_mode="HTML")
            return
        tip_waiting.discard(message.from_user.id)
        try:
            await send_tip_invoice(message.from_user.id, int(raw))
        except Exception as e:
            logger.error(f"Bəxşiş invoice-u göndərilmədi: {e}")
            await message.reply("❌ Ödəniş pəncərəsi açılmadı, bir az sonra yenidən cəhd et.")

    # ── 1) Plan seçildi → invoice ──
    @dp.callback_query(F.data.startswith("buy:"))
    async def buy_plan(cb: CallbackQuery):
        plan = cb.data.split(":", 1)[1]
        uid = cb.from_user.id
        if plan not in PLAN_MAP:
            await cb.answer()
            return
        if is_creator(uid):
            await cb.answer("👑 Creator-un bütün imkanları onsuz da açıqdır", show_alert=True)
            return
        price = await asyncio.to_thread(get_price, plan)
        if not price:
            await cb.answer("Bu plan hazırda satışda deyil", show_alert=True)
            return
        cur = await asyncio.to_thread(get_db().get_premium, uid)
        if cur and cur["until"] is None:
            await cb.answer("💎 Sizin artıq ömürlük premiumunuz var", show_alert=True)
            return

        days, label = PLAN_MAP[plan]
        extend = " Aktiv premiumunuzun üstünə əlavə olunacaq." if cur and days else ""
        await cb.answer()
        try:
            await bot.send_invoice(
                chat_id=uid,
                title=f"DLLMaster Premium — {label.replace('♾ ', '')}"[:32],
                description=(
                    f"💎 Premium: {label.replace('♾ ', '').lower()}. "
                    "Mahnılar \"via @dllmasterbot\" yazısı olmadan göndərilir." + extend
                )[:255],
                payload=make_payload(plan, uid, price),
                currency="XTR",                      # Telegram Stars
                provider_token="",                   # Stars üçün boş olmalıdır
                prices=[LabeledPrice(label=f"Premium {label.replace('♾ ', '')}", amount=price)],
            )
        except Exception as e:
            logger.error(f"Invoice göndərilmədi ({uid}, {plan}): {e}")
            await bot.send_message(uid, "❌ Ödəniş pəncərəsi açılmadı, bir az sonra yenidən cəhd edin.")

    # ── 2) Ödənişdən əvvəl son yoxlama (10 saniyə ərzində cavab verilməlidir) ──
    @dp.pre_checkout_query()
    async def pre_checkout(q: PreCheckoutQuery):
        tip = parse_tip_payload(q.invoice_payload)
        if tip:
            uid, amount = tip
            ok = (q.currency == "XTR" and uid == q.from_user.id and q.total_amount == amount
                  and MIN_STARS <= amount <= MAX_STARS)
            await q.answer(ok=ok, error_message=None if ok else "Bəxşiş tanınmadı, yenidən cəhd et.")
            return
        data = parse_payload(q.invoice_payload)
        if not data or q.currency != "XTR":
            await q.answer(ok=False, error_message="Ödəniş tanınmadı. /premium ilə yenidən cəhd edin.")
            return
        plan, uid, price = data
        current = get_price(plan)
        if uid != q.from_user.id:
            await q.answer(ok=False, error_message="Bu ödəniş başqa istifadəçi üçündür.")
        elif not current:
            await q.answer(ok=False, error_message="Bu plan artıq satışda deyil.")
        elif current != price or q.total_amount != price:
            await q.answer(ok=False, error_message="Qiymət dəyişib. /premium ilə yenidən cəhd edin.")
        else:
            cur = get_db().get_premium(uid)
            if cur and cur["until"] is None:
                await q.answer(ok=False, error_message="Sizin artıq ömürlük premiumunuz var.")
            else:
                await q.answer(ok=True)

    # ── 3) Ödəniş uğurlu → premium ──
    @dp.message(F.successful_payment)
    async def successful_payment(message: Message):
        sp = message.successful_payment
        uid = message.from_user.id
        if parse_tip_payload(sp.invoice_payload):
            await tip_paid(message, sp)
            return
        data = parse_payload(sp.invoice_payload)
        if not data:
            logger.error(f"Naməlum ödəniş payload-ı: {sp.invoice_payload} ({uid})")
            return
        plan, _, _ = data
        days, label = PLAN_MAP[plan]
        db = get_db()

        payment_id = await asyncio.to_thread(
            db.add_payment, uid, plan, days, sp.total_amount, sp.telegram_payment_charge_id
        )
        if not payment_id:
            logger.warning(f"Təkrar ödəniş bildirişi ötürüldü: {sp.telegram_payment_charge_id}")
            return
        until = await asyncio.to_thread(db.grant_premium, uid, days, 0)
        logger.info(f"⭐ Ödəniş: {uid} → {plan} ({sp.total_amount}⭐), #{payment_id}")

        await message.answer(
            "🎉 <b>Təşəkkürlər! Premium aktivdir.</b>\n\n"
            f"⭐ Ödənildi: <b>{sp.total_amount}</b> ulduz\n"
            f"💎 Plan: <b>{escape(label)}</b>\n"
            f"⏳ {fmt_until(until)}\n\n"
            "✨ Mahnılar artıq \"via @dllmasterbot\" yazısı olmadan göndəriləcək.\n"
            "<i>Statusunu istənilən vaxt /premium ilə görə bilərsən.</i>",
            parse_mode="HTML",
        )

        # Creator-a bildiriş
        if context.creator_id:
            u = message.from_user
            name = escape(" ".join(filter(None, [u.first_name, u.last_name])) or str(uid))
            uname = f" · @{escape(u.username)}" if u.username else ""
            try:
                await bot.send_message(
                    context.creator_id,
                    "⭐ <b>Yeni premium ödənişi</b>\n\n"
                    f"👤 <a href=\"tg://user?id={uid}\">{name}</a>{uname} · <code>{uid}</code>\n"
                    f"💎 {escape(label)} · <b>{sp.total_amount}⭐</b>\n"
                    f"⏳ {fmt_until(until)}\n"
                    f"🧾 Ödəniş #{payment_id}",
                    parse_mode="HTML",
                )
            except Exception as e:
                logger.warning(f"Creator-a ödəniş bildirişi getmədi: {e}")

    async def tip_paid(message: Message, sp):
        uid = message.from_user.id
        payment_id = await asyncio.to_thread(
            get_db().add_payment, uid, TIP_PLAN, None, sp.total_amount, sp.telegram_payment_charge_id
        )
        if not payment_id:
            return                                  # təkrar bildiriş
        logger.info(f"💝 Bəxşiş: {uid} → {sp.total_amount}⭐ (#{payment_id})")
        await message.answer(
            f"💙 <b>Çox sağ ol!</b>\n\n⭐ <b>{sp.total_amount}</b> ulduz bəxşişin alındı.\n"
            "Dəstəyin botun inkişafına kömək edir 🙏",
            parse_mode="HTML",
        )
        if context.creator_id:
            u = message.from_user
            name = escape(" ".join(filter(None, [u.first_name, u.last_name])) or str(uid))
            uname = f" · @{escape(u.username)}" if u.username else ""
            try:
                await bot.send_message(
                    context.creator_id,
                    "💝 <b>Yeni bəxşiş!</b>\n\n"
                    f"👤 <a href=\"tg://user?id={uid}\">{name}</a>{uname} · <code>{uid}</code>\n"
                    f"⭐ <b>{sp.total_amount}</b> ulduz\n🧾 Ödəniş #{payment_id}",
                    parse_mode="HTML",
                )
            except Exception as e:
                logger.warning(f"Creator-a bəxşiş bildirişi getmədi: {e}")

    # ── /paysupport ──
    @dp.message(Command("paysupport"))
    async def paysupport(message: Message):
        await message.answer(
            "🧾 <b>Ödəniş dəstəyi</b>\n\n"
            "Premium ödənişi ilə bağlı problem varsa, bota birbaşa yazın və ya @ilgarww ilə əlaqə saxlayın.\n"
            "Ödənişin nömrəsini və tarixini qeyd edin.\n\n"
            "<i>Premium rəqəmsal xidmətdir; geri qaytarma hallara görə creator tərəfindən edilir.</i>",
            parse_mode="HTML",
        )

    logger.info("✅ Payments plugin yükləndi")
