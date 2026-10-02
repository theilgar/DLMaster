"""🎯 .filter — açar sözə və ya cümləyə görə avtomatik cavab.

  .filter <söz/cümlə> <cavab> — yeni filtr (dırnaqla: .filter "salam aleykum" "aleykuma salam")
  .filters                    — filtrlər kartı (🗑 silmə və ➕ əlavə etmə düymələri ilə)
  .stopfilter <söz/cümlə>     — filtri sil

Filtrlər DB-də saxlanır (restartdan sonra da qalır).
"""
import json
import re

from telethon import events
from core.userbot_api import Out, ff, get_db, logger

DONE_VISIBLE = 4
DB_KEY = "userbot:filters"
MAX_BUTTONS = 24

# {chat_id: {keyword: reply_text}}
_FILTERS = {}
_RX = {}                 # keyword -> compiled regex (keş)
_WAITING_ADD = {}        # chat_id -> Out instance (➕ düyməsi ilə gözləmə rejimi)


def _load():
    _FILTERS.clear()
    try:
        raw = get_db().get_setting(DB_KEY)
        data = json.loads(raw) if raw else {}
        for cid, fl in data.items():
            if isinstance(fl, dict) and fl:
                _FILTERS[int(cid)] = {str(k): str(v) for k, v in fl.items()}
    except Exception as e:
        logger.warning(f".filter yüklənmədi: {e}")


def _save():
    try:
        get_db().set_setting(DB_KEY, json.dumps({str(c): f for c, f in _FILTERS.items() if f}, ensure_ascii=False))
    except Exception as e:
        logger.warning(f".filter saxlanmadı: {e}")


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

    # 1. Açar söz/cümlə dırnaq içindədirsə ("...", '...', “...”)
    m = re.match(r'''^["'“]([^"'”]+)["'”](?:\s+([\s\S]+))?$''', text)
    if m:
        kw = m.group(1).strip()
        rep = (m.group(2) or "").strip()
        rep_m = re.match(r'''^["'“]([\s\S]*?)["'”]$''', rep)
        if rep_m:
            rep = rep_m.group(1)
        return kw, rep

    # 2. Dırnaqsız adi hal
    parts = text.split(None, 1)
    kw = parts[0].strip()
    rep = parts[1].strip() if len(parts) > 1 else ""
    if rep:
        rep_m = re.match(r'''^["'“]([\s\S]*?)["'”]$''', rep)
        if rep_m:
            rep = rep_m.group(1)
    return kw, rep


def _rx(keyword):
    r = _RX.get(keyword)
    if r is None:
        words = keyword.split()
        if words:
            pattern = r"\s+".join(re.escape(w) for w in words)
        else:
            pattern = re.escape(keyword)
        r = _RX[keyword] = re.compile(rf"(?<!\w){pattern}(?!\w)", re.IGNORECASE)
    return r


def _preview(text, n=60):
    t = " ".join(str(text).split())
    return t if len(t) <= n else t[:n - 1] + "…"


