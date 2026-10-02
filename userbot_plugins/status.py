"""📊 Status — userbot vəziyyəti (panel bölməsi + .status kartı), fastfetch görünüşündə."""
import time
from datetime import datetime

from core.userbot_api import InlineKeyboardMarkup, ff, fmt_span, plain_name, static_info, telethon_version


def register(ub):
    ub.add_help("p_status", "📊 Status", "userbot vəziyyəti",
                "Hesab, ping, botun işləmə müddəti, aktiv işlər, Telegram mənbələri.", "panel")

    async def build() -> str:
        client = ub.client
        if not client or not client.is_connected():
            return ff(ub.title("status"), [("Userbot", "○ qoşulmayıb")], logo=True)
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
        body = [
            ("Hesab", plain_name(me)),
            ("ID", me.id),
            ("Ping", f"{ping:.0f} ms"),
            ("Uptime", fmt_span(time.time() - st["bot_start"])),
            "# İşlər",
            ("Aktiv", f"{ub.active_tasks()} fon işi"),
            ("Kart", f"{len(ub.cards)} açıq"),
            ("Mənbə", f"{n_src} Telegram" + (" · skan gedir" if scan.get("running") else "")),
            ("Plugin", f"{len(ub.plugin_files)} fayl · {len(ub.commands)} komanda"),
            "# Versiya",
            ("Telethon", telethon_version),
            ("Python", st["py"]),
        ]
        return ff(ub.title("status"), body, logo=True, footer=f"{datetime.now():%H:%M:%S}")

    # ── panel bölməsi ──
    @ub.section("status", "📊 Status", order=10)
    async def render(sctx):
        rows = [[sctx.btn("🔄 Yenilə", "r")], sctx.nav()]
        return await build(), InlineKeyboardMarkup(inline_keyboard=rows)

    @ub.on_section("status")
    async def handle(sctx):
        await sctx.answer("🔄")
        text, kb = await render(sctx)
        await sctx.show(text, kb)

    # ── .status — istənilən çatda kart ──
    @ub.command("status", help=("userbot statusu (kart)",
                                "Statusu inline kart kimi göstərir (🔄 ilə yenilənir).\n\n"
                                "<b>İstifadə:</b> <code>.status</code>"))
    async def on_status(event):
        out = await ub.out(event, await build())

        async def refresh(o):
            await o.update(await build())
        out.refresh = refresh
