"""🎯 .filter — açar sözə və ya cümləyə görə avtomatik cavab.

  .filter <söz/cümlə> <cavab> — yeni filtr (dırnaqla: .filter "salam aleykum" "aleykuma salam")
  .filter <söz>               — cavabsız: bot cavabı soruşur → mətn YAZ və ya stiker GÖNDƏR
  .filters                    — filtrlər kartı: filtrə bas → rejimləri dəyiş / sil, ➕ əlavə et
  .stopfilter <söz/cümlə>     — filtri sil
  .clearfilters               — bu çatdakı BÜTÜN filtrləri sil (təsdiqlə; kartda 🧹 düyməsi)

Hər filtrin iki ayarı var (kartdan və ya bayraqlarla):
  🎯 Tam uyğun   — mesaj YALNIZ bu söz/cümlədir ("salam", "Salam!", "salam 👋" — bəli; "salam aleykum" — xeyr)
  🔍 İçində      — söz/cümlə mesajın içində ayrıca söz kimi keçir ("salam necəsən" — bəli)
  👥 Hamıya      — çatda kim yazsa
  ↩️ Mənə reply  — yalnız kimsə MƏNİM mesajıma reply edib yazanda

Bayraqlar (.filter-dən sonra, istənilən sırada):
  -e tam uyğun · -c içində · -r yalnız mənə reply · -a hamıya
  Nümunə: .filter -r -e necəsən "Yaxşıyam, sən necəsən?"

Bir mesaja bir neçə filtr uyğun gələrsə, ən dəqiqi cavab verir: əvvəl 🎯 tam uyğun, sonra ən uzun cümlə.
Yəni "salam aleykum" yazılanda "salam aleykum" filtri cavab verir, "salam" filtri susur.

🖼 Stiker:
  .filter salam  (stikerə reply edib)   — "salam" yazılanda həmin stikerlə cavab
  .sfilter <cavab>  (stikerə reply edib) — kimsə HƏMİN stikeri göndərəndə cavab (mətn; kartdan stiker də təyin olunur)
  Kartda filtrə bas → 🖼 Stiker təyin et / sil

🔔 Tağ cavabı (.filters → 🔔 və ya .tagcavab):
  kimsə məni @tağ edəndə həmin adama seçilmiş stiker (reply) göndərilir; stiker göndərilə bilməsə
  (çatda stiker qadağandır və s.) seçilmiş reaksiya onun mesajına qoyulur.
  🌐 Qlobal (bütün çatlar) və ya 💬 yalnız bu çat — çat ayarı qlobaldan üstündür.

Yeni filtrlərin standart rejimi kartda (⚙️) seçilir.
Filtrlər 🗄 userbot bazasında saxlanır (data/userbot.db → records, ns="filters"; hər filtr ayrıca sətir).
Köhnə JSON (userbot:filters) ilk açılışda avtomatik köçürülür.
"""
import contextlib
import json
import re
import time
import unicodedata

from telethon import events
from telethon.tl.types import (DocumentAttributeSticker, InputDocument, InputStickerSetID, MessageEntityMention,
                               MessageEntityMentionName)
from core.userbot_api import Out, ff, get_db, logger
from core.userbot_db import get_udb

DONE_VISIBLE = 4
NS = "filters"                    # 🗄 userbot bazası: records(ns, chat_id, keyword)
DB_KEY = "userbot:filters"        # köhnə JSON (yalnız köçürmə üçün)
DEF_KEY = "userbot:filters_def"
PAGE_SIZE = 8            # 📄 filtrlər kartında bir səhifədə neçə filtr

MODES = {"exact": "🎯 Tam uyğun", "contains": "🔍 İçində"}
SCOPES = {"all": "👥 Hamıya", "reply": "↩️ Mənə reply"}
MODE_ICON = {"exact": "🎯", "contains": "🔍"}
SCOPE_ICON = {"all": "👥", "reply": "↩️"}
FLAGS = {"-e": ("m", "exact"), "-c": ("m", "contains"), "-r": ("s", "reply"), "-a": ("s", "all")}

# {chat_id: {keyword: {"r": cavab, "m": "exact"|"contains", "s": "all"|"reply"}}}
_FILTERS = {}
_RX = {}                 # keyword -> compiled regex (keş)
_WAITING_ADD = {}        # chat_id -> Out instance (➕ düyməsi ilə gözləmə rejimi)
_DEF = {"m": "exact", "s": "all"}
_WAITING_STICKER = {}    # chat_id -> {"o": Out, "kind": "mention"|"filter", "target": ..., "back": coroutine fn}

MENTION_NS = "mention"
REACTIONS = ["👍", "❤️", "🔥", "😁", "🤔", "👀", "🥰", "👏", "😎", "🙏", "💯", "🤝"]
COOLDOWNS = [0, 30, 60, 300, 900]
MENTION_DEF = {"on": False, "st": None, "react": "👍", "cd": 60, "rep": False}
_MENTION_LAST = {}       # (chat_id, user_id) -> son cavab vaxtı


# ───────────────────────── saxlama ─────────────────────────
def _entry(v) -> dict:
    """Köhnə format (sadəcə cavab mətni) → yeni. Köhnə filtrlər əvvəlki kimi 🔍 içində işləyir."""
    if isinstance(v, dict):
        e = {"r": str(v.get("r", "")), "m": v.get("m") if v.get("m") in MODES or v.get("m") == "sticker"
             else "contains", "s": v.get("s") if v.get("s") in SCOPES else "all"}
        if v.get("st"):
            e["st"] = v["st"]                      # 🖼 cavab stikeri
        if v.get("trig"):
            e["trig"] = v["trig"]                  # 🖼 tetikləyici stiker {"doc", "emoji"}
        return e
    return {"r": str(v), "m": "contains", "s": "all"}


def _load():
    _FILTERS.clear()
    udb = get_udb()
    try:
        rows = udb.rec_list(NS)
        if not rows:                                   # köhnə JSON-dan bir dəfəlik köçürmə
            raw = get_db().get_setting(DB_KEY)
            data = json.loads(raw) if raw else {}
            rows = [(int(cid), str(k), _entry(v)) for cid, fl in data.items() if isinstance(fl, dict)
                    for k, v in fl.items()]
            if rows:
                udb.rec_replace(NS, rows)
                logger.info(f"🗄 .filter: {len(rows)} filtr userbot bazasına köçürüldü")
        for cid, kw, f in rows:
            _FILTERS.setdefault(int(cid), {})[kw] = _entry(f)
    except Exception as e:
        logger.warning(f".filter yüklənmədi: {e}")
    d = udb.kv_get(NS, "defaults") or {}
    if not d:
        try:
            d = json.loads(get_db().get_setting(DEF_KEY) or "{}")
        except Exception:
            d = {}
    if d.get("m") in MODES:
        _DEF["m"] = d["m"]
    if d.get("s") in SCOPES:
        _DEF["s"] = d["s"]


def _save():
    try:
        get_udb().rec_replace(NS, [(c, kw, f) for c, fl in _FILTERS.items() for kw, f in fl.items()])
    except Exception as e:
        logger.warning(f".filter saxlanmadı: {e}")


def _save_def():
    try:
        get_udb().kv_set(NS, "defaults", dict(_DEF))
    except Exception as e:
        logger.warning(f".filter standartları saxlanmadı: {e}")


