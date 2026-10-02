"""🎯 .filter — Açar sözə görə avtomatik cavab (reply) filtri modulu.

  .filter [söz] [cavab] — yeni filtr əlavə edir (və ya mesaja reply edib .filter [söz] yazın)
  .filters              — bu çatdakı aktiv filtrləri göstərir
  .stopfilter [söz]     — açar söz üzrə filtri silir
"""
import asyncio
from html import escape
import re

from telethon import events
from core.userbot_api import logger, safe_edit

DONE_VISIBLE = 4

# Filtrləri yaddaşda saxlamaq üçün struktur: {chat_id: {keyword: reply_text}}
_FILTERS = {}


def register(ub):
    client = getattr(ub, "client", ub)

    @ub.command("filter", pattern=r"^\.filter(?:\s+(\S+)(?:\s+([\s\S]+))?)?$",
                help=("açar söz filtri əlavə et",
                      "<b>İstifadə:</b>\n"
                      "• <code>.filter salam Aleykum salam!</code>\n"
                      "• Mesaja reply edib <code>.filter salam</code> yazdıqda reply olunan mətn cavab kimi götürülür."))
    async def on_add_filter(event):
        match = event.pattern_match
        keyword = match.group(1)
        reply_text = match.group(2)

        # Əgər mətn yazılmayıbsa, reply edilən mesajın mətnini yoxla
        if not reply_text and event.is_reply:
            reply_msg = await event.get_reply_message()
            reply_text = reply_msg.raw_text

        if not keyword or not reply_text:
            await safe_edit(
                event,
                "ℹ️ <b>Düzgün istifadə:</b>\n"
                "• <code>.filter [söz] [cavab mətni]</code>\n"
                "• və ya cavab mesajına reply edib <code>.filter [söz]</code> yazın."
            )
            return

        chat_id = event.chat_id
        keyword_clean = keyword.lower().strip()

        if chat_id not in _FILTERS:
            _FILTERS[chat_id] = {}

        _FILTERS[chat_id][keyword_clean] = reply_text.strip()

        await safe_edit(event, f"✅ <b>'{escape(keyword_clean)}'</b> filtri bu çat üçün aktiv edildi!")
        await asyncio.sleep(DONE_VISIBLE)
        try:
            await event.delete()
        except Exception:
            pass

    @ub.command("stopfilter", pattern=r"^\.stopfilter(?:\s+(\S+))?$",
                help=("filtri sil", "Təyin olunmuş açar söz filtrini ləğv edir."))
    async def on_stop_filter(event):
        keyword = (event.pattern_match.group(1) or "").lower().strip()
        chat_id = event.chat_id

        if not keyword:
            await safe_edit(event, "ℹ️ Silmək istədiyiniz açar sözü qeyd edin: <code>.stopfilter [söz]</code>")
            return

        if chat_id in _FILTERS and keyword in _FILTERS[chat_id]:
            del _FILTERS[chat_id][keyword]
            if not _FILTERS[chat_id]:
                del _FILTERS[chat_id]
            await safe_edit(event, f"🗑 <b>'{escape(keyword)}'</b> filtri silindi.")
        else:
            await safe_edit(event, f"❌ Bu çatda <b>'{escape(keyword)}'</b> adlı aktiv filtr tapılmadı.")

        await asyncio.sleep(DONE_VISIBLE)
        try:
            await event.delete()
        except Exception:
            pass

    @ub.command("filters", pattern=r"^\.filters$",
                help=("aktiv filtrləri göstər", "Cari çatda olan bütün aktiv filtrləri siyahılayır."))
    async def on_list_filters(event):
        chat_id = event.chat_id
        chat_filters = _FILTERS.get(chat_id, {})

        if not chat_filters:
            await safe_edit(event, "ℹ️ Bu çatda heç bir aktiv filtr yoxdur.")
            await asyncio.sleep(DONE_VISIBLE)
            try:
                await event.delete()
            except Exception:
                pass
            return

        text = "📋 <b>Bu çatdakı aktiv filtrlər:</b>\n\n"
        for idx, kw in enumerate(chat_filters.keys(), 1):
            text += f"{idx}. <code>{escape(kw)}</code>\n"

        text += "\n<i>Filtri silmək üçün: <code>.stopfilter [söz]</code></i>"
        await safe_edit(event, text)

    # Gələn mesajları dinləyən və filtr uyğun gəldikdə cavab verən tətikləyici
    @client.on(events.NewMessage(incoming=True))
    async def on_incoming_message_filter(event):
        # Yalnız filtr olan çatları və mətni olan mesajları yoxlayırıq
        if event.chat_id not in _FILTERS or not event.raw_text:
            return

        text = event.raw_text
        chat_filters = _FILTERS[event.chat_id]

        for keyword, reply_body in chat_filters.items():
            # Açar sözün cümlə daxilində bütöv söz kimi keçdiyini yoxlayır
            pattern = rf"(?<!\w){re.escape(keyword)}(?!\w)"
            if re.search(pattern, text, re.IGNORECASE):
                try:
                    await event.reply(reply_body)
                except Exception as e:
                    logger.info(f"Filter cavab xətası: {e}")
                break
                