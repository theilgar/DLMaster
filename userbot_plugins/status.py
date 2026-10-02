"""📊 Status — userbot vəziyyəti (panel bölməsi)."""
import time
from datetime import datetime
from html import escape

from core.userbot_api import InlineKeyboardButton, InlineKeyboardMarkup, fmt_span, mention, static_info, telethon_version


def register(ub):
    ub.add_help("p_status", "📊 Status", "userbot vəziyyəti",
                "Hesab, ping, botun işləmə müddəti, aktiv işlər, Telegram mənbələri.", "panel")

    @ub.section("status", "📊 Status", order=10)
    async def render(sctx):
        client = ub.client
        rows = [[sctx.btn("🔄 Yenilə", "r")], sctx.nav()]
        if not client or not client.is_connected():
            return "📊 <b>Status</b>\n\n🔴 Userbot qoşulmayıb.", InlineKeyboardMarkup(inline_keyboard=rows)
        t0 = time.monotonic()
        me = await client.get_me()
        ping = (time.monotonic() - t0) * 1000
        st = await static_info()
        filler = getattr(ub.context, "depo_filler", None)
        try:
            n_src = len(filler.tg_source_ids()) if filler else 0
        except Exception:
            n_src = 0
        scan = getattr(ub.context, "depo_scan_state", {}) or {}
        text = ("📊 <b>Status</b>\n\n"
                f"👤 Hesab: {mention(me)} (@{escape(me.username or '—')})\n"
                f"🆔 <code>{me.id}</code>\n"
                f"📶 Ping: {ping:.0f} ms\n"
                f"⏱ Bot işləyir: {fmt_span(time.time() - st['bot_start'])}\n"
                f"⚙️ Aktiv .fastfetch / .clear: {ub.active_tasks()}\n"
                f"✈️ Telegram mənbələri: {n_src}" + (" · 🔎 skan gedir" if scan.get("running") else "") + "\n"
                f"🧩 Telethon {telethon_version} · Py {st['py']}\n"
                f"<i>{datetime.now():%H:%M:%S}</i>")
        return text, InlineKeyboardMarkup(inline_keyboard=rows)

    @ub.on_section("status")
    async def handle(sctx):
        await sctx.answer("🔄")
        text, kb = await render(sctx)
        await sctx.show(text, kb)
