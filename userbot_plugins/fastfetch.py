"""🖥 .fastfetch — sistem məlumatı (3 saniyədən bir yenilənir) + panelə 🖥 Sistem bölməsi."""
import asyncio
import time

from core.userbot_api import (FF_DEFAULT, FF_INTERVAL, FF_MAX, InlineKeyboardMarkup,
                              LiveStats, logger, render_fastfetch, safe_edit, static_info)


def register(ub):
    @ub.command("fastfetch", pattern=r"^\.fastfetch(?:\s+(\d+))?$",
                help=("sistem məlumatı (canlı)",
                      "OS, host, kernel, uptime, paketlər, CPU, GPU, temperatur, load, CPU / RAM / Swap / Disk "
                      "zolaqları, şəbəkə sürəti, botun RAM-ı. <b>3 saniyədən bir</b> yenilənir.\n\n"
                      "<b>İstifadə:</b>\n• <code>.fastfetch</code> — 60 san.\n• <code>.fastfetch 300</code> — 5 dəq.\n"
                      "• <code>.fastfetch 0</code> — <code>.stop</code>-a qədər (maks. 1 saat)\n\n"
                      "<i>Paneldə:</i> 🖥 Sistem → ▶️ Canlı"))
    async def on_fastfetch(event):
        arg = (event.pattern_match.group(1) or "").strip()
        duration = int(arg) if arg.isdigit() else FF_DEFAULT
        duration = FF_MAX if duration == 0 else min(duration, FF_MAX)

        async def loop():
            st = await static_info()
            live = LiveStats()
            await asyncio.sleep(0.5)
            end = time.monotonic() + duration
            try:
                while True:
                    remaining = end - time.monotonic()
                    left = f"{int(remaining)} san. qalıb" if remaining > 0 else "dayandı"
                    await safe_edit(event, render_fastfetch(st, live.sample(), left))
                    if remaining <= 0:
                        break
                    await asyncio.sleep(min(FF_INTERVAL, max(0.5, remaining)))
            except asyncio.CancelledError:
                try:
                    await safe_edit(event, render_fastfetch(st, live.sample(), "dayandırıldı"))
                except Exception:
                    pass
                raise
            except Exception as e:
                logger.info(f".fastfetch xətası: {e}")

        ub.track(event.chat_id, "ff", asyncio.create_task(loop()))

    ub.add_help("p_sys", "🖥 Sistem", "server məlumatı",
                "Fastfetch-in panel versiyası: 🔄 ilə yenilə, ▶️ Canlı — 60 san. ərzində 3 saniyədən bir.", "panel")

    def sys_kb(sctx, live=False):
        first = [sctx.btn("⏹ Dayandır", "stop")] if live else \
            [sctx.btn("🔄 Yenilə", "refresh"), sctx.btn("▶️ Canlı (60 san.)", "live")]
        return InlineKeyboardMarkup(inline_keyboard=[first, sctx.nav()])

    @ub.section("sys", "🖥 Sistem", order=20)
    async def render_sys(sctx):
        st = await static_info()
        live = LiveStats()
        await asyncio.sleep(0.5)
        return render_fastfetch(st, live.sample(), "bir dəfəlik"), sys_kb(sctx)

    async def sys_live(ub_, tok, iid):
        st = await static_info()
        live = LiveStats()
        await asyncio.sleep(0.5)
        end = time.monotonic() + FF_DEFAULT
        from core.userbot_api import SectionCtx
        sctx = SectionCtx(ub_, "sys", tok, None, "", [])
        while ub_.views.get(tok) == "sys:live":
            remaining = end - time.monotonic()
            if remaining <= 0:
                await ub_.edit_panel(iid, render_fastfetch(st, live.sample(), "bitdi"), sys_kb(sctx))
                break
            await ub_.edit_panel(iid, render_fastfetch(st, live.sample(), f"{int(remaining)} san. qalıb"),
                                 sys_kb(sctx, live=True))
            await asyncio.sleep(FF_INTERVAL)

    @ub.on_section("sys")
    async def handle_sys(sctx):
        if sctx.action == "live":
            await sctx.answer("▶️ Canlı rejim")
            old = ub.ptasks.pop(sctx.tok, None)
            if old and not old.done():
                old.cancel()
            ub.views[sctx.tok] = "sys:live"
            ub.ptasks[sctx.tok] = asyncio.create_task(sys_live(ub, sctx.tok, sctx.iid))
        else:
            await sctx.answer("⏹" if sctx.action == "stop" else "🔄")
            text, kb = await render_sys(sctx)
            await sctx.show(text, kb)
