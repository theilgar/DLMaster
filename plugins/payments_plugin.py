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
from core.stars import PLAN_MAP, get_price, make_payload, parse_payload

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
        data = parse_payload(sp.invoice_payload)
        uid = message.from_user.id
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
