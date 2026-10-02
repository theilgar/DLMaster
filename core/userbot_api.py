"""
🧩 Userbot plugin çərçivəsi — core/userbot_api.py

Telethon userbot-un "mini plugin sistemi". Host plugin (plugins/userbot_tools_plugin.py) bir UB
obyekti qurur, userbot_plugins/ qovluğundakı hər .py faylını yükləyib onun register(ub) funksiyasını
çağırır. Hər komanda ayrıca fayldır.

Komanda faylı nümunəsi (userbot_plugins/ping.py):

    from core.userbot_api import safe_edit

    def register(ub):
        @ub.command("ping", help=("yoxlama", "<code>.ping</code> — cavab verir"))
        async def _(event):
            await safe_edit(event, "🏓 pong")

API (ub):
  ub.context            — bot context (bot, dp, creator_id, depo_filler, ...)
  ub.client             — Telethon client (qoşulandan sonra; register vaxtı None ola bilər)
  ub.state              — pluginlər arası paylaşılan dict (məs. mute reyestri)
  ub.is_creator(uid)
  ub.command(name, pattern=None, outgoing=True, help=None, category="acc")(fn)
                        — .<name> komandası. pattern verilməsə "^\\.name$". help=(qısa, ətraflı)
  ub.raw(event_builder)(fn)          — xam Telethon handler (məs. gələn mesajları silmək)
  ub.help_category(key, title, sub, order=50)
  ub.add_help(key, button, short, long, category="acc")
  ub.section(key, label, order=50)(render)   — admin panelinə bölmə; render(sctx)->(mətn, rows)
  ub.on_section(key)(handle)                 — həmin bölmənin düymələrini idarə edir
  ub.track(chat_id, kind, task) / ub.stop_tasks(chat_id) / ub.active_tasks()
  ub.bot / ub.dp / ub.creator_id
  await ub.out(event, text, rows=None, inline=True) -> Out
                        — komanda cavabını bot vasitəsilə inline kart kimi göndərir (düymələrlə);
                          inline mümkün deyilsə avtomatik sadə mesaja (komandanın özünü redaktə) keçir.
  ub.title("status")    — fastfetch başlığı: "status@<bot_username>"

Kart (Out):
  await out.update(text, rows=KEEP)   — kartı yenilə (inline: bot edit, mətn: mesaj edit)
  out.btn(text, action) + @out.on(action) async def h(out, cb)  — karta xüsusi düymə
  out.track(kind, coro)               — fon işi (kartda ⏹ Dayandır çıxır, .stop da işləyir)
  out.refresh = async fn(out)         — 🔄 düyməsi
  await out.close(delay=0)

fastfetch görünüşü:
  ff(title, body, footer=None, logo=False)
     body: ("Açar", dəyər) | Bar("Açar", faiz, "etiket") | "# Bölmə" | "sətir"

Panel bölməsi (sctx — SectionCtx):
  sctx.ub, sctx.tok, sctx.menu (chat_id, reply_uid, msg_id), sctx.action, sctx.args, sctx.cb, sctx.iid
  sctx.btn(text, *parts)   — bu bölməyə yönələn düymə (callback: ubm:<tok>:<key>:<parts>)
  sctx.nav(label="⬅️ Menyu") — ana menyuya qayıtma sətri
  sctx.answer(text=None, alert=False)
  await sctx.show(text, rows) — paneli yenilə
  await sctx.client(), await sctx.chat() — userbot client / bölmənin bağlı olduğu çat
"""
import asyncio
import getpass
import glob
import json
import logging
import os
import platform
import re
import shutil
import sys
import time
from datetime import datetime
from html import escape

from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from telethon import events, utils
from telethon import __version__ as telethon_version
from telethon.errors import FloodWaitError, MessageNotModifiedError, UserNotParticipantError
from telethon.tl.functions.channels import (EditBannedRequest, GetFullChannelRequest, GetParticipantRequest)
from telethon.tl.functions.messages import GetFullChatRequest, SendReactionRequest
from telethon.tl.functions.users import GetFullUserRequest
from telethon.tl.types import (
    Channel, Chat, ChatBannedRights, ReactionEmoji, User,
    ChannelParticipantAdmin, ChannelParticipantBanned, ChannelParticipantCreator, ChannelParticipantLeft,
    ChannelParticipantSelf, ChannelParticipantsBanned, ChannelParticipantsKicked,
    ChatParticipantAdmin, ChatParticipantCreator,
    UserStatusLastMonth, UserStatusLastWeek, UserStatusOffline, UserStatusOnline, UserStatusRecently,
)

from core.database import get_db

logger = logging.getLogger(__name__)

FF_INTERVAL = 3
FF_DEFAULT = 60
FF_MAX = 3600

ADMIN_RIGHTS = [
    ("change_info", "Qrup məlumatını dəyişmək"),
    ("post_messages", "Kanalda paylaşım"),
    ("edit_messages", "Başqasının mesajını redaktə"),
    ("delete_messages", "Mesaj silmək"),
    ("ban_users", "Ban / məhdudlaşdırmaq"),
    ("invite_users", "Dəvət etmək / link"),
    ("pin_messages", "Mesaj sabitləmək"),
    ("manage_topics", "Mövzuları idarə"),
    ("manage_call", "Səsli söhbəti idarə"),
    ("post_stories", "Hekayə paylaşmaq"),
    ("edit_stories", "Hekayə redaktə"),
    ("delete_stories", "Hekayə silmək"),
    ("add_admins", "Admin təyin etmək"),
    ("anonymous", "Anonim qalmaq"),
]
BANNED_RIGHTS = [
    ("send_messages", "Mesaj"), ("send_photos", "Şəkil"), ("send_videos", "Video"),
    ("send_roundvideos", "Dairəvi video"), ("send_audios", "Musiqi"), ("send_voices", "Səs"),
    ("send_docs", "Fayl"), ("send_stickers", "Stiker"), ("send_gifs", "GIF"), ("send_inline", "Inline"),
    ("send_polls", "Sorğu"), ("embed_links", "Link önizləmə"), ("invite_users", "Dəvət"),
    ("pin_messages", "Sabitləmə"), ("change_info", "Məlumat dəyişmə"),
]

def fmt_dt(dt) -> str:
    if not dt:
        return "—"
    try:
        return dt.astimezone().strftime("%d.%m.%Y %H:%M")
    except Exception:
        return str(dt)


def fmt_span(sec) -> str:
    sec = int(sec)
    d, rem = divmod(sec, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    parts = ([f"{d}g"] if d else []) + ([f"{h}s"] if h or d else []) + [f"{m}d"]
    return " ".join(parts)


def fmt_bytes(n, digits=1) -> str:
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.{digits if unit not in ('B', 'KB') else 0}f}{unit}"
        n /= 1024


