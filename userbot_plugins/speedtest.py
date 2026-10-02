"""⚡ .speedtest — fastfetch üslubunda canlı sürət testi (Cloudflare).

  .speedtest — server/userbot internet sürətini canlı ölçür, .stop ilə dayandırılır.
"""
import asyncio
import time
from contextlib import suppress
from datetime import datetime
from html import escape

from core.userbot_api import Bar, ff, logger, safe_edit, static_info

try:
    import aiohttp
except ImportError:
    aiohttp = None

# ── Parametrlər ──────────────────────────────────────────────
TEST_DURATION = 10.0          # hər mərhələnin maks. müddəti
STREAMS = 2                   # paralel axın
CHUNK = 64 * 1024
PING_COUNT = 5
SAMPLE_INTERVAL = 0.5         # qrafik nöqtəsi hər 0.5 san.
UI_INTERVAL = 1.5             # Telegram FloodWait-dən qorunmaq üçün

# Cloudflare limitləri:
#   • __down?bytes= 100 MB-dan (1e8) böyük olanda 403 qaytarır
#   • qısa müddətdə ~30-40 sorğudan sonra 429 verir və IP-ni ~1 saat bloklayır
# Ona görə sorğu sayı büdcə ilə məhdudlaşdırılır (ping + meta + aşağıdakılar ≈ 25 sorğu).
DOWN_REQ_BYTES = 50_000_000   # bir download sorğusu (limitdən aşağı)
DOWN_MAX_REQS = 12            # → maks. 600 MB
UP_REQ_BYTES = 10_000_000     # bir upload sorğusu
UP_MAX_REQS = 8               # → maks. 80 MB

# İstəyə görə: download üçün böyük statik fayl (məs. hosting provayderinin 10GB test faylı).
# Təyin olunsa, download sorğu büdcəsi olmadan tam TEST_DURATION boyunca ondan axır.
DOWN_URL = None

CF = "https://speed.cloudflare.com"

# ── Görünüş (render_fastfetch ilə eyni quruluş) ──────────────
SPARK = "▁▂▃▄▅▆▇█"
SPARK_W = 10


def _spark(vals) -> str:
    vals = vals[-SPARK_W:]
    if not vals:
        return ""
    hi = max(vals) or 1
    return "".join(SPARK[min(7, int(v / hi * 7.999))] for v in vals)


def _fmt(mbps: float) -> str:
    return f"{mbps / 1000:.2f} Gbps" if mbps >= 1000 else f"{mbps:.2f} Mbps"


def _speed_rows(label: str, s: dict) -> list:
    if s["phase"] == "wait":
        return [Bar(label, 0, "gözlənilir")]
    if s["phase"] == "run":
        out = [Bar(label, s["pct"], f"{s['pct']}% {_fmt(s['cur'])}")]
    else:
        out = [(label, f"✓ {_fmt(s['final'])}")]
    if s["hist"]:
        out.append(("Peak", f"{_spark(s['hist'])} {_fmt(s['peak'])}"))
    return out


def render(st: dict) -> str:
    if st["ping"] is None:
        ping = "ölçülür..." if st["stage"] == "ping" else "—"
    else:
        ping = f"{st['ping']:.1f} ms ±{st['jitter']:.1f}"
    server = st["server"] + (f" · {st['colo']}" if st["colo"] else "")
    body = [("ISP", st["isp"]), ("Server", server), ("Ping", ping),
            "# Download", *_speed_rows("Down", st["down"]),
            "# Upload", *_speed_rows("Up", st["up"]),
            "", ("Test", f"{int(time.monotonic() - st['t0'])} san. · Cloudflare")]
    body += [("Qeyd", note) for note in st["notes"]]
    if st["error"]:
        body.append(("Xəta", st["error"][:120]))
    return ff(st["who"], body, logo=True,
              footer=f"⚡ {UI_INTERVAL:g} san.-dən bir · {st['status']} · {datetime.now():%H:%M:%S}")


def _new_state(who: str) -> dict:
    def speed():
        return {"phase": "wait", "cur": 0.0, "final": 0.0, "pct": 0, "hist": [], "peak": 0.0}
    return {"t0": time.monotonic(), "stage": "ping", "status": "ping ölçülür", "who": who,
            "isp": "…", "server": "…", "colo": "", "ping": None, "jitter": 0.0,
            "down": speed(), "up": speed(), "error": "", "notes": []}


