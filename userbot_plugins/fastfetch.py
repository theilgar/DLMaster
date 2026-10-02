"""🖥 .fastfetch [san] / .ff [san] — canlı sistem məlumatı, inline kart (⏹ / 🔁 düymələri ilə).

  .ff          — 60 san. canlı (hər 3 san. yenilənir)
  .ff 300      — 300 san. canlı (maks. 3600)
  .ff 0        — bir dəfəlik snapshot
"""
import asyncio
import time

from core.userbot_api import FF_DEFAULT, FF_INTERVAL, FF_MAX, LiveStats, Out, render_fastfetch, static_info


def register(ub):
    @ub.command("fastfetch", pattern=r"^\.(?:fastfetch|ff)(?:\s+(\d+))?$",
                help=("sistem məlumatı (canlı)",
                      "Server haqqında fastfetch görünüşündə məlumat: OS, CPU, RAM, disk, şəbəkə, bot.\n\n"
                      "<b>İstifadə:</b>\n• <code>.ff</code> — 60 san. canlı\n"
                      f"• <code>.ff 300</code> — 300 san. (maks. {FF_MAX})\n• <code>.ff 0</code> — snapshot\n"
                      "• kartda ⏹ və ya <code>.stop</code> — dayandır"))
    async def on_ff(event):
        arg = event.pattern_match.group(1)
        secs = min(FF_MAX, int(arg)) if arg is not None else FF_DEFAULT
        st = await static_info()
        live = LiveStats()
        await asyncio.sleep(0.5)                      # ilk CPU/şəbəkə ölçüsü boş olmasın
        out = Out(ub, event)
        again = [[out.btn("🔁 Canlı", "again")]]

        async def loop(dur):
            end = time.monotonic() + dur
            try:
                while (left := end - time.monotonic()) > 0:
                    await asyncio.sleep(FF_INTERVAL)
                    await out.update(render_fastfetch(st, live.sample(), f"{int(left)} san. qalıb"), rows=[])
            except asyncio.CancelledError:
                await out.update(render_fastfetch(st, live.sample(), "dayandırıldı"), rows=again)
                raise
            await out.update(render_fastfetch(st, live.sample(), "bitdi"), rows=again)

        @out.on("again")
        async def _(o, cb):
            if not o.running():
                o.track("ff", loop(secs or FF_DEFAULT))

        async def refresh(o):
            await o.update(render_fastfetch(st, live.sample(), "snapshot"))
        out.refresh = refresh

        await out.open(render_fastfetch(st, live.sample(), "başlayır" if secs else "snapshot"),
                       [] if secs else again)
        if secs:
            out.track("ff", loop(secs))