def bar(pct, width=10) -> str:
    pct = max(0.0, min(100.0, pct))
    fill = int(round(pct / 100 * width))
    return "█" * fill + "░" * (width - fill)


def status_text(status) -> str:
    if isinstance(status, UserStatusOnline):
        return "🟢 onlayn"
    if isinstance(status, UserStatusOffline):
        return f"⚪ son görülmə: {fmt_dt(status.was_online)}"
    if isinstance(status, UserStatusRecently):
        return "🟡 bu yaxınlarda"
    if isinstance(status, UserStatusLastWeek):
        return "🟠 bu həftə"
    if isinstance(status, UserStatusLastMonth):
        return "🔴 bu ay"
    return "⚫ gizli / çoxdan"


def mention(user) -> str:
    name = " ".join(x for x in (getattr(user, "first_name", None), getattr(user, "last_name", None)) if x) \
        or getattr(user, "title", None) or "Silinmiş hesab"
    if isinstance(user, User):
        return f'<a href="tg://user?id={user.id}">{escape(name)}</a>'
    return escape(name)


async def safe_edit(event_or_msg, text: str):
    try:
        return await event_or_msg.edit(text, parse_mode="html", link_preview=False)
    except MessageNotModifiedError:
        return None
    except FloodWaitError as e:
        await asyncio.sleep(e.seconds + 1)
        return None


# ───────────────────────── 🖥 sistem məlumatı ─────────────────────────
def _read(path, default=""):
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            return f.read().strip()
    except OSError:
        return default


def _os_name() -> str:
    data = {}
    for line in _read("/etc/os-release").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            data[k] = v.strip('"')
    name = data.get("PRETTY_NAME") or data.get("NAME") or f"{platform.system()} {platform.release()}"
    return f"{name} {platform.machine()}"


def _host() -> str:
    vendor = _read("/sys/devices/virtual/dmi/id/sys_vendor")
    product = _read("/sys/devices/virtual/dmi/id/product_name") or _read("/sys/firmware/devicetree/base/model")
    s = f"{vendor} {product}".strip().replace("\x00", "")
    return s if s and "To be filled" not in s else platform.node()


def _cpu_model() -> str:
    model, cores = "", os.cpu_count() or 1
    for line in _read("/proc/cpuinfo").splitlines():
        if line.lower().startswith(("model name", "hardware", "cpu model")):
            model = line.split(":", 1)[1].strip()
            break
    for junk in ("(R)", "(TM)", "(tm)", " CPU", " Processor", "-Core"):
        model = model.replace(junk, "")
    model = model.split("@")[0].strip() or platform.processor() or "?"
    freq = _read("/sys/devices/system/cpu/cpu0/cpufreq/cpuinfo_max_freq")
    ghz = f" @ {int(freq) / 1e6:.2f}GHz" if freq.isdigit() else ""
    return f"{' '.join(model.split())} ({cores}){ghz}"


def _cpu_times():
    parts = _read("/proc/stat").splitlines()[0].split()[1:]
    vals = [int(x) for x in parts]
    idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
    return idle, sum(vals)


def _cur_freq() -> str:
    freqs = []
    for p in glob.glob("/sys/devices/system/cpu/cpu[0-9]*/cpufreq/scaling_cur_freq"):
        v = _read(p)
        if v.isdigit():
            freqs.append(int(v))
    return f"{sum(freqs) / len(freqs) / 1e6:.2f}GHz" if freqs else ""


def _temp() -> str:
    best = None
    for d in glob.glob("/sys/class/hwmon/hwmon*"):
        name = _read(f"{d}/name")
        if name in ("coretemp", "k10temp", "zenpower", "cpu_thermal", "acpitz", "soc_thermal"):
            v = _read(f"{d}/temp1_input")
            if v.lstrip("-").isdigit():
                best = int(v) / 1000
                if name in ("coretemp", "k10temp", "zenpower"):
                    break
    if best is None:
        temps = [int(_read(p)) / 1000 for p in glob.glob("/sys/class/thermal/thermal_zone*/temp")
                 if _read(p).lstrip("-").isdigit()]
        best = max(temps) if temps else None
    return f"{best:.0f}°C" if best is not None else ""


def _meminfo() -> dict:
    out = {}
    for line in _read("/proc/meminfo").splitlines():
        k, _, v = line.partition(":")
        if v.strip().split():
            out[k] = int(v.strip().split()[0]) * 1024
    return out


def _net_bytes():
    rx = tx = 0
    for line in _read("/proc/net/dev").splitlines()[2:]:
        iface, _, data = line.partition(":")
        iface = iface.strip()
        if iface == "lo" or iface.startswith(("docker", "veth", "br-", "virbr")):
            continue
        f = data.split()
        if len(f) >= 9:
            rx += int(f[0])
            tx += int(f[8])
    return rx, tx


def _proc_rss() -> int:
    for line in _read("/proc/self/status").splitlines():
        if line.startswith("VmRSS:"):
            return int(line.split()[1]) * 1024
    return 0


async def _run(cmd: str, timeout=8) -> str:
    try:
        p = await asyncio.create_subprocess_shell(cmd, stdout=asyncio.subprocess.PIPE,
                                                  stderr=asyncio.subprocess.DEVNULL)
        out, _ = await asyncio.wait_for(p.communicate(), timeout)
        return out.decode(errors="ignore").strip()
    except Exception:
        return ""


_static = {}


async def static_info() -> dict:
    """Dəyişməyən hissə (OS, paketlər, GPU...) — bir dəfə hesablanır."""
    if _static:
        return _static
    pkgs = []
    for cmd, label in (("pacman -Qq 2>/dev/null | wc -l", "pacman"),
                       ("dpkg-query -f '.\\n' -W 2>/dev/null | wc -l", "dpkg"),
                       ("rpm -qa 2>/dev/null | wc -l", "rpm"),
                       ("flatpak list 2>/dev/null | wc -l", "flatpak"),
                       ("snap list 2>/dev/null | tail -n +2 | wc -l", "snap")):
        if shutil.which(cmd.split()[0]):
            n = await _run(cmd)
            if n.isdigit() and int(n) > 0:
                pkgs.append(f"{n} ({label})")
    gpu = ""
    if shutil.which("lspci"):
        for line in (await _run("lspci")).splitlines():
            if any(k in line for k in ("VGA compatible", "3D controller", "Display controller")):
                g = line.split(":", 2)[-1].strip()
                for junk in ("Corporation", "Integrated Graphics Controller", "[AMD/ATI]", "Advanced Micro Devices, Inc."):
                    g = g.replace(junk, "")
                gpu = " ".join(g.split())[:40]
                break
    try:
        bot_start = os.stat(f"/proc/{os.getpid()}").st_ctime
    except OSError:
        bot_start = time.time()
    _static.update(
        user=getpass.getuser(), host=platform.node(), os=_os_name(), model=_host(),
        kernel=platform.release(), shell=os.path.basename(os.environ.get("SHELL", "")) or "—",
        cpu=_cpu_model(), gpu=gpu, pkgs=", ".join(pkgs) or "—", bot_start=bot_start,
        py=platform.python_version(),
    )
    return _static


