"""
🛠 Userbot alətləri (Telethon) — şəxsi hesabdan yazılan nöqtəli komandalar.

Ayrıca client yaratmır: depo_filler_plugin-in qoşduğu userbot-u (context.telethon_client) istifadə edir,
ona görə eyni sessiya faylı iki dəfə açılmır.

Komandalar (yalnız öz hesabından, istənilən çatda):
  .alive                 — reaksiya qoyur, 2 san. sonra mesajı silir
  .info                  — reply etdiyin şəxs haqqında məlumat (qrupdadırsa: statusu + admin icazələri)
  .info @username | ID   — reply olmadan
  .fastfetch [san.]      — sistem məlumatı, 3 saniyədən bir yenilənir (default 60 san., 0 = .stop-a qədər)
  .clear                 — qrupun ban siyahısını təmizləyir (hamını unban edir)
  .clear all             — banlarla yanaşı məhdudiyyətləri (mute və s.) də götürür
  .stop                  — bu çatdakı .fastfetch / .clear-i dayandırır
  .menu / .help / .panel — admin paneli: 📊 Status · 🖥 Sistem (canlı) · 👥 Bu çat (məlumat, reply edilənin
                           .info-su, ban təmizləmə — təsdiqlə) · 📦 Depo (aç/söndür, növbə, skan) · ❓ Help

Panel necə işləyir: şəxsi hesab düymə göndərə bilmir, ona görə .menu botun inline rejimini çağırır
("ub:menu:<token>" sorğusu) və nəticəni sənin adından həmin çata göndərir. Token paneli çata (və reply
edilən şəxsə) bağlayır. Düymələrə yalnız creator basa bilər; əməliyyatlar userbot hesabı ilə icra olunur.
"""
import asyncio
import getpass
import glob
import logging
import os
import platform
import shutil
import sys
import time
from datetime import datetime
from html import escape

from aiogram import F, types as ag_types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, InlineQueryResultArticle, InputTextMessageContent
from telethon import events, utils
from telethon import __version__ as telethon_version
from telethon.errors import FloodWaitError, MessageNotModifiedError, UserNotParticipantError
from telethon.tl.functions.channels import EditBannedRequest, GetFullChannelRequest, GetParticipantRequest
from telethon.tl.functions.messages import GetFullChatRequest, SendReactionRequest
from telethon.tl.functions.users import GetFullUserRequest
from telethon.tl.types import (
    Channel, Chat, ChatBannedRights, ReactionEmoji, User,
    ChannelParticipantAdmin, ChannelParticipantBanned, ChannelParticipantCreator, ChannelParticipantLeft,
    ChannelParticipantSelf, ChannelParticipantsBanned, ChannelParticipantsKicked,
    ChatParticipantAdmin, ChatParticipantCreator,
    UserStatusLastMonth, UserStatusLastWeek, UserStatusOffline, UserStatusOnline, UserStatusRecently,
)

logger = logging.getLogger(__name__)

FF_INTERVAL = 3            # .fastfetch yenilənmə intervalı (san.)
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


# ───────────────────────── köməkçilər ─────────────────────────
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

    rows = [
        ("OS", st["os"]), ("Host", st["model"]), ("Kernel", st["kernel"]),
        ("Uptime", fmt_span(uptime)), ("Paket", st["pkgs"]), ("Shell", st["shell"]),
        ("CPU", st["cpu"]), *([("GPU", st["gpu"])] if st["gpu"] else []),
        ("Tezlik", freq or "—"), *([("Temp", temp)] if temp else []), ("Load", load),
    ]
    w = max(len(k) for k, _ in rows)
    lines = [f"{k.ljust(w)}  {v}" for k, v in rows]
    lines += [
        "",
        f"CPU   {bar(live['cpu'])} {live['cpu']:5.1f}%",
        f"RAM   {bar(pct(used, total))} {fmt_bytes(used)}/{fmt_bytes(total)}",
        f"Swap  {bar(pct(s_total - s_free, s_total))} {fmt_bytes(s_total - s_free)}/{fmt_bytes(s_total)}",
        f"Disk  {bar(pct(disk.used, disk.total))} {fmt_bytes(disk.used, 0)}/{fmt_bytes(disk.total, 0)}",
        f"Şəbəkə ↓ {fmt_bytes(live['rx'])}/s  ↑ {fmt_bytes(live['tx'])}/s",
        "",
        f"Bot   RAM {fmt_bytes(_proc_rss())} · {fmt_span(time.time() - st['bot_start'])}",
        f"Py {st['py']} · Telethon {telethon_version}",
    ]
    return (f"🖥 <b>{escape(st['user'])}@{escape(st['host'])}</b>\n"
            f"<pre>{escape(chr(10).join(lines))}</pre>\n"
            f"<i>🔄 {FF_INTERVAL} san.-dən bir · {escape(left)} · {datetime.now():%H:%M:%S}</i>")


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


