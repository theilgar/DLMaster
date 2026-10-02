"""🧹 .purge — reply etdiyin mesajdan axırıncı mesaja qədər hamısını sil.

  .purge       — reply edilən mesaj da daxil, ondan sonrakı bütün mesajlar (və .purge-in özü) silinir
  .purge me    — yalnız sənin öz mesajların silinir (başqalarına toxunmur)

Qrupda başqalarının mesajını silmək üçün "mesaj silmə" admin icazən olmalıdır; icazə yoxdursa yalnız öz
mesajların silinir (xəbərdarlıqla). Şəxsi çatda hər iki tərəfdən silinir. Gedən silməni .stop dayandırır.
Təsadüfən çox mesaj silinməsin deyə bir dəfəyə maksimum MAX_MESSAGES mesaj.
"""
import asyncio
from html import escape

from core.userbot_api import Chat, FloodWaitError, User, logger, safe_edit

MAX_MESSAGES = 5000        # bundan çox mesaj aralığı — silinmir
BATCH = 100                # Telegram bir sorğuda maksimum 100 mesaj silir
DONE_VISIBLE = 4           # nəticə mesajı neçə saniyə görünsün


async def can_delete_others(client, chat) -> bool:
    if isinstance(chat, User):
        return True
    perms = await client.get_permissions(chat, "me")
    return bool(perms.is_creator or getattr(perms, "delete_messages", False)
                or (isinstance(chat, Chat) and perms.is_admin))


def register(ub):
    @ub.command("purge", pattern=r"^\.purge(?:\s+(me))?$",
                help=("reply edilənə qədər mesajları sil",
                      "Reply etdiyin mesajdan <b>axırıncı mesaja qədər</b> hamısını silir (reply edilən mesaj da "
                      "daxil, <code>.purge</code>-in özü də).\n\n"
                      "<b>İstifadə:</b>\n• mesaja reply → <code>.purge</code> — aralıqdakı hamısı\n"
                      "• mesaja reply → <code>.purge me</code> — yalnız öz mesajların\n\n"
                      "Qrupda başqalarının mesajını silmək üçün <b>mesaj silmə</b> admin icazən olmalıdır; "
                      "yoxdursa yalnız öz mesajların silinir. Gedən silməni <code>.stop</code> dayandırır.\n"
                      f"⚠️ Geri qaytarmaq olmur. Bir dəfəyə maksimum {MAX_MESSAGES} mesaj."))
    async def on_purge(event):
        client = event.client
        mine_only = bool(event.pattern_match.group(1))
        if not event.is_reply or not event.reply_to_msg_id:
            await safe_edit(event, "ℹ️ Silməyə başlayacağın mesaja <b>reply</b> edib <code>.purge</code> yaz.")
            return
        chat = await event.get_chat()
        start_id, cmd_id, chat_id = event.reply_to_msg_id, event.id, event.chat_id

        try:
            others_ok = await can_delete_others(client, chat)
        except Exception as e:
            logger.info(f".purge icazə yoxlanmadı: {e}")
            others_ok = False
        own_only = mine_only or not others_ok
        warn = bool(not mine_only and not others_ok)

        async def runner():
            ids, total_seen = [], 0
            deleted = failed = 0
            try:
                await safe_edit(event, "🔎 <i>Mesajlar toplanır...</i>")
                async for m in client.iter_messages(chat_id, min_id=start_id - 1, max_id=cmd_id + 1):
                    total_seen += 1
                    if m.id == cmd_id or not own_only or getattr(m, "out", False):
                        ids.append(m.id)
                    if total_seen > MAX_MESSAGES:
                        await safe_edit(event, f"⛔ Aralıq çox böyükdür (>{MAX_MESSAGES} mesaj) — heç nə silinmədi.\n"
                                               "<i>Daha yaxın mesaja reply et.</i>")
                        return
                if cmd_id not in ids:
                    ids.append(cmd_id)
                to_delete = [i for i in ids if i != cmd_id]
                if not to_delete:
                    await safe_edit(event, "ℹ️ Silinəcək mesaj tapılmadı.")
                    await asyncio.sleep(2)
                    try:
                        await event.delete()
                    except Exception:
                        pass
                    return
                await safe_edit(event, f"🧹 <b>{len(to_delete)}</b> mesaj silinir...")
                for i in range(0, len(to_delete), BATCH):
                    chunk = to_delete[i:i + BATCH]
                    while True:
                        try:
                            await client.delete_messages(chat_id, chunk, revoke=True)
                            deleted += len(chunk)
                            break
                        except FloodWaitError as e:
                            await asyncio.sleep(e.seconds + 1)
                        except Exception as e:
                            failed += len(chunk)
                            logger.info(f".purge silmə xətası: {e}")
                            break
                    await asyncio.sleep(0.3)
            except asyncio.CancelledError:
                try:
                    await client.delete_messages(chat_id, [cmd_id], revoke=True)
                except Exception:
                    pass
                await _notice(client, chat_id, f"⏹ <b>.purge</b> dayandırıldı — {deleted} mesaj silindi.")
                raise
            except Exception as e:
                logger.info(f".purge xətası: {e}")
                await safe_edit(event, f"❌ <b>.purge:</b> <code>{escape(str(e))[:200]}</code>")
                return
            try:
                await client.delete_messages(chat_id, [cmd_id], revoke=True)     # komandanın özü
            except Exception:
                pass
            text = f"🧹 <b>{deleted}</b> mesaj silindi" + (f" · ❌ {failed} silinmədi" if failed else "")
            if warn:
                text += "\n<i>Silmə icazən yoxdur — yalnız öz mesajların silindi.</i>"
            await _notice(client, chat_id, text)

        ub.track(chat_id, "purge", asyncio.create_task(runner()))


async def _notice(client, chat_id, text):
    """Nəticə mesajı: göndər, bir neçə saniyə sonra sil."""
    try:
        msg = await client.send_message(chat_id, text, parse_mode="html")
        await asyncio.sleep(DONE_VISIBLE)
        await client.delete_messages(chat_id, [msg.id], revoke=True)
    except Exception as e:
        logger.debug(f".purge bildirişi: {e}")
