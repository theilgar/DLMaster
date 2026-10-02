"""📦 Depo — depo doldurucu idarəsi (panel bölməsi). depo_filler_plugin ilə işləyir."""
from html import escape

from core.userbot_api import InlineKeyboardMarkup


def register(ub):
    ub.add_help("p_depo", "📦 Depo", "depo doldurucu",
                "Vəziyyət, növbə, statistika; ▶️/⏸, 🔁 növbəni yenidən qur, ⏹ skanı dayandır.", "panel")

    def view(sctx, note=""):
        filler = getattr(ub.context, "depo_filler", None)
        rows = []
        if filler is None:
            L = ["📦 <b>Depo</b>", "", "❌ depo_filler_plugin yüklənməyib."]
        else:
            st = filler.stats
            on = filler.running
            L = ["📦 <b>Depo doldurucu</b>", "",
                 f"📶 Vəziyyət: <b>{'🟢 işləyir' if on else '🔴 söndürülüb'}</b>",
                 f"⬆️ Yükləndi: <b>{st.get('done', 0)}</b> · ⏭ depoda idi: {st.get('cached', 0)} · ❌ {st.get('failed', 0)}",
                 f"📋 Növbə: <b>{len(filler.queue)}</b>"]
            if st.get("current"):
                L.append(f"⏳ İndi: <i>{escape(str(st['current'])[:60])}</i>")
            if st.get("note"):
                L.append(f"ℹ️ {escape(str(st['note']))}")
            try:
                L.append(f"🗂 Mənbə: {len(filler.sources())} · ✈️ Telegram: {len(filler.tg_source_ids())}")
            except Exception:
                pass
            fix_count = getattr(ub.context, "fix_artist_count", None)
            try:
                bad = fix_count() if fix_count else 0
            except Exception:
                bad = 0
            if bad:
                L.append(f"🩹 Təmizlik gözləyir: {bad} <i>(botda /fix)</i>")
            scan = getattr(ub.context, "depo_scan_state", {}) or {}
            rows.append([sctx.btn("⏸ Söndür", "off") if on else sctx.btn("▶️ İşə sal", "on"),
                         sctx.btn("🔁 Növbəni qur", "rebuild")])
            if scan.get("running"):
                L.append("🔎 <i>Skan gedir</i>")
                rows.append([sctx.btn("⏹ Skanı dayandır", "scanstop")])
            rows.append([sctx.btn("🔄 Yenilə", "r")])
        if note:
            L += ["", note]
        rows.append(sctx.nav())
        return "\n".join(L), InlineKeyboardMarkup(inline_keyboard=rows)

    @ub.section("depo", "📦 Depo", order=40)
    async def render(sctx):
        return view(sctx)

    @ub.on_section("depo")
    async def handle(sctx):
        filler = getattr(ub.context, "depo_filler", None)
        a = sctx.action
        note = ""
        if filler is not None and a == "on":
            filler.start()
            note = "🟢 İşə salındı"
        elif filler is not None and a == "off":
            await filler.stop()
            note = "🔴 Söndürüldü"
        elif filler is not None and a == "rebuild":
            filler.queue.clear()
            filler.seen.clear()
            filler.stats["queue_built"] = 0
            filler.stats["last_build"] = None
            note = "🔁 Növbə növbəti addımda yenidən qurulacaq"
        elif a == "scanstop":
            scan = getattr(ub.context, "depo_scan_state", None)
            if scan and scan.get("running"):
                scan["stop"] = True
                note = "⏹ Skan dayandırılır"
        await sctx.answer(note or None)
        text, kb = view(sctx, f"<i>{note}</i>" if note else "")
        await sctx.show(text, kb)
