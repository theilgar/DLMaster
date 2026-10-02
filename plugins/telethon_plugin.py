"""
📱 Telethon Userbot İnteqrasiyası — depo üçün (şəxsi hesab)

Bu plugin şəxsi Telegram hesabınızı (userbot) botla eyni loop-da işə salır.
Qrup və gizli kanallardakı audiolara çıxış əldə edir və onları birbaşa
depo doldurucu (depo_filler_plugin) növbəsinə əlavə edir.

Əmrlər:
  .alive                                — istənilən çatda reaksiya verir, 2 saniyə sonra mesajı silir
  /userbot                              — userbot-un statusunu yoxla
  /scan_chat <link/ID> [mesaj_sayı]     — kanalı skan et (⏹ Dayandır düyməsi ilə)
  /stop_scan                            — cari skan prosesini dayandır
"""
import os
import asyncio
import logging
from html import escape

from telethon import TelegramClient, events
from telethon.tl.functions.messages import SendReactionRequest
from telethon.tl.types import ReactionEmoji
from aiogram import F, types
from aiogram.filters import Command, CommandObject
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

logger = logging.getLogger(__name__)

API_ID = os.getenv("TELETHON_API_ID")
API_HASH = os.getenv("TELETHON_API_HASH")
SESSION_FILE = "userbot.session"


