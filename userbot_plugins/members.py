"""👥 .members — Qrup üzvlərini idarə etmə modulu.

Komandalar:
  • .kick        — İstifadəçini qrupdan çıxardır (banlamır)
  • .add         — İstifadəçini qrupa əlavə edir / dəvət edir
  • .promote     — Admin edir: yetki paneli (düymələr), şablonlar, titul
  • .promote all — Bütün üzvləri seçilmiş şablonla admin edir (təsdiqlə)
  • .demote      — Adminliyi geri alır
  • .demote all  — Dəyişə bildiyin bütün adminləri götürür (təsdiqlə)

Düymələr ub.out() inline kartı ilə gəlir (@bot vasitəsilə); yalnız creator / userbot hesabı basa bilər.
"""
import asyncio
import inspect
import time
from html import escape

from telethon.errors import (
    ChatAdminRequiredError,
    FloodWaitError,
    PeerFloodError,
    UserAdminInvalidError,
    UserAlreadyParticipantError,
    UserChannelsTooMuchError,
    UserNotMutualContactError,
    UserPrivacyRestrictedError,
)
from telethon.errors import UserNotParticipantError
from telethon.tl.functions.channels import EditAdminRequest, GetParticipantRequest, InviteToChannelRequest
from telethon.tl.functions.messages import AddChatUserRequest, EditChatAdminRequest, GetFullChatRequest
from telethon.tl.types import (
    Channel, Chat, ChatAdminRights, User,
    ChannelParticipantAdmin, ChannelParticipantCreator, ChannelParticipantsAdmins,
    ChatParticipantAdmin, ChatParticipantCreator,
)

from core.userbot_api import (
    Out,
    logger,
    mention,
    resolve_target,
    safe_edit,
)


async def check_rights(client, chat, action="kick"):
    if isinstance(chat, User):
        return "❌ Bu əmr yalnız <b>qruplarda</b> işlədilə bilər."
    perms = await client.get_permissions(chat, "me")
    if perms.is_creator:
        return None

    if action == "kick":
        if getattr(perms, "ban_users", False) or (isinstance(chat, Chat) and perms.is_admin):
            return None
        return "⛔ Bu qrupda <b>istifadəçiləri çıxarmaq / banlamaq</b> admin icazəniz yoxdur."

    if action == "add":
        if getattr(perms, "invite_users", True):
            return None
        return "⛔ Bu qrupda <b>istifadəçi əlavə etmək</b> icazəniz yoxdur."

    return None


# ───────────────────────── 🛡 promote / demote köməkçiləri ─────────────────────────
RIGHT_LABELS = {
    "change_info": "ℹ️ Məlumat",
    "post_messages": "📢 Paylaşım",
    "edit_messages": "✏️ Redaktə",
    "delete_messages": "🗑 Silmə",
    "ban_users": "🚫 Ban",
    "invite_users": "➕ Dəvət",
    "pin_messages": "📌 Pin",
    "manage_topics": "🗂 Mövzular",
    "manage_call": "🎙 Səsli yayım",
    "post_stories": "📸 Hekayə",
    "edit_stories": "🖊 Hekayə red.",
    "delete_stories": "🧽 Hekayə sil",
    "add_admins": "👮 Admin təyini",
    "anonymous": "🎭 Anonim",
}
PRESETS = {
    "full": ("🛡 Tam", None),                       # anonimlikdən başqa hamısı
    "mod": ("🧹 Moderator", {"delete_messages", "ban_users", "invite_users", "pin_messages",
                            "manage_call", "manage_topics", "post_messages", "edit_messages"}),
    "min": ("📌 Minimal", {"invite_users", "pin_messages"}),
}
PRESET_ALIASES = {"full": "full", "tam": "full", "all": "full",
                  "mod": "mod", "moderator": "mod",
                  "min": "min", "minimal": "min"}
RANK_MAX = 16
BULK_DELAY = 0.6                                    # iki əməliyyat arası (flood-dan qorunma)

