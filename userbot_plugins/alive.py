"""⚡ .alive — userbot işləyirmi + bot nə qədərdir işləyir (fastfetch kartı)."""
import time
from datetime import datetime

from core.userbot_api import _read, ff, plain_name, static_info, telethon_version


def long_span(sec) -> str:
    """200000 → '2 gün 07:33:20', 42 → '00:00:42'"""
    sec = int(max(0, sec))
    d, rem = divmod(sec, 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    return (f"{d} gün " if d else "") + f"{h:02d}:{m:02d}:{s:02d}"


def since(ts) -> str:
    return datetime.fromtimestamp(ts).strftime("%d.%m.%Y %H:%M")


def register(ub):
    async def build() -> str:
        st = await static_info()
        now = time.time()
        bot_up = now - st["bot_start"]
        body = [("Status", "● alive"), "# Bot", ("Uptime", long_span(bot_up)), ("Başlayıb", since(st["bot_start"]))]

        attach = ub.state.get("attach_time")
        client = ub.client
        if client is not None and client.is_connected():
            t0 = time.monotonic()
            try:
                me = await client.get_me()
                ping = (time.monotonic() - t0) * 1000
                body += ["# Userbot", ("Hesab", plain_name(me)), ("Ping", f"{ping:.0f} ms")]
            except Exception:
                body += ["# Userbot", ("Hesab", "?")]
            if attach:
                body.append(("Qoşulub", long_span(now - attach)))
        else:
            body += ["# Userbot", ("Hesab", "○ qoşulmayıb")]

        sys_up = float((_read("/proc/uptime") or "0").split()[0])
        body += ["# Server", ("Uptime", long_span(sys_up)), ("Host", st["host"]),
                 ("Versiya", f"Py {st['py']} · Telethon {telethon_version}")]
        return ff(ub.title("alive"), body, logo=True, footer=f"{datetime.now():%H:%M:%S}")

    @ub.command("alive", help=("bot nə qədərdir işləyir",
                               "Botun, userbot-un və serverin işləmə müddətini (uptime), başlama vaxtını və "
                               "ping-i fastfetch kartında göstərir. 🔄 ilə yenilənir.\n\n"
                               "<b>İstifadə:</b> <code>.alive</code>"))
    async def on_alive(event):
        out = await ub.out(event, await build())

        async def refresh(o):
            await o.update(await build())
        out.refresh = refresh
