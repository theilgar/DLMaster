"""
📱 Vahid Telethon client — core/tg_session.py

Bütün proses üçün BİR userbot client-i olur (context.telethon_client). Onu telethon_plugin yaradır
(PRIORITY 10 — hamıdan əvvəl); o plugin yoxdursa depo_filler_plugin özü yaradır.

"database is locked" qarşısı:
  Telethon-un SQLite sessiyası tranzaksiyanı save()-ə qədər açıq saxlayır — eyni userbot.session-u
  başqa client / proses açanda kilid yaranır. Burada sessiya faylı YALNIZ OXUNUR (read-only, kilid
  olsa belə oxunur), auth key yaddaşa (StringSession) köçürülür və fayla heç vaxt yazılmır.
  Fayl rejiminə qayıtmaq: config.env → TELETHON_SESSION_MODE=file
  login.py ilə yenidən giriş edəndən sonra botu restart et (yeni auth key oxunsun).

Handler reyestri:
  add_handler(context, "ad", callback, event) — handler cari client-ə qoşulur və yadda saxlanır;
  client yenidən yarananda (telethon_plugin reload) avtomatik yeni client-ə köçür. Eyni ad
  ikinci dəfə verilsə (plugin reload) köhnəsi silinir — handler təkrarlanmır.
"""
import logging
import os

logger = logging.getLogger(__name__)

API_ID = os.getenv("TELETHON_API_ID")
API_HASH = os.getenv("TELETHON_API_HASH")
SESSION_FILE = os.getenv("TELETHON_SESSION_FILE", "userbot.session")
# /menu → 🖥 Sistem → 📱 Userbotlar → 🔑 ilə bot daxilində qoşulan hesab (StringSession, 600 icazə).
# Varsa userbot.session-dan (login.py) üstündür.
STRING_FILE = os.getenv("TELETHON_STRING_FILE", os.path.join("data", "userbot.string"))