class LiveStats:
    """CPU və şəbəkə sürəti üçün iki ölçü arasındakı fərq."""

    def __init__(self):
        self.cpu = _cpu_times()
        self.net = _net_bytes()
        self.t = time.monotonic()

    def sample(self) -> dict:
        idle, total = _cpu_times()
        rx, tx = _net_bytes()
        now = time.monotonic()
        d_total, d_idle = total - self.cpu[1], idle - self.cpu[0]
        dt = max(0.001, now - self.t)
        res = {"cpu": 100.0 * (1 - d_idle / d_total) if d_total > 0 else 0.0,
               "rx": (rx - self.net[0]) / dt, "tx": (tx - self.net[1]) / dt}
        self.cpu, self.net, self.t = (idle, total), (rx, tx), now
        return res


# ───────────────────────── 🎨 fastfetch üslubu ─────────────────────────
def _env_on(name, default="1") -> bool:
    return (os.getenv(name, default) or "").strip().lower() not in ("0", "false", "no", "off", "")


FF_LOGO = _env_on("UB_FF_LOGO")                       # UB_FF_LOGO=0 → loqosuz
FF_WIDTH = int(os.getenv("UB_FF_WIDTH", "44") or 44)  # <pre> blokunun maks. eni (telefon üçün ~44)
FF_PALETTE = _env_on("UB_FF_PALETTE")                 # fastfetch-in rəng blokları
PALETTE = "🟥🟧🟨🟩🟦🟪⬛⬜"

# fastfetch-in "_small" loqoları (eni 9-13 simvol)
LOGOS = {
    "arch": ["      /\\", "     /  \\", "    /\\   \\", "   /      \\",
             "  /   ,,   \\", " /   |  |  -\\", "/_-''    ''-_\\"],
    "debian": ["  _____", " /  __ \\", "|  /    |", "|  \\___-", "-_", "  --_"],
    "ubuntu": ["         _", "     ---(_)", " _/  ---  \\", "(_) |   |", "  \\  --- _/", "     ---(_)"],
    "fedora": ["   _____", "  /   __)\\", "  |  /  \\ \\", "__|  |__/ /", "\\ \\     / /", " \\_\\___/ /", "   \\____/"],
    "alpine": ["   /\\ /\\", "  /    \\ \\", " /     /\\ \\", "/     /  \\ \\", "      \\   \\"],
    "linux": ["    ___", "   (.. |", "   (<> |", "  / __  \\", " ( /  \\ /|", "_/\\ __)/_)", "\\/-____\\/"],
}
_LOGO_ALIAS = {"arch": "arch", "manjaro": "arch", "endeavouros": "arch", "cachyos": "arch", "garuda": "arch",
               "artix": "arch", "debian": "debian", "raspbian": "debian", "ubuntu": "ubuntu", "pop": "ubuntu",
               "linuxmint": "ubuntu", "elementary": "ubuntu", "fedora": "fedora", "rhel": "fedora",
               "centos": "fedora", "rocky": "fedora", "almalinux": "fedora", "alpine": "alpine"}
_logo_cache = []


def os_logo() -> list:
    """os-release ID / ID_LIKE-a görə fastfetch loqosu."""
    if _logo_cache:
        return _logo_cache[0]
    data = {}
    for line in _read("/etc/os-release").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            data[k] = v.strip('"').lower()
    key = "linux"
    for cand in [data.get("ID", "")] + data.get("ID_LIKE", "").split():
        if cand in _LOGO_ALIAS:
            key = _LOGO_ALIAS[cand]
            break
    _logo_cache.append(LOGOS[key])
    return LOGOS[key]


class Bar:
    """ff() üçün zolaq sətri:  CPU  ████░░░░ 41.2%"""
    __slots__ = ("key", "pct", "label")

    def __init__(self, key, pct, label=""):
        self.key, self.pct, self.label = str(key), float(pct or 0), str(label)


def _wrap(text: str, n: int) -> list:
    """Sözlərə görə n enə bölür (söz n-dən uzundursa kəsilir)."""
    n = max(6, n)
    lines, cur = [], ""
    for w in text.split(" "):
        if not cur:
            cur = w
        elif len(cur) + 1 + len(w) <= n:
            cur += " " + w
        else:
            lines.append(cur)
            cur = w
    lines.append(cur)
    return lines or [""]


def _cut(s: str, n: int) -> str:
    return s if len(s) <= n else s[:max(1, n - 1)] + "…"


def ff(title, body=(), *, footer=None, logo=False, palette=None, width=None) -> str:
    """fastfetch görünüşlü HTML blok.

    body elementləri:
      ("Açar", dəyər)         → hizalanmış açar / dəyər sətri
      Bar("RAM", 41, "3/8GB") → zolaq (eni sətrə avtomatik sığdırılır)
      "# Bölmə"               → ── Bölmə ──────── ayırıcı
      "istənilən mətn" / ""   → olduğu kimi / boş sətir
    Hər şey <pre> içində escape olunur — dəyərlərə HTML yazma.
    """
    width = width or FF_WIDTH
    logo_lines = os_logo() if (logo and FF_LOGO) else []
    lw = max((len(x) for x in logo_lines), default=0)
    gap = 2 if logo_lines else 0
    room = max(18, width - lw - gap)
    keys = [str(i[0]) for i in body if isinstance(i, tuple)] + [i.key for i in body if isinstance(i, Bar)]
    kw = min(max((len(k) for k in keys), default=0), 10)

    title = str(title)
    out = [_cut(title, room), "-" * min(len(title), room)]
    for it in body:
        if isinstance(it, Bar):
            head = f"{it.key.ljust(kw)}  "
            bw = max(4, min(10, room - len(head) - 1 - len(it.label)))
            line = f"{head}{bar(it.pct, bw)} {it.label}".rstrip()
        elif isinstance(it, tuple):
            pad = " " * (kw + 2)
            vlines = _wrap(str(it[1]), room - len(pad))
            out.append(_cut(f"{str(it[0]).ljust(kw)}  {vlines[0]}", room))
            out += [_cut(pad + v, room) for v in vlines[1:3]]     # maks. 3 sətir
            continue
        elif isinstance(it, str) and it.startswith("# "):
            head = f"── {it[2:]} "
            line = head + "─" * max(2, room - len(head))
        else:
            line = str(it or "")
        out.append(_cut(line, room))

    if logo_lines:
        merged = []
        for i in range(max(len(out), len(logo_lines))):
            left = logo_lines[i] if i < len(logo_lines) else ""
            right = out[i] if i < len(out) else ""
            merged.append((left.ljust(lw) + " " * gap + right).rstrip())
        out = merged

    html = f"<pre>{escape(chr(10).join(out))}</pre>"
    if (FF_PALETTE and logo) if palette is None else palette:
        html += "\n" + PALETTE
    if footer:
        html += f"\n<i>{escape(str(footer))}</i>"
    return html


