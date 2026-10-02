"""🧹 .purge — reply ilə və ya say göstərərək mesajları sil.

  .purge           — reply edilən mesajdan axırıncı mesaja qədər hamısını silir
  .purge [say]     — son [say] qədər mesajı silir (məs: .purge 100)
  .purge me        — reply edilən yerdən yalnız öz mesajlarını silir
  .purge me [say]  — son [say] qədər yalnız öz mesajlarını silir (məs: .purge me 50)
"""
import asyncio
from html import escape

from core.userbot_api import Chat, FloodWaitError, User, logger, safe_edit

MAX_MESSAGES = 5000        # bir dəfəyə maksimum icazə verilən mesaj aralığı/sayı
BATCH = 100                # Telegram bir sorğuda maksimum 100 mesaj silir
DONE_VISIBLE = 4           # nəticə mesajı neçə saniyə görünsün


async def can_delete_others(client, chat) -> bool:
    if isinstance(chat, User):
        return True
    perms = await client.get_permissions(chat, "me")
    return bool(perms.is_creator or getattr(perms, "delete_messages", False)
                or (isinstance(chat, Chat) and perms.is_admin))


def register(ub):
    @ub.command("purge", pattern=r"^\.purge(?:\s+(.+))?$",
                help=("mesajları sayla və ya reply ilə sil",
                      "<b>İstifadə:</b>\n"
                      "• <code>.purge 100</code> — son 100 mesajı silir\n"
                      "• <code>.purge me 50</code> — yalnız sənin son 50 mesajını silir\n"
                      "• mesaja reply → <code>.purge</code> — həmin mesajdan bura qədər silir\n"
                      "• mesaja reply → <code>.purge me</code> — aralıqdakı yalnız öz mesajlarını silir\n\n"
                      f"⚠️️ Maksimum limit: {MAX_MESSAGES} mesaj. Dayandırmaq üçün: <code>.stop</code>"))
    async def on_purge(event):
        client = event.client
        raw_args = (event.pattern_match.group(1) or "").strip().split()
        
        # 'me' və say arqumentlərini müəyyən edirik
        mine_only = any(arg.lower() == "me" for arg in raw_args)
        count = None
        for arg in raw_args:
            if arg.isdigit():
                count = int(arg)
                break

        # Nə say verilməyibsə, nə də reply edilməyibsə xəbərdarlıq ver
        if count is None and (not event.is_reply or not event.reply_to_msg_id):
            await safe_edit(
                event,
                "ℹ️ Mesaj sayını qeyd et (məs: <code>.purge 100</code>) və ya "
                "silməyə başlayacağın mesaja <b>reply</b> edib <code>.purge</code> yaz."
            )
            return

        if count is not None and count > MAX_MESSAGES:
            await safe_edit(event, f"⛔ Bir dəfəyə maksimum <b>{MAX_MESSAGES}</b> mesaj silə bilərsiniz.")
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
                
                if count is not None:
                    # Say parametri verilibsə: son mesajlardan geriyə doğru topla
                    async for m in client.iter_messages(chat_id, max_id=cmd_id, limit=None if own_only else count):
                        total_seen += 1
                        if not own_only or getattr(m, "out", False):
                            ids.append(m.id)
                            if len(ids) >= count:
                                break
                        if total_seen >= MAX_MESSAGES:
                            break
                else:
                    # Reply verilibsə: reply olunan mesajdan indiyə qədər topla
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