def setup(context):
    dp = context.dp
    bot = context.bot

    if not API_ID or not API_HASH:
        logger.error("❌ TELETHON_API_ID və ya TELETHON_API_HASH tapılmadı. Telethon işə düşməyəcək.")
        return

    client = TelegramClient(SESSION_FILE, int(API_ID), API_HASH)
    context.telethon_client = client

    scan_state = {
        "running": False,
        "stop": False,
        "chat_id": None,
        "msg_id": None,
    }

    def is_creator(uid: int) -> bool:
        return bool(context.creator_id) and uid == context.creator_id

    async def start_telethon():
        try:
            logger.info("📱 Telethon userbot qoşulur...")
            await client.connect()

            if not await client.is_user_authorized():
                logger.warning("⚠️ Userbot avtorizasiya olunmayıb! Əvvəlcə login.py ilə giriş edin.")
                return

            me = await client.get_me()
            logger.info(f"✅ Telethon userbot qoşuldu: {me.first_name} (@{me.username or 'yoxdur'})")
        except Exception as e:
            logger.error(f"❌ Telethon qoşulma xətası: {e}", exc_info=True)

    context.telethon_task = asyncio.create_task(start_telethon())

    # ── 1) .alive komandası ──
    @client.on(events.NewMessage(outgoing=True, pattern=r"^\.alive$"))
    async def on_alive_command(event):
        try:
            peer = await event.get_input_chat()
            for emo in ("\u2705", "👍", "🔥"):
                try:
                    await client(SendReactionRequest(
                        peer=peer,
                        msg_id=event.id,
                        reaction=[ReactionEmoji(emoticon=emo)]
                    ))
                    break
                except Exception:
                    continue
        except Exception:
            pass

        await asyncio.sleep(2)
        try:
            await event.delete()
        except Exception as e:
            logger.error(f".alive silinmə xətası: {e}")

    # ── 2) Yeni audiolara qulaq asmaq ──
    @client.on(events.NewMessage(func=lambda e: bool(e.audio)))
    async def on_new_audio(event):
        filler = getattr(context, "depo_filler", None)
        if not filler:
            return

        audio = event.audio
        title = next((attr.title for attr in audio.attributes if hasattr(attr, "title")), "")
        performer = next((attr.performer for attr in audio.attributes if hasattr(attr, "performer")), "")

        if not title:
            file_name = next((attr.file_name for attr in audio.attributes if hasattr(attr, "file_name")), "")
            title = os.path.splitext(file_name)[0] if file_name else "Naməlum"

        full_title = f"{performer} - {title}" if performer else title
        query = f"{performer} {title}".strip()

        item = {
            "title": full_title,
            "query": query,
            "url": None,
            "raw_duration": int(getattr(audio, "duration", 0) or 0),
            "meta": None,
            "thumb": None,
            "sp_id": None,
        }

        filler._add(item)
        logger.info(f"📥 Yeni audio birbaşa depo növbəsinə əlavə edildi: {full_title}")

    # ── 3) Userbot statusu ──
    @dp.message(Command("userbot"), F.from_user.id.func(is_creator))
    async def userbot_status(message: types.Message):
        if not client.is_connected() or not await client.is_user_authorized():
            await message.answer("🔴 <b>Userbot aktiv deyil!</b>\n<i>Əvvəlcə serverdə login.py ilə sessiya yaradın.</i>", parse_mode="HTML")
            return

        me = await client.get_me()
        await message.answer(
            f"🟢 <b>Userbot Aktivdir!</b>\n"
            f"👤 <b>Hesab:</b> {escape(me.first_name)} (@{escape(me.username or 'yoxdur')})\n"
            f"🆔 <b>ID:</b> <code>{me.id}</code>\n"
            f"📦 <i>Qrup və gizli kanallardan mahnıları skan edib /depo üçün istifadə edə bilərsiniz.</i>\n"
            f"⚡ <i>İstənilən çatda <code>.alive</code> yazaraq hesabı yoxlaya bilərsiniz.</i>",
            parse_mode="HTML",
        )

    # ── 4) Kanalı skan etmək ──
    @dp.message(Command("scan_chat"), F.from_user.id.func(is_creator))
    async def scan_chat_history(message: types.Message, command: CommandObject):
        if not client.is_connected() or not await client.is_user_authorized():
            await message.answer("🔴 Userbot aktiv deyil.", parse_mode="HTML")
            return

        if scan_state["running"]:
            await message.answer(
                "⚠️ Artıq aktiv bir skan prosesi gedir!\n"
                "<i>Dayandırmaq üçün:</i> /stop_scan",
                parse_mode="HTML",
            )
            return

        args = (command.args or "").strip().split()
        if not args:
            await message.answer(
                "ℹ️ <b>İstifadə:</b> <code>/scan_chat &lt;kanal_linki_və_ya_id&gt; [mesaj_sayı]</code>\n\n"
                "<i>Nümunələr:</i>\n"
                "• <code>/scan_chat https://t.me/+AbCdEfGhIjK</code> <i>(bütün kanalı skan edir)</i>\n"
                "• <code>/scan_chat https://t.me/kanal_adi 300</code> <i>(son 300 mesajı skan edir)</i>\n"
                "• <code>/scan_chat -1001234567890 500</code>",
                parse_mode="HTML",
            )
            return

        target_raw = args[0]
        target = int(target_raw) if (target_raw.startswith("-") and target_raw[1:].isdigit()) or target_raw.isdigit() else target_raw

        limit = None
        if len(args) > 1 and args[1].isdigit():
            limit = int(args[1])

        filler = getattr(context, "depo_filler", None)
        if not filler:
            await message.answer("❌ Depo doldurucu (<code>depo_filler_plugin</code>) aktiv deyil.", parse_mode="HTML")
            return

        scan_state["running"] = True
        scan_state["stop"] = False

        stop_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⏹ Skanı Dayandır", callback_data="scan:stop")]
        ])

        limit_label = f"Son {limit} mesaj" if limit else "Bütün tarixçə"
        status = await message.answer(
            f"🔎 <i>Kanal skan edilir ({limit_label})...\nDayandırmaq üçün aşağıdakı düyməni sıxa bilərsiniz.</i>",
            parse_mode="HTML",
            reply_markup=stop_kb,
        )

        scan_state["chat_id"] = status.chat.id
        scan_state["msg_id"] = status.message_id

        try:
            count = 0
            async for msg in client.iter_messages(target, limit=limit):
                if scan_state["stop"]:
                    break

                if msg.audio:
                    title = next((attr.title for attr in msg.audio.attributes if hasattr(attr, "title")), "")
                    performer = next((attr.performer for attr in msg.audio.attributes if hasattr(attr, "performer")), "")

                    if not title:
                        file_name = next((attr.file_name for attr in msg.audio.attributes if hasattr(attr, "file_name")), "")
                        title = os.path.splitext(file_name)[0] if file_name else "Naməlum"

                    full_title = f"{performer} - {title}" if performer else title
                    query = f"{performer} {title}".strip()

                    item = {
                        "title": full_title,
                        "query": query,
                        "url": None,
                        "raw_duration": int(getattr(msg.audio, "duration", 0) or 0),
                        "meta": None,
                        "thumb": None,
                        "sp_id": None,
                    }

                    filler._add(item)
                    count += 1

            if scan_state["stop"]:
                await status.edit_text(
                    f"⏹ <b>Skan dayandırıldı!</b>\n\n"
                    f"Dayandırılana qədər <b>{count}</b> ədəd audio <b>/depo</b> növbəsinə əlavə edildi.",
                    parse_mode="HTML",
                )
            else:
                await status.edit_text(
                    f"✅ <b>Skan tamamlandı!</b>\n\n"
                    f"🎶 <b>{count}</b> ədəd audio <b>/depo</b> növbəsinə əlavə edildi.\n"
                    f"<i>Vəziyyəti izləmək üçün <b>/depo</b> menyusuna baxa bilərsiniz.</i>",
                    parse_mode="HTML",
                )

        except Exception as e:
            logger.error(f"Kanal skan xətası ({target}): {e}", exc_info=True)
            await status.edit_text(f"❌ <b>Xəta baş verdi:</b> <code>{escape(str(e))[:300]}</code>", parse_mode="HTML")
        finally:
            scan_state["running"] = False
            scan_state["stop"] = False

    # ── 5) Skanı dayandırmaq üçün callback düyməsi və komanda ──
    @dp.callback_query(F.data == "scan:stop")
    async def cb_stop_scan(cb: types.CallbackQuery):
        if not is_creator(cb.from_user.id):
            await cb.answer("⛔ İcazəniz yoxdur.", show_alert=True)
            return

        if scan_state["running"]:
            scan_state["stop"] = True
            await cb.answer("⏹ Skan dayandırılır...")
        else:
            await cb.answer("Hazırda aktiv skan yoxdur.", show_alert=True)

    @dp.message(Command("stop_scan"), F.from_user.id.func(is_creator))
    async def cmd_stop_scan(message: types.Message):
        if scan_state["running"]:
            scan_state["stop"] = True
            await message.answer("⏹ <b>Skan prosesi dayandırılır...</b>", parse_mode="HTML")
        else:
            await message.answer("ℹ️ Hazırda aktiv skan prosesi getmir.", parse_mode="HTML")


async def teardown(context):
    task = getattr(context, "telethon_task", None)
    if task and not task.done():
        task.cancel()

    client = getattr(context, "telethon_client", None)
    if client and client.is_connected():
        await client.disconnect()
        logger.info("🛑 Telethon userbot bağlantısı kəsildi.")
