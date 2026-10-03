"""
🔑 Telegram hesabına bot daxilində giriş — core/tg_login.py

Həm 1️⃣ əsas userbot (telethon_plugin), həm 2️⃣ canlı yayım asistanı (voice_assistant_plugin) eyni axından
istifadə edir: telefon → kod → (varsa) 2FA parolu. Nəticə StringSession sətridir — plugin onu öz faylına yazır.

Təhlükəsizlik:
  • yalnız bot sahibi (creator) və yalnız şəxsi çatda;
  • nömrə / kod / parol mesajları dərhal silinir;
  • kod rəqəmlər arasında boşluqla yazılmalıdır — düz yazılmış kod Telegram tərəfindən "paylaşılıb" sayılıb
    etibarsız edilir;
  • gözləmə 10 dəq., sonra avtomatik bağlanır.
"""
import contextlib
import logging
import os
import re
import time
from html import escape

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

logger = logging.getLogger(__name__)

API_ID = os.getenv("TELETHON_API_ID")
API_HASH = os.getenv("TELETHON_API_HASH")
TTL = 600
_ACTIVE = {}                    # açar → LoginFlow


def any_waiting(uid) -> bool:
    """music_plugin: giriş məlumatı mahnı axtarışı sayılmasın."""
    return any(f.waiting(uid) for f in list(_ACTIVE.values()))


def write_secret(path: str, data: str):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(data)


def read_secret(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


class LoginFlow:
    """
    on_done(session_str, me) → (mətn, klaviatura) | qaytarılan mətn "❌" ilə başlayırsa sessiya saxlanmır
    cancel_cb — "❌ Ləğv et" düyməsinin callback_data-sı (plugin özü cancel() çağırır)
    """

    def __init__(self, key: str, context, title: str, hint: str, on_done, cancel_cb: str):
        self.key, self.ctx, self.title, self.hint = key, context, title, hint
        self.on_done, self.cancel_cb = on_done, cancel_cb
        self.st = None
        self._last = None

    # ── vəziyyət ──
    def waiting(self, uid) -> bool:
        st = self.st
        return bool(st) and uid == self.ctx.creator_id and time.time() < st["until"]

    def view(self, note: str = ""):
        step = (self.st or {}).get("step")
        text = f"🔑 <b>{self.title}</b>\n━━━━━━━━━━━━━━━━━━\n"
        if step == "phone":
            text += ("📱 Hesabın <b>telefon nömrəsini</b> göndər (beynəlxalq formatda):\n"
                     "<code>+994501234567</code>\n\n" + (f"<i>{self.hint}</i>\n" if self.hint else "")
                     + "<i>Mesajın dərhal silinir.</i>")
        elif step == "code":
            text += ("📨 Telegram həmin hesaba <b>kod</b> göndərdi.\n\n"
                     "⚠️ Kodu <b>rəqəmlər arasında boşluqla</b> yaz: <code>1 2 3 4 5</code>\n"
                     "<i>Düz yazılmış kodu Telegram \"paylaşılıb\" sayıb etibarsız edir.</i>")
        elif step == "password":
            text += "🔐 Hesabda <b>iki addımlı doğrulama</b> var — parolu göndər.\n<i>Mesaj dərhal silinir.</i>"
        if note:
            text += f"\n\n{note}"
        return text, InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="❌ Ləğv et", callback_data=self.cancel_cb)]])

    async def begin(self, chat_id: int, msg_id: int):
        await self.cancel()
        if not API_ID or not API_HASH:
            raise RuntimeError("config.env: TELETHON_API_ID / TELETHON_API_HASH yoxdur")
        self.st = {"step": "phone", "until": time.time() + TTL, "chat": chat_id, "msg": msg_id}
        _ACTIVE[self.key] = self
        return self.view()

    async def cancel(self):
        st, self.st = self.st, None
        _ACTIVE.pop(self.key, None)
        if st and st.get("client"):
            with contextlib.suppress(Exception):
                await st["client"].disconnect()

    async def _show(self, text, kb):
        st = self.st or self._last
        with contextlib.suppress(Exception):
            await self.ctx.bot.edit_message_text(chat_id=st["chat"], message_id=st["msg"], text=text,
                                                 parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True)

    # ── mesaj ──
    async def handle(self, message):
        st = self.st
        self._last = st
        text = (message.text or "").strip()
        with contextlib.suppress(Exception):
            await message.delete()
        from telethon.errors import (FloodWaitError, PasswordHashInvalidError, PhoneCodeExpiredError,
                                     PhoneCodeInvalidError, PhoneNumberBannedError, PhoneNumberInvalidError,
                                     SessionPasswordNeededError)
        st["until"] = time.time() + TTL
        try:
            if st["step"] == "phone":
                phone = re.sub(r"[^\d+]", "", text)
                if not re.fullmatch(r"\+?\d{8,15}", phone):
                    return await self._show(*self.view("⚠️ Nömrə düzgün deyil — <code>+994501234567</code>"))
                from telethon import TelegramClient
                from telethon.sessions import StringSession
                client = TelegramClient(StringSession(), int(API_ID), API_HASH)
                await client.connect()
                st["client"] = client
                sent = await client.send_code_request(phone)
                st.update(step="code", phone=phone, hash=sent.phone_code_hash)
                return await self._show(*self.view())
            if st["step"] == "code":
                code = re.sub(r"\D", "", text)
                if not 4 <= len(code) <= 8:
                    return await self._show(*self.view("⚠️ Kod tapılmadı — rəqəmləri boşluqla yaz: "
                                                       "<code>1 2 3 4 5</code>"))
                try:
                    await st["client"].sign_in(st["phone"], code, phone_code_hash=st["hash"])
                except SessionPasswordNeededError:
                    st["step"] = "password"
                    return await self._show(*self.view())
                return await self._finish()
            if st["step"] == "password":
                await st["client"].sign_in(password=text)
                return await self._finish()
        except PhoneNumberInvalidError:
            await self._show(*self.view("❌ Nömrə səhvdir və ya Telegram-da yoxdur"))
        except PhoneNumberBannedError:
            await self.cancel()
            await self._show(*self._done_view("❌ Bu nömrə Telegram tərəfindən bloklanıb"))
        except PhoneCodeInvalidError:
            await self._show(*self.view("❌ Kod səhvdir — yenidən yaz (boşluqla)"))
        except PhoneCodeExpiredError:
            await self.cancel()
            await self._show(*self._done_view("⌛ Kodun vaxtı bitdi (və ya düz yazılıb \"paylaşılmış\" sayıldı) — "
                                              "🔑 ilə yenidən başla"))
        except PasswordHashInvalidError:
            await self._show(*self.view("❌ Parol səhvdir — yenidən göndər"))
        except FloodWaitError as e:
            await self.cancel()
            await self._show(*self._done_view(f"⏳ Telegram limiti: {e.seconds} san. sonra yenidən sına"))
        except Exception as e:
            logger.warning(f"🔑 Giriş xətası ({self.key}): {e}")
            await self._show(*self.view(f"❌ <code>{escape(str(e)[:200])}</code>"))

    def _done_view(self, note):
        return note, InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="⬅️ Geri", callback_data=self.cancel_cb)]])

    async def _finish(self):
        st = self.st
        client = st["client"]
        session_str = client.session.save()
        me = await client.get_me()
        try:
            text, kb = await self.on_done(session_str, me, client)
        finally:
            st["client"] = None
            with contextlib.suppress(Exception):
                await client.disconnect()
            self.st = None
            _ACTIVE.pop(self.key, None)
        await self._show(text, kb)
