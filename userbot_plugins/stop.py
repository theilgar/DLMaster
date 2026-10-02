"""⏹ .stop — bu çatdakı fon işlərini (.fastfetch, .speedtest, .purge, .clear ...) dayandır."""
from core.userbot_api import ff


def register(ub):
    @ub.command("stop", help=("işləyəni dayandır",
                              "Bu çatdakı <code>.fastfetch</code>, <code>.speedtest</code>, <code>.purge</code>, "
                              "<code>.clear</code> işlərini dayandırır. Kartlardakı ⏹ düyməsi də eyni işi görür.\n\n"
                              "<b>İstifadə:</b> <code>.stop</code>"))
    async def on_stop(event):
        n = ub.stop_tasks(event.chat_id)
        body = [("Nəticə", f"● {n} iş dayandırıldı" if n else "○ işləyən komanda yoxdur")]
        out = await ub.out(event, ff(ub.title("stop"), body))
        await out.close(3)