# ───────────────────────── ❓ Help məzmunu ─────────────────────────
# kateqoriya: (açar, düymə, başlıq, [komanda açarları])
HELP_CATS = [
    ("acc", "💬 Hesab komandaları", "Öz hesabından istənilən çatda yazılır", ["menu", "alive", "info", "fastfetch", "clear", "stop"]),
    ("bot", "🤖 Bot komandaları", "Botun özündə (@dllmasterbot) yazılır", ["userbot", "scan", "stopscan", "depoadd", "depo", "fix"]),
    ("panel", "🛠 Panel", "Bu admin panelinin bölmələri", ["p_status", "p_sys", "p_chat", "p_depo"]),
]
# açar: (düymə, qısa, ətraflı HTML)
HELP = {
    "menu": (".menu", "admin paneli",
             "Bu admin panelini açır. Hər şey buradan idarə olunur: status, canlı sistem, çat alətləri "
             "(məlumat, ban təmizləmə), depo doldurucu və bu help.\n\n"
             "<b>İstifadə:</b> istənilən çatda <code>.menu</code> (və ya <code>.help</code>)\n"
             "💡 Kiməsə <b>reply edib</b> <code>.menu</code> yazsan, 👥 Bu çat bölməsində o şəxsin məlumat düyməsi də olur.\n"
             "<i>Çatda botlar vasitəsilə göndərmə qadağandırsa, sadə mətn göstərilir.</i>"),
    "alive": (".alive", "hesab işləyirmi",
              "Mesajına ✅ reaksiya qoyur və 2 saniyə sonra silir.\n\n<b>İstifadə:</b> <code>.alive</code>"),
    "info": (".info", "şəxs haqqında məlumat",
             "Ad, username, ID, DC, bio, status, ortaq qruplar, profil şəkli sayı, Premium / bot / scam / fake.\n"
             "Qrupda: <b>yaradıcı / admin / üzv / banlı / məhdud</b>, titul, kim admin edib, bütün "
             "<b>admin icazələri</b> ✅/❌; banlıdırsa — kim, nə vaxt, nə vaxta qədər, qadağalar.\n\n"
             "<b>İstifadə:</b>\n• reply → <code>.info</code>\n• <code>.info @username</code>\n"
             "• <code>.info 123456789</code>\n• şəxsi çatda reply-siz — qarşı tərəf\n\n"
             "<i>Paneldə:</i> 👥 Bu çat → 👤 Reply edilən"),
    "fastfetch": (".fastfetch", "sistem məlumatı (canlı)",
                  "OS, host, kernel, uptime, paketlər, CPU, GPU, temperatur, load, CPU / RAM / Swap / Disk "
                  "zolaqları, şəbəkə sürəti, botun RAM-ı. <b>3 saniyədən bir</b> yenilənir.\n\n"
                  "<b>İstifadə:</b>\n• <code>.fastfetch</code> — 60 san.\n• <code>.fastfetch 300</code> — 5 dəq.\n"
                  "• <code>.fastfetch 0</code> — <code>.stop</code>-a qədər (maks. 1 saat)\n\n"
                  "<i>Paneldə:</i> 🖥 Sistem → ▶️ Canlı"),
    "clear": (".clear", "qrupun banlarını təmizlə",
              "Superqrupun ban siyahısındakı hər kəsi unban edir, proqres 3 saniyədən bir yenilənir, "
              "Telegram limit qoysa gözləyib davam edir.\n\n<b>İstifadə:</b>\n• <code>.clear</code> — banlar\n"
              "• <code>.clear all</code> — banlar + məhdudiyyətlər (mute)\n\n"
              "⚠️ Komanda kimi təsdiqsiz başlayır. Ban icazən olmalıdır. Dayandırmaq: <code>.stop</code>\n"
              "<i>Paneldə:</i> 👥 Bu çat → 🧹 (təsdiq soruşur)"),
    "stop": (".stop", "işləyəni dayandır",
             "Bu çatdakı <code>.fastfetch</code> və <code>.clear</code>-i dayandırır.\n\n<b>İstifadə:</b> <code>.stop</code>"),
    "userbot": ("/userbot", "userbot statusu",
                "Botda yazılır: userbot qoşulubmu, hesab, Telegram mənbələrinin sayı.\n\n"
                "<i>Paneldə:</i> 📊 Status"),
    "scan": ("/scan_chat", "çatı birdəfəlik skan et",
             "Kanal / qrupdakı audioları userbot ilə oxuyur, adları təmizləyir, depo növbəsinə əlavə edir "
             "(mənbə kimi saxlamır).\n\n<b>İstifadə (botda):</b>\n• <code>/scan_chat https://t.me/+dəvət</code>\n"
             "• <code>/scan_chat @kanal 300</code> — son 300 audio\n• <code>/scan_chat -1001234567890</code>"),
    "stopscan": ("/stop_scan", "skanı dayandır",
                 "Gedən skanı dayandırır.\n\n<b>İstifadə (botda):</b> <code>/stop_scan</code>\n"
                 "<i>Paneldə:</i> 📦 Depo → ⏹ Skanı dayandır"),
    "depoadd": ("/depo add", "daimi Telegram mənbəyi",
                "Kanalı / qrupu depoya daimi mənbə kimi əlavə edir: dərhal skan edir, sonra yeni audiolar "
                "canlı növbəyə düşür.\n\n<b>İstifadə (botda):</b>\n• <code>/depo add @kanal</code>\n"
                "• <code>/depo add https://t.me/+dəvət 500</code>"),
    "depo": ("/depo", "depo doldurucu paneli",
             "Doldurucunun tam paneli: sürət, mənbələr, növbə.\n\n<b>İstifadə (botda):</b> <code>/depo</code>, "
             "<code>/depo on</code>, <code>/depo off</code>\n<i>Paneldə:</i> 📦 Depo (aç/söndür, növbə)"),
    "fix": ("/fix", "depo təmizliyi",
            "Dublikatları birləşdirir, adlardakı tarix / zibili təmizləyir, YouTube adı ilə uyğun gəlməyənləri "
            "düzəldir.\n\n<b>İstifadə (botda):</b> <code>/fix</code>"),
    "p_status": ("📊 Status", "userbot vəziyyəti",
                 "Hesab, ping, botun işləmə müddəti, aktiv işlər, Telegram mənbələri, depo qısa statusu."),
    "p_sys": ("🖥 Sistem", "server məlumatı",
              "Fastfetch-in panel versiyası: 🔄 ilə yenilə və ya ▶️ Canlı — 60 san. ərzində 3 saniyədən bir."),
    "p_chat": ("👥 Bu çat", "menyunun açıldığı çat",
               "Çat məlumatı (üzv, admin, ban sayı, sənin rolun), reply edilən şəxsin .info-su, "
               "banları / məhdudiyyətləri təmizləmə (təsdiqlə) və dayandırma."),
    "p_depo": ("📦 Depo", "depo doldurucu",
               "Doldurucunun vəziyyəti, növbə, statistikalar; ▶️ / ⏸, 🔁 növbəni yenidən qur, ⏹ skanı dayandır."),
}


