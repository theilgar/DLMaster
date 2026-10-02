"""🧹 .purge — reply ilə və ya say göstərərək mesajları sil (inline deyil — komanda mesajının özü redaktə olunur).

  .purge           — reply edilən mesajdan axırıncı mesaja qədər hamısını silir
  .purge [say]     — son [say] qədər mesajı silir (məs: .purge 100)
  .purge me        — reply edilən yerdən yalnız öz mesajlarını silir
  .purge me [say]  — son [say] qədər yalnız öz mesajlarını silir (məs: .purge me 50)
"""
import asyncio
from html import escape

from core.userbot_api import Bar, Chat, FloodWaitError, User, ff, logger

MAX_MESSAGES = 5000        # bir dəfəyə maksimum icazə verilən mesaj aralığı/sayı
BATCH = 100                # Telegram bir sorğuda maksimum 100 mesaj silir
DONE_VISIBLE = 4           # nəticə kartı neçə saniyə görünsün


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
            await ub.out(event, ff(ub.title("purge"), [
                ("Say", ".purge 100"), ("Mənimki", ".purge me 50"),
                ("Reply", "mesaja reply → .purge"), ("Limit", f"{MAX_MESSAGES} mesaj")],
                footer="say yaz və ya mesaja reply et"), inline=False)
            return

        if count is not None and count > MAX_MESSAGES:
            await ub.out(event, ff(ub.title("purge"), [("Status", "✗ limit aşıldı"), ("Maks.", f"{MAX_MESSAGES} mesaj")]), inline=False)
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

        mode = "yalnız mənim" if own_only else "hamısı"
        src = f"son {count}" if count is not None else "reply-dan bura"

        def card(state, done=0, total=0, fail=0, note=None):
            body = [("Rejim", mode), ("Aralıq", src), ("Status", state)]
            if total:
                body.append(Bar("Silindi", 100 * done / total, f"{done}/{total}"))
            if fail:
                body.append(("Alınmadı", fail))
            if note:
                body.append(("Qeyd", note))
            return ff(ub.title("purge"), body)

        out = await ub.out(event, card("● mesajlar toplanır"), inline=False)

        async def runner():
            ids, total_seen = [], 0
            deleted = failed = 0
            try:
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
                    # Reply verilibsə: reply olunan mesajdan komandaya qədər topla (kartın özü daxil deyil)
                    async for m in client.iter_messages(chat_id, min_id=start_id - 1, max_id=cmd_id):
                        total_seen += 1
                        if not own_only or getattr(m, "out", False):
                            ids.append(m.id)
                        if total_seen > MAX_MESSAGES:
                            await out.update(card("○ dayandı", note=f"aralıq > {MAX_MESSAGES} mesaj, "
                                                                     "daha yaxın mesaja reply et"))
                            await out.close(DONE_VISIBLE + 2)
                            return

                to_delete = [i for i in ids if i != cmd_id]
                if not to_delete:
                    await out.update(card("○ silinəcək mesaj yoxdur"))
                    await out.close(DONE_VISIBLE)
                    return

                total = len(to_delete)
                await out.update(card("● silinir", 0, total))
                for i in range(0, total, BATCH):
                    chunk = to_delete[i:i + BATCH]
                    while True:
                        try:
                            await client.delete_messages(chat_id, chunk, revoke=True)
                            deleted += len(chunk)
                            break
                        except FloodWaitError as e:
                            await out.update(card(f"● limit, {e.seconds} san. gözlənilir", deleted, total, failed))
                            await asyncio.sleep(e.seconds + 1)
                        except Exception as e:
                            failed += len(chunk)
                            logger.info(f".purge silmə xətası: {e}")
                            break
                    if total > BATCH:
                        await out.update(card("● silinir", deleted + failed, total, failed))
                    await asyncio.sleep(0.3)
            except asyncio.CancelledError:
                await out.update(card("⏹ dayandırıldı", fail=failed, note=f"{deleted} mesaj silindi"))
                asyncio.create_task(out.close(DONE_VISIBLE))
                raise
            except Exception as e:
                logger.info(f".purge xətası: {e}")
                await out.update(card("✗ xəta", fail=failed, note=f"{deleted} silindi · {str(e)[:100]}"))
                return

            note = "silmə icazən yoxdur — yalnız öz mesajların" if warn else None
            await out.update(card("✓ bitdi", deleted, deleted or 1, failed, note=note))
            await out.close(DONE_VISIBLE)

        out.track("purge", runner())