class _Meter:
    """Bütün axınların baytlarını sayır, hər SAMPLE_INTERVAL-da sürəti qrafikə əlavə edir."""

    def __init__(self, s: dict, max_reqs: int = 0, req_bytes: int = 0):
        self.s = s
        self.t0 = self.last = self.end = time.perf_counter()
        self.bytes = self.last_bytes = 0
        self.reqs_left = max_reqs or None          # None = büdcə yoxdur
        self.budget = max_reqs * req_bytes
        self.limited = 0                           # 429 alan axın sayı
        self.retry_after = 0
        s["phase"] = "run"

    def take(self) -> bool:
        """Yeni sorğu açmağa icazə (vaxt + büdcə)."""
        if self.expired() or self.limited:
            return False
        if self.reqs_left is None:
            return True
        if self.reqs_left <= 0:
            return False
        self.reqs_left -= 1
        return True

    def add(self, n: int):
        self.bytes += n
        now = self.end = time.perf_counter()
        dt = now - self.last
        if dt >= SAMPLE_INTERVAL:
            cur = (self.bytes - self.last_bytes) * 8 / dt / 1e6
            self.s["cur"] = cur
            self.s["hist"].append(cur)
            self.s["peak"] = max(self.s["peak"], cur)
            self.last, self.last_bytes = now, self.bytes
        frac = (now - self.t0) / TEST_DURATION
        if self.budget:
            frac = max(frac, self.bytes / self.budget)
        self.s["pct"] = min(100, int(frac * 100))

    def hit_429(self, r):
        self.limited += 1
        with suppress(Exception):
            self.retry_after = max(self.retry_after, int(r.headers.get("Retry-After", 0)))

    def expired(self) -> bool:
        return time.perf_counter() - self.t0 >= TEST_DURATION

    def finish(self):
        # yalnız real ötürmə müddəti — boş qalan vaxt ortalamanı aşağı salmasın
        dt = max(self.end - self.t0, 1e-6)
        self.s["final"] = self.bytes * 8 / dt / 1e6
        self.s["peak"] = max(self.s["peak"], self.s["final"])
        self.s["secs"] = dt
        self.s["pct"] = 100
        self.s["phase"] = "done"


async def _download_worker(session, m: _Meter):
    url = DOWN_URL or f"{CF}/__down?bytes={DOWN_REQ_BYTES}"
    while m.take():
        async with session.get(url) as r:
            if r.status == 429:
                m.hit_429(r)
                return
            r.raise_for_status()
            async for chunk in r.content.iter_chunked(CHUNK):
                m.add(len(chunk))
                if m.expired():
                    return


async def _upload_worker(session, m: _Meter, payload: bytes):
    async def gen():
        sent = 0
        while sent < UP_REQ_BYTES and not m.expired():
            size = min(CHUNK, UP_REQ_BYTES - sent)
            sent += size
            m.add(size)
            yield payload[:size]

    while m.take():
        async with session.post(f"{CF}/__up", data=gen()) as r:
            if r.status == 429:
                m.hit_429(r)
                return
            r.raise_for_status()
            await r.read()


async def _json(session, url) -> dict:
    with suppress(Exception):
        async with session.get(url) as r:
            if r.status == 200:
                data = await r.json(content_type=None)
                return data if isinstance(data, dict) else {}
    return {}


async def _fill_meta(session, st: dict):
    """ISP / şəhər / data-center. Cloudflare /meta boş qaytarsa — ehtiyat mənbələr."""
    meta = await _json(session, f"{CF}/meta")
    isp = meta.get("asOrganization") or ""
    city, country = meta.get("city") or "", meta.get("country") or ""
    colo = meta.get("colo") or ""

    if not colo or not country:
        with suppress(Exception):
            async with session.get(f"{CF}/cdn-cgi/trace") as r:
                trace = dict(l.split("=", 1) for l in (await r.text()).splitlines() if "=" in l)
            colo = colo or trace.get("colo", "")
            country = country or trace.get("loc", "")

    if not isp or not city:
        info = await _json(session, "https://ipinfo.io/json")
        org = info.get("org", "")                       # "AS12345 Şirkət adı"
        isp = isp or (org.split(" ", 1)[1] if org.startswith("AS") and " " in org else org)
        city = city or info.get("city", "")
        country = country or info.get("country", "")

    st["isp"] = isp or "Bilinmir"
    st["server"] = ", ".join(x for x in (city, country) if x) or "Cloudflare Edge"
    st["colo"] = colo