ERRORS = {
    "ChatAdminRequiredError": "⛔ Bunun üçün admin icazən çatmır (və ya bu admini başqası təyin edib).",
    "RightForbiddenError": "⛔ Özündə olmayan yetkini verə bilməzsən.",
    "UserCreatorError": "👑 Qrupun yaradıcısının yetkiləri dəyişdirilə bilməz.",
    "AdminsTooMuchError": "📛 Admin limiti dolub (superqrupda maks. 50).",
    "UserNotParticipantError": "🚪 İstifadəçi qrupda deyil.",
    "UserPrivacyRestrictedError": "🔒 İstifadəçi qrupda deyil, gizlilik ayarları əlavə olunmağa icazə vermir.",
    "UserNotMutualContactError": "🔒 İstifadəçi qrupda deyil, gizlilik ayarları əlavə olunmağa icazə vermir.",
    "AdminRankEmojiNotAllowedError": "🏷 Titulda emoji ola bilməz.",
    "AdminRankInvalidError": f"🏷 Titul yanlışdır (maks. {RANK_MAX} simvol).",
    "BotChannelsNaError": "🤖 Bu botu admin etmək olmur.",
    "UserRestrictedError": "🔇 İstifadəçi məhdudlaşdırılıb (spam-blok) — admin edilə bilmir.",
    "UserIdInvalidError": "❌ İstifadəçi yanlışdır.",
    "UserBlockedError": "🚫 İstifadəçi bloklanıb.",
}


def err_text(e) -> str:
    if isinstance(e, FloodWaitError):
        return f"⏳ Telegram limiti: {e.seconds} san. gözləyin."
    return ERRORS.get(type(e).__name__) or f"❌ <code>{escape(str(e))[:200]}</code>"


def _rights_fields() -> set:
    """Quraşdırılmış Telethon versiyasının ChatAdminRights-ində olan sahələr."""
    return set(inspect.signature(ChatAdminRights.__init__).parameters) - {"self"}


def make_rights(sel: dict) -> ChatAdminRights:
    allowed = _rights_fields()
    return ChatAdminRights(**{k: bool(v) for k, v in (sel or {}).items() if k in allowed})


def avail_keys(chat) -> list:
    """Bu çat növündə verilə bilən yetkilər (adi qrupda yetki seçimi yoxdur)."""
    if not isinstance(chat, Channel):
        return []
    if getattr(chat, "broadcast", False):
        keys = ["change_info", "post_messages", "edit_messages", "delete_messages", "invite_users",
                "manage_call", "post_stories", "edit_stories", "delete_stories", "add_admins"]
    else:
        keys = ["change_info", "delete_messages", "ban_users", "invite_users", "pin_messages"]
        if getattr(chat, "forum", False):
            keys.append("manage_topics")
        keys += ["manage_call", "add_admins", "anonymous"]
    allowed = _rights_fields()
    return [k for k in keys if k in allowed]


def my_rights(perms, keys) -> dict:
    """Userbot-un özünün verə biləcəyi yetkilər (yaradıcı → hamısı)."""
    if perms.is_creator:
        return {k: True for k in keys}
    ar = getattr(getattr(perms, "participant", None), "admin_rights", None)
    return {k: bool(getattr(ar, k, False)) for k in keys}


def preset_sel(name, keys, mine) -> dict:
    want = PRESETS[name][1]
    sel = {k: bool(mine.get(k)) and (k != "anonymous" if want is None else k in want) for k in keys}
    if keys and not any(sel.values()):                # heç nə qalmadısa — verə bildiyin ilk yetki
        first = next((k for k in keys if mine.get(k) and k != "anonymous"), None)
        if first:
            sel[first] = True
    return sel


async def admin_check(client, chat):
    """(perms, xəta mətni) — admin təyin etmək mümkündürmü."""
    if not isinstance(chat, (Channel, Chat)):
        return None, "❌ Bu əmr yalnız <b>qruplarda / kanallarda</b> işlədilə bilər."
    perms = await client.get_permissions(chat, "me")
    if perms.is_creator:
        return perms, None
    if isinstance(chat, Chat):
        return None, "⛔ Adi qrupda adminləri yalnız <b>yaradıcı</b> təyin edə / götürə bilər."
    if not getattr(perms, "add_admins", False):
        return None, "⛔ Bu qrupda <b>admin təyin etmə</b> icazən yoxdur."
    return perms, None


async def member_info(client, chat, ent) -> dict:
    """Hədəfin qrupdakı vəziyyəti: kind = creator / admin / member / none."""
    info = {"kind": "none", "rights": None, "rank": "", "can_edit": True}
    if isinstance(chat, Channel):
        try:
            p = (await client(GetParticipantRequest(chat, ent))).participant
        except UserNotParticipantError:
            return info
        if isinstance(p, ChannelParticipantCreator):
            info.update(kind="creator", rank=getattr(p, "rank", "") or "", can_edit=False)
        elif isinstance(p, ChannelParticipantAdmin):
            r = p.admin_rights
            info.update(kind="admin", rights={k: bool(getattr(r, k, False)) for k in RIGHT_LABELS},
                        rank=p.rank or "", can_edit=bool(getattr(p, "can_edit", False)))
        else:
            info["kind"] = "member"
        return info
    full = await client(GetFullChatRequest(chat.id))
    parts = getattr(full.full_chat.participants, "participants", []) or []
    p = next((x for x in parts if x.user_id == ent.id), None)
    if isinstance(p, ChatParticipantCreator):
        info.update(kind="creator", can_edit=False)
    elif isinstance(p, ChatParticipantAdmin):
        info["kind"] = "admin"
    elif p is not None:
        info["kind"] = "member"
    return info


