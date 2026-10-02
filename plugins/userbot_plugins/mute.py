"""🔇 .mute — mute edilən şəxs bu çatda yazan kimi mesajı silinir (bazada saxlanır)."""
import asyncio
import json
import time
from html import escape

from core.userbot_api import (FloodWaitError, User, Chat, events, fmt_left, fmt_span, get_db,
                              logger, mention, parse_duration, plain_name, resolve_target, safe_edit)

MUTE_KEY = "userbot:mutes"


class MuteRegistry:
    def __init__(self):
        self.data = self._load()

    def _load(self):
        try:
            raw = json.loads(get_db().get_setting(MUTE_KEY) or "{}")
            return {int(c): {int(u): v for u, v in us.items()} for c, us in raw.items()}
        except Exception:
            return {}

    def _save(self):
        try:
            get_db().set_setting(MUTE_KEY, json.dumps({str(c): {str(u): v for u, v in us.items()}
                                                       for c, us in self.data.items() if us}))
        except Exception as e:
            logger.warning(f"Mute siyahısı saxlanmadı: {e}")

    def get(self, chat_id, uid):
        e = self.data.get(chat_id, {}).get(uid)
        if e and e.get("until") and time.time() > e["until"]:
            self.remove(chat_id, uid)
            return None
        return e

    def add(self, chat_id, uid, name, seconds=None):
        self.data.setdefault(chat_id, {})[uid] = {"until": time.time() + seconds if seconds else 0,
                                                  "name": name, "at": time.time()}
        self._save()

    def remove(self, chat_id, uid) -> bool:
        ok = self.data.get(chat_id, {}).pop(uid, None) is not None
        if chat_id in self.data and not self.data[chat_id]:
            self.data.pop(chat_id)
        if ok:
            self._save()
        return ok

    def items(self, chat_id):
        for uid in list(self.data.get(chat_id, {})):
            self.get(chat_id, uid)                     # bitmişləri təmizlə
        return list(self.data.get(chat_id, {}).items())


async def check_mute_rights(client, chat):
    if isinstance(chat, User):
        return None
    perms = await client.get_permissions(chat, "me")
    if perms.is_creator or getattr(perms, "delete_messages", False) or (isinstance(chat, Chat) and perms.is_admin):
        return None
    return "⛔ Bu çatda <b>mesaj silmə</b> admin icazən yoxdur — mute işləməyəcək."


def register(ub):
    reg = MuteRegistry()
    ub.state["mute"] = reg
    ub.state["mute_check_rights"] = check_mute_rights

    @ub.command("mute", pattern=r"^\.mute(?:\s+(.+))?$",
                help=("yazan kimi mesajı silinsin",
                      "Şəxs bu çatda nə yazsa, mesajı dərhal silinir (userbot silir). Restartdan sonra da qalır.\n\n"
                      "<b>İstifadə:</b>\n• reply → <code>.mute</code> — həmişəlik\n• reply → <code>.mute 30m</code> / "
                      "<code>2h</code> / <code>1d</code>\n• <code>.mute @username 1h</code> · <code>.mute 123456789</code>\n\n"
                      "Qrupda <b>mesaj silmə</b> admin icazən olmalıdır; şəxsi çatda lazım deyil.\n"
                      "<i>Paneldə:</i> 👥 Bu çat → 🔇 Mute et / 🔇 Mute siyahısı"))
    async def on_mute(event):
        client = event.client
        args = (event.pattern_match.group(1) or "").split()
        try:
            ent, rest = await resolve_target(event, args)
            if ent is None:
                await safe_edit(event, "ℹ️ Kimi? Mesajına <b>reply</b> et və ya <code>.mute @username [10m|2h|1d]</code>")
                return
            me = await client.get_me()
            if ent.id == me.id:
                await safe_edit(event, "🙃 Özünü mute edə bilməzsən.")
                return
            chat = await event.get_chat()
            err = await check_mute_rights(client, chat)
            if err:
                await safe_edit(event, err)
                return
            seconds = None
            if rest:
                seconds = parse_duration(rest[0])
                if seconds is None:
                    await safe_edit(event, "ℹ️ Vaxt formatı: <code>30m</code>, <code>2h</code>, <code>1d</code> "
                                           "(rəqəm təkdirsə — dəqiqə)")
                    return
            reg.add(event.chat_id, ent.id, plain_name(ent), seconds)
            await safe_edit(event, f"🔇 {mention(ent)} mute edildi — "
                                   f"{'həmişəlik' if not seconds else fmt_span(seconds)}.\n"
                                   f"<i>Bu çatda yazdığı hər mesaj silinəcək. Götürmək: .unmute</i>")
        except Exception as e:
            await safe_edit(event, f"❌ <b>.mute:</b> <code>{escape(str(e))[:200]}</code>")

    @ub.command("unmute", pattern=r"^\.unmute(?:\s+(.+))?$",
                help=("mute-u götür",
                      "<b>İstifadə:</b>\n• reply → <code>.unmute</code>\n• <code>.unmute @username</code> · "
                      "<code>.unmute 123456789</code>\n<i>Paneldə:</i> 🔇 Mute siyahısı → 🔊 ad"))
    async def on_unmute(event):
        args = (event.pattern_match.group(1) or "").split()
        try:
            ent, _ = await resolve_target(event, args)
            if ent is None:
                await safe_edit(event, "ℹ️ Kimi? Reply et və ya <code>.unmute @username</code>")
                return
            if reg.remove(event.chat_id, ent.id):
                await safe_edit(event, f"🔊 {mention(ent)} artıq mute deyil.")
            else:
                await safe_edit(event, f"ℹ️ {mention(ent)} bu çatda mute edilməyib.")
        except Exception as e:
            await safe_edit(event, f"❌ <b>.unmute:</b> <code>{escape(str(e))[:200]}</code>")

    @ub.command("mutelist", help=("bu çatdakı mute edilənlər",
                                  "Mute edilənləri və qalan vaxtı göstərir.\n\n<b>İstifadə:</b> <code>.mutelist</code>"))
    async def on_mutelist(event):
        items = reg.items(event.chat_id)
        if not items:
            await safe_edit(event, "🔊 Bu çatda mute edilən yoxdur.")
            return
        L = [f"🔇 <b>Mute edilənlər</b> ({len(items)}):"]
        L += [f'• <a href="tg://user?id={uid}">{escape(e.get("name") or str(uid))}</a> — '
              f"<i>{fmt_left(e.get('until'))}</i>" for uid, e in items]
        await safe_edit(event, "\n".join(L))

    @ub.raw(events.NewMessage(incoming=True))
    async def on_incoming(event):
        if event.out or not reg.data.get(event.chat_id):
            return
        uid = event.sender_id
        if uid is None or not reg.get(event.chat_id, uid):
            return
        try:
            await event.delete()
        except FloodWaitError as e:
            await asyncio.sleep(e.seconds + 1)
            try:
                await event.delete()
            except Exception:
                pass
        except Exception as e:
            logger.info(f"Mute: mesaj silinmədi ({event.chat_id}/{uid}): {e}")