def _check_limit(st: dict, m: _Meter, phase: str):
    wait = f", ~{max(1, m.retry_after // 60)} dəq. blok" if m.retry_after else ""
    if m.limited and m.bytes == 0:
        raise RuntimeError(f"Cloudflare {phase} sorğularını blokladı (429{wait}) — sonra yenidən yoxlayın")
    secs = m.end - m.t0
    if m.limited:
        st["notes"].append(f"{phase}: 429 limiti{wait}, {secs:.1f} san. ölçüldü")
    elif secs < TEST_DURATION - 0.5:
        st["notes"].append(f"{phase}: {m.bytes / 1e6:.0f} MB büdcə {secs:.1f} san.-də bitdi")


async def _measure(st: dict):
    timeout = aiohttp.ClientTimeout(total=TEST_DURATION * 2 + 40, sock_read=20)
    connector = aiohttp.TCPConnector(limit=STREAMS + 2)
    async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
        # 1. Ping (ilk sorğu TLS handshake daxildir — atılır)
        pings = []
        for _ in range(PING_COUNT + 1):
            t = time.perf_counter()
            async with session.get(f"{CF}/__down?bytes=0") as r:
                await r.read()
            pings.append((time.perf_counter() - t) * 1000)
        pings = pings[1:]
        st["ping"] = min(pings)
        st["jitter"] = sum(abs(a - b) for a, b in zip(pings, pings[1:])) / max(1, len(pings) - 1)

        await _fill_meta(session, st)

        # 2. Download — STREAMS paralel axın, TEST_DURATION boyunca
        st["stage"], st["status"] = "download", "endirilir"
        m = _Meter(st["down"], 0 if DOWN_URL else DOWN_MAX_REQS, DOWN_REQ_BYTES)
        await asyncio.gather(*(_download_worker(session, m) for _ in range(STREAMS)))
        m.finish()
        _check_limit(st, m, "Down")

        # 3. Upload — eyni qayda ilə
        st["stage"], st["status"] = "upload", "göndərilir"
        m = _Meter(st["up"], UP_MAX_REQS, UP_REQ_BYTES)
        payload = b"\x00" * CHUNK
        await asyncio.gather(*(_upload_worker(session, m, payload) for _ in range(STREAMS)))
        m.finish()
        _check_limit(st, m, "Up")


async def _ui_loop(out, st: dict):
    while True:
        await asyncio.sleep(UI_INTERVAL)
        with suppress(Exception):
            await out.update(render(st))


async def _run(out):
    info = await static_info()
    st = _new_state(f"{info['user']}@{info['host']}")
    await out.update(render(st))
    ui = asyncio.create_task(_ui_loop(out, st))
    try:
        await _measure(st)
        st["status"] = "bitdi"
    except asyncio.CancelledError:
        st["status"] = "dayandırıldı"
        raise
    except Exception as e:
        st["status"] = "xəta"
        st["error"] = str(e) or type(e).__name__
        logger.info(f".speedtest xətası: {e}")
    finally:
        ui.cancel()
        with suppress(Exception):
            await out.update(render(st), rows=[[out.btn("🔁 Yenidən", "again")]])


def register(ub):
    @ub.command("speedtest", pattern=r"^\.speedtest$",
                help=("canlı şəbəkə sürətini yoxla",
                      "Ping/jitter, Download və Upload — fastfetch görünüşündə, zolaq və sürət qrafiki ilə "
                      "canlı yenilənir. Hər mərhələ maks. 10 san. (Cloudflare limitlərinə uyğun)\n\n"
                      "<b>İstifadə:</b>\n• <code>.speedtest</code>\n• kartda ⏹ və ya <code>.stop</code> — dayandır"))
    async def on_speedtest(event):
        if aiohttp is None:
            await safe_edit(event, "❌ <code>aiohttp</code> yoxdur. Quraşdırmaq üçün: <code>pip install aiohttp</code>")
            return
        out = await ub.out(event, ff(ub.title("speedtest"), [("Status", "● başlayır")]))

        @out.on("again")
        async def _(o, cb):
            if o.running():
                return
            o.rows = []
            o.track("st", _run(o))

        out.track("st", _run(out))