# ───────────────────────── setup ─────────────────────────
def setup(context):
    dp = context.dp
    state = {"client": None, "handlers": [], "tasks": {}, "menus": {}, "views": {}, "ptasks": {}}
    context.userbot_tools_state = state

    def is_creator(uid) -> bool:
        return bool(context.creator_id) and uid == context.creator_id

    def track(chat_id, kind, task):
        old = state["tasks"].setdefault(chat_id, {}).get(kind)
        if old and not old.done():
            old.cancel()
        state["tasks"][chat_id][kind] = task

    # ═════════════ hesab komandaları ═════════════
    async def on_alive(event):
        client = event.client
        try:
            peer = await event.get_input_chat()
            for emo in ("\u2705", "👍", "🔥"):
                try:
                    await client(SendReactionRequest(peer=peer, msg_id=event.id,
                                                     reaction=[ReactionEmoji(emoticon=emo)]))
                    break
                except Exception:
                    continue
        except Exception:
            pass
        await asyncio.sleep(2)
        try:
            await event.delete()
        except Exception as e:
            logger.debug(f".alive silinmədi: {e}")

    async def build_info(client, ent, chat=None) -> str:
        lines = await user_block(client, ent)
        if chat is not None and not isinstance(chat, User):
            lines += await group_block(client, chat, ent)
        return "\n".join(lines)

    async def on_info(event):
        client = event.client
        arg = (event.pattern_match.group(1) or "").strip()
        await safe_edit(event, "🔎 <i>Məlumat toplanır...</i>")
        try:
            if event.is_reply:
                reply = await event.get_reply_message()
                ent = await reply.get_sender() if reply else None
                if ent is None and reply is not None:
                    ent = await client.get_entity(reply.sender_id)
            elif arg:
                ent = await client.get_entity(int(arg) if arg.lstrip("-").isdigit() else arg)
            elif event.is_private:
                ent = await event.get_chat()
            else:
                ent = await client.get_me()
            if ent is None:
                raise ValueError("Göndərən tapılmadı")
            await safe_edit(event, await build_info(client, ent, await event.get_chat() if event.is_group else None))
        except Exception as e:
            logger.info(f".info xətası: {e}")
            await safe_edit(event, f"❌ <b>.info:</b> <code>{escape(str(e))[:200]}</code>")

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

        track(event.chat_id, "ff", asyncio.create_task(loop()))

    async def check_clear_rights(client, chat):
        if not isinstance(chat, Channel):
            return "ℹ️ Ban təmizləmə yalnız superqrup / kanalda işləyir (adi qrupda ban siyahısı olmur)."
        perms = await client.get_permissions(chat, "me")
        if not (perms.is_creator or getattr(perms, "ban_users", False)):
            return "⛔ Bu çatda <b>ban etmə</b> admin icazən yoxdur."
        return None

    async def on_clear(event):
        client = event.client
        mode = (event.pattern_match.group(1) or "").strip().lower()
        chat = await event.get_chat()
        err = await check_clear_rights(client, chat)
        if err:
            await safe_edit(event, err)
            return

        async def report(text, final):
            await safe_edit(event, text + ("" if final else "\n<i>Dayandırmaq: .stop</i>"))

        async def runner():
            try:
                await run_clear(client, chat, mode, report)
            except asyncio.CancelledError:
                await safe_edit(event, "⏹ <b>.clear</b> dayandırıldı.")
                raise
            except Exception as e:
                logger.info(f".clear xətası: {e}")
                await safe_edit(event, f"❌ <b>.clear:</b> <code>{escape(str(e))[:200]}</code>")

        track(event.chat_id, "clear", asyncio.create_task(runner()))

    async def on_stop(event):
        stopped = 0
        for task in (state["tasks"].pop(event.chat_id, {}) or {}).values():
            if task and not task.done():
                task.cancel()
                stopped += 1
        await safe_edit(event, "⏹ Dayandırıldı." if stopped else "ℹ️ Bu çatda işləyən komanda yoxdur.")
        await asyncio.sleep(2)
        try:
            await event.delete()
        except Exception:
            pass

    # ═════════════ 🛠 .menu — admin paneli ═════════════
    async def bot_username():
        if not state.get("bot_username"):
            state["bot_username"] = (await context.bot.get_me()).username
        return state["bot_username"]

    def text_menu() -> str:
        L = ["🛠 <b>Userbot paneli</b> <i>(mətn rejimi — bu çatda düymə göndərilə bilmədi)</i>"]
        for _, title, sub, keys in HELP_CATS[:2]:
            L += ["", f"<b>{title}</b>"]
            L += [f"• <code>{escape(HELP[k][0])}</code> — {escape(HELP[k][1])}" for k in keys]
        return "\n".join(L)

    async def on_menu(event):
        client = event.client
        tok = os.urandom(4).hex()[:6]
        reply_uid = None
        if event.is_reply:
            try:
                reply = await event.get_reply_message()
                reply_uid = reply.sender_id if reply else None
            except Exception:
                pass
        state["menus"][tok] = {"chat_id": event.chat_id, "reply_uid": reply_uid, "msg_id": None,
                               "time": time.time()}
        if len(state["menus"]) > 200:
            state["menus"].pop(next(iter(state["menus"])))
        try:
            results = await client.inline_query(await bot_username(), f"ub:menu:{tok}",
                                                entity=await event.get_input_chat())
            if not results:
                raise RuntimeError("inline nəticə gəlmədi")
            sent = await results[0].click(event.chat_id, reply_to=event.reply_to_msg_id, hide_via=True)
            if sent is not None:
                state["menus"][tok]["msg_id"] = sent.id
            await event.delete()
        except Exception as e:
            logger.info(f".menu inline alınmadı: {e}")
            await safe_edit(event, text_menu())

    def B(text, tok, data):
        return InlineKeyboardButton(text=text, callback_data=f"ubm:{tok}:{data}"[:64])

    def main_view(tok):
        m = state["menus"].get(tok) or {}
        filler = getattr(context, "depo_filler", None)
        client = state.get("client")
        on = client is not None and client.is_connected()
        L = ["🛠 <b>Userbot admin paneli</b>", "",
             f"📶 Userbot: {'🟢 qoşulub' if on else '🔴 qoşulmayıb'}"]
        if filler is not None:
            L.append(f"📦 Depo: {'🟢 işləyir' if filler.running else '🔴 söndürülüb'} · növbə {len(filler.queue)}")
        active = sum(1 for kinds in state["tasks"].values() for t in kinds.values() if t and not t.done())
        if active:
            L.append(f"⚙️ Aktiv iş: {active}")
        if m.get("reply_uid"):
            L.append("👤 <i>Reply edilən şəxs var — 👥 Bu çat bölməsində</i>")
        L += ["", "<i>Bölmə seç:</i>"]
        rows = [
            [B("📊 Status", tok, "status"), B("🖥 Sistem", tok, "sys")],
            [B("👥 Bu çat", tok, "chat"), B("📦 Depo", tok, "depo")],
            [B("❓ Help", tok, "help"), B("❌ Bağla", tok, "close")],
        ]
        return "\n".join(L), InlineKeyboardMarkup(inline_keyboard=rows)

    def back_row(tok, target="main", label="⬅️ Geri"):
        return [B(label, tok, target), B("🏠 Menyu", tok, "main")] if target != "main" else [B("⬅️ Menyu", tok, "main")]

    async def status_view(tok):
        client = state.get("client")
        rows = [[B("🔄 Yenilə", tok, "status")], back_row(tok)]
        if not client or not client.is_connected():
            return "📊 <b>Status</b>\n\n🔴 Userbot qoşulmayıb.", InlineKeyboardMarkup(inline_keyboard=rows)
        t0 = time.monotonic()
        me = await client.get_me()
        ping = (time.monotonic() - t0) * 1000
        st = await static_info()
        active = sum(1 for kinds in state["tasks"].values() for t in kinds.values() if t and not t.done())
        filler = getattr(context, "depo_filler", None)
        try:
            n_src = len(filler.tg_source_ids()) if filler else 0
        except Exception:
            n_src = 0
        scan = getattr(context, "depo_scan_state", {}) or {}
        text = ("📊 <b>Status</b>\n\n"
                f"👤 Hesab: {mention(me)} (@{escape(me.username or '—')})\n"
                f"🆔 <code>{me.id}</code>\n"
                f"📶 Ping: {ping:.0f} ms\n"
                f"⏱ Bot işləyir: {fmt_span(time.time() - st['bot_start'])}\n"
                f"⚙️ Aktiv .fastfetch / .clear: {active}\n"
                f"✈️ Telegram mənbələri: {n_src}" + (" · 🔎 skan gedir" if scan.get("running") else "") + "\n"
                f"🧩 Telethon {telethon_version} · Py {st['py']}\n"
                f"<i>{datetime.now():%H:%M:%S}</i>")
        return text, InlineKeyboardMarkup(inline_keyboard=rows)

    def sys_kb(tok, live=False):
        first = [B("⏹ Dayandır", tok, "sys:stop")] if live else [B("🔄 Yenilə", tok, "sys"),
                                                                  B("▶️ Canlı (60 san.)", tok, "sys:live")]
        return InlineKeyboardMarkup(inline_keyboard=[first, back_row(tok)])

    async def sys_view(tok):
        st = await static_info()
        live = LiveStats()
        await asyncio.sleep(0.5)
        return render_fastfetch(st, live.sample(), "bir dəfəlik"), sys_kb(tok)

    async def chat_entity(tok):
        m = state["menus"].get(tok)
        client = state.get("client")
        if not m or not client:
            return None, None
        return client, await client.get_entity(m["chat_id"])

    async def chat_view(tok, note=""):
        m = state["menus"].get(tok)
        if not m:
            return ("⌛ Bu panel köhnəlib (bot yenidən başladı). Çatda yenidən <code>.menu</code> yaz.",
                    InlineKeyboardMarkup(inline_keyboard=[[B("❓ Help", tok, "help"), B("❌ Bağla", tok, "close")]]))
        client, chat = await chat_entity(tok)
        L = ["👥 <b>Bu çat</b>", ""] + await chat_block(client, chat)
        rows = [[B("🔄 Yenilə", tok, "chat")]]
        if m.get("reply_uid"):
            rows.append([B("👤 Reply edilənin məlumatı", tok, "chat:reply")])
        if isinstance(chat, Channel):
            rows.append([B("🧹 Banları təmizlə", tok, "chat:ask:ban"),
                         B("🧹 Ban + məhdud", tok, "chat:ask:all")])
        t = state["tasks"].get(m["chat_id"], {}).get("clear")
        if t and not t.done():
            L.append("\n🧹 <i>Ban təmizləmə gedir...</i>")
            rows.append([B("⏹ Təmizləməni dayandır", tok, "chat:stop")])
        if note:
            L += ["", note]
        rows.append(back_row(tok))
        return "\n".join(L), InlineKeyboardMarkup(inline_keyboard=rows)

    def depo_view(tok, note=""):
        filler = getattr(context, "depo_filler", None)
        rows = []
        if filler is None:
            L = ["📦 <b>Depo</b>", "", "❌ depo_filler_plugin yüklənməyib."]
        else:
            st = filler.stats
            on = filler.running
            L = ["📦 <b>Depo doldurucu</b>", "",
                 f"📶 Vəziyyət: <b>{'🟢 işləyir' if on else '🔴 söndürülüb'}</b>",
                 f"⬆️ Yükləndi: <b>{st.get('done', 0)}</b> · ⏭ depoda idi: {st.get('cached', 0)} · ❌ {st.get('failed', 0)}",
                 f"📋 Növbə: <b>{len(filler.queue)}</b>"]
            if st.get("current"):
                L.append(f"⏳ İndi: <i>{escape(str(st['current'])[:60])}</i>")
            if st.get("note"):
                L.append(f"ℹ️ {escape(str(st['note']))}")
            try:
                srcs = filler.sources()
                L.append(f"🗂 Mənbə: {len(srcs)} · ✈️ Telegram: {len(filler.tg_source_ids())}")
            except Exception:
                pass
            scan = getattr(context, "depo_scan_state", {}) or {}
            fix_count = getattr(context, "fix_artist_count", None)
            try:
                bad = fix_count() if fix_count else 0
            except Exception:
                bad = 0
            if bad:
                L.append(f"🩹 Təmizlik gözləyir: {bad} <i>(botda /fix)</i>")
            rows.append([B("⏸ Söndür", tok, "depo:off") if on else B("▶️ İşə sal", tok, "depo:on"),
                         B("🔁 Növbəni qur", tok, "depo:rebuild")])
            if scan.get("running"):
                L.append("🔎 <i>Skan gedir</i>")
                rows.append([B("⏹ Skanı dayandır", tok, "depo:scanstop")])
            rows.append([B("🔄 Yenilə", tok, "depo")])
        if note:
            L += ["", note]
        rows.append(back_row(tok))
        return "\n".join(L), InlineKeyboardMarkup(inline_keyboard=rows)

    def help_view(tok):
        L = ["❓ <b>Help</b>", "", "Bölmə seç, sonra komandanın düyməsinə bas:"]
        L += [f"• <b>{title}</b> — <i>{escape(sub)}</i>" for _, title, sub, _ in HELP_CATS]
        rows = [[B(title, tok, f"help:c:{key}")] for key, title, _, _ in HELP_CATS]
        rows.append(back_row(tok))
        return "\n".join(L), InlineKeyboardMarkup(inline_keyboard=rows)

    def help_cat_view(tok, cat):
        c = next((x for x in HELP_CATS if x[0] == cat), None)
        if not c:
            return help_view(tok)
        key, title, sub, keys = c
        L = [f"<b>{title}</b>", f"<i>{escape(sub)}</i>", ""]
        L += [f"• <code>{escape(HELP[k][0])}</code> — {escape(HELP[k][1])}" for k in keys]
        btns = [B(HELP[k][0], tok, f"help:x:{k}") for k in keys]
        rows = [btns[i:i + 3] for i in range(0, len(btns), 3)]
        rows.append(back_row(tok, "help", "⬅️ Help"))
        return "\n".join(L), InlineKeyboardMarkup(inline_keyboard=rows)

    def help_cmd_view(tok, k):
        if k not in HELP:
            return help_view(tok)
        b, short, long = HELP[k]
        cat = next((c[0] for c in HELP_CATS if k in c[3]), "acc")
        rows = [back_row(tok, f"help:c:{cat}", "⬅️ Bölmə")]
        return f"<b>{escape(b)}</b> — {escape(short)}\n\n{long}", InlineKeyboardMarkup(inline_keyboard=rows)

    # ── bot tərəfi: inline sorğu və düymələr ──
    @dp.inline_query(F.query.startswith("ub:"))
    async def ub_inline(q: ag_types.InlineQuery):
        if not is_creator(q.from_user.id):
            await q.answer([], cache_time=0, is_personal=True)
            return
        parts = q.query.split(":")
        tok = parts[2] if len(parts) > 2 and parts[2].isalnum() else "0"
        text, kb = main_view(tok)
        await q.answer([InlineQueryResultArticle(
            id=f"ubmenu{tok}", title="🛠 Userbot admin paneli", description="Status, sistem, çat, depo, help",
            input_message_content=InputTextMessageContent(message_text=text, parse_mode="HTML"),
            reply_markup=kb,
        )], cache_time=0, is_personal=True)

    async def edit_panel(cb_or_iid, text, kb):
        iid = cb_or_iid if isinstance(cb_or_iid, str) else cb_or_iid.inline_message_id
        try:
            if iid:
                await context.bot.edit_message_text(inline_message_id=iid, text=text, parse_mode="HTML",
                                                    reply_markup=kb, disable_web_page_preview=True)
            elif getattr(cb_or_iid, "message", None):
                await cb_or_iid.message.edit_text(text, parse_mode="HTML", reply_markup=kb,
                                                  disable_web_page_preview=True)
        except Exception as e:
            if "not modified" not in str(e):
                logger.debug(f"panel yenilənmədi: {e}")

    def cancel_ptask(tok):
        t = state["ptasks"].pop(tok, None)
        if t and not t.done():
            t.cancel()

    async def sys_live(tok, iid):
        st = await static_info()
        live = LiveStats()
        await asyncio.sleep(0.5)
        end = time.monotonic() + FF_DEFAULT
        while state["views"].get(tok) == "sys:live":
            remaining = end - time.monotonic()
            if remaining <= 0:
                await edit_panel(iid, render_fastfetch(st, live.sample(), "bitdi"), sys_kb(tok))
                break
            await edit_panel(iid, render_fastfetch(st, live.sample(), f"{int(remaining)} san. qalıb"),
                             sys_kb(tok, live=True))
            await asyncio.sleep(FF_INTERVAL)

    async def panel_clear(tok, iid, mode):
        client, chat = await chat_entity(tok)
        chat_id = state["menus"][tok]["chat_id"]

        async def report(text, final):
            if state["views"].get(tok) != "chat:run":
                return                       # istifadəçi başqa bölməyə keçib — paneli üstələmə
            kb = InlineKeyboardMarkup(inline_keyboard=[back_row(tok, "chat", "⬅️ Çat")] if final else
                                      [[B("⏹ Dayandır", tok, "chat:stop")], back_row(tok, "chat", "⬅️ Çat")])
            await edit_panel(iid, text, kb)

        try:
            await run_clear(client, chat, mode, report)
        except asyncio.CancelledError:
            if state["views"].get(tok) == "chat:run":
                await edit_panel(iid, "⏹ Ban təmizləmə dayandırıldı.",
                                 InlineKeyboardMarkup(inline_keyboard=[back_row(tok, "chat", "⬅️ Çat")]))
            raise
        except Exception as e:
            logger.info(f"panel clear xətası: {e}")
            await edit_panel(iid, f"❌ <code>{escape(str(e))[:200]}</code>",
                             InlineKeyboardMarkup(inline_keyboard=[back_row(tok, "chat", "⬅️ Çat")]))
        finally:
            state["tasks"].get(chat_id, {}).pop("clear", None)

    @dp.callback_query(F.data.startswith("ubm:"))
    async def ub_panel_cb(cb: ag_types.CallbackQuery):
        if not is_creator(cb.from_user.id):
            await cb.answer("⛔ Bu panel yalnız sahibi üçündür", show_alert=True)
            return
        parts = cb.data.split(":")
        if len(parts) < 3:                     # köhnə format (əvvəlki versiyanın menyusu)
            await cb.answer("Bu menyu köhnədir — yenidən .menu yaz", show_alert=True)
            return
        tok, screen, args = parts[1], parts[2], parts[3:]
        iid = cb.inline_message_id
        view = ":".join([screen] + args)
        if view != "sys:live":
            cancel_ptask(tok)                 # canlı sistem rejimi başqa bölməni üstələməsin
        state["views"][tok] = view
        if len(state["views"]) > 300:
            state["views"].pop(next(iter(state["views"])))

        try:
            if screen == "main":
                await cb.answer()
                await edit_panel(cb, *main_view(tok))
            elif screen == "status":
                await cb.answer()
                await edit_panel(cb, *(await status_view(tok)))
            elif screen == "sys":
                if args[:1] == ["live"]:
                    await cb.answer("▶️ Canlı rejim")
                    cancel_ptask(tok)
                    state["ptasks"][tok] = asyncio.create_task(sys_live(tok, iid))
                elif args[:1] == ["stop"]:
                    await cb.answer("⏹")
                    await edit_panel(cb, *(await sys_view(tok)))
                else:
                    await cb.answer("🔄")
                    await edit_panel(cb, *(await sys_view(tok)))
            elif screen == "chat":
                m = state["menus"].get(tok)
                sub = args[0] if args else ""
                if sub == "reply" and m and m.get("reply_uid"):
                    await cb.answer()
                    client, chat = await chat_entity(tok)
                    ent = await client.get_entity(m["reply_uid"])
                    text = await build_info(client, ent, chat)
                    await edit_panel(cb, text, InlineKeyboardMarkup(inline_keyboard=[
                        [B("🔄 Yenilə", tok, "chat:reply")], back_row(tok, "chat", "⬅️ Çat")]))
                elif sub == "ask" and m:
                    client, chat = await chat_entity(tok)
                    err = await check_clear_rights(client, chat)
                    if err:
                        await cb.answer()
                        await edit_panel(cb, *(await chat_view(tok, err)))
                        return
                    mode = args[1] if len(args) > 1 else "ban"
                    flt = ChannelParticipantsBanned if mode == "all" else ChannelParticipantsKicked
                    try:
                        n = (await client.get_participants(chat, limit=0, filter=ChannelParticipantsKicked())).total
                        if mode == "all":
                            n += (await client.get_participants(chat, limit=0, filter=flt())).total
                    except Exception:
                        n = "?"
                    await cb.answer()
                    what = "banları və məhdudiyyətləri" if mode == "all" else "banları"
                    await edit_panel(cb, f"⚠️ <b>{escape(getattr(chat, 'title', '') or 'qrup')}</b>\n\n"
                                         f"<b>{n}</b> nəfərin {what} götürüləcək. Əminsən?",
                                     InlineKeyboardMarkup(inline_keyboard=[
                                         [B("✅ Bəli, təmizlə", tok, f"chat:go:{mode}"), B("❌ Xeyr", tok, "chat")]]))
                elif sub == "go" and m:
                    t = state["tasks"].get(m["chat_id"], {}).get("clear")
                    if t and not t.done():
                        await cb.answer("Artıq gedir", show_alert=True)
                        return
                    await cb.answer("🧹 Başladı")
                    state["views"][tok] = "chat:run"
                    track(m["chat_id"], "clear",
                          asyncio.create_task(panel_clear(tok, iid, args[1] if len(args) > 1 else "ban")))
                elif sub == "stop" and m:
                    t = state["tasks"].get(m["chat_id"], {}).get("clear")
                    if t and not t.done():
                        state["views"][tok] = "chat:run"         # dayandırma mesajı paneldə görünsün
                        t.cancel()
                        await cb.answer("⏹ Dayandırılır")
                    else:
                        await cb.answer("Gedən iş yoxdur")
                        await edit_panel(cb, *(await chat_view(tok)))
                else:
                    await cb.answer()
                    await edit_panel(cb, *(await chat_view(tok)))
            elif screen == "depo":
                filler = getattr(context, "depo_filler", None)
                sub = args[0] if args else ""
                note = ""
                if filler is not None and sub == "on":
                    filler.start()
                    note = "🟢 İşə salındı"
                elif filler is not None and sub == "off":
                    await filler.stop()
                    note = "🔴 Söndürüldü"
                elif filler is not None and sub == "rebuild":
                    filler.queue.clear()
                    filler.seen.clear()
                    filler.stats["queue_built"] = 0
                    filler.stats["last_build"] = None
                    note = "🔁 Növbə növbəti addımda yenidən qurulacaq"
                elif sub == "scanstop":
                    scan = getattr(context, "depo_scan_state", None)
                    if scan and scan.get("running"):
                        scan["stop"] = True
                        note = "⏹ Skan dayandırılır"
                await cb.answer(note or None)
                await edit_panel(cb, *depo_view(tok, f"<i>{note}</i>" if note else ""))
            elif screen == "help":
                await cb.answer()
                if args[:1] == ["c"] and len(args) > 1:
                    await edit_panel(cb, *help_cat_view(tok, args[1]))
                elif args[:1] == ["x"] and len(args) > 1:
                    await edit_panel(cb, *help_cmd_view(tok, args[1]))
                else:
                    await edit_panel(cb, *help_view(tok))
            elif screen == "close":
                await cb.answer("Bağlandı")
                cancel_ptask(tok)
                m = state["menus"].pop(tok, None)
                client = state.get("client")
                deleted = False
                if m and m.get("msg_id") and client:
                    try:
                        await client.delete_messages(m["chat_id"], [m["msg_id"]])
                        deleted = True
                    except Exception as e:
                        logger.debug(f"panel silinmədi: {e}")
                if not deleted:
                    await edit_panel(cb, "✖️ <i>Panel bağlandı</i>", None)
            else:
                await cb.answer()
                await edit_panel(cb, *main_view(tok))
        except Exception as e:
            logger.warning(f"ub panel ({view}) xətası: {e}")
            try:
                await cb.answer(f"Xəta: {str(e)[:150]}", show_alert=True)
            except Exception:
                pass

    # ═════════════ userbot-a qoşulma ═════════════
    handlers = [
        (on_alive, events.NewMessage(outgoing=True, pattern=r"^\.alive$")),
        (on_info, events.NewMessage(outgoing=True, pattern=r"^\.info(?:\s+(\S+))?$")),
        (on_fastfetch, events.NewMessage(outgoing=True, pattern=r"^\.fastfetch(?:\s+(\d+))?$")),
        (on_clear, events.NewMessage(outgoing=True, pattern=r"^\.clear(?:\s+(all))?$")),
        (on_stop, events.NewMessage(outgoing=True, pattern=r"^\.stop$")),
        (on_menu, events.NewMessage(outgoing=True, pattern=r"^\.(?:menu|help|panel)$")),
    ]

    async def attach():
        for _ in range(120):                      # depo_filler client-i yaradana qədər gözlə
            client = getattr(context, "telethon_client", None)
            if client:
                break
            await asyncio.sleep(1)
        else:
            logger.warning("⚠️ userbot_tools: telethon_client tapılmadı (depo_filler_plugin yüklənməyib?)")
            return
        for fn, ev in handlers:
            client.add_event_handler(fn, ev)
        state["client"], state["handlers"] = client, handlers
        logger.info("✅ Userbot alətləri qoşuldu: .menu .alive .info .fastfetch .clear .stop")

    try:
        state["attach"] = asyncio.create_task(attach())
    except RuntimeError:
        logger.error("userbot_tools: event loop işləmir")


async def teardown(context):
    state = getattr(context, "userbot_tools_state", None)
    if not state:
        return
    t = state.get("attach")
    if t and not t.done():
        t.cancel()
    for kinds in state["tasks"].values():
        for task in kinds.values():
            if task and not task.done():
                task.cancel()
    for task in state["ptasks"].values():
        if task and not task.done():
            task.cancel()
    client = state.get("client")
    if client:
        for fn, _ in state["handlers"]:
            try:
                client.remove_event_handler(fn)
            except Exception:
                pass