# ───────────────────────── mətn ─────────────────────────
def _norm(text: str) -> str:
    """Müqayisə üçün: Azərbaycan / türk İ-ı düzgün kiçildilir, artıq boşluqlar yığılır."""
    t = unicodedata.normalize("NFC", text or "").replace("İ", "i").replace("I", "ı").lower()
    t = t.replace("\u0307", "")                       # "i̇" (birləşən nöqtə) qalıqları
    return " ".join(t.split())


def _strip_edges(t: str) -> str:
    """Tam uyğunluq üçün: kənarlardakı durğu işarələri və emojilər sayılmır ("Salam!!! 👋" = "salam")."""
    return re.sub(r"^[\W_]+|[\W_]+$", "", t)


def _rx(keyword):
    r = _RX.get(keyword)
    if r is None:
        words = keyword.split()
        pattern = r"\s+".join(re.escape(w) for w in words) if words else re.escape(keyword)
        r = _RX[keyword] = re.compile(rf"(?<!\w){pattern}(?!\w)", re.IGNORECASE)
    return r


def _match(kw: str, f: dict, norm_text: str, bare: str) -> bool:
    if f["m"] == "exact":
        return bare == _strip_edges(kw)
    return bool(_rx(kw).search(norm_text))


def _pick(fl: dict, text: str, is_reply_to_me: bool):
    """Ən dəqiq uyğun filtr: 🎯 tam uyğun > ən çox söz > ən uzun. Yoxdursa None."""
    norm_text = _norm(text)
    bare = _strip_edges(norm_text)
    best, best_key = None, None
    for kw, f in fl.items():
        if f["m"] == "sticker" or (f["s"] == "reply" and not is_reply_to_me):
            continue
        if not _match(kw, f, norm_text, bare):
            continue
        key = (1 if f["m"] == "exact" else 0, len(kw.split()), len(kw))
        if best_key is None or key > best_key:
            best, best_key = (kw, f), key
    return best


def _pick_sticker(fl: dict, doc_id: int, is_reply_to_me: bool):
    for kw, f in fl.items():
        if f["m"] == "sticker" and (f.get("trig") or {}).get("doc") == doc_id \
                and (f["s"] != "reply" or is_reply_to_me):
            return kw, f
    return None


# ───────────────────────── 🖼 stikerlər ─────────────────────────
def _sticker_attr(doc):
    return next((a for a in getattr(doc, "attributes", []) or [] if isinstance(a, DocumentAttributeSticker)), None)


def sticker_ref(msg, src=None) -> dict:
    """Stiker mesajı → saxlanıla bilən istinad. src — təzələmə mənbəyi (Saxlanılan mesajlar)."""
    doc = msg.document
    attr = _sticker_attr(doc)
    sset = getattr(attr, "stickerset", None)
    return {"id": doc.id, "ah": doc.access_hash, "fr": (doc.file_reference or b"").hex(),
            "src": src, "set": [sset.id, sset.access_hash] if isinstance(sset, InputStickerSetID) else None,
            "emoji": getattr(attr, "alt", "") or "🖼"}


async def capture_sticker(client, msg) -> dict:
    """Stikeri "Saxlanılan mesajlar"-a da göndərir — file_reference köhnələndə oradan təzələnir
    (orijinal mesaj silinsə də stiker işləməyə davam edir)."""
    src = None
    try:
        saved = await client.send_file("me", msg.document, silent=True)
        src = ["me", saved.id]
        msg = saved
    except Exception as e:
        logger.debug(f"stiker Saxlanılanlara göndərilmədi: {e}")
        src = [msg.chat_id, msg.id]
    return sticker_ref(msg, src)


async def _refresh_ref(client, ref) -> bool:
    """file_reference köhnəlib — mənbə mesajdan və ya stiker paketindən təzəsini al."""
    try:
        if ref.get("src"):
            chat, mid = ref["src"]
            m = await client.get_messages(chat, ids=mid)
            if m and m.document and m.document.id == ref["id"]:
                ref.update(ah=m.document.access_hash, fr=(m.document.file_reference or b"").hex())
                return True
        if ref.get("set"):
            from telethon.tl.functions.messages import GetStickerSetRequest
            res = await client(GetStickerSetRequest(InputStickerSetID(*ref["set"]), hash=0))
            for d in res.documents:
                if d.id == ref["id"]:
                    ref.update(ah=d.access_hash, fr=(d.file_reference or b"").hex())
                    return True
    except Exception as e:
        logger.debug(f"stiker təzələnmədi: {e}")
    return False


async def send_sticker(client, chat_id, ref, reply_to=None):
    """→ (göndərildi?, ref dəyişdi?)"""
    changed = False
    for attempt in (1, 2):
        try:
            await client.send_file(chat_id, InputDocument(ref["id"], ref["ah"], bytes.fromhex(ref["fr"] or "")),
                                   reply_to=reply_to)
            return True, changed
        except Exception as e:
            err = str(e).upper()
            if attempt == 1 and "FILE_REFERENCE" in err and await _refresh_ref(client, ref):
                changed = True
                continue
            logger.info(f"stiker göndərilmədi ({chat_id}): {e}")
            return False, changed
    return False, changed


async def send_reaction(client, chat_id, msg_id, emoji) -> bool:
    from telethon.tl.functions.messages import SendReactionRequest
    from telethon.tl.types import ReactionEmoji
    try:
        await client(SendReactionRequest(peer=chat_id, msg_id=msg_id, reaction=[ReactionEmoji(emoticon=emoji)]))
        return True
    except TypeError:                               # köhnə Telethon: reaction=str
        try:
            await client(SendReactionRequest(peer=chat_id, msg_id=msg_id, reaction=emoji))
            return True
        except Exception as e:
            logger.info(f"reaksiya qoyulmadı: {e}")
    except Exception as e:
        logger.info(f"reaksiya qoyulmadı ({chat_id}): {e}")
    return False


# ───────────────────────── 🔔 tağ cavabı ayarları ─────────────────────────
def mention_cfg(chat_id=None, effective=True):
    """effective=True: çat ayarı varsa o, yoxdursa qlobal. chat_id=None → qlobal."""
    udb = get_udb()
    if chat_id is not None:
        c = udb.rec_get(MENTION_NS, chat_id, "cfg")
        if c is not None or not effective:
            return dict(MENTION_DEF, **(c or {})) if c is not None else None
    return dict(MENTION_DEF, **(udb.kv_get(MENTION_NS, "global") or {}))


def mention_save(target, cfg):
    udb = get_udb()
    if target == "global":
        udb.kv_set(MENTION_NS, "global", cfg)
    elif cfg is None:
        udb.rec_del(MENTION_NS, target, "cfg")
    else:
        udb.rec_put(MENTION_NS, target, "cfg", cfg)


def _is_tag(msg, me, include_reply: bool) -> bool:
    if not getattr(msg, "mentioned", False):        # Telegram özü işarələyir (tağ və ya mənə reply)
        return False
    names = {u.lower() for u in [getattr(me, "username", None)] +
             [getattr(x, "username", None) for x in (getattr(me, "usernames", None) or [])] if u}
    try:
        for ent, txt in msg.get_entities_text():
            if isinstance(ent, MessageEntityMentionName) and ent.user_id == me.id:
                return True
            if isinstance(ent, MessageEntityMention) and txt.lstrip("@").lower() in names:
                return True
    except Exception:
        pass
    return include_reply and bool(msg.reply_to)    # yalnız reply ilə "mentioned" olub