def render_fastfetch(st: dict, live: dict, left: str) -> str:
    mem = _meminfo()
    total, avail = mem.get("MemTotal", 0), mem.get("MemAvailable", 0)
    used = total - avail
    s_total, s_free = mem.get("SwapTotal", 0), mem.get("SwapFree", 0)
    disk = shutil.disk_usage("/")
    uptime = float((_read("/proc/uptime") or "0").split()[0])
    load = " ".join(f"{x:.2f}" for x in os.getloadavg()) if hasattr(os, "getloadavg") else "—"
    temp, freq = _temp(), _cur_freq()

    def pct(a, b):
        return 100.0 * a / b if b else 0.0

    body = [
        ("OS", st["os"]), ("Host", st["model"]), ("Kernel", st["kernel"]),
        ("Uptime", fmt_span(uptime)), ("Pkgs", st["pkgs"]), ("Shell", st["shell"]),
        ("CPU", st["cpu"]), *([("GPU", st["gpu"])] if st["gpu"] else []),
        ("Freq", freq or "—"), *([("Temp", temp)] if temp else []), ("Load", load),
        "# Resurs",
        Bar("CPU", live["cpu"], f"{live['cpu']:.1f}%"),
        Bar("RAM", pct(used, total), f"{fmt_bytes(used)}/{fmt_bytes(total)}"),
        *([Bar("Swap", pct(s_total - s_free, s_total), f"{fmt_bytes(s_total - s_free)}/{fmt_bytes(s_total)}")]
          if s_total else []),
        Bar("Disk", pct(disk.used, disk.total), f"{fmt_bytes(disk.used, 0)}/{fmt_bytes(disk.total, 0)}"),
        ("Net", f"↓{fmt_bytes(live['rx'])}/s ↑{fmt_bytes(live['tx'])}/s"),
        "# Bot",
        ("RAM", fmt_bytes(_proc_rss())),
        ("Uptime", fmt_span(time.time() - st["bot_start"])),
        ("Python", f"{st['py']} · Telethon {telethon_version}"),
    ]
    return ff(f"{st['user']}@{st['host']}", body, logo=True,
              footer=f"🔄 {FF_INTERVAL} san.-dən bir · {left} · {datetime.now():%H:%M:%S}")


# ───────────────────────── 👤 .info ─────────────────────────
async def user_block(client, ent) -> list:
    if not isinstance(ent, User):                     # reply kanal / anonim admin adından gəlibsə
        kind = "📢 Kanal" if getattr(ent, "broadcast", False) else "👥 Qrup"
        L = [f"{kind}: <b>{mention(ent)}</b>",
             f"🆔 ID: <code>{utils.get_peer_id(ent)}</code>"]
        if getattr(ent, "username", None):
            L.append(f"🔗 @{escape(ent.username)}")
        return L
    L = [f"👤 <b>{mention(ent)}</b>"]
    if ent.username:
        L.append(f"🔗 Username: @{escape(ent.username)}")
    extra = [f"@{u.username}" for u in (getattr(ent, "usernames", None) or [])
             if u.username and u.username != ent.username]
    if extra:
        L.append(f"🔗 Digər: {escape(', '.join(extra))}")
    L.append(f"🆔 ID: <code>{ent.id}</code>")
    if getattr(ent, "photo", None) and getattr(ent.photo, "dc_id", None):
        L.append(f"🌐 DC: {ent.photo.dc_id}")
    if ent.phone:
        L.append(f"📞 Telefon: <code>+{escape(ent.phone)}</code>")
    flags = []
    if ent.bot:
        flags.append("🤖 bot")
    if ent.deleted:
        flags.append("🗑 silinmiş hesab")
    if getattr(ent, "premium", False):
        flags.append("⭐ Premium")
    if ent.verified:
        flags.append("✔️ təsdiqlənmiş")
    if ent.scam:
        flags.append("⚠️ SCAM")
    if ent.fake:
        flags.append("⚠️ FAKE")
    if ent.restricted:
        flags.append("⛔ məhdud")
    if ent.contact:
        flags.append("📇 kontaktdır" + (" (qarşılıqlı)" if ent.mutual_contact else ""))
    if flags:
        L.append("🏷 " + " · ".join(flags))
    if not ent.bot:
        L.append(f"👁 Status: {status_text(ent.status)}")
    try:
        full = await client(GetFullUserRequest(ent))
        fu = full.full_user
        if fu.about:
            L.append(f"📝 Bio: <i>{escape(fu.about)}</i>")
        L.append(f"👥 Ortaq qruplar: {fu.common_chats_count}")
        if getattr(fu, "blocked", False):
            L.append("🚫 Səndə bloklanıb")
    except Exception as e:
        logger.debug(f"GetFullUser: {e}")
    try:
        photos = await client.get_profile_photos(ent, limit=0)
        L.append(f"🖼 Profil şəkli: {photos.total}")
    except Exception:
        pass
    return L


def rights_lines(rights, table, yes="✅", no="❌") -> list:
    return [f"  {yes if getattr(rights, k, False) else no} {label}" for k, label in table
            if hasattr(rights, k)]


