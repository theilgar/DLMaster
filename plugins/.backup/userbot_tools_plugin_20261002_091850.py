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

from telethon import events, utils
from telethon import __version__ as telethon_version
from telethon.errors import FloodWaitError, MessageNotModifiedError, UserNotParticipantError
from telethon.tl.functions.channels import EditBannedRequest, GetParticipantRequest
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


# ───────────────────────── setup ─────────────────────────
def setup(context):
    state = {"client": None, "handlers": [], "tasks": {}}     # tasks: chat_id -> {"ff": task, "clear": task}
    context.userbot_tools_state = state

    def track(chat_id, kind, task):
        old = state["tasks"].setdefault(chat_id, {}).get(kind)
        if old and not old.done():
            old.cancel()
        state["tasks"][chat_id][kind] = task

    # ── .alive ──
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

    # ── .info ──
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
            lines = await user_block(client, ent)
            if event.is_group:
                lines += await group_block(client, await event.get_chat(), ent)
            await safe_edit(event, "\n".join(lines))
        except Exception as e:
            logger.info(f".info xətası: {e}")
            await safe_edit(event, f"❌ <b>.info:</b> <code>{escape(str(e))[:200]}</code>")

    # ── .fastfetch ──
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
                    text = render_fastfetch(st, live.sample(), left)
                    await safe_edit(event, text)
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

    # ── .clear ──
    async def on_clear(event):
        client = event.client
        mode = (event.pattern_match.group(1) or "").strip().lower()
        chat = await event.get_chat()
        if not isinstance(chat, Channel):
            await safe_edit(event, "ℹ️ <b>.clear</b> yalnız superqrup / kanalda işləyir "
                                   "(adi qrupda ban siyahısı olmur).")
            return
        perms = await client.get_permissions(chat, "me")
        if not (perms.is_creator or getattr(perms, "ban_users", False)):
            await safe_edit(event, "⛔ Bu qrupda <b>ban etmə</b> admin icazən yoxdur.")
            return

        async def work():
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
            if not total:
                await safe_edit(event, "✅ Ban siyahısı onsuz da boşdur.")
                return
            done = failed = 0
            last = 0.0
            title = escape(chat.title or "qrup")
            await safe_edit(event, f"🧹 <b>{title}</b>: {total} nəfər təmizlənir...")
            for label, flt in filters:
                async for user in client.iter_participants(chat, filter=flt()):
                    while True:
                        try:
                            await client(EditBannedRequest(chat, user, ChatBannedRights(until_date=None)))
                            done += 1
                            break
                        except FloodWaitError as e:
                            await safe_edit(event, f"⏳ Telegram limiti — {e.seconds} san. gözlənilir... "
                                                   f"({done}/{total})")
                            await asyncio.sleep(e.seconds + 1)
                        except Exception as e:
                            failed += 1
                            logger.debug(f"unban {user.id}: {e}")
                            break
                    if time.monotonic() - last > 3:
                        last = time.monotonic()
                        await safe_edit(event, f"🧹 <b>{title}</b>\n{label}: {done}/{total} · ❌ {failed}\n"
                                               f"<i>Dayandırmaq: .stop</i>")
                    await asyncio.sleep(0.3)
            summary = " · ".join(f"{k}: {v}" for k, v in counts.items())
            await safe_edit(event, f"✅ <b>{title}</b> — təmizləndi\n"
                                   f"🧹 Götürüldü: <b>{done}</b> · ❌ alınmadı: {failed}\n<i>{summary}</i>")

        async def runner():
            try:
                await work()
            except asyncio.CancelledError:
                await safe_edit(event, "⏹ <b>.clear</b> dayandırıldı.")
                raise
            except Exception as e:
                logger.info(f".clear xətası: {e}")
                await safe_edit(event, f"❌ <b>.clear:</b> <code>{escape(str(e))[:200]}</code>")

        track(event.chat_id, "clear", asyncio.create_task(runner()))

    # ── .stop ──
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

    handlers = [
        (on_alive, events.NewMessage(outgoing=True, pattern=r"^\.alive$")),
        (on_info, events.NewMessage(outgoing=True, pattern=r"^\.info(?:\s+(\S+))?$")),
        (on_fastfetch, events.NewMessage(outgoing=True, pattern=r"^\.fastfetch(?:\s+(\d+))?$")),
        (on_clear, events.NewMessage(outgoing=True, pattern=r"^\.clear(?:\s+(all))?$")),
        (on_stop, events.NewMessage(outgoing=True, pattern=r"^\.stop$")),
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
        logger.info("✅ Userbot alətləri qoşuldu: .alive .info .fastfetch .clear .stop")

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
    client = state.get("client")
    if client:
        for fn, _ in state["handlers"]:
            try:
                client.remove_event_handler(fn)
            except Exception:
                pass
