"""👤 .info — şəxs/çat haqqında məlumat (qrupda admin icazələri də)."""
from html import escape

from core.userbot_api import build_info, logger, safe_edit


def register(ub):
    @ub.command("info", pattern=r"^\.info(?:\s+(\S+))?$",
                help=("şəxs haqqında məlumat",
                      "Ad, username, ID, DC, bio, status, ortaq qruplar, profil şəkli, Premium / bot / scam / fake.\n"
                      "Qrupda: <b>yaradıcı / admin / üzv / banlı / məhdud</b>, titul, kim admin edib, bütün "
                      "<b>admin icazələri</b> ✅/❌; banlıdırsa — kim, nə vaxt, nə vaxta qədər, qadağalar.\n\n"
                      "<b>İstifadə:</b>\n• reply → <code>.info</code>\n• <code>.info @username</code>\n"
                      "• <code>.info 123456789</code>\n• şəxsi çatda reply-siz — qarşı tərəf\n\n"
                      "<i>Paneldə:</i> 👥 Bu çat → 👤 Reply edilən"))
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
            chat = await event.get_chat() if event.is_group else None
            await safe_edit(event, await build_info(client, ent, chat))
        except Exception as e:
            logger.info(f".info xətası: {e}")
            await safe_edit(event, f"❌ <b>.info:</b> <code>{escape(str(e))[:200]}</code>")