# ───────────────────────── əmr arqumentləri ─────────────────────────
def _take_flags(text: str):
    """Mətnin əvvəlindəki -e / -c / -r / -a bayraqları → (qalan mətn, {"m":..,"s":..})."""
    opts = {}
    while True:
        m = re.match(r"^(-[ecra])(?:\s+|$)", text)
        if not m:
            return text, opts
        k, v = FLAGS[m.group(1)]
        opts[k] = v
        text = text[m.end():]


def _parse_filter_args(raw_text):
    """Mətndən açar sözü/cümləni və cavabı ayırır.

    Nümunələr:
      "salam aleykum" "aleykuma salam"
      "salam aleykum" aleykuma salam
      salam aleykuma salam
      "salam aleykum" (reply olduqda cavabsız)
    """
    if not raw_text:
        return None, None
    text = raw_text.strip()
    if text.startswith(".filter"):
        text = text[7:].strip()
    if not text:
        return None, None

    m = re.match(r'''^["'“]([^"'”]+)["'”](?:\s+([\s\S]+))?$''', text)
    if m:
        kw = m.group(1).strip()
        rep = (m.group(2) or "").strip()
        rep_m = re.match(r'''^["'“]([\s\S]*?)["'”]$''', rep)
        if rep_m:
            rep = rep_m.group(1)
        return kw, rep

    parts = text.split(None, 1)
    kw = parts[0].strip()
    rep = parts[1].strip() if len(parts) > 1 else ""
    if rep:
        rep_m = re.match(r'''^["'“]([\s\S]*?)["'”]$''', rep)
        if rep_m:
            rep = rep_m.group(1)
    return kw, rep


def _preview(text, n=60):
    t = " ".join(str(text).split())
    return t if len(t) <= n else t[:n - 1] + "…"


def _tags(f) -> str:
    m = "🖼" if f["m"] == "sticker" else MODE_ICON[f["m"]]
    return f"{m}{SCOPE_ICON[f['s']]}" + ("🎴" if f.get("st") else "")


def _label(kw, f) -> str:
    """Siyahıda göstəriləcək ad (stiker tetikləyicisi üçün emoji)."""
    if f["m"] == "sticker":
        return f"stiker {(f.get('trig') or {}).get('emoji', '🖼')}"
    return kw


def _answer_preview(f, n=36) -> str:
    parts = []
    if f.get("st"):
        parts.append(f"🎴 stiker {f['st'].get('emoji', '')}")
    if f.get("r"):
        parts.append(_preview(f["r"], n))
    return " + ".join(parts) or "—"


def _put(chat_id, keyword, reply_text, opts=None, st=None, trig=None) -> tuple:
    """Filtri yazır; mövcuddursa cavab yenilənir, rejimlər (bayraq verilməyibsə) saxlanır.
    st — cavab stikeri, trig — tetikləyici stiker (onda açar "🖼<doc_id>", rejim "sticker")."""
    kw = f"🖼{trig['doc']}" if trig else _norm(keyword).strip()
    fl = _FILTERS.setdefault(chat_id, {})
    old = fl.get(kw)
    opts = opts or {}
    fl[kw] = {"r": (reply_text or "").strip(),
              "m": "sticker" if trig else (opts.get("m") or (old["m"] if old and old["m"] != "sticker" else _DEF["m"])),
              "s": opts.get("s") or (old["s"] if old else _DEF["s"])}
    if st or (old and old.get("st") and not reply_text):
        fl[kw]["st"] = st or old["st"]
    if trig:
        fl[kw]["trig"] = trig
    _RX.pop(kw, None)
    _save()
    return kw, old is not None


def _clear_chat(chat_id) -> int:
    fl = _FILTERS.pop(chat_id, {}) or {}
    for kw in fl:
        _RX.pop(kw, None)
    if fl:
        _save()
    return len(fl)


