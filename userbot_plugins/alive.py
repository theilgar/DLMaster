"""⚡ .alive — userbot işləyirmi."""
import asyncio

from core.userbot_api import ReactionEmoji, SendReactionRequest, logger


def register(ub):
    @ub.command("alive", help=("hesab işləyirmi",
                               "Mesajına ✅ reaksiya qoyur və 2 saniyə sonra silir.\n\n"
                               "<b>İstifadə:</b> <code>.alive</code>"))
    async def on_alive(event):
        client = event.client
        try:
            peer = await event.get_input_chat()
            for emo in ("\u2705", "👍", "🔥"):
                try:
                    await client(SendReactionRequest(peer=peer, msg_id=event.id,
                                                     reaction=[ReactionEmoji(emoticon=emo)]))
                    break
                except Exception:
                    continue
        except Exception:
            pass
        await asyncio.sleep(2)
        try:
            await event.delete()
        except Exception as e:
            logger.debug(f".alive silinmədi: {e}")