async def group_block(client, chat, ent) -> list:
    title = escape(getattr(chat, "title", "") or "qrup")
    L = ["", f"👥 <b>{title}</b>"]
    if not isinstance(ent, User):
        return L + ["<i>Kanal / anonim admin adından yazılıb — icazə məlumatı yoxdur.</i>"]

    if isinstance(chat, Chat):                        # adi (köhnə tip) qrup
        try:
            full = await client(GetFullChatRequest(chat.id))
            parts = getattr(full.full_chat.participants, "participants", []) or []
        except Exception as e:
            return L + [f"<i>Üzvlər oxunmadı: {escape(str(e))[:100]}</i>"]
        p = next((x for x in parts if x.user_id == ent.id), None)
        if p is None:
            return L + ["🚪 Qrupda deyil"]
        if isinstance(p, ChatParticipantCreator):
            return L + ["👑 Status: <b>Yaradıcı</b>"]
        if isinstance(p, ChatParticipantAdmin):
            return L + ["🛡 Status: <b>Admin</b> <i>(adi qrup — bütün admin icazələri var)</i>",
                        f"📅 Qoşulub: {fmt_dt(p.date)}"]
        return L + ["👤 Status: Üzv", f"📅 Qoşulub: {fmt_dt(p.date)}"]

    try:
        res = await client(GetParticipantRequest(chat, ent))
        p = res.participant
    except UserNotParticipantError:
        return L + ["🚪 Qrupda deyil"]
    except Exception as e:
        return L + [f"<i>İştirakçı məlumatı alınmadı: {escape(str(e))[:120]}</i>"]

    async def who(uid):
        if not uid:
            return "—"
        try:
            return mention(await client.get_entity(uid))
        except Exception:
            return f"<code>{uid}</code>"

    if isinstance(p, (ChannelParticipantCreator, ChannelParticipantAdmin)):
        creator = isinstance(p, ChannelParticipantCreator)
        L.append(f"{'👑' if creator else '🛡'} Status: <b>{'Yaradıcı' if creator else 'Admin'}</b>")
        if getattr(p, "rank", None):
            L.append(f"🏷 Titul: <i>{escape(p.rank)}</i>")
        if not creator:
            L.append(f"⬆️ Admin edən: {await who(getattr(p, 'promoted_by', None))}")
            if getattr(p, "date", None):
                L.append(f"📅 Tarix: {fmt_dt(p.date)}")
            if getattr(p, "can_edit", False):
                L.append("✏️ <i>Sən bu adminin icazələrini dəyişə bilərsən</i>")
        rights = p.admin_rights
        have = sum(1 for k, _ in ADMIN_RIGHTS if getattr(rights, k, False))
        L.append(f"\n🔐 <b>Admin icazələri</b> ({have}/{sum(1 for k, _ in ADMIN_RIGHTS if hasattr(rights, k))}):")
        L += rights_lines(rights, ADMIN_RIGHTS)
    elif isinstance(p, ChannelParticipantBanned):
        br = p.banned_rights
        if br.view_messages:
            L.append("🚫 Status: <b>Banlıdır</b> (qrupdan qovulub)")
        else:
            L.append("🔇 Status: <b>Məhdudlaşdırılıb</b>")
        L.append(f"👮 Edən: {await who(getattr(p, 'kicked_by', None))}")
        L.append(f"📅 Tarix: {fmt_dt(p.date)}")
        until = getattr(br, "until_date", None)
        L.append(f"⏳ Bitmə: {fmt_dt(until) if until and until.year < 2037 else 'həmişəlik'}")
        if not br.view_messages:
            L.append("\n🔒 <b>Qadağalar</b> (❌ = qadağan):")
            L += [f"  {'❌' if getattr(br, k, False) else '✅'} {label}" for k, label in BANNED_RIGHTS
                  if hasattr(br, k)]
    elif isinstance(p, ChannelParticipantLeft):
        L.append("🚪 Status: Qrupdan çıxıb")
    else:
        L.append("👤 Status: Üzv")
        if getattr(p, "date", None):
            L.append(f"📅 Qoşulub: {fmt_dt(p.date)}")
        inviter = getattr(p, "inviter_id", None)
        if inviter:
            L.append(f"✉️ Dəvət edən: {await who(inviter)}")
        if isinstance(p, ChannelParticipantSelf) and getattr(p, "via_request", False):
            L.append("📨 Qoşulma sorğusu ilə gəlib")
    return L


async def chat_block(client, chat) -> list:
    """Çat haqqında: növ, ID, üzv sayı, sənin rolun və əsas icazələrin, ban sayı."""
    if isinstance(chat, User):
        return ["💬 <b>Şəxsi çat</b>", ""] + await user_block(client, chat)
    kind = "📢 Kanal" if getattr(chat, "broadcast", False) else (
        "👥 Superqrup" if isinstance(chat, Channel) else "👥 Adi qrup")
    L = [f"{kind}: <b>{escape(getattr(chat, 'title', '') or '—')}</b>",
         f"🆔 <code>{utils.get_peer_id(chat)}</code>"]
    if getattr(chat, "username", None):
        L.append(f"🔗 @{escape(chat.username)}")
    try:
        if isinstance(chat, Channel):
            full = await client(GetFullChannelRequest(chat))
            fc = full.full_chat
            L.append(f"👤 Üzv: <b>{fc.participants_count or '—'}</b>"
                     + (f" · 🛡 admin: {fc.admins_count}" if fc.admins_count else "")
                     + (f" · 🚫 ban: {fc.kicked_count}" if fc.kicked_count else "")
                     + (f" · 🔇 məhdud: {fc.banned_count}" if fc.banned_count else ""))
            if fc.slowmode_seconds:
                L.append(f"🐢 Yavaş rejim: {fc.slowmode_seconds} san.")
        else:
            full = await client(GetFullChatRequest(chat.id))
            parts = getattr(full.full_chat.participants, "participants", []) or []
            L.append(f"👤 Üzv: <b>{len(parts) or getattr(chat, 'participants_count', '—')}</b>")
    except Exception as e:
        logger.debug(f"chat_block full: {e}")
    try:
        perms = await client.get_permissions(chat, "me")
        if perms.is_creator:
            L.append("👑 Sən: <b>yaradıcı</b>")
        elif perms.is_admin:
            keys = [("ban_users", "ban"), ("delete_messages", "silmə"), ("pin_messages", "pin"),
                    ("invite_users", "dəvət"), ("change_info", "məlumat"), ("add_admins", "admin təyini")]
            have = [label for k, label in keys if getattr(perms, k, False)]
            L.append(f"🛡 Sən: <b>admin</b> ({', '.join(have) or 'icazəsiz'})")
        elif getattr(perms, "is_banned", False):
            L.append("🔇 Sən: məhdudlaşdırılmısan")
        else:
            L.append("👤 Sən: üzv")
    except Exception:
        pass
    return L


async def run_clear(client, chat, mode: str, report, is_stopped=lambda: False):
    """Ban siyahısını (mode="all" olsa məhdudiyyətləri də) təmizləyir. report(text, final) — proqres."""
    filters = [("🚫 ban", ChannelParticipantsKicked)]
    if mode == "all":
        filters.append(("🔇 məhdudiyyət", ChannelParticipantsBanned))
    counts = {}
    for label, flt in filters:
        try:
            counts[label] = (await client.get_participants(chat, limit=0, filter=flt())).total
        except Exception:
            counts[label] = 0
    total = sum(counts.values())
    title = escape(getattr(chat, "title", "") or "qrup")
    if not total:
        await report("✅ Ban siyahısı onsuz da boşdur.", True)
        return
    done = failed = 0
    last = 0.0
    await report(f"🧹 <b>{title}</b>: {total} nəfər təmizlənir...", False)
    for label, flt in filters:
        async for user in client.iter_participants(chat, filter=flt()):
            if is_stopped():
                raise asyncio.CancelledError()
            while True:
                try:
                    await client(EditBannedRequest(chat, user, ChatBannedRights(until_date=None)))
                    done += 1
                    break
                except FloodWaitError as e:
                    await report(f"⏳ Telegram limiti — {e.seconds} san. gözlənilir... ({done}/{total})", False)
                    await asyncio.sleep(e.seconds + 1)
                except Exception as e:
                    failed += 1
                    logger.debug(f"unban {user.id}: {e}")
                    break
            if time.monotonic() - last > 3:
                last = time.monotonic()
                await report(f"🧹 <b>{title}</b>\n{label}: {done}/{total} · ❌ {failed}", False)
            await asyncio.sleep(0.3)
    summary = " · ".join(f"{k}: {v}" for k, v in counts.items())
    await report(f"✅ <b>{title}</b> — təmizləndi\n🧹 Götürüldü: <b>{done}</b> · ❌ alınmadı: {failed}\n"
                 f"<i>{summary}</i>", True)