async def apply_admin(client, chat, ent, sel=None, rank="", demote=False):
    if isinstance(chat, Channel):
        rights = ChatAdminRights() if demote else make_rights(sel)
        await client(EditAdminRequest(chat, ent, rights, "" if demote else (rank or "")))
    else:
        await client(EditChatAdminRequest(chat.id, ent, is_admin=not demote))


async def run_bulk(users, op, report, title, verb):
    """users üzərində op(u) — FloodWait-i gözləyir, admin limitində dayanır, proqres göstərir."""
    total, done, failed, last = len(users), 0, 0, 0.0
    errs, stop_reason = {}, None
    for u in users:
        while True:
            try:
                await op(u)
                done += 1
                break
            except FloodWaitError as e:
                await report(f"⏳ Telegram limiti — {e.seconds} san. gözlənilir... ({done}/{total})")
                await asyncio.sleep(e.seconds + 1)
            except Exception as e:
                name = type(e).__name__
                if name == "AdminsTooMuchError":
                    stop_reason = ERRORS[name]
                    break
                failed += 1
                errs[name] = errs.get(name, 0) + 1
                logger.debug(f"bulk {verb} {getattr(u, 'id', '?')}: {e}")
                break
        if stop_reason:
            break
        if time.monotonic() - last > 3:
            last = time.monotonic()
            await report(f"{verb} <b>{title}</b>\n✅ {done}/{total} · ❌ {failed}")
        await asyncio.sleep(BULK_DELAY)
    lines = [f"🏁 <b>{title}</b> — bitdi", f"✅ Uğurlu: <b>{done}</b>/{total} · ❌ alınmadı: {failed}"]
    if stop_reason:
        lines.append(stop_reason)
    if errs:
        lines.append("<i>" + " · ".join(f"{k.replace('Error', '')}: {v}" for k, v in errs.items()) + "</i>")
    return "\n".join(lines)


