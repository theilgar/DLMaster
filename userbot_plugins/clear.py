"""🧹 .clear — qrupun ban siyahısını təmizlə (hamını unban)."""
import asyncio
from html import escape

from core.userbot_api import check_clear_rights, logger, run_clear, safe_edit


def register(ub):
    @ub.command("clear", pattern=r"^\.clear(?:\s+(all))?$",
                help=("qrupun banlarını təmizlə",
                      "Superqrupun ban siyahısındakı hər kəsi unban edir, proqres 3 saniyədən bir yenilənir, "
                      "Telegram limit qoysa gözləyib davam edir.\n\n<b>İstifadə:</b>\n• <code>.clear</code> — banlar\n"
                      "• <code>.clear all</code> — banlar + məhdudiyyətlər (mute)\n\n"
                      "⚠️ Komanda kimi təsdiqsiz başlayır. Ban icazən olmalıdır. Dayandırmaq: <code>.stop</code>\n"
                      "<i>Paneldə:</i> 👥 Bu çat → 🧹 (təsdiq soruşur)"))
    async def on_clear(event):
        client = event.client
        mode = (event.pattern_match.group(1) or "").strip().lower()
        chat = await event.get_chat()
        err = await check_clear_rights(client, chat)
        if err:
            await safe_edit(event, err)
            return

        async def report(text, final):
            await safe_edit(event, text + ("" if final else "\n<i>Dayandırmaq: .stop</i>"))

        async def runner():
            try:
                await run_clear(client, chat, mode, report)
            except asyncio.CancelledError:
                await safe_edit(event, "⏹ <b>.clear</b> dayandırıldı.")
                raise
            except Exception as e:
                logger.info(f".clear xətası: {e}")
                await safe_edit(event, f"❌ <b>.clear:</b> <code>{escape(str(e))[:200]}</code>")

        ub.track(event.chat_id, "clear", asyncio.create_task(runner()))