# ───────────────────────── 🔇 mute köməkçiləri ─────────────────────────
MUTE_KEY = "userbot:mutes"
_DUR_RE = re.compile(r"^(\d+)\s*(m|dəq|deq|min|h|saat|s|d|gün|gun|g)?$", re.I)
_DUR_MULT = {"m": 60, "dəq": 60, "deq": 60, "min": 60, "h": 3600, "saat": 3600, "s": 3600,
             "d": 86400, "gün": 86400, "gun": 86400, "g": 86400}


def parse_duration(tok: str):
    """'10m' / '2h' / '1d' / '30' (dəq.) → saniyə; uyğun deyilsə None."""
    m = _DUR_RE.match((tok or "").strip())
    if not m:
        return None
    return int(m.group(1)) * _DUR_MULT.get((m.group(2) or "m").lower(), 60)


def fmt_left(until: float) -> str:
    return "həmişəlik" if not until else f"{fmt_span(max(60, until - time.time()))} qalıb"



# ───────────────────────── ✏️ ümumi kömək ─────────────────────────
async def build_info(client, ent, chat=None) -> str:
    """user_block (+ qrupdursa group_block) → tam .info mətni."""
    lines = await user_block(client, ent)
    if chat is not None and not isinstance(chat, User):
        lines += await group_block(client, chat, ent)
    return "\n".join(lines)


async def resolve_target(event, args):
    """Komanda hədəfi: reply > @username / ID. (entity, qalan arqumentlər)."""
    client = event.client
    if event.is_reply:
        reply = await event.get_reply_message()
        ent = await reply.get_sender() if reply else None
        if ent is None and reply is not None:
            ent = await client.get_entity(reply.sender_id)
        return ent, args
    if args and (args[0].startswith("@") or (args[0].lstrip("-").isdigit() and len(args[0]) >= 5)):
        a = args[0]
        return await client.get_entity(int(a) if a.lstrip("-").isdigit() else a), args[1:]
    return None, args


def plain_name(ent) -> str:
    n = " ".join(x for x in (getattr(ent, "first_name", None), getattr(ent, "last_name", None)) if x) \
        or getattr(ent, "title", None) or "Silinmiş hesab"
    return f"{n} (@{ent.username})" if getattr(ent, "username", None) else n


# ───────────────────────── 🧩 reyestr ─────────────────────────
# ───────────────────────── 🃏 inline kart (komanda cavabı) ─────────────────────────
KEEP = object()          # update(rows=KEEP) → düymələr dəyişmir
CARD_MIN_GAP = 1.1       # iki redaktə arası minimum (FloodWait-dən qorunma)
CARD_LIMIT = 200


class Out:
    """Komandanın cavabı. Mümkünsə bot vasitəsilə inline kart (düymələrlə), yoxsa sadə mesaj.

    Inline axın: userbot  →  @bot inline sorğu "ub:card:<tok>"  →  nəticəni çata göndərir  →  komanda silinir.
    Sonrakı redaktələr bot tərəfindən inline_message_id ilə edilir. inline_message_id iki yolla gəlir:
      1) chosen_inline_result (BotFather → /setinlinefeedback aktivdirsə)
      2) olmasa — userbot kartdakı 🔄 düyməsini özü "basır", bot callback-dən id-ni götürür.
    Heç biri alınmasa kart silinir və sadə mesaj rejiminə keçilir.
    """

    def __init__(self, ub, event, inline=True):
        self.ub = ub
        self.event = event
        self.client = event.client
        self.chat_id = event.chat_id
        self.tok = os.urandom(4).hex()
        self.want_inline = inline
        self.mode = "text"              # "inline" | "text"
        self.msg = event                # mətn rejimində redaktə olunan mesaj
        self.sent = None                # inline rejimdə göndərilən kart (userbot tərəfi)
        self.iid = None                 # inline_message_id (bot tərəfi)
        self.iid_ready = asyncio.Event()
        self._acquiring = False
        self.text = ""
        self.rows = []
        self.task = None
        self.actions = {}
        self.refresh = None
        self.closed = False
        self._last = 0.0
        self._lock = asyncio.Lock()

    # ── düymələr ──
    def btn(self, text, action):
        return InlineKeyboardButton(text=text, callback_data=f"ubc:{self.tok}:{action}"[:64])

    def on(self, action):
        def deco(fn):
            self.actions[action] = fn
            return fn
        return deco

    def running(self) -> bool:
        return bool(self.task and not self.task.done())

    def kb(self) -> InlineKeyboardMarkup:
        rows = [list(r) for r in (self.rows or [])]
        sysrow = [self.btn("🔄", "r")]
        if self.running():
            sysrow.append(self.btn("⏹ Dayandır", "s"))
        sysrow.append(self.btn("❌ Bağla", "x"))
        rows.append(sysrow)
        return InlineKeyboardMarkup(inline_keyboard=rows)

    def track(self, kind, coro_or_task):
        task = coro_or_task if isinstance(coro_or_task, asyncio.Task) else asyncio.create_task(coro_or_task)
        self.task = task
        self.ub.track(self.chat_id, kind, task)
        return task

    def _text_body(self) -> str:
        t = self.text
        if self.running():
            t += "\n<i>⏹ dayandırmaq: </i><code>.stop</code>"
        return t

    # ── açmaq ──
    async def open(self, text, rows=None):
        self.text, self.rows = text, rows or []
        self.ub.cards[self.tok] = self
        while len(self.ub.cards) > CARD_LIMIT:
            self.ub.cards.pop(next(iter(self.ub.cards)))
        if self.want_inline and await self._open_inline():
            self._last = time.monotonic()
            return self
        self.mode = "text"
        await safe_edit(self.msg, self._text_body())
        self._last = time.monotonic()
        return self

    async def _open_inline(self) -> bool:
        try:
            username = await self.ub.bot_username()
            results = await self.client.inline_query(username, f"ub:card:{self.tok}",
                                                     entity=await self.event.get_input_chat())
            if not results:
                raise RuntimeError("inline nəticə gəlmədi")
            self.sent = await results[0].click(self.chat_id, reply_to=self.event.reply_to_msg_id, hide_via=True)
        except Exception as e:
            logger.info(f"kart inline alınmadı ({self.chat_id}) → mətn: {e}")
            return False
        self.mode = "inline"
        try:
            await self.event.delete()
        except Exception:
            pass
        return True

    # ── yeniləmək ──
    async def update(self, text=None, rows=KEEP):
        if self.closed:
            return
        if text is not None:
            self.text = text
        if rows is not KEEP:
            self.rows = rows or []
        async with self._lock:
            if self.closed:
                return
            wait = CARD_MIN_GAP - (time.monotonic() - self._last)
            if wait > 0:
                await asyncio.sleep(wait)
            if self.mode == "inline" and not self.iid and not await self._ensure_iid():
                await self._degrade()
            elif self.mode == "inline":
                await self._bot_edit()
            else:
                await safe_edit(self.msg, self._text_body())
            self._last = time.monotonic()

    def set_iid(self, iid):
        if iid and not self.iid:
            self.iid = iid
            self.iid_ready.set()

    async def _ensure_iid(self) -> bool:
        if self.iid:
            return True
        with _suppress():
            await asyncio.wait_for(self.iid_ready.wait(), 1.5)       # chosen_inline_result
        if self.iid or self.sent is None:
            return bool(self.iid)
        self._acquiring = True                                      # 🔄 düyməsini özümüz basırıq
        try:
            await self.sent.click(data=f"ubc:{self.tok}:r".encode())
        except Exception as e:
            logger.debug(f"kart özü-klik: {e}")
        finally:
            self._acquiring = False
        with _suppress():
            await asyncio.wait_for(self.iid_ready.wait(), 3)
        return bool(self.iid)

    async def _degrade(self):
        logger.warning("kart: inline_message_id alınmadı → sadə mesaj rejiminə keçildi")
        try:
            await self.client.delete_messages(self.chat_id, [self.sent.id])
        except Exception:
            pass
        self.mode = "text"
        try:
            self.msg = await self.client.send_message(self.chat_id, self._text_body(), parse_mode="html",
                                                      link_preview=False)
        except Exception as e:
            logger.info(f"kart mətn rejimi göndərilmədi: {e}")
            self.closed = True

    async def _bot_edit(self):
        for _ in range(3):
            try:
                await self.ub.bot.edit_message_text(inline_message_id=self.iid, text=self.text, parse_mode="HTML",
                                                    reply_markup=self.kb(), disable_web_page_preview=True)
                return
            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after + 1)
            except TelegramBadRequest as e:
                if "not modified" not in str(e):
                    logger.debug(f"kart redaktə: {e}")
                return
            except Exception as e:
                logger.debug(f"kart redaktə: {e}")
                return

    # ── bağlamaq ──
    async def close(self, delay=0, cancel=True):
        if delay:
            await asyncio.sleep(delay)
        if self.closed:
            return
        self.closed = True
        if cancel and self.running() and self.task is not asyncio.current_task():
            self.task.cancel()
        self.ub.cards.pop(self.tok, None)
        try:
            if self.mode == "inline" and self.sent is not None:
                await self.client.delete_messages(self.chat_id, [self.sent.id])
            else:
                await self.msg.delete()
        except Exception:
            if self.iid:
                with _suppress():
                    await self.ub.bot.edit_message_text(inline_message_id=self.iid, text="✖️ <i>bağlandı</i>",
                                                        parse_mode="HTML")