def register(ub):
    async def owner_ids(client) -> set:
        if "me_id" not in ub.state:
            ub.state["me_id"] = (await client.get_me()).id
        return {i for i in (ub.creator_id, ub.state["me_id"]) if i}

    async def guard(cb, st) -> bool:
        if cb.from_user.id in st["owners"]:
            return True
        try:
            await cb.answer("⛔ Bu panel sənin deyil", show_alert=True)
        except Exception:
            pass
        return False

    async def toast(cb, text, alert=False):
        try:
            await cb.answer(text, show_alert=alert)
        except Exception:
            pass

    # ───────────────────────── .promote ─────────────────────────
    @ub.command(
        "promote",
        pattern=r"^\.promote(?:\s+([\s\S]+))?$",
        help=(
            "istifadəçini admin et (yetki paneli)",
            "İstifadəçini admin edir. Düymələrlə hər yetkini aç/bağla, şablon seç, təsdiq et.\n\n"
            "<b>İstifadə:</b>\n"
            "• reply → <code>.promote [şablon] [titul]</code>\n"
            "• <code>.promote @username [şablon] [titul]</code>\n"
            "• <code>.promote all [şablon]</code> — bütün üzvlər (təsdiqlə)\n\n"
            "<b>Şablonlar</b> (yazılsa panelsiz dərhal tətbiq olunur):\n"
            "<code>full</code> — tam · <code>mod</code> — moderator · <code>min</code> — minimal\n\n"
            f"Titul maks. {RANK_MAX} simvol. <i>Superqrupda admin limiti 50-dir.</i>\n"
            "Admin təyin etmə icazən olmalıdır.",
        ),
    )
    async def on_promote(event):
        client = event.client
        chat = await event.get_chat()
        perms, err = await admin_check(client, chat)
        if err:
            await safe_edit(event, err)
            return

        raw = (event.pattern_match.group(1) or "").strip()
        args = raw.split() if raw else []
        if args and args[0].lower() == "all" and not event.is_reply:
            preset = PRESET_ALIASES.get(args[1].lower(), "mod") if len(args) > 1 else "mod"
            await promote_all(event, chat, perms, preset)
            return

        try:
            ent, rest = await resolve_target(event, args)
        except Exception as e:
            await safe_edit(event, f"❌ Hədəf tapılmadı: <code>{escape(str(e))[:100]}</code>")
            return
        if ent is None:
            await safe_edit(
                event,
                "ℹ️ <b>Kimi admin etmək istəyirsiniz?</b>\n\n"
                "• Mesajına <b>reply</b> edib <code>.promote [şablon] [titul]</code>\n"
                "• Və ya: <code>.promote @username [şablon] [titul]</code>\n"
                "• Hamısı: <code>.promote all [full|mod|min]</code>",
            )
            return
        if not isinstance(ent, User):
            await safe_edit(event, "❌ Yalnız istifadəçini admin etmək olar (kanal / anonim admin yox).")
            return
        if ent.is_self:
            await safe_edit(event, "🙃 Özünü admin edə bilməzsən.")
            return

        preset = None
        if rest and rest[0].lower() in PRESET_ALIASES:
            preset = PRESET_ALIASES[rest[0].lower()]
            rest = rest[1:]
        rank = " ".join(rest).strip()
        if len(rank) > RANK_MAX:
            await safe_edit(event, f"🏷 Titul çox uzundur ({len(rank)}), maks. {RANK_MAX} simvol.")
            return

        try:
            info = await member_info(client, chat, ent)
        except Exception as e:
            await safe_edit(event, err_text(e))
            return
        if info["kind"] == "creator":
            await safe_edit(event, ERRORS["UserCreatorError"])
            return

        keys = avail_keys(chat)
        mine = my_rights(perms, keys)
        if info["rights"]:
            sel = {k: bool(info["rights"].get(k)) for k in keys}
        else:
            sel = preset_sel(preset or "mod", keys, mine)
        if preset:
            sel = preset_sel(preset, keys, mine)

        st = {"chat": chat, "ent": ent, "keys": keys, "mine": mine, "sel": sel,
              "rank": rank or info["rank"], "info": info, "owners": await owner_ids(client), "client": client}

        if preset:                                     # panelsiz dərhal
            try:
                await apply_admin(client, chat, ent, sel, st["rank"])
            except Exception as e:
                await safe_edit(event, err_text(e))
                return
            st["info"]["kind"] = "admin"
            out = Out(ub, event)
            wire_promote(out, st)
            await out.open(done_text(st, "⬆️ Admin edildi"), result_rows(out, st))
            return

        out = Out(ub, event)
        wire_promote(out, st)
        await out.open(panel_text(st), panel_rows(out, st))

    def panel_text(st) -> str:
        chat, ent, info = st["chat"], st["ent"], st["info"]
        L = [f"🛡 <b>Admin et</b>",
             f"👤 {mention(ent)}",
             f"👥 {escape(getattr(chat, 'title', '') or 'qrup')}",
             f"🏷 Titul: <i>{escape(st['rank']) if st['rank'] else '—'}</i>"]
        if info["kind"] == "admin":
            L.append("\nℹ️ Artıq admindir — yetkiləri dəyişib təsdiq et.")
            if not info["can_edit"]:
                L.append("⚠️ <i>Bu admini başqası təyin edib — dəyişmək mümkün olmaya bilər.</i>")
        elif info["kind"] == "none":
            L.append("\n🚪 <i>Qrupda deyil — gizlilik ayarları icazə versə əlavə olunacaq.</i>")
        if st["keys"]:
            n = sum(1 for v in st["sel"].values() if v)
            L.append(f"\n🔐 Seçilib: <b>{n}/{len(st['keys'])}</b>  ·  🔒 = səndə olmayan yetki")
        else:
            L.append("\n<i>Adi qrup: admin bütün yetkiləri alır, seçim yoxdur.</i>")
        L.append("<i>Titulu dəyişmək: </i><code>.promote @user Titul</code>")
        return "\n".join(L)

    def done_text(st, head) -> str:
        L = [f"{head}: {mention(st['ent'])}",
             f"👥 {escape(getattr(st['chat'], 'title', '') or 'qrup')}"]
        if st["keys"] and st["info"]["kind"] == "admin":
            got = [RIGHT_LABELS[k] for k in st["keys"] if st["sel"].get(k)]
            L.append("🔐 " + (", ".join(got) or "—"))
        if st["rank"] and st["info"]["kind"] == "admin":
            L.append(f"🏷 <i>{escape(st['rank'])}</i>")
        return "\n".join(L)

    def panel_rows(out, st) -> list:
        rows = []
        btns = []
        for k in st["keys"]:
            label = RIGHT_LABELS[k]
            if not st["mine"].get(k):
                btns.append(out.btn(f"🔒 {label}", f"t:{k}"))
            else:
                btns.append(out.btn(f"{'✅' if st['sel'].get(k) else '▫️'} {label}", f"t:{k}"))
        rows += [btns[i:i + 2] for i in range(0, len(btns), 2)]
        if st["keys"]:
            rows.append([out.btn(PRESETS[p][0], f"p:{p}") for p in PRESETS])
        last = [out.btn("✅ Təsdiq et", "ok")]
        if st["info"]["kind"] == "admin":
            last.append(out.btn("⬇️ Demote", "dm"))
        rows.append(last)
        return rows

    def result_rows(out, st) -> list:
        if st["info"]["kind"] == "admin":
            return [[out.btn("⚙️ Yetkiləri dəyiş", "ed"), out.btn("⬇️ Demote", "dm")]]
        return [[out.btn("⬆️ Yenidən admin et", "ed")]]

    def wire_promote(out, st):
        """Karta toggle / şablon / təsdiq / demote düymələrini bağlayır."""
        client = st["client"]

        for key in st["keys"]:
            async def _toggle(o, cb, k=key):
                if not await guard(cb, st):
                    return
                if not st["mine"].get(k):
                    await toast(cb, "🔒 Bu yetki səndə yoxdur — verə bilməzsən", True)
                    return
                st["sel"][k] = not st["sel"].get(k)
                await o.update(panel_text(st), panel_rows(o, st))
            out.on(f"t:{key}")(_toggle)

        for name in PRESETS:
            async def _preset(o, cb, p=name):
                if not await guard(cb, st):
                    return
                st["sel"] = preset_sel(p, st["keys"], st["mine"])
                await toast(cb, f"{PRESETS[p][0]} seçildi")
                await o.update(panel_text(st), panel_rows(o, st))
            out.on(f"p:{name}")(_preset)

        @out.on("ok")
        async def _ok(o, cb):
            if not await guard(cb, st):
                return
            if st["keys"] and not any(st["sel"].values()):
                await toast(cb, "Ən azı bir yetki seç (adminliyi götürmək üçün ⬇️ Demote)", True)
                return
            try:
                await apply_admin(client, st["chat"], st["ent"], st["sel"], st["rank"])
            except Exception as e:
                await toast(cb, "Alınmadı")
                await o.update(panel_text(st) + "\n\n" + err_text(e), panel_rows(o, st))
                return
            was_admin = st["info"]["kind"] == "admin"
            st["info"].update(kind="admin", can_edit=True)
            await o.update(done_text(st, "🔄 Yetkilər yeniləndi" if was_admin else "⬆️ Admin edildi"),
                           result_rows(o, st))

        @out.on("dm")
        async def _dm(o, cb):
            if not await guard(cb, st):
                return
            try:
                await apply_admin(client, st["chat"], st["ent"], demote=True)
            except Exception as e:
                await toast(cb, "Alınmadı")
                await o.update(done_text(st, "⚠️ Demote alınmadı") + "\n\n" + err_text(e))
                return
            st["info"].update(kind="member", rights=None)
            await o.update(done_text(st, "⬇️ Adminlik götürüldü"), result_rows(o, st))

        @out.on("ed")
        async def _ed(o, cb):
            if not await guard(cb, st):
                return
            await o.update(panel_text(st), panel_rows(o, st))

    # ── .promote all ──
    async def promote_all(event, chat, perms, preset):
        client = event.client
        keys = avail_keys(chat)
        mine = my_rights(perms, keys)
        title = escape(getattr(chat, "title", "") or "qrup")
        st = {"preset": preset, "bots": False, "humans": [], "botlist": [],
              "owners": await owner_ids(client)}

        await safe_edit(event, f"🔎 <b>{title}</b>: üzvlər yoxlanılır...")
        out = Out(ub, event)
        try:
            async for u in client.iter_participants(chat):
                p = getattr(u, "participant", None)
                if u.is_self or u.deleted or isinstance(
                        p, (ChannelParticipantAdmin, ChannelParticipantCreator,
                            ChatParticipantAdmin, ChatParticipantCreator)):
                    continue
                (st["botlist"] if u.bot else st["humans"]).append(u)
        except Exception as e:
            await safe_edit(event, f"❌ Üzvlər oxunmadı: {err_text(e)}")
            return

        def text():
            n = len(st["humans"]) + (len(st["botlist"]) if st["bots"] else 0)
            L = [f"⬆️ <b>Hamını admin et</b> — {title}",
                 f"👤 İnsan: <b>{len(st['humans'])}</b> · 🤖 bot: {len(st['botlist'])}",
                 f"🎯 Admin ediləcək: <b>{n}</b>"]
            if keys:
                L.append(f"🔐 Şablon: <b>{PRESETS[st['preset']][0]}</b>")
                L.append(f"<i>📛 Superqrupda admin limiti 50-dir — dolanda dayanacaq.</i>")
            L.append("\n⚠️ <b>Diqqət:</b> hamı admin olacaq. Davam edilsin?")
            return "\n".join(L)

        def rows(o):
            r = []
            if keys:
                r.append([o.btn(("• " if st["preset"] == p else "") + PRESETS[p][0], f"p:{p}") for p in PRESETS])
            r.append([o.btn(("✅" if st["bots"] else "▫️") + " 🤖 Botlar da", "b")])
            r.append([o.btn("▶️ Başla", "go")])
            return r

        for name in PRESETS:
            async def _preset(o, cb, p=name):
                if not await guard(cb, st):
                    return
                st["preset"] = p
                await o.update(text(), rows(o))
            out.on(f"p:{name}")(_preset)

        @out.on("b")
        async def _bots(o, cb):
            if not await guard(cb, st):
                return
            st["bots"] = not st["bots"]
            await o.update(text(), rows(o))

        @out.on("go")
        async def _go(o, cb):
            if not await guard(cb, st):
                return
            if o.running():
                await toast(cb, "Artıq gedir")
                return
            users = st["humans"] + (st["botlist"] if st["bots"] else [])
            if not users:
                await toast(cb, "Admin ediləcək üzv yoxdur", True)
                return
            sel = preset_sel(st["preset"], keys, mine)

            async def job():
                try:
                    res = await run_bulk(users, lambda u: apply_admin(client, chat, u, sel),
                                         lambda t: o.update(t, rows=[]), title, "⬆️")
                    await o.update(res, rows=[])
                except asyncio.CancelledError:
                    await o.update(f"⏹ <b>{title}</b> — dayandırıldı", rows=[])
                    raise
            o.track("promote_all", job())
            await o.update(f"⬆️ <b>{title}</b>: başlayır... (0/{len(users)})", rows=[])

        await out.open(text(), rows(out))

    # ───────────────────────── .demote ─────────────────────────
    @ub.command(
        "demote",
        pattern=r"^\.demote(?:\s+([\s\S]+))?$",
        help=(
            "adminliyi geri al",
            "İstifadəçinin admin yetkilərini tamamilə götürür.\n\n"
            "<b>İstifadə:</b>\n"
            "• reply → <code>.demote</code>\n"
            "• <code>.demote @username</code> / <code>.demote 123456789</code>\n"
            "• <code>.demote all</code> — dəyişə bildiyin bütün adminlər (təsdiqlə)\n\n"
            "<i>Yalnız sənin (və ya yaradıcıysansa hər kəsin) təyin etdiyi adminlər götürülə bilər.</i>",
        ),
    )
    async def on_demote(event):
        client = event.client
        chat = await event.get_chat()
        perms, err = await admin_check(client, chat)
        if err:
            await safe_edit(event, err)
            return

        raw = (event.pattern_match.group(1) or "").strip()
        args = raw.split() if raw else []
        if args and args[0].lower() == "all" and not event.is_reply:
            await demote_all(event, chat, perms)
            return

        try:
            ent, _ = await resolve_target(event, args)
        except Exception as e:
            await safe_edit(event, f"❌ Hədəf tapılmadı: <code>{escape(str(e))[:100]}</code>")
            return
        if ent is None:
            await safe_edit(
                event,
                "ℹ️ <b>Kimin adminliyini götürmək istəyirsiniz?</b>\n\n"
                "• Mesajına <b>reply</b> edib <code>.demote</code>\n"
                "• Və ya: <code>.demote @username</code>\n"
                "• Hamısı: <code>.demote all</code>",
            )
            return
        if not isinstance(ent, User) or ent.is_self:
            await safe_edit(event, "❌ Bu hədəfin adminliyini götürmək olmaz.")
            return

        try:
            info = await member_info(client, chat, ent)
        except Exception as e:
            await safe_edit(event, err_text(e))
            return
        if info["kind"] == "creator":
            await safe_edit(event, ERRORS["UserCreatorError"])
            return
        if info["kind"] != "admin":
            await safe_edit(event, f"ℹ️ {mention(ent)} admin deyil.")
            return

        try:
            await apply_admin(client, chat, ent, demote=True)
        except Exception as e:
            await safe_edit(event, err_text(e))
            return

        keys = avail_keys(chat)
        old = info["rights"] or {}
        st = {"chat": chat, "ent": ent, "keys": keys, "mine": my_rights(perms, keys),
              "sel": {k: bool(old.get(k)) for k in keys} if old else preset_sel("mod", keys, my_rights(perms, keys)),
              "rank": info["rank"], "info": {**info, "kind": "member", "rights": None},
              "owners": await owner_ids(client), "client": client}
        out = Out(ub, event)
        wire_promote(out, st)
        await out.open(done_text(st, "⬇️ Adminlik götürüldü"), result_rows(out, st))  # ⬆️ köhnə yetkilərlə geri

    # ── .demote all ──
    async def demote_all(event, chat, perms):
        client = event.client
        title = escape(getattr(chat, "title", "") or "qrup")
        st = {"bots": False, "humans": [], "botlist": [], "locked": 0, "owners": await owner_ids(client)}

        await safe_edit(event, f"🔎 <b>{title}</b>: adminlər yoxlanılır...")
        out = Out(ub, event)
        try:
            flt = ChannelParticipantsAdmins() if isinstance(chat, Channel) else None
            async for u in client.iter_participants(chat, filter=flt):
                p = getattr(u, "participant", None)
                if u.is_self or not isinstance(p, (ChannelParticipantAdmin, ChatParticipantAdmin)):
                    continue
                if isinstance(p, ChannelParticipantAdmin) and not perms.is_creator and not p.can_edit:
                    st["locked"] += 1                 # başqasının təyin etdiyi — dəyişə bilmirik
                    continue
                (st["botlist"] if u.bot else st["humans"]).append(u)
        except Exception as e:
            await safe_edit(event, f"❌ Adminlər oxunmadı: {err_text(e)}")
            return

        def text():
            n = len(st["humans"]) + (len(st["botlist"]) if st["bots"] else 0)
            L = [f"⬇️ <b>Bütün adminləri götür</b> — {title}",
                 f"👤 İnsan: <b>{len(st['humans'])}</b> · 🤖 bot: {len(st['botlist'])}",
                 f"🎯 Götürüləcək: <b>{n}</b>"]
            if st["locked"]:
                L.append(f"🔒 Başqasının təyin etdiyi {st['locked']} admin toxunulmaz qalacaq.")
            L.append("<i>🤖 Botlar default saxlanılır (məs. musiqi botu admin qalsın).</i>")
            L.append("\n⚠️ <b>Davam edilsin?</b>")
            return "\n".join(L)

        def rows(o):
            return [[o.btn(("✅" if st["bots"] else "▫️") + " 🤖 Botlar da", "b")],
                    [o.btn("▶️ Başla", "go")]]

        @out.on("b")
        async def _bots(o, cb):
            if not await guard(cb, st):
                return
            st["bots"] = not st["bots"]
            await o.update(text(), rows(o))

        @out.on("go")
        async def _go(o, cb):
            if not await guard(cb, st):
                return
            if o.running():
                await toast(cb, "Artıq gedir")
                return
            users = st["humans"] + (st["botlist"] if st["bots"] else [])
            if not users:
                await toast(cb, "Götürüləcək admin yoxdur", True)
                return

            async def job():
                try:
                    res = await run_bulk(users, lambda u: apply_admin(client, chat, u, demote=True),
                                         lambda t: o.update(t, rows=[]), title, "⬇️")
                    await o.update(res, rows=[])
                except asyncio.CancelledError:
                    await o.update(f"⏹ <b>{title}</b> — dayandırıldı", rows=[])
                    raise
            o.track("demote_all", job())
            await o.update(f"⬇️ <b>{title}</b>: başlayır... (0/{len(users)})", rows=[])

        await out.open(text(), rows(out))

    # ───────────────────────── .kick ─────────────────────────
    @ub.command(
        "kick",
        pattern=r"^\.kick(?:\s+([\s\S]+))?$",
        help=(
            "istifadəçini qrupdan çıxart",
            "İstifadəçini qrupdan çıxarır (banlamır, təkrar daxil ola bilər).\n\n"
            "<b>İstifadə:</b>\n"
            "• reply → <code>.kick [səbəb]</code>\n"
            "• <code>.kick @username [səbəb]</code>\n"
            "• <code>.kick 123456789 [səbəb]</code>\n\n"
            "Qrupda istifadəçiləri çıxarmaq admin icazəniz olmalıdır.",
        ),
    )
    async def on_kick(event):
        client = event.client
        chat = await event.get_chat()

        err = await check_rights(client, chat, action="kick")
        if err:
            await safe_edit(event, err)
            return

        raw_args = (event.pattern_match.group(1) or "").strip()
        args = raw_args.split() if raw_args else []

        try:
            ent, rest = await resolve_target(event, args)
        except Exception as e:
            await safe_edit(event, f"❌ Hədəf tapılmadı: <code>{escape(str(e))[:100]}</code>")
            return

        if ent is None:
            await safe_edit(
                event,
                "ℹ️ <b>Kimi çıxarmaq istəyirsiniz?</b>\n\n"
                "• Mesajına <b>reply</b> edib <code>.kick [səbəb]</code> yazın\n"
                "• Və ya: <code>.kick @username [səbəb]</code>",
            )
            return

        me = await client.get_me()
        if ent.id == me.id:
            await safe_edit(event, "🙃 Özünüzü qrupdan çıxara bilməzsiniz.")
            return

        reason = " ".join(rest).strip() if rest else ""

        try:
            await client.kick_participant(chat, ent)
        except UserAdminInvalidError:
            await safe_edit(event, "❌ <b>Adminləri</b> qrupdan çıxarmaq mümkün deyil.")
            return
        except ChatAdminRequiredError:
            await safe_edit(event, "⛔ İstifadəçini çıxarmaq üçün kifayət qədər admin icazəniz yoxdur.")
            return
        except Exception as e:
            logger.info(f".kick xətası ({ent.id}): {e}")
            await safe_edit(event, f"❌ <b>.kick xətası:</b> <code>{escape(str(e))[:200]}</code>")
            return

        user_mention = mention(ent)
        reason_line = f"\n📄 <b>Səbəb:</b> <i>{escape(reason)}</i>" if reason else ""

        await safe_edit(
            event,
            f"👢 {user_mention} qrupdan çıxarıldı.{reason_line}"
        )

    # ───────────────────────── .add ─────────────────────────
    @ub.command(
        "add",
        pattern=r"^\.add(?:\s+(\S+))?$",
        help=(
            "istifadəçini qrupa əlavə et",
            "İstifadəçini cari qrupa birbaşa əlavə edir.\n\n"
            "<b>İstifadə:</b>\n"
            "• reply → <code>.add</code>\n"
            "• <code>.add @username</code>\n"
            "• <code>.add 123456789</code>\n\n"
            "Qrupda istifadəçi əlavə etmək icazəniz olmalıdır.",
        ),
    )
    async def on_add(event):
        client = event.client
        chat = await event.get_chat()

        err = await check_rights(client, chat, action="add")
        if err:
            await safe_edit(event, err)
            return

        raw_arg = (event.pattern_match.group(1) or "").strip()
        args = [raw_arg] if raw_arg else []

        try:
            ent, _ = await resolve_target(event, args)
        except Exception as e:
            await safe_edit(event, f"❌ İstifadəçi tapılmadı: <code>{escape(str(e))[:100]}</code>")
            return

        if ent is None:
            await safe_edit(
                event,
                "ℹ️ <b>Kimi əlavə etmək istəyirsiniz?</b>\n\n"
                "• Mesajına <b>reply</b> edib <code>.add</code> yazın\n"
                "• Və ya: <code>.add @username</code> / <code>.add 123456789</code>",
            )
            return

        if not isinstance(ent, User):
            await safe_edit(event, "❌ Yalnız istifadəçiləri qrupa əlavə etmək olar (kanal və ya qrup yox).")
            return

        user_mention = mention(ent)

        try:
            if isinstance(chat, Channel):
                await client(InviteToChannelRequest(channel=chat, users=[ent]))
            elif isinstance(chat, Chat):
                await client(AddChatUserRequest(chat_id=chat.id, user_id=ent.id, fwd_limit=0))
            else:
                await safe_edit(event, "❌ Bu əmr yalnız <b>qruplarda</b> işlədilə bilər.")
                return

            await safe_edit(event, f"✅ {user_mention} qrupa əlavə edildi.")

        except UserAlreadyParticipantError:
            await safe_edit(event, f"ℹ️ {user_mention} artıq bu qrupdadır.")
        except (UserPrivacyRestrictedError, UserNotMutualContactError):
            await safe_edit(event, f"🔒 {user_mention} istifadəçisinin gizlilik ayarları qrupa əlavə olunmağa icazə vermir.")
        except UserChannelsTooMuchError:
            await safe_edit(event, f"❌ {user_mention} həddindən artıq çox qrup və ya kanala üzvdür.")
        except PeerFloodError:
            await safe_edit(event, "⛔ Telegram limiti (PeerFlood): Hesabınız hazırda qrupa üzv əlavə edə bilmir.")
        except FloodWaitError as fe:
            await safe_edit(event, f"⏳ Telegram limiti: {fe.seconds} saniyə gözləyin.")
        except ChatAdminRequiredError:
            await safe_edit(event, "⛔ Qrupda üzv əlavə etmək icazəniz yoxdur.")
        except Exception as e:
            logger.info(f".add xətası ({ent.id}): {e}")
            await safe_edit(event, f"❌ <b>.add xətası:</b> <code>{escape(str(e))[:200]}</code>")
            