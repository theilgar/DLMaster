"""⏹ .stop — bu çatdakı .fastfetch / .clear işlərini dayandır."""
import asyncio

from core.userbot_api import safe_edit


def register(ub):
    @ub.command("stop", help=("işləyəni dayandır",
                              "Bu çatdakı <code>.fastfetch</code> və <code>.clear</code>-i dayandırır.\n\n"
                              "<b>İstifadə:</b> <code>.stop</code>"))
    async def on_stop(event):
        n = ub.stop_tasks(event.chat_id)
        await safe_edit(event, "⏹ Dayandırıldı." if n else "ℹ️ Bu çatda işləyən komanda yoxdur.")
        await asyncio.sleep(2)
        try:
            await event.delete()
        except Exception:
            pass