def register(ub):
    _load()

    def card(rows, footer=None):
        return ff(ub.title("filter"), rows, footer=footer)

    def list_body(chat_id):
        fl = _FILTERS.get(chat_id, {})
        body = [("Çat", chat_id), ("Filtr", f"{len(fl)} aktiv")]
        if fl:
            body.append("# Siyahı")
            body += [(f"{i}.", f"{kw} → {_preview(rep, 40)}") for i, (kw, rep) in enumerate(fl.items(), 1)]
        return body

    def make_rows(out, chat_id):
        kws = list(_FILTERS.get(chat_id, {}))[:MAX_BUTTONS]
        del_btns = [out.btn(f"🗑 {k[:16]}", f"d{i}") for i, k in enumerate(kws)]
        grid = [del_btns[i:i + 3] for i in range(0, len(del_btns), 3)]
        grid.append([out.btn("➕ Filtr əlavə et", "add_fl")])
        return grid

    def foot(chat_id, extra=None):
        if extra:
            return extra
        return "🗑 sil | ➕ əlavə et" if _FILTERS.get(chat_id) else "əlavə: ➕ düyməsi və ya .filter \"cümlə\" \"cavab\""

    @ub.command("filter", pattern=r"^\.filter(?:\s+([\s\S]+))?$",
                help=("açar söz və ya cümlə filtri əlavə et",
                      "<b>İstifadə:</b>\n"
                      "• <code>.filter salam Əleykum salam!</code>\n"
                      "• <code>.filter \"salam aleykum\" \"aleykuma salam\"</code>\n"
                      "• mesaja reply edib: <code>.filter \"salam aleykum\"</code>\n\n"
                      "Siyahı: <code>.filters</code> · Silmək: <code>.stopfilter &lt;söz/cümlə&gt;</code>"))
    async def on_add_filter(event):
        raw_args = event.pattern_match.group(1) or ""
        keyword, reply_text = _parse_filter_args(raw_args)

        if not reply_text and event.is_reply:
            reply_msg = await event.get_reply_message()
            reply_text = (reply_msg.raw_text or "") if reply_msg else ""

        if not keyword or not (reply_text or "").strip():
            await ub.out(event, card([("Əlavə (tək)", ".filter <söz> <cavab>"),
                                      ("Əlavə (cümlə)", '.filter "<cümlə>" "<cavab>"'),
                                      ("Reply", "reply → .filter <söz/cümlə>"),
                                      ("Siyahı", ".filters"),
                                      ("Sil", '.stopfilter <söz/cümlə>')],
                                     footer="söz/cümlə və cavab daxil edilməlidir"))
            return

        kw = keyword.lower().strip()
        existed = kw in _FILTERS.get(event.chat_id, {})
        _FILTERS.setdefault(event.chat_id, {})[kw] = reply_text.strip()
        _RX.pop(kw, None)
        _save()

        out = await ub.out(event, card([("Filtr", kw), ("Cavab", _preview(reply_text, 80)),
                                        ("Status", "✓ yeniləndi" if existed else "✓ əlavə olundu"),
                                        ("Cəmi", f"{len(_FILTERS[event.chat_id])} filtr bu çatda")]))
        await out.close(DONE_VISIBLE)

    @ub.command("stopfilter", pattern=r"^\.stopfilter(?:\s+([\s\S]+))?$",
                help=("filtri sil", "Filtrlənmiş sözü və ya cümləni silir.\n\n<b>İstifadə:</b> <code>.stopfilter salam</code> və ya <code>.stopfilter \"salam aleykum\"</code>"))
    async def on_stop_filter(event):
        raw = (event.pattern_match.group(1) or "").strip()
        kw = re.sub(r'''^["'“](.*?)["'”]$''', r'\1', raw).lower().strip()
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
                help=("aktiv filtrləri göstər", "Bu çatdakı filtrlər — kartda 🗑 ilə sil, ➕ ilə əlavə et."))
    async def on_list_filters(event):
        chat_id = event.chat_id
        out = Out(ub, event)

        # 🗑 Düymələri
        for i in range(MAX_BUTTONS):
            async def delete(o, cb, i=i):
                _WAITING_ADD.pop(chat_id, None)
                kws = list(_FILTERS.get(chat_id, {}))
                if i < len(kws):
                    removed = kws[i]
                    _FILTERS[chat_id].pop(removed, None)
                    _RX.pop(removed, None)
                    if not _FILTERS[chat_id]:
                        _FILTERS.pop(chat_id, None)
                    _save()
                await o.update(card(list_body(chat_id), footer=foot(chat_id)), rows=make_rows(o, chat_id))
            out.on(f"d{i}")(delete)

        # ➕ Əlavə et düyməsi
        async def on_click_add(o, cb):
            _WAITING_ADD[chat_id] = o
            body = [
                ("Rejim", "➕ Yeni filtr əlavə et"),
                ("Format 1", '"cümlə" "cavab"'),
                ("Format 2", 'söz cavab'),
                ("Nümunə", '"salam aleykum" "aleykuma salam"'),
            ]
            footer_text = "Mətni bu çata göndərin (və ya ləğv üçün 'imtina' yazın)"
            await o.update(card(body, footer=footer_text), rows=[[out.btn("🔙 İmtina / Geri", "cancel_add")]])

        # 🔙 İmtina düyməsi
        async def on_click_cancel(o, cb):
            _WAITING_ADD.pop(chat_id, None)
            await o.update(card(list_body(chat_id), footer=foot(chat_id)), rows=make_rows(o, chat_id))

        out.on("add_fl")(on_click_add)
        out.on("cancel_add")(on_click_cancel)

        async def refresh(o):
            _WAITING_ADD.pop(chat_id, None)
            await o.update(card(list_body(chat_id), footer=foot(chat_id)), rows=make_rows(o, chat_id))
        out.refresh = refresh

        await out.open(card(list_body(chat_id), footer=foot(chat_id)), make_rows(out, chat_id))

    # ── ➕ Düyməsi ilə gözləmə rejimində yazılan mesajı tutmaq ──
    @ub.raw(events.NewMessage(outgoing=True))
    async def on_outgoing_add_filter(event):
        chat_id = event.chat_id
        if chat_id not in _WAITING_ADD:
            return

        text = (event.raw_text or "").strip()
        if not text:
            return

        o = _WAITING_ADD.get(chat_id)

        # İmtina komandaları
        if text.lower() in ("imtina", "cancel", "çıx", "yox"):
            _WAITING_ADD.pop(chat_id, None)
            try:
                await event.delete()
            except Exception:
                pass
            await o.update(card(list_body(chat_id), footer=foot(chat_id)), rows=make_rows(o, chat_id))
            return

        keyword, reply_text = _parse_filter_args(text)
        if not keyword or not reply_text:
            body = [
                ("Xəta", "Format düzgün deyil!"),
                ("Tələb", 'Həm cümlə, həm də cavab yazılmalıdır'),
                ("Nümunə", '"salam aleykum" "aleykuma salam"'),
            ]
            await o.update(card(body, footer="Yenidən yazın və ya 'imtina' göndərin"),
                           rows=[[o.btn("🔙 İmtina / Geri", "cancel_add")]])
            return

        _WAITING_ADD.pop(chat_id, None)
        kw = keyword.lower().strip()
        _FILTERS.setdefault(chat_id, {})[kw] = reply_text.strip()
        _RX.pop(kw, None)
        _save()

        try:
            await event.delete()
        except Exception:
            pass

        await o.update(card(list_body(chat_id), footer=foot(chat_id, "✓ Filtr əlavə edildi!")),
                       rows=make_rows(o, chat_id))

    # ── Gələn mesajlara avtomatik cavab ──
    @ub.raw(events.NewMessage(incoming=True))
    async def on_incoming_message_filter(event):
        fl = _FILTERS.get(event.chat_id)
        if not fl or not event.raw_text:
            return
        for kw, reply_body in list(fl.items()):
            if _rx(kw).search(event.raw_text):
                try:
                    await event.reply(reply_body)
                except Exception as e:
                    logger.info(f"filter cavab xətası: {e}")
                break
