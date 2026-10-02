"""🎯 .filter — açar sözə görə avtomatik cavab (inline kart, fastfetch görünüşü).

  .filter <söz> <cavab>   — yeni filtr (və ya mesaja reply edib .filter <söz>)
  .filters                — bu çatdakı filtrlər (kartda 🗑 düymələri ilə silmək olur)
  .stopfilter <söz>       — filtri sil

Filtrlər DB-də saxlanır (restartdan sonra da qalır).
"""
import json
import re

from telethon import events

from core.userbot_api import ff, get_db, logger

DONE_VISIBLE = 4
DB_KEY = "userbot:filters"
MAX_BUTTONS = 24

# {chat_id: {keyword: reply_text}}
_FILTERS = {}
_RX = {}                                   # keyword -> compiled regex (keş)


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


def _rx(keyword):
    r = _RX.get(keyword)
    if r is None:
        r = _RX[keyword] = re.compile(rf"(?<!\w){re.escape(keyword)}(?!\w)", re.IGNORECASE)
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

    @ub.command("filter", pattern=r"^\.filter(?:\s+(\S+)(?:\s+([\s\S]+))?)?$",
                help=("açar söz filtri əlavə et",
                      "<b>İstifadə:</b>\n"
                      "• <code>.filter salam Əleykum salam!</code>\n"
                      "• mesaja reply edib <code>.filter salam</code> — reply olunan mətn cavab olur\n\n"
                      "Siyahı: <code>.filters</code> · Silmək: <code>.stopfilter salam</code>"))
    async def on_add_filter(event):
        keyword, reply_text = event.pattern_match.group(1), event.pattern_match.group(2)
        if not reply_text and event.is_reply:
            reply_msg = await event.get_reply_message()
            reply_text = (reply_msg.raw_text or "") if reply_msg else ""

        if not keyword or not (reply_text or "").strip():
            await ub.out(event, card([("Əlavə", ".filter <söz> <cavab>"), ("Reply", "reply → .filter <söz>"),
                                      ("Siyahı", ".filters"), ("Sil", ".stopfilter <söz>")],
                                     footer="söz və cavab lazımdır"))
            return

        kw = keyword.lower().strip()
        existed = kw in _FILTERS.get(event.chat_id, {})
        _FILTERS.setdefault(event.chat_id, {})[kw] = reply_text.strip()
        _save()
        out = await ub.out(event, card([("Söz", kw), ("Cavab", _preview(reply_text, 80)),
                                        ("Status", "✓ yeniləndi" if existed else "✓ əlavə olundu"),
                                        ("Cəmi", f"{len(_FILTERS[event.chat_id])} filtr bu çatda")]))
        await out.close(DONE_VISIBLE)

    @ub.command("stopfilter", pattern=r"^\.stopfilter(?:\s+(\S+))?$",
                help=("filtri sil", "Açar söz filtrini silir.\n\n<b>İstifadə:</b> <code>.stopfilter salam</code>"))
    async def on_stop_filter(event):
        kw = (event.pattern_match.group(1) or "").lower().strip()
        chat_id = event.chat_id
        if not kw:
            await ub.out(event, card([("İstifadə", ".stopfilter <söz>"), ("Siyahı", ".filters")]))
            return
        fl = _FILTERS.get(chat_id, {})
        if kw in fl:
            del fl[kw]
            if not fl:
                _FILTERS.pop(chat_id, None)
            _save()
            status = "✓ silindi"
        else:
            status = "✗ belə filtr yoxdur"
        out = await ub.out(event, card([("Söz", kw), ("Status", status),
                                        ("Qalıb", f"{len(_FILTERS.get(chat_id, {}))} filtr")]))
        await out.close(DONE_VISIBLE)

    @ub.command("filters", pattern=r"^\.filters$",
                help=("aktiv filtrləri göstər", "Bu çatdakı filtrlər — kartda 🗑 düyməsi ilə silmək olur."))
    async def on_list_filters(event):
        from core.userbot_api import Out
        chat_id = event.chat_id
        out = Out(ub, event)

        def rows():
            kws = list(_FILTERS.get(chat_id, {}))[:MAX_BUTTONS]
            btns = [out.btn(f"🗑 {k[:20]}", f"d{i}") for i, k in enumerate(kws)]
            return [btns[i:i + 3] for i in range(0, len(btns), 3)]

        def foot():
            return "🗑 bas — filtr silinir" if _FILTERS.get(chat_id) else "əlavə: .filter <söz> <cavab>"

        for i in range(MAX_BUTTONS):
            async def delete(o, cb, i=i):
                kws = list(_FILTERS.get(chat_id, {}))
                if i < len(kws):
                    _FILTERS[chat_id].pop(kws[i], None)
                    if not _FILTERS[chat_id]:
                        _FILTERS.pop(chat_id, None)
                    _save()
                await o.update(card(list_body(chat_id), footer=foot()), rows=rows())
            out.on(f"d{i}")(delete)

        async def refresh(o):
            await o.update(card(list_body(chat_id), footer=foot()), rows=rows())
        out.refresh = refresh

        await out.open(card(list_body(chat_id), footer=foot()), rows())

    # ── gələn mesajlar: client qoşulandan sonra host özü bağlayır ──
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
