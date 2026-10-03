"""👥 .invite — Qrup username-i, ID-si və ya linki ilə üzvləri cari qrupa daşıyır.

İstifadə:
  .invite @qrup_username
  .invite -100123456789
  .invite https://t.me/qrup_linki
  .invite https://t.me/+dəvət_hash

Dayandırmaq üçün: .stop
"""
import asyncio
import re
import time
from html import escape

from telethon.errors import (
    ChatAdminRequiredError,
    FloodWaitError,
    PeerFloodError,
    UserAlreadyParticipantError,
    UserChannelsTooMuchError,
    UserKickedError,
    UserNotMutualContactError,
    UserPrivacyRestrictedError,
)
from telethon.tl.functions.channels import InviteToChannelRequest
from telethon.tl.functions.messages import AddChatUserRequest, CheckChatInviteRequest
from telethon.tl.types import Channel, Chat, ChatInviteAlready, User

from core.userbot_api import Bar, ff, logger, safe_edit

INVITE_DELAY = 2.5
DONE_VISIBLE = 5


async def resolve_source_chat(client, target_str: str):
    """Yalnız link, username və ya ID vasitəsilə çat obyektini tapır."""
    raw = target_str.strip()

    # Özəl dəvət linkləri (t.me/+hash və ya t.me/joinchat/hash)
    invite_match = re.search(r"(?:joinchat/|\+)([a-zA-Z0-9_-]+)", raw)
    if invite_match:
        invite_hash = invite_match.group(1)
        try:
            res = await client(CheckChatInviteRequest(invite_hash))
            if isinstance(res, ChatInviteAlready):
                return res.chat, None
            return None, "❌ Hesabınız bu qrupda üzv deyil. Üzvləri çəkmək üçün əvvəlcə qrupa qoşulmalısınız."
        except Exception as e:
            return None, f"❌ Dəvət linki yoxlanıla bilmədi: <code>{escape(str(e))[:100]}</code>"

    # Link formatını normallaşdır və @ işarəsini təmizlə
    cleaned = re.sub(r"^(?:https?://)?(?:t\.me/|telegram\.me/)", "", raw).strip("/").lstrip("@")

    # ID və ya username yoxlanışı
    try:
        query = int(cleaned) if (cleaned.lstrip("-").isdigit()) else cleaned
        entity = await client.get_entity(query)
        if isinstance(entity, (Channel, Chat)):
            return entity, None
        return None, "❌ Verilən hədəf qrup və ya superqrup deyil (istifadəçi və ya botdur)."
    except Exception as e:
        return None, f"❌ Qrup tapılmadı: <code>{escape(str(e))[:150]}</code>\n" \
                     "<i>(Username, ID və ya linkin düzgünlüyünə əmin olun).</i>"


