"""⚡ .speedtest — Canlı progress və hər saniyə yenilənən sürət testi modulu.

  .speedtest — cari server/userbot internet sürətini canlı ölçür.
"""
import asyncio
from html import escape
import time

from core.userbot_api import logger, safe_edit

try:
    import aiohttp
except ImportError:
    aiohttp = None

# Ölçü parametrləri (Baytla)
DOWNLOAD_BYTES = 35_000_000   # ~35 MB yükləmə testi
UPLOAD_BYTES = 15_000_000     # ~15 MB göndərmə testi


def _progress_bar(percent: int, length: int = 10) -> str:
    filled = int(length * (percent / 100))
    return "▰" * filled + "▱" * (length - filled)


def register(ub):
    @ub.command("speedtest", pattern=r"^\.speedtest$",
                help=("canlı şəbəkə sürətini yoxla",
                      "Hər saniyə real vaxtda yenilənən Download/Upload və Ping göstəricilərini ölçür."))
    async def on_speedtest(event):
        if aiohttp is None:
            await safe_edit(
                event,
                "❌ <code>aiohttp</code> kitabxanası tapılmadı.\n"
                "Quraşdırmaq üçün terminalda yazın: <code>pip install aiohttp</code>"
            )
            return

        await safe_edit(event, "⚡ <i>Sürət testi başladılır, server axtarılır...</i>")

        state = {
            "stage": "init",      # ping, download, upload, done
            "running": True,
            "ping": 0.0,
            "isp": "Bilinmir",
            "server": "Bilinmir",
            "down_cur": 0.0,
            "down_pct": 0,
            "down_final": 0.0,
            "up_cur": 0.0,
            "up_pct": 0,
            "up_final": 0.0,
        }

        async def ui_monitor():
            """Hər saniyə mesajı canlı göstəricilərlə yeniləyən arxa plan prosesi."""
            while state["running"]:
                await asyncio.sleep(1.0)
                if not state["running"]:
                    break

                stage = state["stage"]
                if stage == "download":
                    bar = _progress_bar(state["down_pct"])
                    text = (
                        "⚡ <b>Canlı Sürət Testi</b>\n\n"
                        f"📡 <b>ISP:</b> <code>{escape(state['isp'])}</code>\n"
                        f"🌐 <b>Server:</b> <code>{escape(state['server'])}</code>\n"
                        f"📶 <b>Ping:</b> <code>{state['ping']:.1f} ms</code>\n\n"
                        f"📥 <b>Download:</b> <code>{state['down_cur']:.2f} Mbps</code>\n"
                        f"<code>{bar} {state['down_pct']}%</code>\n\n"
                        "📤 <b>Upload:</b> <i>Gözlənilir...</i>"
                    )
                    try:
                        await safe_edit(event, text)
                    except Exception:
                        pass

                elif stage == "upload":
                    bar = _progress_bar(state["up_pct"])
                    text = (
                        "⚡ <b>Canlı Sürət Testi</b>\n\n"
                        f"📡 <b>ISP:</b> <code>{escape(state['isp'])}</code>\n"
                        f"🌐 <b>Server:</b> <code>{escape(state['server'])}</code>\n"
                        f"📶 <b>Ping:</b> <code>{state['ping']:.1f} ms</code>\n\n"
                        f"📥 <b>Download:</b> <code>{state['down_final']:.2f} Mbps</code> ✅\n\n"
                        f"📤 <b>Upload:</b> <code>{state['up_cur']:.2f} Mbps</code>\n"
                        f"<code>{bar} {state['up_pct']}%</code>"
                    )
                    try:
                        await safe_edit(event, text)
                    except Exception:
                        pass

        monitor_task = asyncio.create_task(ui_monitor())

        try:
            timeout = aiohttp.ClientTimeout(total=45)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                # 1. Ping və Provayder (ISP/Server) məlumatı
                t_start = time.perf_counter()
                async with session.get("https://speed.cloudflare.com/__down?bytes=0") as r:
                    await r.read()
                state["ping"] = (time.perf_counter() - t_start) * 1000

                try:
                    async with session.get("https://speed.cloudflare.com/meta") as r:
                        meta = await r.json()
                        state["isp"] = meta.get("asOrganization", "Bilinmir")
                        city = meta.get("city", "")
                        country = meta.get("country", "")
                        state["server"] = f"{city}, {country}" if city else "Cloudflare Edge"
                except Exception:
                    state["isp"] = "Avtomatik"
                    state["server"] = "Cloudflare CDN"

                # 2. Download Testi (Canlı axın)
                state["stage"] = "download"
                down_bytes = 0
                down_start = time.perf_counter()
                last_time = down_start
                last_bytes = 0

                down_url = f"https://speed.cloudflare.com/__down?bytes={DOWNLOAD_BYTES}"
                async with session.get(down_url) as r:
                    async for chunk in r.content.iter_chunked(64 * 1024):
                        down_bytes += len(chunk)
                        now = time.perf_counter()
                        delta = now - last_time
                        if delta >= 0.8:
                            # Cari saniyədəki sürət
                            state["down_cur"] = ((down_bytes - last_bytes) * 8) / (delta * 1_000_000)
                            state["down_pct"] = min(100, int((down_bytes / DOWNLOAD_BYTES) * 100))
                            last_time = now
                            last_bytes = down_bytes

                down_duration = time.perf_counter() - down_start
                state["down_final"] = (down_bytes * 8) / (down_duration * 1_000_000)

                # 3. Upload Testi (Canlı axın)
                state["stage"] = "upload"
                up_bytes = 0
                up_start = time.perf_counter()
                last_up_time = up_start
                last_up_bytes = 0

                async def upload_generator():
                    nonlocal up_bytes, last_up_time, last_up_bytes
                    chunk = b"\x00" * (64 * 1024)
                    remaining = UPLOAD_BYTES
                    while remaining > 0:
                        size = min(len(chunk), remaining)
                        remaining -= size
                        up_bytes += size
                        now = time.perf_counter()
                        delta = now - last_up_time
                        if delta >= 0.8:
                            state["up_cur"] = ((up_bytes - last_up_bytes) * 8) / (delta * 1_000_000)
                            state["up_pct"] = min(100, int((up_bytes / UPLOAD_BYTES) * 100))
                            last_up_time = now
                            last_up_bytes = up_bytes
                        yield chunk[:size]

                await session.post("https://speed.cloudflare.com/__up", data=upload_generator())
                up_duration = time.perf_counter() - up_start
                state["up_final"] = (up_bytes * 8) / (up_duration * 1_000_000)

            # Test tamamlandı
            state["running"] = False
            monitor_task.cancel()

            final_text = (
                "🚀 <b>Speedtest Nəticəsi:</b>\n\n"
                f"📡 <b>ISP:</b> <code>{escape(state['isp'])}</code>\n"
                f"🌐 <b>Server:</b> <code>{escape(state['server'])}</code>\n\n"
                f"📶 <b>Ping:</b> <code>{state['ping']:.1f} ms</code>\n"
                f"📥 <b>Download:</b> <code>{state['down_final']:.2f} Mbps</code>\n"
                f"📤 <b>Upload:</b> <code>{state['up_final']:.2f} Mbps</code>"
            )
            await safe_edit(event, final_text)

        except asyncio.CancelledError:
            state["running"] = False
            monitor_task.cancel()
            raise
        except Exception as e:
            state["running"] = False
            monitor_task.cancel()
            logger.info(f".speedtest xətası: {e}")
            await safe_edit(event, f"❌ <b>Speedtest xətası:</b> <code>{escape(str(e))[:200]}</code>")
            