class _suppress:
    """contextlib.suppress(Exception) + asyncio.TimeoutError, async-dostu qısa forma."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return exc[0] is not None and issubclass(exc[0], Exception)


class SectionCtx:
    def __init__(self, ub, section_key, tok, cb, action, args):
        self.ub = ub
        self.key = section_key
        self.tok = tok
        self.cb = cb
        self.action = action
        self.args = args
        self.iid = getattr(cb, "inline_message_id", None)
        self.menu = ub.menus.get(tok) or {}

    def btn(self, text, *parts):
        data = ":".join(["ubm", self.tok, self.key, *[str(p) for p in parts if p != ""]]) if parts \
            else f"ubm:{self.tok}:{self.key}"
        return InlineKeyboardButton(text=text, callback_data=data[:64])

    def gbtn(self, text, section, *parts):
        """başqa bölməyə / ana menyuya düymə."""
        data = ":".join(["ubm", self.tok, section, *[str(p) for p in parts]]) if parts \
            else f"ubm:{self.tok}:{section}"
        return InlineKeyboardButton(text=text, callback_data=data[:64])

    def nav(self, label="⬅️ Menyu"):
        return [self.gbtn(label, "main")]

    async def answer(self, text=None, alert=False):
        try:
            await self.cb.answer(text, show_alert=alert)
        except Exception:
            pass

    async def client(self):
        return self.ub.client

    async def chat(self):
        if not self.menu or not self.ub.client:
            return None
        return await self.ub.client.get_entity(self.menu["chat_id"])

    async def show(self, text, rows):
        kb = rows if isinstance(rows, InlineKeyboardMarkup) else InlineKeyboardMarkup(inline_keyboard=rows)
        await self.ub.edit_panel(self.iid or self.cb, text, kb)


class UB:
    def __init__(self, context):
        self.context = context
        self.bot = context.bot
        self.dp = context.dp
        self.creator_id = context.creator_id
        self.client = None
        self.state = {}                 # pluginlər arası paylaşılan
        self.commands = []              # {name, pattern, outgoing, fn}
        self.raw_handlers = []          # (fn, event_builder)
        self.help = {}                  # key -> (button, short, long, category)
        self.help_cats = {}             # key -> {title, sub, order}
        self.sections = {}              # key -> {label, order, render, handle}
        self.tasks = {}                 # chat_id -> {kind: task}
        self.menus = {}                 # tok -> {chat_id, reply_uid, msg_id, time}
        self.views = {}                 # tok -> cari bölmə (canlı rejim idarəsi üçün)
        self.ptasks = {}                # tok -> panel arxa-plan task-ı (canlı sistem)
        self._handlers = []             # (fn, event) — client-ə qoşulanlar
        self._attached_raw = set()      # id(fn) — artıq qoşulmuş xam handler-lər
        self._loading = None            # hazırda yüklənən plugin faylının adı (owner)
        self.help_owner = {}            # help key -> owner
        self.plugin_files = {}          # ad -> fayl yolu (install/uninstall üçün)
        self.cards = {}                 # tok -> Out (inline komanda kartları)

    # ── identik ──
    def is_creator(self, uid) -> bool:
        return bool(self.creator_id) and uid == self.creator_id

    async def bot_username(self) -> str:
        if not self.state.get("bot_username"):
            self.state["bot_username"] = (await self.bot.get_me()).username
        return self.state["bot_username"]

    def title(self, name: str) -> str:
        """fastfetch başlığı: menu@dllmasterbot"""
        return f"{name}@{self.state.get('bot_username') or 'userbot'}"

    # ── komanda cavabı: inline kart / sadə mesaj ──
    async def out(self, event, text, rows=None, inline=True) -> "Out":
        return await Out(self, event, inline=inline).open(text, rows)

    # ── komanda qeydiyyatı ──
    def command(self, name, pattern=None, outgoing=True, help=None, category="acc"):
        def deco(fn):
            pat = pattern if pattern is not None else rf"^\.{name}$"
            self.commands.append({"name": name, "pattern": pat, "outgoing": outgoing, "fn": fn,
                                  "owner": self._loading, "_attached": False})
            if help:
                short, long = help
                self.add_help(name, f".{name}", short, long, category)
            return fn
        return deco

    def raw(self, event_builder):
        def deco(fn):
            self.raw_handlers.append({"fn": fn, "ev": event_builder, "owner": self._loading})
            return fn
        return deco

    # ── help ──
    def help_category(self, key, title, sub, order=50):
        self.help_cats[key] = {"title": title, "sub": sub, "order": order}

    def add_help(self, key, button, short, long, category="acc"):
        self.help[key] = (button, short, long, category)
        self.help_owner[key] = self._loading

    def help_keys(self, category):
        return [k for k, v in sorted(self.help.items(), key=lambda kv: kv[0]) if v[3] == category]

    def ordered_cats(self):
        return sorted(self.help_cats.items(), key=lambda kv: kv[1]["order"])

    # ── panel bölmələri ──
    def section(self, key, label, order=50):
        def deco(render):
            self.sections.setdefault(key, {})["label"] = label
            self.sections[key]["order"] = order
            self.sections[key]["render"] = render
            self.sections[key]["owner"] = self._loading
            self.sections[key].setdefault("handle", None)
            return render
        return deco

    def on_section(self, key):
        def deco(handle):
            self.sections.setdefault(key, {})["handle"] = handle
            return handle
        return deco

    def ordered_sections(self):
        return sorted(((k, v) for k, v in self.sections.items() if v.get("render")),
                      key=lambda kv: kv[1]["order"])

    # ── fon işləri ──
    def track(self, chat_id, kind, task):
        old = self.tasks.setdefault(chat_id, {}).get(kind)
        if old and not old.done():
            old.cancel()
        self.tasks[chat_id][kind] = task

    def get_task(self, chat_id, kind):
        return self.tasks.get(chat_id, {}).get(kind)

    def stop_tasks(self, chat_id) -> int:
        n = 0
        for task in (self.tasks.pop(chat_id, {}) or {}).values():
            if task and not task.done():
                task.cancel()
                n += 1
        return n

    def active_tasks(self) -> int:
        return sum(1 for kinds in self.tasks.values() for t in kinds.values() if t and not t.done())

    # ── client-ə qoşulma (canlı install üçün də istifadə olunur) ──
    def attach_pending(self) -> int:
        """Hələ qoşulmamış komanda və xam handler-ləri client-ə bağlayır. Client yoxdursa 0."""
        if not self.client:
            return 0
        n = 0
        for c in self.commands:
            if c.get("_attached"):
                continue
            ev = events.NewMessage(outgoing=True, pattern=c["pattern"]) if c["outgoing"] \
                else events.NewMessage(pattern=c["pattern"])
            self.client.add_event_handler(c["fn"], ev)
            self._handlers.append((c["fn"], ev))
            c["_attached"] = True
            n += 1
        for h in self.raw_handlers:
            if id(h["fn"]) in self._attached_raw:
                continue
            self.client.add_event_handler(h["fn"], h["ev"])
            self._handlers.append((h["fn"], h["ev"]))
            self._attached_raw.add(id(h["fn"]))
            n += 1
        return n

    def plugin_summary(self, owner):
        cmds = [c["name"] for c in self.commands if c.get("owner") == owner]
        secs = [v["label"] for k, v in self.sections.items() if v.get("owner") == owner]
        raws = sum(1 for h in self.raw_handlers if h.get("owner") == owner)
        return {"commands": cmds, "sections": secs, "raw": raws}

    def remove_plugin(self, owner) -> dict:
        """Bir plugin faylının bütün qeydlərini (komanda, xam handler, bölmə, help) geri alır."""
        info = self.plugin_summary(owner)
        for c in [c for c in self.commands if c.get("owner") == owner]:
            if c.get("_attached") and self.client:
                try:
                    self.client.remove_event_handler(c["fn"])
                except Exception:
                    pass
            self.commands.remove(c)
        for h in [h for h in self.raw_handlers if h.get("owner") == owner]:
            if id(h["fn"]) in self._attached_raw and self.client:
                try:
                    self.client.remove_event_handler(h["fn"])
                except Exception:
                    pass
            self._attached_raw.discard(id(h["fn"]))
            self.raw_handlers.remove(h)
        for k in [k for k, v in self.sections.items() if v.get("owner") == owner]:
            self.sections.pop(k, None)
        for k in [k for k, o in self.help_owner.items() if o == owner]:
            self.help.pop(k, None)
            self.help_owner.pop(k, None)
        return info

    # ── panel redaktə (host təyin edir) ──
    async def edit_panel(self, cb_or_iid, text, kb):
        iid = cb_or_iid if isinstance(cb_or_iid, str) else getattr(cb_or_iid, "inline_message_id", None)
        try:
            if iid:
                await self.bot.edit_message_text(inline_message_id=iid, text=text, parse_mode="HTML",
                                                 reply_markup=kb, disable_web_page_preview=True)
            elif getattr(cb_or_iid, "message", None):
                await cb_or_iid.message.edit_text(text, parse_mode="HTML", reply_markup=kb,
                                                  disable_web_page_preview=True)
        except Exception as e:
            if "not modified" not in str(e):
                logger.debug(f"panel yenilənmədi: {e}")


async def check_clear_rights(client, chat):
    """Ban təmizləmə üçün icazə yoxlaması; uyğundursa None, yoxsa mətn."""
    if not isinstance(chat, Channel):
        return "ℹ️ Ban təmizləmə yalnız superqrup / kanalda işləyir (adi qrupda ban siyahısı olmur)."
    perms = await client.get_permissions(chat, "me")
    if not (perms.is_creator or getattr(perms, "ban_users", False)):
        return "⛔ Bu çatda <b>ban etmə</b> admin icazən yoxdur."
    return None


async def ban_counts(client, chat, mode):
    """(kicked, banned_extra) sayı — təsdiq ekranı üçün."""
    try:
        k = (await client.get_participants(chat, limit=0, filter=ChannelParticipantsKicked())).total
    except Exception:
        k = 0
    b = 0
    if mode == "all":
        try:
            b = (await client.get_participants(chat, limit=0, filter=ChannelParticipantsBanned())).total
        except Exception:
            b = 0
    return k, b