def register(ub):
    @ub.command("invite", pattern=r"^\.invite(?:\s+(\S+))?$",
                help=("userləri qrupa daşı",
                      "Qrup username-i, ID-si və ya linki vasitəsilə üzvləri cari qrupa daşıyır.\n\n"
                      "<b>İstifadə:</b>\n"
                      "• <code>.invite @qrup_username</code>\n"
                      "• <code>.invite -100123456789</code>\n"
                      "• <code>.invite https://t.me/qrup_linki</code>\n\n"
                      "⏹ Dayandırmaq: <code>.stop</code>"))
    async def on_invite(event):
        client = event.client
        target_arg = (event.pattern_match.group(1) or "").strip()

        if not target_arg:
            await safe_edit(
                event,
                "ℹ️ <b>Qrup username-i, ID-si və ya linkini qeyd edin!</b>\n\n"
                "• <code>.invite @qrup_username</code>\n"
                "• <code>.invite -100123456789</code>\n"
                "• <code>.invite https://t.me/link</code>"
            )
            return

        dest_chat = await event.get_chat()
        if not isinstance(dest_chat, (Channel, Chat)):
            await safe_edit(event, "❌ Bu əmr yalnız <b>qruplarda</b> işlədilə bilər!")
            return

        perms = await client.get_permissions(dest_chat, "me")
        if not perms.is_creator and not getattr(perms, "invite_users", True):
            await safe_edit(event, "⛔ Bu qrupda <b>üzv əlavə etmək</b> icazəniz yoxdur.")
            return

        await safe_edit(event, "🔍 <i>Qrup axtarılır və üzvlər yoxlanılır...</i>")
        source_chat, err = await resolve_source_chat(client, target_arg)
        if err or not source_chat:
            await safe_edit(event, err or "❌ Qrup tapılmadı.")
            return

        if getattr(source_chat, "id", None) == getattr(dest_chat, "id", None):
            await safe_edit(event, "🙃 Hədəf qrup ilə cari qrup eynidir!")
            return

        source_title = getattr(source_chat, "title", "Mənbə qrup")
        dest_title = getattr(dest_chat, "title", "Bu qrup")

        def card(status_text, done=0, total=0, priv=0, exist=0, fail=0, note=""):
            body = [
                ("Mənbə", source_title[:25]),
                ("Hədəf", dest_title[:25]),
                ("Status", status_text),
            ]
            if total > 0:
                pct = 100 * done / total
                body.append(Bar("İrəliləyiş", pct, f"{done}/{total}"))
            body += [
                ("✅ Əlavə edildi", str(done)),
                ("🔒 Gizlilik / Blok", str(priv)),
                ("👥 Artıq qrupdadır", str(exist)),
            ]
            if fail:
                body.append(("❌ Digər xətalar", str(fail)))
            if note:
                body.append(("ℹ️ Qeyd", note))
            return ff(ub.title("invite"), body, footer="Dayandırmaq üçün: .stop")

        out = await ub.out(event, card("● Üzvlər toplanır..."), inline=False)

        async def runner():
            users_to_add = []
            try:
                async for member in client.iter_participants(source_chat, limit=1000):
                    if isinstance(member, User):
                        if not member.bot and not member.deleted and not member.is_self:
                            users_to_add.append(member)
            except Exception as e:
                logger.info(f".invite üzv toplama xətası: {e}")
                await out.update(card("✗ Üzvləri oxumaq olmadı", note=f"İcazə xətası: {str(e)[:80]}"))
                await out.close(DONE_VISIBLE + 2)
                return

            total = len(users_to_add)
            if total == 0:
                await out.update(card("○ Əlavə ediləcək uyğun istifadəçi tapılmadı"))
                await out.close(DONE_VISIBLE)
                return

            done = 0
            priv = 0
            exist = 0
            fail = 0
            last_edit_time = 0

            for idx, user in enumerate(users_to_add, start=1):
                try:
                    if isinstance(dest_chat, Channel):
                        await client(InviteToChannelRequest(channel=dest_chat, users=[user]))
                    else:
                        await client(AddChatUserRequest(chat_id=dest_chat.id, user_id=user.id, fwd_limit=0))
                    done += 1

                except (UserPrivacyRestrictedError, UserNotMutualContactError):
                    priv += 1
                except UserAlreadyParticipantError:
                    exist += 1
                except (UserChannelsTooMuchError, UserKickedError):
                    priv += 1
                except ChatAdminRequiredError:
                    await out.update(card("✗ Dayandırıldı", done, total, priv, exist, fail, note="Qrupda üzv əlavə etmək icazəniz ləğv edildi."))
                    await out.close(DONE_VISIBLE + 3)
                    return
                except PeerFloodError:
                    await out.update(card("⛔ Telegram limiti (PeerFlood)", done, total, priv, exist, fail,
                                          note="Hesabınıza müvəqqəti dəvət limiti qoyuldu."))
                    await out.close(DONE_VISIBLE + 5)
                    return
                except FloodWaitError as fe:
                    await out.update(card(f"⏳ Limit: {fe.seconds} san. gözlənilir", done, total, priv, exist, fail))
                    await asyncio.sleep(fe.seconds + 2)
                    continue
                except Exception as e:
                    fail += 1
                    logger.debug(f".invite istifadəçi əlavə olunmadı ({user.id}): {e}")

                now = time.monotonic()
                if now - last_edit_time >= 3.0 or idx == total:
                    await out.update(card("● Daşınır...", done, total, priv, exist, fail))
                    last_edit_time = now

                await asyncio.sleep(INVITE_DELAY)

            await out.update(card("✓ Tamamlandı", done, total, priv, exist, fail))
            await out.close(DONE_VISIBLE)

        ub.track(event.chat_id, "invite", asyncio.create_task(runner()))