def register(ub):
    _load()

    def card(rows, footer=None):
        return ff(ub.title("filter"), rows, footer=footer)

    # ── kart görünüşləri ──
    def pages_of(chat_id) -> int:
        return max(1, (len(_FILTERS.get(chat_id, {})) + PAGE_SIZE - 1) // PAGE_SIZE)

    def page_of(out, chat_id) -> int:
        p = max(0, min(getattr(out, "page", 0), pages_of(chat_id) - 1))
        out.page = p
        return p

    def list_body(chat_id, page=0):
        fl = _FILTERS.get(chat_id, {})
        n = pages_of(chat_id)
        page = max(0, min(page, n - 1))
        body = [("Çat", chat_id), ("Filtr", f"{len(fl)} aktiv" + (f" · 📄 {page + 1}/{n}" if n > 1 else "")),
                ("Yeni filtr", f"{MODES[_DEF['m']]} · {SCOPES[_DEF['s']]}")]
        if fl:
            items = list(fl.items())
            start = page * PAGE_SIZE
            body.append("# Siyahı" + (f" — səhifə {page + 1}/{n}" if n > 1 else ""))
            body += [(f"{i}. {_tags(f)}", f"{_label(kw, f)} → {_answer_preview(f)}")
                     for i, (kw, f) in enumerate(items[start:start + PAGE_SIZE], start + 1)]
            body.append("# İşarələr")
            body.append(("🎯 / 🔍 / 🖼", "tam uyğun / içində / stiker göndəriləndə"))
            body.append(("👥 / ↩️", "hamıya / yalnız mənə reply"))
            body.append(("🎴", "cavab stikerdir"))
        mc = mention_cfg(chat_id)
        body.append("# 🔔 Tağ cavabı")
        body.append(("Bu çatda", ("🟢 " if mc["on"] else "🔴 ") + ("çat ayarı" if mention_cfg(chat_id, False) else "qlobal")
                     + (f" · 🎴 {mc['st'].get('emoji', '')}" if mc.get("st") else "") + f" · {mc['react']}"))
        return body

    def list_rows(out, chat_id):
        page = page_of(out, chat_id)
        n = pages_of(chat_id)
        kws = list(_FILTERS.get(chat_id, {}))
        start = page * PAGE_SIZE
        ensure = getattr(out, "ensure_actions", None)
        btns = []
        for i in range(start, min(start + PAGE_SIZE, len(kws))):
            if ensure:
                ensure(i)                                     # düymə hərəkətləri səhifə açılanda qeydə alınır
            k = kws[i]
            btns.append(out.btn(f"{i + 1}. {_tags(_FILTERS[chat_id][k])} {_label(k, _FILTERS[chat_id][k])[:14]}",
                                f"o{i}"))
        grid = [btns[i:i + 2] for i in range(0, len(btns), 2)]
        if n > 1:
            grid.append([out.btn("⏮" if page > 1 else "·", "pg0"), out.btn("◀️", "pgp"),
                         out.btn(f"📄 {page + 1}/{n}", "pgi"), out.btn("▶️", "pgn"),
                         out.btn("⏭" if page < n - 2 else "·", "pgl")])
        grid.append([out.btn("➕ Filtr əlavə et", "add_fl"), out.btn("⚙️ Standart rejim", "defs")])
        grid.append([out.btn("🔔 Tağ cavabı", "men")]
                    + ([out.btn(f"🧹 Hamısını təmizlə ({len(kws)})", "clr")] if kws else []))
        return grid

    def foot(chat_id, extra=None):
        if extra:
            return extra
        return "filtrə bas → rejim / sil | ➕ əlavə et" if _FILTERS.get(chat_id) \
            else "əlavə: ➕ düyməsi və ya .filter \"cümlə\" \"cavab\""

    def detail_body(kw, f):
        when = {"exact": "mesaj yalnız bu söz/cümlədir", "contains": "söz/cümlə mesajın içində keçir",
                "sticker": "kimsə bu stikeri göndərir"}[f["m"]]
        return [("Filtr", _label(kw, f)), ("Cavab", _answer_preview(f, 80)),
                ("Uyğunluq", "🖼 Stiker" if f["m"] == "sticker" else MODES[f["m"]]), ("Kimə", SCOPES[f["s"]]),
                "# Nə vaxt cavab verir",
                ("", when + (" və mənim mesajıma reply-dır" if f["s"] == "reply" else ""))]

    def detail_rows(out, i, f):
        rows = []
        if f["m"] != "sticker":
            rows.append([out.btn(("✅ " if f["m"] == "exact" else "") + "🎯 Tam uyğun", f"me{i}"),
                         out.btn(("✅ " if f["m"] == "contains" else "") + "🔍 İçində", f"mc{i}")])
        rows.append([out.btn(("✅ " if f["s"] == "all" else "") + "👥 Hamıya", f"sa{i}"),
                     out.btn(("✅ " if f["s"] == "reply" else "") + "↩️ Mənə reply", f"sr{i}")])
        rows.append([out.btn("🎴 Cavab stikeri" + (" (dəyiş)" if f.get("st") else ""), f"st{i}")]
                    + ([out.btn("🗑 Stikeri sil", f"sx{i}")] if f.get("st") and f.get("r") else []))
        rows.append([out.btn("🗑 Sil", f"d{i}"), out.btn("🔙 Siyahı", "back")])
        return rows

    def defs_body():
        return [("Rejim", "⚙️ Yeni filtrlərin standartı"),
                ("Uyğunluq", MODES[_DEF["m"]]), ("Kimə", SCOPES[_DEF["s"]]),
                "# Qeyd",
                ("", "mövcud filtrlərə toxunmur; .filter -e/-c/-r/-a ilə hər dəfə ayrıca da seçmək olar")]

    def defs_rows(out):
        return [[out.btn(("✅ " if _DEF["m"] == "exact" else "") + "🎯 Tam uyğun", "dme"),
                 out.btn(("✅ " if _DEF["m"] == "contains" else "") + "🔍 İçində", "dmc")],
                [out.btn(("✅ " if _DEF["s"] == "all" else "") + "👥 Hamıya", "dsa"),
                 out.btn(("✅ " if _DEF["s"] == "reply" else "") + "↩️ Mənə reply", "dsr")],
                [out.btn("🔙 Siyahı", "back")]]

    async def ask_answer(o, chat_id, keyword, opts, back, cancel_action="fcx", after_id=0):
        """Tək söz yazılıb — cavab gözlənilir: növbəti MƏTN cavab olur, STİKER isə stiker cavabı.
        after_id — bu mesajdan sonrakılar (sözü yazan mesajın özü cavab sayılmasın)."""
        kw_view = _norm(keyword).strip()
        _WAITING_STICKER[chat_id] = {"o": o, "kind": "answer", "target": keyword, "opts": opts, "back": back,
                                     "after": after_id}
        exists = kw_view in _FILTERS.get(chat_id, {})
        await o.update(card([("Filtr", kw_view),
                             ("Uyğunluq", MODES[opts.get("m") or _DEF["m"]]),
                             ("Kimə", SCOPES[opts.get("s") or _DEF["s"]]),
                             "# Cavab nə olsun?",
                             ("✍️ Mətn", "cavabı bu çata yaz"),
                             ("🎴 Stiker", "və ya istədiyin stikeri bu çata göndər")]
                            + ([("Qeyd", "bu filtr artıq var — cavabı yenilənəcək")] if exists else []),
                            footer="ləğv: 'imtina' yaz və ya düyməyə bas"),
                       rows=[[o.btn("❌ Ləğv et", cancel_action)]])

    @ub.command("filter", pattern=r"^\.filter(?:\s+([\s\S]+))?$",
                help=("açar söz və ya cümlə filtri əlavə et",
                      "<b>İstifadə:</b>\n"
                      "• <code>.filter salam Əleykum salam!</code>\n"
                      "• <code>.filter \"salam aleykum\" \"aleykuma salam\"</code>\n"
                      "• mesaja reply edib: <code>.filter \"salam aleykum\"</code>\n\n"
                      "<b>Rejimlər</b> (bayraqlar, istənilən sırada):\n"
                      "• <code>-e</code> 🎯 tam uyğun — mesaj yalnız bu sözdür\n"
                      "• <code>-c</code> 🔍 içində — söz mesajın içində keçir\n"
                      "• <code>-r</code> ↩️ yalnız kimsə mənə reply edəndə\n"
                      "• <code>-a</code> 👥 hamıya\n"
                      "Nümunə: <code>.filter -r -e necəsən \"Yaxşıyam, sən?\"</code>\n\n"
                      "Bir neçə filtr uyğun gəlsə — ən dəqiqi (tam uyğun, sonra ən uzun cümlə) cavab verir.\n\n"
                      "🎴 <b>Stikerlə cavab:</b> stikerə reply edib <code>.filter salam</code>\n"
                      "🖼 <b>Stiker göndəriləndə:</b> stikerə reply edib <code>.sfilter cavab</code>\n"
                      "Siyahı / rejim dəyişmək: <code>.filters</code> · Silmək: <code>.stopfilter &lt;söz/cümlə&gt;</code>"))
    async def on_add_filter(event):
        raw_args = (event.pattern_match.group(1) or "").strip()
        raw_args, opts = _take_flags(raw_args)
        keyword, reply_text = _parse_filter_args(raw_args)

        st = None
        if not reply_text and event.is_reply:
            reply_msg = await event.get_reply_message()
            if reply_msg and reply_msg.sticker:
                st = await capture_sticker(event.client, reply_msg)       # 🎴 stikerlə cavab
            reply_text = (reply_msg.raw_text or "") if reply_msg else ""

        if keyword and not ((reply_text or "").strip() or st):
            # tək söz — cavabı soruş: mətn yaz və ya stiker göndər
            chat_id = event.chat_id
            out = Out(ub, event)

            async def cancel(o, cb):
                _WAITING_STICKER.pop(chat_id, None)
                await o.update(card([("Filtr", _norm(keyword).strip()), ("Status", "❌ ləğv edildi")]), rows=[])
                await o.close(DONE_VISIBLE)
            out.on("fcx")(cancel)

            async def done(o, note=None):
                kw = _norm(keyword).strip()
                f = _FILTERS.get(chat_id, {}).get(kw)
                body = [("Filtr", kw), ("Status", note or "—")]
                if f and note and note.startswith("✓"):
                    body = [("Filtr", kw), ("Cavab", _answer_preview(f, 80)),
                            ("Uyğunluq", MODES[f["m"]]), ("Kimə", SCOPES[f["s"]]), ("Status", note),
                            ("Cəmi", f"{len(_FILTERS[chat_id])} filtr bu çatda")]
                await o.update(card(body), rows=[])
                await o.close(DONE_VISIBLE)
            await out.open(card([("Filtr", _norm(keyword).strip())]), [])
            await ask_answer(out, chat_id, keyword, opts, done, after_id=event.id)
            return

        if not keyword or not ((reply_text or "").strip() or st):
            await ub.out(event, card([("Əlavə (tək)", ".filter <söz> <cavab>"),
                                      ("Əlavə (cümlə)", '.filter "<cümlə>" "<cavab>"'),
                                      ("Reply", "reply → .filter <söz/cümlə>"),
                                      ("Bayraqlar", "-e tam · -c içində · -r mənə reply · -a hamıya"),
                                      ("Siyahı", ".filters"),
                                      ("Sil", '.stopfilter <söz/cümlə>')],
                                     footer="söz/cümlə və cavab daxil edilməlidir"))
            return

        kw, existed = _put(event.chat_id, keyword, reply_text, opts, st=st)
        f = _FILTERS[event.chat_id][kw]
        out = await ub.out(event, card([("Filtr", kw), ("Cavab", _answer_preview(f, 80)),
                                        ("Uyğunluq", MODES[f["m"]]), ("Kimə", SCOPES[f["s"]]),
                                        ("Status", "✓ yeniləndi" if existed else "✓ əlavə olundu"),
                                        ("Cəmi", f"{len(_FILTERS[event.chat_id])} filtr bu çatda")]))
        await out.close(DONE_VISIBLE)

    @ub.command("sfilter", pattern=r"^\.sfilter(?:\s+([\s\S]+))?$",
                help=("stiker göndəriləndə cavab",
                      "Kimsə müəyyən stikeri göndərəndə avtomatik cavab.\n\n"
                      "<b>İstifadə:</b> stikerə reply edib <code>.sfilter Cavab mətni</code>\n"
                      "Bayraqlar: <code>-r</code> yalnız mənə reply · <code>-a</code> hamıya\n"
                      "Cavabı stiker etmək: <code>.filters</code> → filtrə bas → 🎴 Cavab stikeri"))
    async def on_add_sticker_filter(event):
        raw, opts = _take_flags((event.pattern_match.group(1) or "").strip())
        reply_msg = await event.get_reply_message() if event.is_reply else None
        if not reply_msg or not reply_msg.sticker:
            await ub.out(event, card([("İstifadə", "stikerə reply → .sfilter <cavab>"),
                                      ("Nümunə", ".sfilter -r Bu stikeri sevirəm 😄")],
                                     footer="stikerə reply etmək lazımdır"))
            return
        text = re.sub(r"^[\"'“]([\s\S]*?)[\"'”]$", r"\1", raw).strip()
        if not text:
            await ub.out(event, card([("Xəta", "cavab mətni yoxdur"),
                                      ("İstifadə", "stikerə reply → .sfilter <cavab>")]))
            return
        attr = _sticker_attr(reply_msg.document)
        trig = {"doc": reply_msg.document.id, "emoji": getattr(attr, "alt", "") or "🖼"}
        kw, existed = _put(event.chat_id, "", text, opts, trig=trig)
        f = _FILTERS[event.chat_id][kw]
        out = await ub.out(event, card([("Filtr", _label(kw, f)), ("Cavab", _answer_preview(f, 80)),
                                        ("Kimə", SCOPES[f["s"]]),
                                        ("Status", "✓ yeniləndi" if existed else "✓ əlavə olundu")]))
        await out.close(DONE_VISIBLE)

    # ── 🔔 tağ cavabı kartı (həm .filters içində, həm .tagcavab ilə) ──
    def mention_view(chat_id, target):
        g = mention_cfg(None)
        c = mention_cfg(chat_id, effective=False)
        cur = g if target == "global" else (c or dict(g))
        eff = mention_cfg(chat_id)

        def line(cfg):
            if cfg is None:
                return "— (qlobal işləyir)"
            return (("🟢 açıq" if cfg["on"] else "🔴 söndürülüb")
                    + (f" · 🎴 {cfg['st'].get('emoji', '')}" if cfg.get("st") else " · stiker yox")
                    + f" · {cfg['react']}")
        body = [("Nə edir", "məni @tağ edənə stiker (reply); alınmasa — reaksiya"),
                ("🌐 Qlobal", line(g)), ("💬 Bu çat", line(c)),
                ("İndi bu çatda", "🟢 işləyir" if eff["on"] else "🔴 işləmir"),
                "# Redaktə: " + ("🌐 Qlobal (bütün çatlar)" if target == "global" else "💬 Yalnız bu çat"),
                ("Vəziyyət", "🟢 açıq" if cur["on"] else "🔴 söndürülüb"),
                ("Stiker", f"🎴 {cur['st'].get('emoji', '')}" if cur.get("st") else "yoxdur → yalnız reaksiya"),
                ("Reaksiya", cur["react"]),
                ("Təkrar", f"eyni adama {cur['cd']} san.-dən bir" if cur["cd"] else "hər dəfə"),
                ("Reply də", "✅ mənə reply də tağ sayılır" if cur["rep"] else "❌ yalnız @tağ")]
        return body, cur

    def mention_rows(o, chat_id, target, cur):
        rows = [[o.btn(("✅ " if target == "global" else "") + "🌐 Qlobal", "mtg"),
                 o.btn(("✅ " if target != "global" else "") + "💬 Bu çat", "mtc")],
                [o.btn("🟢 Açıqdır — söndür" if cur["on"] else "🔴 Söndürülüb — aç", "mon")],
                [o.btn("🎴 Stiker seç" + (" (dəyiş)" if cur.get("st") else ""), "mst")]
                + ([o.btn("🗑 Stikeri sil", "msx")] if cur.get("st") else [])]
        reacts = [o.btn(("✅" if cur["react"] == e else "") + e, f"mr{i}") for i, e in enumerate(REACTIONS)]
        rows += [reacts[i:i + 6] for i in range(0, len(reacts), 6)]
        rows.append([o.btn(f"⏱ Təkrar: {cur['cd']} san." if cur["cd"] else "⏱ Təkrar: hər dəfə", "mcd"),
                     o.btn(f"↩️ Reply də: {'✅' if cur['rep'] else '❌'}", "mrp")])
        if target != "global" and mention_cfg(chat_id, effective=False) is not None:
            rows.append([o.btn("♻️ Bu çatı qlobala qaytar", "mrs")])
        rows.append([o.btn("🔙 Geri", "back")])
        return rows

    def attach_mention(out, chat_id, back):
        """Out kartına 🔔 bölməsinin düymələrini bağlayır. back(o, cb) — geri qayıtma (None — kart özü təyin edir)."""
        state = {"target": "chat"}

        async def show(o, note=None):
            body, cur = mention_view(chat_id, state["target"])
            await o.update(card(body, footer=note or "🎴 stiker göndərilməsə — reaksiya qoyulur"),
                           rows=mention_rows(o, chat_id, state["target"], cur))

        def tkey():
            return "global" if state["target"] == "global" else chat_id

        def edit(fn):
            async def h(o, cb):
                _, cur = mention_view(chat_id, state["target"])
                note = fn(cur)
                mention_save(tkey(), cur)
                await show(o, note or "✓ yadda saxlandı")
            return h

        async def to_global(o, cb):
            state["target"] = "global"
            await show(o)

        async def to_chat(o, cb):
            state["target"] = "chat"
            await show(o)

        async def pick_sticker(o, cb):
            _WAITING_STICKER[chat_id] = {"o": o, "kind": "mention", "target": tkey(), "back": show}
            await o.update(card([("Rejim", "🎴 Tağ cavabı üçün stiker"),
                                 ("Necə", "istədiyin stikeri BU çata göndər"),
                                 ("Qeyd", "mesajın dərhal silinir, stiker Saxlanılanlarda saxlanır")],
                                footer="ləğv: 'imtina' yaz"), rows=[[o.btn("🔙 Ləğv et", "men")]])

        async def reset(o, cb):
            mention_save(chat_id, None)
            await show(o, "♻️ bu çat qlobal ayarla işləyir")

        def set_react(e):
            def f(cur):
                cur["react"] = e
                return f"reaksiya: {e}"
            return f

        def toggle_on(cur):
            cur["on"] = not cur["on"]
            return "🟢 açıldı" if cur["on"] else "🔴 söndürüldü"

        def drop_st(cur):
            cur["st"] = None
            return "🗑 stiker silindi — yalnız reaksiya"

        def cycle_cd(cur):
            cur["cd"] = COOLDOWNS[(COOLDOWNS.index(cur["cd"]) + 1) % len(COOLDOWNS)] if cur["cd"] in COOLDOWNS else 60
            return None

        def toggle_rep(cur):
            cur["rep"] = not cur["rep"]
            return None

        async def open_(o, cb):
            _WAITING_STICKER.pop(chat_id, None)
            await show(o)

        out.on("men")(open_)
        out.on("mtg")(to_global)
        out.on("mtc")(to_chat)
        out.on("mon")(edit(toggle_on))
        out.on("mst")(pick_sticker)
        out.on("msx")(edit(drop_st))
        out.on("mcd")(edit(cycle_cd))
        out.on("mrp")(edit(toggle_rep))
        out.on("mrs")(reset)
        for i, e in enumerate(REACTIONS):
            out.on(f"mr{i}")(edit(set_react(e)))
        if back is not None:
            out.on("back")(back)
        return show

    @ub.command("tagcavab", pattern=r"^\.(?:tagcavab|mention)$",
                help=("tağ edənə stiker / reaksiya",
                      "Kimsə səni @tağ edəndə ona seçilmiş stiker (reply) göndərilir; stiker göndərilə bilməsə "
                      "seçilmiş reaksiya qoyulur.\n\n🌐 Qlobal və ya 💬 yalnız bu çat — kartda seçilir.\n"
                      "<b>İstifadə:</b> <code>.tagcavab</code> (və ya <code>.filters</code> → 🔔)"))
    async def on_mention_card(event):
        chat_id = event.chat_id
        out = Out(ub, event)

        async def close(o, cb):
            _WAITING_STICKER.pop(chat_id, None)
            await o.close()
        show = attach_mention(out, chat_id, close)
        body, cur = mention_view(chat_id, "chat")

        async def refresh(o):
            await show(o)
        out.refresh = refresh
        await out.open(card(body, footer="🎴 stiker göndərilməsə — reaksiya qoyulur"),
                       mention_rows(out, chat_id, "chat", cur))

    @ub.command("clearfilters", pattern=r"^\.(?:clearfilters|stopall)$",
                help=("bütün filtrləri sil", "Bu çatdakı bütün filtrləri silir (təsdiq soruşulur).\n\n"
                                             "<b>İstifadə:</b> <code>.clearfilters</code> (və ya <code>.filters</code> → 🧹)"))
    async def on_clear_filters(event):
        chat_id = event.chat_id
        n = len(_FILTERS.get(chat_id, {}))
        if not n:
            out = await ub.out(event, card([("Status", "bu çatda filtr yoxdur")]))
            await out.close(DONE_VISIBLE)
            return
        out = Out(ub, event)

        async def ok(o, cb):
            k = _clear_chat(chat_id)
            await o.update(card([("Status", f"🧹 {k} filtr silindi")]), rows=[])
            await o.close(DONE_VISIBLE)

        async def no(o, cb):
            await o.update(card([("Status", "↩️ ləğv edildi — filtrlər qaldı")]), rows=[])
            await o.close(DONE_VISIBLE)
        out.on("clr_ok")(ok)
        out.on("clr_no")(no)
        await out.open(card([("Diqqət", f"🧹 bu çatdakı {n} filtrin hamısı silinsin?"),
                             ("Qeyd", "geri qaytarmaq olmur")], footer="təsdiqlə və ya ləğv et"),
                       [[out.btn(f"✅ Bəli, {n} filtri sil", "clr_ok"), out.btn("↩️ Xeyr", "clr_no")]])

    @ub.command("stopfilter", pattern=r"^\.stopfilter(?:\s+([\s\S]+))?$",
                help=("filtri sil", "Filtrlənmiş sözü və ya cümləni silir.\n\n<b>İstifadə:</b> <code>.stopfilter salam</code> və ya <code>.stopfilter \"salam aleykum\"</code>"))
    async def on_stop_filter(event):
        raw = (event.pattern_match.group(1) or "").strip()
        kw = _norm(re.sub(r'''^["'“](.*?)["'”]$''', r'\1', raw)).strip()
        chat_id = event.chat_id

        if not kw:
            await ub.out(event, card([("İstifadə", '.stopfilter <söz> və ya .stopfilter "<cümlə>"'),
                                      ("Siyahı", ".filters")]))
            return

        fl = _FILTERS.get(chat_id, {})
        if kw in fl:
            del fl[kw]
            _RX.pop(kw, None)
            if not fl:
                _FILTERS.pop(chat_id, None)
            _save()
            status = "✓ silindi"
        else:
            status = "✗ belə filtr yoxdur"

        out = await ub.out(event, card([("Söz/Cümlə", kw), ("Status", status),
                                        ("Qalıb", f"{len(_FILTERS.get(chat_id, {}))} filtr")]))
        await out.close(DONE_VISIBLE)

    @ub.command("filters", pattern=r"^\.filters$",
                help=("aktiv filtrləri göstər", "Bu çatdakı filtrlər — filtrə bas: 🎯/🔍 uyğunluq, 👥/↩️ kimə, 🗑 sil. "
                                                "➕ ilə əlavə et, ⚙️ ilə yeni filtrlərin standartını seç."))
    async def on_list_filters(event):
        chat_id = event.chat_id
        out = Out(ub, event)

        out.page = 0

        async def show_list(o, note=None):
            _WAITING_ADD.pop(chat_id, None)
            _WAITING_STICKER.pop(chat_id, None)
            rows = list_rows(o, chat_id)
            await o.update(card(list_body(chat_id, o.page), footer=foot(chat_id, note)), rows=rows)

        def key_at(i):
            kws = list(_FILTERS.get(chat_id, {}))
            return kws[i] if i < len(kws) else None

        async def show_detail(o, i, note=None):
            kw = key_at(i)
            if kw is None:
                return await show_list(o)
            f = _FILTERS[chat_id][kw]
            await o.update(card(detail_body(kw, f), footer=note or "rejimi seç və ya 🗑 sil"),
                           rows=detail_rows(o, i, f))

        registered = set()

        def ensure(i):
            if i in registered:
                return
            registered.add(i)
            register_index(i)
        out.ensure_actions = ensure

        def register_index(i):
            async def open_detail(o, cb, i=i):
                _WAITING_ADD.pop(chat_id, None)
                _WAITING_STICKER.pop(chat_id, None)
                await show_detail(o, i)

            async def set_opt(o, cb, i=i, field=None, value=None):
                kw = key_at(i)
                if kw is None:
                    return await show_list(o)
                _FILTERS[chat_id][kw][field] = value
                _save()
                await show_detail(o, i, "✓ yadda saxlandı")

            async def delete(o, cb, i=i):
                kw = key_at(i)
                if kw is not None:
                    _FILTERS[chat_id].pop(kw, None)
                    _RX.pop(kw, None)
                    if not _FILTERS[chat_id]:
                        _FILTERS.pop(chat_id, None)
                    _save()
                await show_list(o, f"🗑 silindi: {_label(kw, {'m': 'sticker' if kw.startswith('🖼') else 'x'})}"
                                if kw else None)

            async def pick_reply_sticker(o, cb, i=i):
                kw = key_at(i)
                if kw is None:
                    return await show_list(o)

                async def back(o2, note=None, i=i):
                    await show_detail(o2, i, note)
                _WAITING_STICKER[chat_id] = {"o": o, "kind": "filter", "target": kw, "back": back}
                await o.update(card([("Filtr", _label(kw, _FILTERS[chat_id][kw])),
                                     ("Rejim", "🎴 cavab stikeri seç"),
                                     ("Necə", "istədiyin stikeri BU çata göndər")],
                                    footer="ləğv: 'imtina' yaz"), rows=[[o.btn("🔙 Ləğv et", f"o{i}")]])

            async def drop_reply_sticker(o, cb, i=i):
                kw = key_at(i)
                if kw is not None:
                    _FILTERS[chat_id][kw].pop("st", None)
                    _save()
                await show_detail(o, i, "🗑 stiker silindi")

            out.on(f"o{i}")(open_detail)
            out.on(f"d{i}")(delete)
            out.on(f"st{i}")(pick_reply_sticker)
            out.on(f"sx{i}")(drop_reply_sticker)
            for act, field, value in (("me", "m", "exact"), ("mc", "m", "contains"),
                                      ("sa", "s", "all"), ("sr", "s", "reply")):
                async def handler(o, cb, i=i, field=field, value=value):
                    await set_opt(o, cb, i=i, field=field, value=value)
                out.on(f"{act}{i}")(handler)

        # 🧹 hamısını təmizlə (təsdiqlə)
        async def ask_clear(o, cb):
            n = len(_FILTERS.get(chat_id, {}))
            if not n:
                return await show_list(o, "təmizlənəcək filtr yoxdur")
            stickers = sum(1 for f in _FILTERS[chat_id].values() if f.get("st") or f["m"] == "sticker")
            await o.update(card([("Diqqət", f"🧹 bu çatdakı {n} filtrin hamısı silinsin?"),
                                 ("Mətn", f"{n - stickers}"), ("Stikerli", f"{stickers}"),
                                 ("Toxunulmur", "🔔 tağ cavabı və başqa çatların filtrləri"),
                                 ("Qeyd", "geri qaytarmaq olmur")],
                                footer="təsdiqlə və ya ləğv et"),
                           rows=[[o.btn(f"✅ Bəli, {n} filtri sil", "clr_ok"), o.btn("↩️ Xeyr", "back")]])

        async def do_clear(o, cb):
            n = _clear_chat(chat_id)
            o.page = 0
            await show_list(o, f"🧹 {n} filtr silindi")

        out.on("clr")(ask_clear)
        out.on("clr_ok")(do_clear)

        # 📄 səhifələr
        def goto(fn):
            async def h(o, cb):
                n = pages_of(chat_id)
                o.page = fn(getattr(o, "page", 0), n) % n
                await show_list(o)
            return h
        out.on("pg0")(goto(lambda p, n: 0))
        out.on("pgp")(goto(lambda p, n: p - 1))
        out.on("pgn")(goto(lambda p, n: p + 1))
        out.on("pgl")(goto(lambda p, n: n - 1))
        out.on("pgi")(goto(lambda p, n: p))

        # ⚙️ yeni filtrlərin standartı
        async def show_defs(o, note=None):
            await o.update(card(defs_body(), footer=note or "yeni filtrlər bu rejimlə yaranır"), rows=defs_rows(o))

        out.on("defs")(lambda o, cb: show_defs(o))
        for act, field, value in (("dme", "m", "exact"), ("dmc", "m", "contains"),
                                  ("dsa", "s", "all"), ("dsr", "s", "reply")):
            async def set_def(o, cb, field=field, value=value):
                _DEF[field] = value
                _save_def()
                await show_defs(o, "✓ yadda saxlandı")
            out.on(act)(set_def)

        # ➕ Əlavə et
        async def on_click_add(o, cb):
            _WAITING_ADD[chat_id] = o
            body = [
                ("Rejim", "➕ Yeni filtr əlavə et"),
                ("Format 1", '"cümlə" "cavab"'),
                ("Format 2", 'söz cavab'),
                ("Bayraqlar", "-e tam · -c içində · -r mənə reply · -a hamıya"),
                ("Standart", f"{MODES[_DEF['m']]} · {SCOPES[_DEF['s']]}"),
                ("Nümunə", '-r necəsən "Yaxşıyam, sən?"'),
            ]
            await o.update(card(body, footer="Mətni bu çata göndərin (və ya ləğv üçün 'imtina' yazın)"),
                           rows=[[o.btn("🔙 İmtina / Geri", "back")]])

        out.on("add_fl")(on_click_add)
        attach_mention(out, chat_id, None)                     # 🔔 Tağ cavabı ("back" aşağıda — siyahıya)
        out.on("back")(lambda o, cb: show_list(o))
        out.on("cancel_add")(lambda o, cb: show_list(o))         # köhnə kartlarla uyğunluq

        async def refresh(o):
            await show_list(o)
        out.refresh = refresh

        rows = list_rows(out, chat_id)
        await out.open(card(list_body(chat_id, out.page), footer=foot(chat_id)), rows)

    # ── ➕ Düyməsi ilə gözləmə rejimində yazılan mesajı tutmaq ──
    @ub.raw(events.NewMessage(outgoing=True))
    async def on_outgoing_add_filter(event):
        chat_id = event.chat_id
        if chat_id not in _WAITING_ADD:
            return

        text = (event.raw_text or "").strip()
        if not text or text.startswith("."):
            return

        o = _WAITING_ADD.get(chat_id)

        if text.lower() in ("imtina", "cancel", "çıx", "yox"):
            _WAITING_ADD.pop(chat_id, None)
            try:
                await event.delete()
            except Exception:
                pass
            rows = list_rows(o, chat_id)
            await o.update(card(list_body(chat_id, getattr(o, "page", 0)), footer=foot(chat_id)), rows=rows)
            return

        rest, opts = _take_flags(text)
        keyword, reply_text = _parse_filter_args(rest)
        if keyword and not reply_text:                        # tək söz → cavabı soruş (mətn və ya stiker)
            _WAITING_ADD.pop(chat_id, None)
            try:
                await event.delete()
            except Exception:
                pass

            async def back(o2, note=None, kw=_norm(keyword).strip()):
                kws = list(_FILTERS.get(chat_id, {}))
                if kw in kws:
                    o2.page = kws.index(kw) // PAGE_SIZE          # yeni filtrin olduğu səhifə
                rows = list_rows(o2, chat_id)
                await o2.update(card(list_body(chat_id, getattr(o2, "page", 0)), footer=foot(chat_id, note)),
                                rows=rows)
            await ask_answer(o, chat_id, keyword, opts, back, cancel_action="back", after_id=event.id)
            return
        if not keyword or not reply_text:
            body = [
                ("Xəta", "Format düzgün deyil!"),
                ("Tələb", 'Həm cümlə, həm də cavab yazılmalıdır'),
                ("Nümunə", '"salam aleykum" "aleykuma salam"'),
            ]
            await o.update(card(body, footer="Yenidən yazın və ya 'imtina' göndərin"),
                           rows=[[o.btn("🔙 İmtina / Geri", "back")]])
            return

        _WAITING_ADD.pop(chat_id, None)
        kw, _ = _put(chat_id, keyword, reply_text, opts)

        try:
            await event.delete()
        except Exception:
            pass

        f = _FILTERS[chat_id][kw]
        o.page = list(_FILTERS[chat_id]).index(kw) // PAGE_SIZE
        rows = list_rows(o, chat_id)
        await o.update(card(list_body(chat_id, o.page), footer=foot(chat_id, f"✓ əlavə edildi: {kw} {_tags(f)}")),
                       rows=rows)

    # ── 🎴 gözləmə rejimində göndərilən stikeri tutmaq (tağ cavabı / filtr cavabı) ──
    @ub.raw(events.NewMessage(outgoing=True))
    async def on_outgoing_sticker(event):
        chat_id = event.chat_id
        w = _WAITING_STICKER.get(chat_id)
        if not w or event.id <= w.get("after", 0):
            return
        msg = event.message
        raw = (event.raw_text or "").strip()
        text = raw.lower()
        if not msg.sticker:
            if text in ("imtina", "cancel", "çıx", "yox"):
                _WAITING_STICKER.pop(chat_id, None)
                try:
                    await event.delete()
                except Exception:
                    pass
                await w["back"](w["o"], "❌ ləğv edildi")
            elif w["kind"] == "answer" and raw and not raw.startswith("."):
                _WAITING_STICKER.pop(chat_id, None)            # ✍️ mətn cavabı
                kw, existed = _put(chat_id, w["target"], raw, w.get("opts"))
                try:
                    await event.delete()
                except Exception:
                    pass
                await w["back"](w["o"], f"✓ {'yeniləndi' if existed else 'əlavə olundu'}: {kw} → mətn")
            return
        _WAITING_STICKER.pop(chat_id, None)
        ref = await capture_sticker(event.client, msg)
        if w["kind"] == "answer":                              # 🎴 stiker cavabı
            try:
                await event.delete()
            except Exception:
                pass
            kw, existed = _put(chat_id, w["target"], "", w.get("opts"), st=ref)
            await w["back"](w["o"], f"✓ {'yeniləndi' if existed else 'əlavə olundu'}: {kw} → 🎴 {ref.get('emoji', '')}")
            return
        try:
            await event.delete()
        except Exception:
            pass
        if w["kind"] == "mention":
            target = w["target"]
            cur = mention_cfg(None) if target == "global" else (mention_cfg(target, effective=False)
                                                               or mention_cfg(None))
            cur["st"] = ref
            mention_save(target, cur)
            await w["back"](w["o"], f"🎴 stiker seçildi {ref.get('emoji', '')}")
        else:
            kw = w["target"]
            f = _FILTERS.get(chat_id, {}).get(kw)
            if f is not None:
                f["st"] = ref
                _save()
            await w["back"](w["o"], f"🎴 cavab stikeri təyin olundu {ref.get('emoji', '')}")

    async def _me(client):
        me = ub.state.get("me")
        if me is None or getattr(me, "_client_id", None) != id(client):
            me = await client.get_me()
            with contextlib.suppress(Exception):
                me._client_id = id(client)
            ub.state["me"] = me
        return me

    async def _respond(event, chat_id, kw, f):
        """Filtr cavabı: 🎴 stiker (varsa) → alınmasa / yoxdursa mətn."""
        if f.get("st"):
            ok, changed = await send_sticker(event.client, chat_id, f["st"], reply_to=event.id)
            if changed:
                _save()                                   # təzələnmiş file_reference
            if ok:
                return
        if f.get("r"):
            await event.reply(f["r"])

    async def _mention_reply(event, chat_id):
        cfg = mention_cfg(chat_id)
        if not cfg["on"]:
            return
        me = await _me(event.client)
        if not _is_tag(event.message, me, cfg["rep"]):
            return
        uid = event.sender_id
        key = (chat_id, uid)
        now = time.time()
        if cfg["cd"] and now - _MENTION_LAST.get(key, 0) < cfg["cd"]:
            return
        _MENTION_LAST[key] = now
        if len(_MENTION_LAST) > 5000:
            for k in [k for k, t in _MENTION_LAST.items() if now - t > 3600]:
                _MENTION_LAST.pop(k, None)
        sent = False
        if cfg.get("st"):
            sent, changed = await send_sticker(event.client, chat_id, cfg["st"], reply_to=event.id)
            if changed:                                   # təzələnmiş istinad saxlanılsın
                target = chat_id if mention_cfg(chat_id, effective=False) is not None else "global"
                mention_save(target, cfg)
        if not sent:
            await send_reaction(event.client, chat_id, event.id, cfg["react"])

    # ── Gələn mesajlara avtomatik cavab (filtrlər + 🔔 tağ cavabı) ──
    @ub.raw(events.NewMessage(incoming=True))
    async def on_incoming_message_filter(event):
        chat_id = event.chat_id
        msg = event.message
        fl = _FILTERS.get(chat_id)
        answered = False
        if fl and (event.raw_text or msg.sticker):
            to_me = False
            if event.is_reply and any(f["s"] == "reply" for f in fl.values()):
                try:
                    rep = await event.get_reply_message()
                    to_me = bool(rep and rep.out)             # reply mənim mesajımadır
                except Exception:
                    to_me = False
            hit = _pick_sticker(fl, msg.document.id, to_me) if msg.sticker else _pick(fl, event.raw_text, to_me)
            if hit:
                try:
                    await _respond(event, chat_id, *hit)
                    answered = True
                except Exception as e:
                    logger.info(f"filter cavab xətası: {e}")
        if not answered:
            try:
                await _mention_reply(event, chat_id)
            except Exception as e:
                logger.info(f"tağ cavabı xətası: {e}")