def read_string() -> str:
    try:
        with open(STRING_FILE, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def session_source() -> str:
    """'string' — bot menyusu ilə qoşulub, 'file' — login.py (userbot.session), '' — heç biri."""
    if read_string():
        return "string"
    return "file" if load_memory_session(SESSION_FILE) is not None else ""    # boş fayl sayılmır


def load_memory_session(path: str = SESSION_FILE):
    """Sessiya faylından auth key-i read-only oxuyur → StringSession. Alınmasa None."""
    if not os.path.isfile(path):
        return None
    try:
        import sqlite3
        from telethon.crypto import AuthKey
        from telethon.sessions import StringSession
        con = sqlite3.connect(f"file:{os.path.abspath(path)}?mode=ro", uri=True, timeout=15)
        try:
            row = con.execute("SELECT dc_id, server_address, port, auth_key FROM sessions").fetchone()
        finally:
            con.close()
        if not row or not row[3]:
            return None
        ss = StringSession()
        ss.set_dc(row[0], row[1], row[2])
        ss.auth_key = AuthKey(data=row[3])
        return ss
    except Exception as e:
        logger.warning(f"📱 Sessiya yaddaşa oxunmadı ({e}) — fayl rejimi")
        return None


def make_client(context):
    """context.telethon_client yoxdursa yaradır (qoşulmur) və qaytarır. Telethon / API yoxdursa None."""
    existing = getattr(context, "telethon_client", None)
    if existing is not None:
        return existing
    if not API_ID or not API_HASH:
        logger.warning("⚠️ TELETHON_API_ID / TELETHON_API_HASH yoxdur — userbot işə düşməyəcək")
        return None
    try:
        from telethon import TelegramClient
    except ImportError:
        logger.warning("⚠️ telethon quraşdırılmayıb — userbot işə düşməyəcək")
        return None
    session, mode = SESSION_FILE, "file"
    sstr = read_string()
    if sstr:
        from telethon.sessions import StringSession
        session, mode = StringSession(sstr), "string"
    elif (os.getenv("TELETHON_SESSION_MODE") or os.getenv("DEPO_USERBOT_SESSION") or "memory").lower() != "file":
        mem = load_memory_session(SESSION_FILE)
        if mem is not None:
            session, mode = mem, "memory"
    client = TelegramClient(session, int(API_ID), API_HASH)
    client.flood_sleep_threshold = 120
    context.telethon_client = client
    context.telethon_session_mode = mode
    logger.info("📱 Userbot client yaradıldı — sessiya: "
                + {"memory": "yaddaşda (fayla yazmır, kilid olmur)", "string": f"{STRING_FILE} (bot menyusu)"}
                .get(mode, SESSION_FILE))
    for name, (cb, ev) in _registry(context).items():
        client.add_event_handler(cb, ev)
    return client


async def connect(context):
    """Client-i qoşur; uğurlu olsa me qaytarır."""
    client = getattr(context, "telethon_client", None)
    if client is None:
        return None
    try:
        logger.info("📱 Telethon userbot qoşulur...")
        await client.connect()
        if not await client.is_user_authorized():
            logger.warning("⚠️ Userbot avtorizasiya olunmayıb! Əvvəlcə login.py ilə giriş edin.")
            return None
        me = await client.get_me()
        context.telethon_me = me
        logger.info(f"✅ Telethon userbot qoşuldu: {me.first_name} (@{me.username or 'yoxdur'})")
        return me
    except Exception as e:
        logger.error(f"❌ Telethon qoşulma xətası: {e}", exc_info=True)
        return None


def _registry(context) -> dict:
    reg = getattr(context, "telethon_handlers", None)
    if not isinstance(reg, dict):
        reg = {}
        context.telethon_handlers = reg
    return reg


def add_handler(context, name: str, callback, event):
    """Handler-i cari client-ə qoşur və reyestrə yazır (eyni adlı köhnəsi əvəz olunur)."""
    reg = _registry(context)
    client = getattr(context, "telethon_client", None)
    old = reg.pop(name, None)
    if old and client is not None:
        try:
            client.remove_event_handler(old[0])
        except Exception:
            pass
    reg[name] = (callback, event)
    if client is not None:
        client.add_event_handler(callback, event)


def remove_handler(context, name: str):
    reg = _registry(context)
    old = reg.pop(name, None)
    client = getattr(context, "telethon_client", None)
    if old and client is not None:
        try:
            client.remove_event_handler(old[0])
        except Exception:
            pass


# ───────────────────────── 🔑 hesabı dəyişmək / çıxmaq (bot menyusundan) ─────────────────────────
def _rebind_userbot(context, client):
    """userbot_tools_plugin-in komandaları (.menu və s.) yeni client-ə köçür."""
    ub = getattr(context, "userbot", None)
    if ub is None:
        return
    ub.client = client
    for c in getattr(ub, "commands", []):
        c["_attached"] = False
    with_attr = getattr(ub, "_attached_raw", None)
    if with_attr is not None:
        with_attr.clear()
    if hasattr(ub, "_handlers"):
        ub._handlers.clear()
    if client is not None:
        try:
            n = ub.attach_pending()
            logger.info(f"📱 Userbot komandaları yeni hesaba köçdü ({n})")
        except Exception as e:
            logger.warning(f"📱 Userbot komandaları köçmədi: {e}")


async def _drop_client(context):
    old = getattr(context, "telethon_client", None)
    task = getattr(context, "telethon_task", None)
    if task and not task.done():
        task.cancel()
    context.telethon_task = None
    if old is not None:
        try:
            await old.disconnect()
        except Exception:
            pass
    context.telethon_client = None
    context.telethon_me = None
    return old


async def swap(context, session_str: str):
    """Yeni hesab sessiyasını saxlayır, köhnə client-i bağlayır, yenisini qoşur. me və ya None."""
    from core.tg_login import write_secret
    write_secret(STRING_FILE, session_str)
    await _drop_client(context)
    client = make_client(context)              # tg_session handler-ləri (depo və s.) avtomatik köçür
    me = await connect(context) if client is not None else None
    _rebind_userbot(context, client if me else None)
    return me


async def reconnect(context):
    await _drop_client(context)
    client = make_client(context)
    me = await connect(context) if client is not None else None
    _rebind_userbot(context, client if me else None)
    return me


async def logout(context) -> str:
    """Hesabdan çıxış: Telegram-da sessiya bağlanır, sessiya faylları silinir."""
    client = getattr(context, "telethon_client", None)
    err = ""
    try:
        if client is not None:
            if not client.is_connected():
                await client.connect()
            await client.log_out()
    except Exception as e:
        err = str(e)[:150]
    await _drop_client(context)
    _rebind_userbot(context, None)
    for path in (STRING_FILE, SESSION_FILE):
        if path and os.path.isfile(path):
            try:
                os.replace(path, path + ".bak") if path == SESSION_FILE else os.remove(path)
            except OSError:
                pass
    return err
