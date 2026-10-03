"""
Yeniləmə bildirişi (detallı).

Bot hər başlayanda layihədəki .py faylları (app.py, core/, plugins/ ...) əvvəlki başlanğıcla
müqayisə olunur. Dəyişiklik varsa creator-a:

  1) Mesaj:
     • başlama və son yenilənmə vaxtı, git commit (varsa)
     • Python / aiogram / yt-dlp versiyaları (dəyişibsə köhnə → yeni)
     • botun RAM istifadəsi
     • hər fayl: ölçü, sətir sayı, +əlavə / −silinən sətirlər, yenilənmə saatı
     • əlavə olunan / silinən / dəyişən funksiya və class-lar
     • əlavə olunan / silinən komandalar (/menu, /premium ...)
     • sintaksis xətaları və yüklənməyən plugin-lər
  2) Fayl: bütün dəyişikliklərin tam diff-i (update_....diff)
  3) Layihə git repo-dursa: [✅ GitHub-a göndər] [❌ İmtina] [✏️ Commit mesajı] düymələri.
     Təsdiqdə yalnız hesabatdakı fayllar commit olunur və push edilir.
  4) 📦 Hamısını göndər (full update) — repodakı BÜTÜN commit edilməmiş dəyişikliklər
     (.gitignore və həssas fayllar istisna) bir commit-də göndərilir.
  5) ☑️ Seçərək göndər — hansı faylların gedəcəyini özün seçirsən (fayl və ya qovluq üzrə).
  6) ⚙️ GitHub Quraşdırma Paneli — repo yoxdursa başlatmaq, remote URL, token və müəllif
     məlumatlarını birbaşa Telegram interfeysindən daxil etmək.

GitHub ayarları (verilənlər bazasında və ya config.env-də):
  GITHUB_REMOTE=origin          push ediləcək remote (default: origin)
  GITHUB_TOKEN=ghp_...          HTTPS remote üçün token
"""
import ast
import asyncio
import base64
import difflib
import fnmatch
import hashlib
import json
import logging
import os
import platform
import re
import shutil
import subprocess
import time
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path

from aiogram import F
from aiogram.types import (
    BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message,
)

from core.database import get_db
from core.utilities import BASE_DIR

logger = logging.getLogger(__name__)

PRIORITY = 1000

EXCLUDE_DIRS = {
    "__pycache__", ".git", ".venv", "venv", "env", ".env", "node_modules",
    "download", "data", "logs", ".idea", ".vscode", "site-packages",
}
PACKAGES = ["aiogram", "yt-dlp", "yt-dlp-ejs", "spotipy", "aiohttp"]
MAX_TEXT = 3900
MAX_NAMES = 8
DELAY_AFTER_START = 3
_CMD_RE = re.compile(r"""Command\(\s*(?:commands\s*=\s*)?\[?\s*((?:["'][\w]+["']\s*,?\s*)+)""")


# ───────────────────────── formatlama ─────────────────────────
def get_tz():
    name = os.getenv("BOT_TZ", "Asia/Baku")
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:
        return timezone(timedelta(hours=4))


def fmt_time(ts) -> str:
    return datetime.fromtimestamp(ts, get_tz()).strftime("%d.%m.%Y %H:%M:%S")


def fmt_size(n: int) -> str:
    return f"{n} B" if n < 1024 else f"{n / 1024:.1f} KB"


def fmt_delta(n: int, unit_bytes=True) -> str:
    sign = "+" if n > 0 else ("−" if n < 0 else "±")
    return sign + (fmt_size(abs(n)) if unit_bytes else str(abs(n)))


def kind_icon(path: str) -> str:
    if path.startswith("plugins/"):
        return "🔌"
    if path.startswith("core/"):
        return "⚙️"
    return "📄"


def folder_of(path: str) -> str:
    p = Path(path).parent.as_posix()
    return "(kök)" if p == "." else f"{p}/"


def names_line(icon: str, label: str, names) -> str:
    names = sorted(names)
    shown = ", ".join(f"<code>{escape(n)}</code>" for n in names[:MAX_NAMES])
    more = f" +{len(names) - MAX_NAMES}" if len(names) > MAX_NAMES else ""
    return f"   {icon} {label}: {shown}{more}"


# ───────────────────────── fayl skanı ─────────────────────────
def scan_files(root: Path) -> dict:
    result = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS and not d.startswith(".")]
        for name in filenames:
            if not name.endswith(".py"):
                continue
            path = Path(dirpath) / name
            try:
                data = path.read_bytes()
                st = path.stat()
            except OSError as e:
                logger.warning(f"Fayl oxunmadı ({path}): {e}")
                continue
            result[path.relative_to(root).as_posix()] = {
                "sha256": hashlib.sha256(data).hexdigest(),
                "size": len(data),
                "lines": data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0),
                "mtime": int(st.st_mtime),
                "content": data.decode("utf-8", errors="replace"),
            }
    return result


def compare(old: dict, new: dict):
    added = sorted(p for p in new if p not in old)
    removed = sorted(p for p in old if p not in new)
    changed = sorted(p for p in new if p in old and new[p]["sha256"] != old[p]["sha256"])
    return added, changed, removed


# ───────────────────────── kod analizi ─────────────────────────
def analyze(source: str):
    commands = set()
    for m in _CMD_RE.finditer(source or ""):
        commands.update(re.findall(r"""["'](\w+)["']""", m.group(1)))

    try:
        tree = ast.parse(source or "")
    except SyntaxError as e:
        return {}, commands, f"SyntaxError: {e.msg} (sətir {e.lineno})"

    lines = (source or "").splitlines()
    symbols = {}

    def visit(node, prefix=""):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = f"{prefix}{child.name}"
                kids = [
                    (min([d.lineno for d in getattr(k, "decorator_list", [])] + [k.lineno]), k.end_lineno)
                    for k in ast.walk(child)
                    if k is not child and isinstance(k, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                ]
                start = min([d.lineno for d in getattr(child, "decorator_list", [])] + [child.lineno])
                own = [
                    lines[i - 1].strip()
                    for i in range(start, (child.end_lineno or child.lineno) + 1)
                    if 0 < i <= len(lines) and not any(a <= i <= b for a, b in kids)
                ]
                own = [line for line in own if line]
                symbols[name] = hashlib.md5("\n".join(own).encode()).hexdigest()
                visit(child, name + ".")
            else:
                visit(child, prefix)

    visit(tree)
    return symbols, commands, None


def line_stats(old_text: str, new_text: str):
    plus = minus = 0
    for line in difflib.unified_diff((old_text or "").splitlines(), (new_text or "").splitlines(), lineterm="", n=0):
        if line.startswith("+") and not line.startswith("+++"):
            plus += 1
        elif line.startswith("-") and not line.startswith("---"):
            minus += 1
    return plus, minus


def unified(path: str, old_text, new_text) -> str:
    return "\n".join(difflib.unified_diff(
        (old_text or "").splitlines(), (new_text or "").splitlines(),
        fromfile=f"a/{path}" if old_text is not None else "/dev/null",
        tofile=f"b/{path}" if new_text is not None else "/dev/null",
        lineterm="",
    ))


# ───────────────────────── mühit məlumatı ─────────────────────────
def package_versions() -> dict:
    from importlib import metadata
    out = {"python": platform.python_version()}
    for pkg in PACKAGES:
        try:
            out[pkg] = metadata.version(pkg)
        except Exception:
            pass
    return out


def git_info(root: Path):
    if not (root / ".git").exists() or not shutil.which("git"):
        return None
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "log", "-1", "--format=%h%x1f%s%x1f%ct"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
        if not out:
            return None
        h, subject, ts = out.split("\x1f")
        dirty = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=no"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
        return {"hash": h, "subject": subject, "ts": int(ts), "dirty": bool(dirty)}
    except Exception:
        return None


def ram_mb():
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024
    except Exception:
        return None


# ───────────────────────── hesabat ─────────────────────────
def file_entry(path: str, o, n) -> tuple:
    icon = kind_icon(path)
    if o is None:
        syms, cmds, err = analyze(n["content"])
        head = [f"🆕 {icon} <code>{escape(path)}</code>",
                f"   💾 <b>{fmt_size(n['size'])}</b> · 📃 {n['lines']} sətir · 🕒 {fmt_time(n['mtime'])}"]
        if syms:
            head.append(names_line("🧩", f"{len(syms)} funksiya/class", syms))
        if cmds:
            head.append(names_line("⌨️", "komandalar", {f'/{c}' for c in cmds}))
        return "\n".join(head), err

    if n is None:
        syms, cmds, _ = analyze(o.get("content"))
        head = [f"🗑 {icon} <code>{escape(path)}</code>",
                f"   💾 köhnə: {fmt_size(o['size'])} · 📃 {o['lines']} sətir"]
        if cmds:
            head.append(names_line("⌨️", "silinən komandalar", {f'/{c}' for c in cmds}))
        return "\n".join(head), None

    head = [f"{icon} <code>{escape(path)}</code>",
            f"   💾 {fmt_size(o['size'])} → <b>{fmt_size(n['size'])}</b> ({fmt_delta(n['size'] - o['size'])})",
            f"   📃 {o['lines']} → <b>{n['lines']}</b> sətir"]
    new_syms, new_cmds, err = analyze(n["content"])
    if o.get("content") is None:
        head[-1] += f" ({fmt_delta(n['lines'] - o['lines'], False)})"
        head.append("   <i>ℹ️ köhnə məzmun saxlanmayıb — ətraflı müqayisə növbəti yeniləmədən</i>")
    else:
        plus, minus = line_stats(o["content"], n["content"])
        head[-1] += f" · <b>+{plus}</b> / <b>−{minus}</b>"
        old_syms, old_cmds, _ = analyze(o["content"])
        if not err:
            add = new_syms.keys() - old_syms.keys()
            rem = old_syms.keys() - new_syms.keys()
            chg = {k for k in new_syms.keys() & old_syms.keys() if new_syms[k] != old_syms[k]}
            if add:
                head.append(names_line("➕", "yeni", add))
            if chg:
                head.append(names_line("✏️", "dəyişən", chg))
            if rem:
                head.append(names_line("➖", "silinən", rem))
        if new_cmds - old_cmds:
            head.append(names_line("⌨️", "yeni komandalar", {f'/{c}' for c in new_cmds - old_cmds}))
        if old_cmds - new_cmds:
            head.append(names_line("⌨️", "silinən komandalar", {f'/{c}' for c in old_cmds - new_cmds}))
    head.append(f"   🕒 {fmt_time(n['mtime'])}")
    return "\n".join(head), err


def env_lines(env_old: dict, env_new: dict, git, git_old: str, ram) -> list:
    out = []
    if git:
        mark = " <i>(commit edilməmiş dəyişikliklər var)</i>" if git["dirty"] else ""
        changed = f" (əvvəl <code>{escape(git_old)}</code>)" if git_old and git_old != git["hash"] else ""
        out.append(f"🌿 Git: <code>{escape(git['hash'])}</code> — {escape(git['subject'][:80])}{changed}{mark}")

    diffs = [k for k in env_new if env_old and env_old.get(k) and env_old.get(k) != env_new[k]]
    if diffs:
        out.append("📦 <b>Versiyalar dəyişdi:</b> " + " · ".join(
            f"{escape(k)} {escape(env_old[k])} → <b>{escape(env_new[k])}</b>" for k in diffs))
    news = [k for k in env_new if env_old and k not in env_old]
    if news:
        out.append("📦 Yeni paket: " + " · ".join(f"{escape(k)} {escape(env_new[k])}" for k in news))
    out.append("🐍 " + " · ".join(f"{escape(k)} {escape(v)}" for k, v in env_new.items()))
    if ram:
        out.append(f"🧠 RAM: <b>{ram:.0f} MB</b>")
    return out


def build_report(old, new, added, changed, removed, plugin_status, started, env) -> str:
    head = ["🔄 <b>Bot yeniləndi</b>", f"🕒 Başlama: <b>{fmt_time(started)}</b>"]
    touched = [new[p]["mtime"] for p in added + changed]
    if touched:
        head.append(f"📝 Son yenilənmə: <b>{fmt_time(max(touched))}</b>")
    head += env

    total_plus = total_minus = 0
    for p in changed:
        if old[p].get("content") is not None:
            a, b = line_stats(old[p]["content"], new[p]["content"])
            total_plus, total_minus = total_plus + a, total_minus + b
    total_plus += sum(new[p]["lines"] for p in added)
    total_minus += sum(old[p]["lines"] for p in removed)

    old_total = sum(f["size"] for f in old.values())
    new_total = sum(f["size"] for f in new.values())
    head += [
        f"\n📊 Dəyişən: <b>{len(changed)}</b> · Yeni: <b>{len(added)}</b> · Silinən: <b>{len(removed)}</b>",
        f"📃 Sətirlər: <b>+{total_plus}</b> / <b>−{total_minus}</b>",
        f"💾 Layihə: {fmt_size(old_total)} → <b>{fmt_size(new_total)}</b> ({fmt_delta(new_total - old_total)})",
    ]

    entries, syntax_errors = [], []
    for p in changed:
        e, err = file_entry(p, old[p], new[p])
        entries.append(e)
        if err:
            syntax_errors.append((p, err))
    for p in added:
        e, err = file_entry(p, None, new[p])
        entries.append(e)
        if err:
            syntax_errors.append((p, err))
    for p in removed:
        entries.append(file_entry(p, old[p], None)[0])

    tail = []
    if syntax_errors:
        tail.append("\n🚨 <b>Sintaksis xətaları:</b>")
        tail += [f"❌ <code>{escape(p)}</code> — <i>{escape(err)}</i>" for p, err in syntax_errors]
    failed = {n: e for n, e in (plugin_status or {}).items() if e != "ok"}
    if failed:
        tail.append("\n⚠️ <b>Yüklənməyən plugin-lər:</b>")
        tail += [f"❌ <code>{escape(n)}</code>\n   <i>{escape(e)[:200]}</i>" for n, e in sorted(failed.items())]
    elif plugin_status:
        tail.append(f"\n✅ Bütün plugin-lər yükləndi ({len(plugin_status)})")

    text = "\n".join(head) + "\n"
    tail_text = "\n".join(tail)
    shown = 0
    for entry in entries:
        candidate = text + "\n" + entry + "\n"
        if len(candidate) + len(tail_text) + 80 > MAX_TEXT:
            break
        text, shown = candidate, shown + 1
    if shown < len(entries):
        text += f"\n<i>… və daha {len(entries) - shown} fayl (tam siyahı diff faylındadır)</i>\n"
    return text + tail_text


def build_diff(old, new, added, changed, removed, started) -> str:
    parts = [f"# DLLMaster Bot yeniləməsi — {fmt_time(started)}",
             f"# Dəyişən: {len(changed)}, yeni: {len(added)}, silinən: {len(removed)}", ""]
    for p in changed:
        if old[p].get("content") is None:
            parts.append(f"# {p}: köhnə məzmun saxlanmayıb, diff mümkün deyil\n")
        else:
            parts.append(unified(p, old[p]["content"], new[p]["content"]) + "\n")
    for p in added:
        parts.append(unified(p, None, new[p]["content"]) + "\n")
    for p in removed:
        if old[p].get("content") is not None:
            parts.append(unified(p, old[p]["content"], None) + "\n")
        else:
            parts.append(f"# {p}: silinib\n")
    return "\n".join(parts)


def build_failure_only(plugin_status: dict, started: float) -> str:
    lines = ["⚠️ <b>Bot başladı, amma bəzi plugin-lər yüklənmədi</b>", f"🕒 {fmt_time(started)}\n"]
    for name, err in sorted(plugin_status.items()):
        if err != "ok":
            lines.append(f"❌ <code>{escape(name)}</code>\n   <i>{escape(err)[:200]}</i>")
    return "\n".join(lines)[:MAX_TEXT]


# ───────────────────────── GitHub (git commit + push) ─────────────────────────
def _token() -> str:
    """Verilənlər bazasından və ya config.env-dən GitHub tokenini oxuyur."""
    try:
        db_tok = get_db().get_setting("gh_token")
        if db_tok:
            return db_tok.strip()
    except Exception:
        pass
    return (os.getenv("GITHUB_TOKEN") or "").strip()


def redact(text: str) -> str:
    tok = _token()
    if tok:
        text = text.replace(tok, "***")
        text = text.replace(base64.b64encode(f"x-access-token:{tok}".encode()).decode(), "***")
    env_tok = (os.getenv("GITHUB_TOKEN") or "").strip()
    if env_tok and env_tok != tok:
        text = text.replace(env_tok, "***")
        text = text.replace(base64.b64encode(f"x-access-token:{env_tok}".encode()).decode(), "***")
    return text


def git(root: Path, *args, timeout=60, auth=False, raw=False):
    cmd = ["git", "-C", str(root)]
    if auth and _token():
        basic = base64.b64encode(f"x-access-token:{_token()}".encode()).decode()
        cmd += ["-c", f"http.https://github.com/.extraheader=AUTHORIZATION: basic {basic}"]
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_ASKPASS="true", LC_ALL="C")
    try:
        r = subprocess.run(cmd + list(args), capture_output=True, text=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return 124, "", f"git {args[0]}: vaxt bitdi ({timeout} san.)"
    return r.returncode, (r.stdout if raw else r.stdout.strip()), redact(r.stderr.strip())


def github_web_url(remote_url: str):
    m = re.match(r"^(?:git@github\.com:|ssh://git@github\.com/|https://(?:[^@/]+@)?github\.com/)([^/]+/[^/]+?)(?:\.git)?/?$",
                 remote_url or "")
    return f"https://github.com/{m.group(1)}" if m else None


def short_error(err: str, limit=6) -> str:
    lines = [l for l in (err or "").splitlines() if l.strip() and not l.startswith("hint:")]
    return "\n".join(lines[-limit:])[-600:] or (err or "")[-300:]


def push_hint(err: str) -> str:
    e = err.lower()
    if "non-fast-forward" in e or "fetch first" in e or "rejected" in e:
        return "Uzaq repoda sizdə olmayan commit-lər var. Serverdə <code>git pull --rebase</code> edib yenidən cəhd edin."
    if "permission denied" in e or "authentication" in e or "could not read username" in e or "403" in e:
        return "Giriş alınmadı: GitHub Token və ya SSH açarını yoxlayın. <b>⚙️ Quraşdırma</b> panelindən token təyin edə bilərsiniz."
    if "could not resolve host" in e or "unable to access" in e:
        return "GitHub-a qoşulmaq alınmadı (şəbəkə xətası)."
    return ""


def default_commit_message(files: dict, started: float) -> str:
    def names(paths):
        return ", ".join(Path(p).name for p in paths)
    body = []
    if files.get("changed"):
        body.append(f"Dəyişən: {names(files['changed'])}")
    if files.get("added"):
        body.append(f"Yeni: {names(files['added'])}")
    if files.get("removed"):
        body.append(f"Silinən: {names(files['removed'])}")
    title = f"Bot yeniləməsi ({datetime.fromtimestamp(started, get_tz()).strftime('%d.%m.%Y %H:%M')})"
    return title + ("\n\n" + "\n".join(body) if body else "")


def push_update(root: Path, paths: list, message: str) -> dict:
    res = {"ok": False, "committed": False, "error": "", "hint": ""}

    rc, _, _ = git(root, "rev-parse", "--is-inside-work-tree")
    if rc != 0:
        res["error"] = "Layihə git repo deyil."
        res["hint"] = "GitHub panelindən <b>⚙️ Quraşdırma</b> bölməsinə daxil olub 'Reponu Başlat' seçin."
        return res
    remote = os.getenv("GITHUB_REMOTE", "origin")
    rc, remote_url, _ = git(root, "remote", "get-url", remote)
    if rc != 0:
        res["error"] = f"'{remote}' remote-u tapılmadı."
        res["hint"] = "GitHub panelindən <b>⚙️ Quraşdırma</b> bölməsinə daxil olub Remote URL əlavə edin."
        return res
    rc, branch, _ = git(root, "rev-parse", "--abbrev-ref", "HEAD")
    if rc != 0 or branch == "HEAD":
        res["error"] = "Repo heç bir branch-da deyil (detached HEAD)."
        return res
    res["branch"] = branch

    use, to_add = [], []
    for p in paths:
        tracked = git(root, "ls-files", "--error-unmatch", "--", p)[0] == 0
        in_head = bool(git(root, "ls-tree", "--name-only", "HEAD", "--", p)[1])
        if not tracked and not in_head and not (root / p).exists():
            continue
        if git(root, "check-ignore", "-q", "--", p)[0] == 0:
            continue
        use.append(p)
        if tracked or (root / p).exists():
            to_add.append(p)
    res["files"] = use

    if use:
        rc, _, err = git(root, "add", "-A", "--", *to_add) if to_add else (0, "", "")
        if rc != 0:
            res["error"] = f"git add:\n{short_error(err)}"
            return res
        _, staged, _ = git(root, "diff", "--cached", "--name-only", "--", *use)
        if staged:
            ident = []
            if not git(root, "config", "user.name")[1]:
                ident += ["-c", "user.name=DLLMaster Bot"]
            if not git(root, "config", "user.email")[1]:
                ident += ["-c", "user.email=bot@dllmaster.local"]
            rc, _, err = git(root, *ident, "commit", "-m", message, "--", *use)
            if rc != 0:
                res["error"] = f"git commit:\n{short_error(err)}"
                return res
            res["committed"] = True

    rc, _, err = git(root, "push", "-u", remote, branch, timeout=120, auth=True)
    if rc != 0:
        res["error"] = f"git push:\n{short_error(err)}"
        res["hint"] = push_hint(err)
        return res

    res["ok"] = True
    res["hash"] = git(root, "rev-parse", "--short", "HEAD")[1]
    full = git(root, "rev-parse", "HEAD")[1]
    web = github_web_url(remote_url)
    res["url"] = f"{web}/commit/{full}" if web else None
    others = [l for l in git(root, "status", "--porcelain")[1].splitlines() if l.strip()]
    res["others"] = len(others)
    return res


# ───────────────────────── Full update & dəyişikliklər ─────────────────────────
SENSITIVE_PATTERNS = [
    "config.env", ".env", "*.env", ".env.*", "*.db", "*.db-wal", "*.db-shm", "*.sqlite", "*.sqlite3",
    "*cookies*.txt", "*.pem", "*.key", "*.session", "*.session-journal",
    "data/*", "download/*", "logs/*",
]
STATUS_ICONS = {"new": "🆕", "modified": "✏️", "deleted": "🗑", "renamed": "🔀"}


def is_sensitive(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return any(fnmatch.fnmatch(path, pat) or fnmatch.fnmatch(name, pat) for pat in SENSITIVE_PATTERNS)


def working_changes(root: Path):
    rc, out, _ = git(root, "status", "--porcelain=v1", "-z", "-uall", raw=True)
    if rc != 0:
        return [], []
    items = out.split("\0")
    changes, excluded, i = [], [], 0
    while i < len(items):
        entry = items[i]
        i += 1
        if len(entry) < 4:
            continue
        xy, path = entry[:2], entry[3:]
        if "R" in xy or "C" in xy:
            old = items[i] if i < len(items) else ""
            i += 1
            kind = "renamed"
            if old and "R" in xy:
                if is_sensitive(old):
                    excluded.append(("deleted", old))
                else:
                    changes.append(("deleted", old))
        elif xy == "??":
            kind = "new"
        elif "D" in xy:
            kind = "deleted"
        else:
            kind = "modified"
        if is_sensitive(path):
            excluded.append((kind, path))
        else:
            changes.append((kind, path))
    return changes, excluded


def repo_status(root: Path, fetch: bool = False) -> dict:
    info = {}
    is_repo = git(root, "rev-parse", "--is-inside-work-tree")[0] == 0
    info["is_repo"] = is_repo
    if not is_repo:
        info["error"] = "Layihə git repo deyil."
        info["changes"], info["excluded"] = [], []
        info["configured"] = False
        return info

    remote = os.getenv("GITHUB_REMOTE", "origin")
    info["remote"] = remote
    rc, url, _ = git(root, "remote", "get-url", remote)
    info["remote_url"] = url if rc == 0 else None
    info["web"] = github_web_url(url) if rc == 0 else None
    info["branch"] = git(root, "rev-parse", "--abbrev-ref", "HEAD")[1]
    tok = _token()
    info["has_token"] = bool(tok)
    info["configured"] = bool(url and (tok or not url.startswith("https://")))

    rc, last, _ = git(root, "log", "-1", "--format=%h%x1f%s%x1f%ct")
    if rc == 0 and last:
        h, subj, ts = last.split("\x1f")
        info["last"] = {"hash": h, "subject": subj, "ts": int(ts)}
    if fetch and info["remote_url"]:
        rc, _, err = git(root, "fetch", remote, timeout=30, auth=True)
        info["fetch_error"] = short_error(err) if rc != 0 else None
    rc, counts, _ = git(root, "rev-list", "--left-right", "--count", "@{u}...HEAD")
    if rc == 0 and counts:
        behind, ahead = (int(x) for x in counts.split())
        info["behind"], info["ahead"] = behind, ahead
    info["changes"], info["excluded"] = working_changes(root)
    return info


def full_commit_message(changes, started: float, label: str = "Tam yeniləmə") -> str:
    groups = {}
    for kind, path in changes:
        groups.setdefault(kind, []).append(Path(path).name)
    title = f"{label} ({datetime.fromtimestamp(started, get_tz()).strftime('%d.%m.%Y %H:%M')})"
    labels = [("modified", "Dəyişən"), ("new", "Yeni"), ("deleted", "Silinən"), ("renamed", "Adı dəyişən")]
    body = []
    for key, label in labels:
        names = groups.get(key)
        if names:
            shown = ", ".join(names[:15]) + (f" və daha {len(names) - 15}" if len(names) > 15 else "")
            body.append(f"{label} ({len(names)}): {shown}")
    return title + ("\n\n" + "\n".join(body) if body else "")


def push_all(root: Path, message, selected=None) -> dict:
    changes, excluded = working_changes(root)
    if selected is None:
        chosen, skipped = changes, excluded
    else:
        everything = changes + excluded
        chosen = [(k, p) for k, p in everything if p in selected]
        skipped = [(k, p) for k, p in everything if p not in selected]
    paths = sorted({p for _, p in chosen})
    label = "Tam yeniləmə" if selected is None else "Seçilmiş yeniləmə"
    res = push_update(root, paths, message or full_commit_message(chosen, time.time(), label))
    res["changes"] = chosen
    res["skipped"] = len({p for _, p in skipped})
    res["excluded"] = [p for _, p in skipped if is_sensitive(p)]
    res["sensitive_sent"] = [p for p in paths if is_sensitive(p)]
    if res.get("ok"):
        left, left_ex = working_changes(root)
        res["remaining"] = sorted({p for _, p in left + left_ex})
    return res


def gh_kb(pid: str, configured: bool = True) -> InlineKeyboardMarkup:
    rows = []
    if not configured:
        rows.append([InlineKeyboardButton(text="⚙️ GitHub-ı Quraşdır", callback_data="ghs:menu")])
    rows += [
        [InlineKeyboardButton(text="✅ GitHub-a göndər", callback_data=f"gh:push:{pid}"),
         InlineKeyboardButton(text="❌ İmtina", callback_data=f"gh:skip:{pid}")],
        [InlineKeyboardButton(text="✏️ Commit mesajı", callback_data=f"gh:msg:{pid}"),
         InlineKeyboardButton(text="📦 Hamısını göndər", callback_data="ghf:prep:new")],
        [InlineKeyboardButton(text="☑️ Seçərək göndər", callback_data="ghf:pick:new")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def status_kb(text: str, url: str = None) -> InlineKeyboardMarkup:
    btn_item = InlineKeyboardButton(text=text, url=url) if url else InlineKeyboardButton(text=text, callback_data="gh:noop")
    return InlineKeyboardMarkup(inline_keyboard=[[btn_item]])


# ───────────────────────── plugin ─────────────────────────
def setup(context):
    creator = context.creator_id
    started = time.time()
    dp = context.dp
    bot = context.bot
    root = Path(BASE_DIR)
    push_lock = asyncio.Lock()
    gh_state = {"await_msg": None}
    gh_full = {
        "message": None, "await": False, "chat_id": None, "panel_id": None,
        "selected": None, "items": [], "page": 0,
        "folder_filter": None, "folders": [], "folder_page": 0,
    }
    gh_setup = {"step": None, "chat_id": None, "panel_id": None, "status_msg": ""}

    prev_waiting = getattr(context, "menu_waiting_text", None)

    def waiting_text(user_id: int) -> bool:
        if user_id == creator and (gh_state["await_msg"] or gh_full["await"] or gh_setup["step"]):
            return True
        return bool(prev_waiting and prev_waiting(user_id))

    context.menu_waiting_text = waiting_text

    def load_pending():
        try:
            return json.loads(get_db().get_setting("gh_pending") or "null")
        except ValueError:
            return None

    def save_pending(p):
        get_db().set_setting("gh_pending", json.dumps(p) if p else None)

    async def set_report_kb(p, kb):
        try:
            await bot.edit_message_reply_markup(chat_id=creator, message_id=p["message_id"], reply_markup=kb)
        except Exception as e:
            if "not modified" not in str(e):
                logger.debug(f"Hesabat düymələri yenilənmədi: {e}")

    def nav(back: str):
        row = [InlineKeyboardButton(text="⬅️ Geri", callback_data=back)]
        if back != "menu:main":
            row.append(InlineKeyboardButton(text="🏠 Menyu", callback_data="menu:main"))
        row.append(InlineKeyboardButton(text="❌ Ləğv et", callback_data="menu:cancel"))
        return row

    def btn(text, data=None, url=None):
        return InlineKeyboardButton(text=text, url=url) if url else InlineKeyboardButton(text=text, callback_data=data)

    async def edit_full_panel(text: str, kb):
        try:
            await bot.edit_message_text(chat_id=gh_full["chat_id"], message_id=gh_full["panel_id"],
                                        text=text, parse_mode="HTML", reply_markup=kb,
                                        disable_web_page_preview=True)
        except Exception as e:
            if "not modified" not in str(e):
                logger.warning(f"GitHub paneli yenilənmədi: {e}")

    async def edit_setup_panel(text: str, kb):
        cid = gh_setup.get("chat_id") or creator
        mid = gh_setup.get("panel_id")
        if mid:
            try:
                await bot.edit_message_text(chat_id=cid, message_id=mid, text=text,
                                            parse_mode="HTML", reply_markup=kb,
                                            disable_web_page_preview=True)
                return
            except Exception as e:
                if "not modified" in str(e):
                    return
        sent = await bot.send_message(cid, text, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True)
        gh_setup["chat_id"] = sent.chat.id
        gh_setup["panel_id"] = sent.message_id

    # ═════════════ ⚙️ GitHub Quraşdırma Menyusu ═════════════
    async def setup_view():
        is_repo = git(root, "rev-parse", "--is-inside-work-tree")[0] == 0
        remote = os.getenv("GITHUB_REMOTE", "origin")
        rc, url, _ = git(root, "remote", "get-url", remote) if is_repo else (1, "", "")
        tok = _token()
        uname = git(root, "config", "user.name")[1] if is_repo else ""
        uemail = git(root, "config", "user.email")[1] if is_repo else ""

        lines = ["⚙️ <b>GitHub Hesabının Quraşdırılması</b>\n"]
        if gh_setup.get("status_msg"):
            lines.append(f"{gh_setup['status_msg']}\n")
            gh_setup["status_msg"] = ""

        lines.append(f"📁 <b>Git Repozitoriyası:</b> {'✅ Aktiv' if is_repo else '❌ Başladılmayıb'}")
        lines.append(f"🔗 <b>Remote URL ({remote}):</b> {f'<code>{escape(url)}</code>' if (rc == 0 and url) else '❌ Təyin edilməyib'}")
        masked = f"<code>{escape(tok[:4] + '***' + tok[-4:])}</code>" if len(tok) >= 8 else ("✅ Mövcuddur" if tok else "❌ Daxil edilməyib")
        lines.append(f"🔑 <b>GitHub Token (PAT):</b> {masked}")
        lines.append(f"👤 <b>Git Müəllif:</b> {escape(uname or 'Default')} &lt;{escape(uemail or 'bot@dllmaster.local')}&gt;\n")

        kb = []
        if not is_repo:
            kb.append([btn("🛠 Repozitoriyanı Başlat (git init)", "ghs:act:init")])
        kb.append([btn("🔗 Remote URL Təyin Et", "ghs:ask:remote")])
        kb.append([btn("🔑 GitHub Token Daxil Et", "ghs:ask:token")])
        kb.append([btn("👤 Git Müəllif Məlumatı", "ghs:ask:user")])
        if tok:
            kb.append([btn("🗑 Tokeni Sil", "ghs:act:del_token")])
        kb.append(nav("ghf:open"))
        return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=kb)

    @dp.callback_query(F.data.startswith("ghs:"))
    async def gh_setup_callback(cb: CallbackQuery):
        if cb.from_user.id != creator:
            await cb.answer("⛔ İcazə yoxdur", show_alert=True)
            return
        parts = cb.data.split(":")
        action = parts[1]

        gh_setup["chat_id"] = cb.message.chat.id
        gh_setup["panel_id"] = cb.message.message_id

        if action == "menu":
            gh_setup["step"] = None
            text, kb = await setup_view()
            await edit_setup_panel(text, kb)
            await cb.answer()
            return

        if action == "act" and len(parts) > 2:
            sub = parts[2]
            if sub == "init":
                git(root, "init")
                git(root, "branch", "-M", "main")
                gh_setup["status_msg"] = "✅ Git repozitoriyası uğurla başladıldı (branch: main)!"
                await cb.answer("Repo yaradıldı")
            elif sub == "del_token":
                await asyncio.to_thread(get_db().set_setting, "gh_token", "")
                if "GITHUB_TOKEN" in os.environ:
                    os.environ.pop("GITHUB_TOKEN", None)
                gh_setup["status_msg"] = "🗑 GitHub Token silindi."
                await cb.answer("Token silindi")
            text, kb = await setup_view()
            await edit_setup_panel(text, kb)
            return

        if action == "ask" and len(parts) > 2:
            sub = parts[2]
            gh_setup["step"] = sub
            if sub == "remote":
                await cb.answer()
                await edit_setup_panel(
                    "🔗 <b>GitHub Remote URL-i daxil edin</b>\n\n"
                    "Repozitoriyanızın linkini mesaj olaraq göndərin:\n"
                    "<code>https://github.com/istifadəçi_adı/repo_adı.git</code>\n\n"
                    "<i>İmtina üçün aşağıdakı düyməni sıxın:</i>",
                    InlineKeyboardMarkup(inline_keyboard=[[btn("↩️ İmtina", "ghs:menu")]]),
                )
            elif sub == "token":
                await cb.answer()
                await edit_setup_panel(
                    "🔑 <b>GitHub Personal Access Token (PAT) daxil edin</b>\n\n"
                    "Tokeni mesaj olaraq göndərin (məs: <code>ghp_...</code> və ya <code>github_pat_...</code>).\n\n"
                    "<i>ℹ️ Token verilənlər bazasında saxlanılacaq və çatdan dərhal silinəcək.</i>",
                    InlineKeyboardMarkup(inline_keyboard=[[btn("↩️ İmtina", "ghs:menu")]]),
                )
            elif sub == "user":
                await cb.answer()
                await edit_setup_panel(
                    "👤 <b>Git Müəllif məlumatlarını daxil edin</b>\n\n"
                    "Format: <code>Ad Soyad | email@example.com</code>\n"
                    "Məsələn: <code>DLLMaster Bot | bot@dllmaster.local</code>",
                    InlineKeyboardMarkup(inline_keyboard=[[btn("↩️ İmtina", "ghs:menu")]]),
                )
            return

        await cb.answer()

    # ═════════════ hesabat düymələri (gh:) ═════════════
    @dp.callback_query(F.data.startswith("gh:"))
    async def gh_callback(cb: CallbackQuery):
        if cb.from_user.id != creator:
            await cb.answer("⛔ İcazə yoxdur", show_alert=True)
            return
        parts = cb.data.split(":")
        action = parts[1]
        if action == "noop":
            await cb.answer()
            return

        pid = parts[2] if len(parts) > 2 else ""
        p = await asyncio.to_thread(load_pending)
        if not p or p["id"] != pid:
            await cb.answer("Bu yeniləmə artıq köhnəlib və ya bağlanıb", show_alert=True)
            try:
                await cb.message.edit_reply_markup(reply_markup=status_kb("⌛ Köhnəlib"))
            except Exception:
                pass
            return

        if action == "skip":
            gh_state["await_msg"] = None
            await asyncio.to_thread(save_pending, None)
            await cb.answer("İmtina edildi")
            await set_report_kb(p, status_kb("❌ GitHub-a göndərilmədi"))
            if cb.message.message_id != p["message_id"]:
                try:
                    await cb.message.edit_reply_markup(reply_markup=None)
                except Exception:
                    pass
            return

        if action == "msg":
            gh_state["await_msg"] = pid
            await cb.answer()
            await bot.send_message(
                creator,
                "✏️ <b>Commit mesajını yazın</b>\n\n"
                f"Hazırkı:\n<pre>{escape(p['message'])}</pre>",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="↩️ Olduğu kimi saxla", callback_data=f"gh:keep:{pid}")
                ]]),
            )
            return

        if action == "keep":
            gh_state["await_msg"] = None
            await cb.answer()
            try:
                await cb.message.edit_reply_markup(reply_markup=None)
            except Exception:
                pass
            return

        if action == "push":
            if push_lock.locked():
                await cb.answer("Artıq göndərilir...")
                return
            await cb.answer("Göndərilir...")
            gh_state["await_msg"] = None
            async with push_lock:
                await set_report_kb(p, status_kb("⏳ GitHub-a göndərilir..."))
                paths = p["files"].get("changed", []) + p["files"].get("added", []) + p["files"].get("removed", [])
                try:
                    res = await asyncio.to_thread(push_update, root, paths, p["message"])
                except Exception as e:
                    logger.error(f"GitHub push xətası: {e}", exc_info=True)
                    res = {"ok": False, "committed": False, "error": redact(str(e)), "hint": ""}

                if res["ok"]:
                    await asyncio.to_thread(save_pending, None)
                    await set_report_kb(p, status_kb(f"✅ GitHub-da: {res['hash']}", res.get("url")))
                    lines = [
                        "✅ <b>GitHub-a göndərildi</b>",
                        f"🌿 Branch: <code>{escape(res['branch'])}</code> · commit <code>{escape(res['hash'])}</code>",
                        f"📁 Fayl: <b>{len(res['files'])}</b>"
                        + ("" if res["committed"] else " <i>(yeni commit lazım olmadı, yalnız push edildi)</i>"),
                    ]
                    if res.get("others"):
                        lines.append(f"ℹ️ <i>Repoda commit edilməmiş başqa {res['others']} dəyişiklik də var "
                                     "(hesabatda olmadığı üçün toxunulmadı).</i>")
                    await bot.send_message(
                        creator, "\n".join(lines), parse_mode="HTML",
                        reply_markup=status_kb("🔗 Commit-ə bax", res["url"]) if res.get("url") else None,
                    )
                else:
                    await set_report_kb(p, gh_kb(p["id"], configured=False))
                    note = "\n\n<i>Commit lokal olaraq yaradıldı, yalnız push alınmadı.</i>" if res["committed"] else ""
                    hint = f"\n\n💡 {res['hint']}" if res.get("hint") else ""
                    await bot.send_message(
                        creator,
                        f"❌ <b>GitHub-a göndərilmədi</b>\n\n<code>{escape(res['error'])}</code>{hint}{note}",
                        parse_mode="HTML",
                        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[btn("⚙️ GitHub-ı Quraşdır", "ghs:menu")]]),
                    )
            return

        await cb.answer()

    # ═════════════ mətn daxiletmələri (commit mesajı və quraşdırma) ═════════════
    async def waiting_commit_msg(message: Message) -> bool:
        return bool(gh_state["await_msg"] or gh_full["await"] or gh_setup["step"]) and message.from_user \
            and message.from_user.id == creator

    @dp.message(F.chat.type == "private", F.text, ~F.text.startswith("/"), waiting_commit_msg)
    async def gh_text_input_handler(message: Message):
        # ⚙️ Quraşdırma addımları
        if gh_setup["step"]:
            step = gh_setup["step"]
            txt = message.text.strip()
            try:
                await message.delete()
            except Exception:
                pass

            if step == "remote":
                gh_setup["step"] = None
                if git(root, "rev-parse", "--is-inside-work-tree")[0] != 0:
                    git(root, "init")
                    git(root, "branch", "-M", "main")
                remote = os.getenv("GITHUB_REMOTE", "origin")
                rc, _, _ = git(root, "remote", "get-url", remote)
                if rc == 0:
                    rc2, _, err = git(root, "remote", "set-url", remote, txt)
                else:
                    rc2, _, err = git(root, "remote", "add", remote, txt)
                if rc2 == 0:
                    gh_setup["status_msg"] = f"✅ Remote URL təyin edildi: <code>{escape(txt)}</code>"
                else:
                    gh_setup["status_msg"] = f"❌ Remote xətası: <code>{escape(err)}</code>"
                text, kb = await setup_view()
                await edit_setup_panel(text, kb)
                return

            if step == "token":
                gh_setup["step"] = None
                try:
                    await asyncio.to_thread(get_db().set_setting, "gh_token", txt)
                    os.environ["GITHUB_TOKEN"] = txt
                    gh_setup["status_msg"] = "✅ GitHub Token saxlanıldı!"
                except Exception as e:
                    gh_setup["status_msg"] = f"❌ Token saxlanılmadı: {escape(str(e))}"
                text, kb = await setup_view()
                await edit_setup_panel(text, kb)
                return

            if step == "user":
                gh_setup["step"] = None
                if "|" in txt:
                    uname, uemail = [x.strip() for x in txt.split("|", 1)]
                else:
                    uname, uemail = txt, "bot@dllmaster.local"
                if git(root, "rev-parse", "--is-inside-work-tree")[0] != 0:
                    git(root, "init")
                    git(root, "branch", "-M", "main")
                git(root, "config", "user.name", uname)
                git(root, "config", "user.email", uemail)
                gh_setup["status_msg"] = f"✅ Müəllif qeyd edildi: <code>{escape(uname)} &lt;{escape(uemail)}&gt;</code>"
                text, kb = await setup_view()
                await edit_setup_panel(text, kb)
                return

        # 📦 Full commit mesajı
        if gh_full["await"]:
            gh_full["await"] = False
            gh_full["message"] = message.text.strip()[:2000]
            try:
                await message.delete()
            except Exception:
                pass
            text, kb = await full_confirm_view()
            await edit_full_panel(text, kb)
            return

        # Hesabat commit mesajı
        pid = gh_state["await_msg"]
        gh_state["await_msg"] = None
        p = await asyncio.to_thread(load_pending)
        if not p or p["id"] != pid:
            await message.reply("⌛ Bu yeniləmə artıq bağlanıb.")
            return
        p["message"] = message.text.strip()[:2000]
        await asyncio.to_thread(save_pending, p)
        await message.reply(
            f"✏️ <b>Commit mesajı yeniləndi:</b>\n<pre>{escape(p['message'])}</pre>",
            parse_mode="HTML",
            reply_markup=gh_kb(pid),
        )

    # ═════════════ 📦 Full update & Seçim Panelləri ═════════════
    def changes_block(changes, excluded, limit=25, skipped=0, title="Dəyişikliklər") -> str:
        counts = {}
        for kind, _ in changes:
            counts[kind] = counts.get(kind, 0) + 1
        summary = " · ".join(f"{STATUS_ICONS[k]} {v}" for k, v in counts.items()) or "—"
        lines = [f"📁 <b>{title}:</b> {len(changes)} ({summary})"]
        for kind, path in changes[:limit]:
            lock = " 🔒" if is_sensitive(path) else ""
            lines.append(f"   {STATUS_ICONS[kind]}{lock} <code>{escape(path)}</code>")
        if len(changes) > limit:
            lines.append(f"   <i>… və daha {len(changes) - limit} fayl</i>")
        if excluded:
            lines.append(f"\n🔒 <b>Göndərilməyəcək (həssas):</b> {len(excluded)}")
            lines += [f"   🔒 <code>{escape(p)}</code>" for _, p in excluded[:10]]
        if skipped:
            lines.append(f"⏭ <i>Seçilmədiyi üçün göndərilməyəcək: {skipped} fayl</i>")
        return "\n".join(lines)

    PAGE = 8
    PAGE_F = 6

    def effective_selection(info) -> set:
        if gh_full["selected"] is None:
            return {p for _, p in info["changes"]}
        every = {p for _, p in info["changes"]} | {p for _, p in info["excluded"]}
        return gh_full["selected"] & every

    def short_path(path: str, n=30) -> str:
        return path if len(path) <= n else "…" + path[-(n - 1):]

    # ── 📁 Qovluqlar paneli ──
    async def folders_view():
        info = await asyncio.to_thread(repo_status, root, False)
        if info.get("error"):
            return f"❌ {escape(info['error'])}", InlineKeyboardMarkup(inline_keyboard=[nav("ghf:open")])

        all_items = [(k, p, False) for k, p in info["changes"]] + [(k, p, True) for k, p in info["excluded"]]
        sel = effective_selection(info)
        gh_full["selected"] = set(sel)

        f_map = {}
        for item in all_items:
            f_map.setdefault(folder_of(item[1]), []).append(item)

        folders = sorted(f_map.keys())
        gh_full["folders"] = folders

        pages = max(1, -(-len(folders) // PAGE_F))
        page = min(max(gh_full.get("folder_page", 0), 0), pages - 1)
        gh_full["folder_page"] = page

        lines = [
            "📁 <b>Qovluqlar üzrə seçim</b>\n",
            "Qovluğun adına basaraq daxilindəki bütün faylları seçə / ləğv edə, "
            "<b>[Aç]</b> düyməsi ilə yalnız həmin qovluğu nəzərdən keçirə bilərsiniz.\n",
            f"Ümumi seçilib: <b>{len(sel)}</b> / {len(all_items)} fayl\n"
        ]

        kb = []
        chunk = folders[page * PAGE_F:(page + 1) * PAGE_F]
        for idx, f in enumerate(chunk, start=page * PAGE_F):
            f_items = f_map[f]
            tot = len(f_items)
            chosen = sum(1 for _, p, _ in f_items if p in sel)
            if chosen == tot and tot > 0:
                mark = "✅"
            elif chosen > 0:
                mark = "🔲"
            else:
                mark = "⬜"

            lines.append(f"{mark} <code>{escape(f)}</code> — <b>{chosen}/{tot}</b> fayl")
            kb.append([
                btn(f"{mark} {f} ({chosen}/{tot})", f"ghf:ftog:{idx}"),
                btn("📂 Aç", f"ghf:fset:{idx}"),
            ])

        if pages > 1:
            kb.append([btn("◀️", f"ghf:fpg:{(page - 1) % pages}"),
                       btn(f"{page + 1}/{pages}", "ghf:noop"),
                       btn("▶️", f"ghf:fpg:{(page + 1) % pages}")])

        kb.append([btn("✅ Hamısını seç (həssassız)", "ghf:fall"), btn("⬜ Hamısını təmizlə", "ghf:fnone")])
        kb.append([btn(f"📄 Fayllar siyahısına qayıt ({len(sel)})", "ghf:pick")])
        kb.append(nav("ghf:open"))
        return "\n".join(lines)[:4000], InlineKeyboardMarkup(inline_keyboard=kb)

    # ── ☑️ Fayl seçimi paneli ──
    async def pick_view():
        info = await asyncio.to_thread(repo_status, root, False)
        if info.get("error"):
            return f"❌ {escape(info['error'])}", InlineKeyboardMarkup(inline_keyboard=[nav("ghf:open")])

        all_items = [(k, p, False) for k, p in info["changes"]] + [(k, p, True) for k, p in info["excluded"]]
        sel = effective_selection(info)
        gh_full["selected"] = set(sel)

        f_map = {}
        for item in all_items:
            f_map.setdefault(folder_of(item[1]), []).append(item)
        gh_full["folders"] = sorted(f_map.keys())

        filt = gh_full.get("folder_filter")
        if filt and filt in f_map:
            items = f_map[filt]
        else:
            gh_full["folder_filter"] = None
            items = all_items

        gh_full["items"] = items
        pages = max(1, -(-len(items) // PAGE))
        page = min(max(gh_full["page"], 0), pages - 1)
        gh_full["page"] = page

        n_sens = sum(1 for _, p, s in all_items if s and p in sel)
        lines = ["☑️ <b>Göndəriləcək faylları seçin</b>\n"]
        if filt:
            lines.append(f"📁 Qovluq: <code>{escape(filt)}</code> ({len(items)} fayl)")
        lines.append(f"Seçilib: <b>{len(sel)}</b> / {len(all_items)}"
                     + (f" · 🚨 həssas: <b>{n_sens}</b>" if n_sens else ""))

        if not items:
            lines.append("\n✅ <i>Commit edilməmiş dəyişiklik yoxdur.</i>")

        kb = []
        chunk = items[page * PAGE:(page + 1) * PAGE]
        if chunk:
            lines.append("")
        for i, (kind, path, sens) in enumerate(chunk, start=page * PAGE):
            mark = "✅" if path in sel else "⬜"
            icon = "🔒" if sens else STATUS_ICONS[kind]
            lines.append(f"{mark} {icon} <code>{escape(path)}</code>")
            kb.append([btn(f"{mark} {icon} {short_path(path)}", f"ghf:t:{i}")])

        if any(s for _, _, s in items):
            lines.append("\n<i>🔒 = həssas fayl. Default seçilmir — seçsəniz repoya düşəcək.</i>")

        if pages > 1:
            kb.append([btn("◀️", f"ghf:pg:{(page - 1) % pages}"),
                       btn(f"{page + 1}/{pages}", "ghf:noop"),
                       btn("▶️", f"ghf:pg:{(page + 1) % pages}")])

        f_btn_text = f"📁 Qovluqlar ({len(gh_full['folders'])})"
        row_folders = [btn(f_btn_text, "ghf:fview")]
        if filt:
            row_folders.append(btn("🌐 Bütün fayllar", "ghf:fclr"))
        kb.append(row_folders)

        if items:
            all_lbl = "✅ Hamısı" if not filt else f"✅ {filt} hamısı"
            none_lbl = "⬜ Heç biri" if not filt else f"⬜ {filt} heç biri"
            kb.append([btn(all_lbl, "ghf:all"), btn(none_lbl, "ghf:none")])

        kb.append([btn(f"➡️ Davam et ({len(sel)})", "ghf:back")])
        kb.append(nav("ghf:open"))
        return "\n".join(lines)[:4000], InlineKeyboardMarkup(inline_keyboard=kb)

    async def status_view(fetch: bool = False):
        info = await asyncio.to_thread(repo_status, root, fetch)
        lines = ["🌿 <b>GitHub</b>\n"]
        kb = []

        if not info.get("is_repo"):
            lines.append("⚠️ <b>Git repozitoriyası başladılmayıb!</b>\n"
                         "Layihəni GitHub ilə sinxronlaşdırmaq üçün əvvəlcə quraşdırmanı tamamlayın.")
            kb.append([btn("⚙️ GitHub-ı Quraşdır", "ghs:menu")])
            kb.append(nav("menu:sys"))
            return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=kb)

        repo = f'<a href="{info["web"]}">{escape(info["web"].replace("https://github.com/", ""))}</a>' \
            if info.get("web") else escape(info.get("remote_url") or "remote yoxdur")
        lines.append(f"📦 Repo: {repo}")
        lines.append(f"🌿 Branch: <code>{escape(info.get('branch', '—'))}</code>")

        if not info.get("remote_url"):
            lines.append("\n⚠️ <b>Remote URL təyin edilməyib!</b>")
        elif not info.get("has_token") and info.get("remote_url", "").startswith("https://"):
            lines.append("\n⚠️ <b>GitHub Token daxil edilməyib (PAT)!</b>")

        if info.get("last"):
            l = info["last"]
            lines.append(f"🕒 Son commit: <code>{escape(l['hash'])}</code> — {escape(l['subject'][:60])} "
                         f"<i>({fmt_time(l['ts'])})</i>")
        if "ahead" in info:
            sync = []
            if info["ahead"]:
                sync.append(f"⬆️ {info['ahead']} commit göndərilməyib")
            if info["behind"]:
                sync.append(f"⬇️ GitHub-da {info['behind']} yeni commit var")
            lines.append("🔄 " + (" · ".join(sync) if sync else "GitHub ilə sinxrondur"))
        else:
            lines.append("🔄 <i>Upstream branch qurulmayıb — ilk push onu yaradacaq</i>")
        if info.get("fetch_error"):
            lines.append(f"⚠️ fetch alınmadı: <code>{escape(info['fetch_error'][:150])}</code>")
        lines.append("")
        lines.append(changes_block(info.get("changes", []), info.get("excluded", []), limit=15))
        if not info.get("changes") and not info.get("ahead"):
            lines.append("\n✅ <i>Göndəriləcək heç nə yoxdur.</i>")

        if info.get("changes") or info.get("ahead"):
            kb.append([btn("📦 Hamısını göndər", "ghf:prep")])
        if info.get("changes") or info.get("excluded"):
            kb.append([btn("☑️ Seçərək göndər", "ghf:pick")])
        kb.append([btn("🔄 Yenilə", "ghf:open"), btn("📡 GitHub-la yoxla", "ghf:fetch")])
        kb.append([btn("⚙️ GitHub Quraşdırma / Ayarlar", "ghs:menu")])
        kb.append(nav("menu:sys"))
        return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=kb)

    async def full_confirm_view():
        info = await asyncio.to_thread(repo_status, root, False)
        if info.get("error"):
            return f"❌ {escape(info['error'])}", InlineKeyboardMarkup(inline_keyboard=[nav("ghf:open")])
        sel = effective_selection(info)
        everything = info["changes"] + info["excluded"]
        changes = [(k, p) for k, p in everything if p in sel]
        excluded = [(k, p) for k, p in info["excluded"] if p not in sel]
        skipped = len({p for _, p in info["changes"] if p not in sel})
        sens_sent = [p for _, p in changes if is_sensitive(p)]
        custom = gh_full["selected"] is not None
        message = gh_full["message"] or full_commit_message(
            changes, time.time(), "Seçilmiş yeniləmə" if custom else "Tam yeniləmə")
        lines = [("☑️ <b>Seçilən fayllar — GitHub-a göndərilsin?</b>\n" if custom
                  else "📦 <b>Tam yeniləmə — GitHub-a göndərilsin?</b>\n"),
                 f"🌿 <code>{escape(info.get('branch', '—'))}</code> → <code>{escape(info.get('remote', 'origin'))}</code>"]
        if info.get("ahead"):
            lines.append(f"⬆️ Əvvəldən göndərilməmiş {info['ahead']} commit də push olunacaq")
        if info.get("behind"):
            lines.append(f"⚠️ GitHub-da sizdə olmayan {info['behind']} commit var — push rədd oluna bilər")
        lines.append("")
        lines.append(changes_block(changes, excluded, skipped=skipped, title="Göndəriləcək"))
        if sens_sent:
            lines.append(f"\n🚨 <b>Diqqət: {len(sens_sent)} həssas fayl da göndəriləcək!</b>")
        lines.append(f"\n📝 <b>Commit mesajı:</b>\n<pre>{escape(message[:600])}</pre>")
        kb = []
        if changes or info.get("ahead"):
            kb.append([btn("✅ Təsdiqlə və göndər", "ghf:go")])
        if everything:
            kb.append([btn(f"☑️ Faylları seç ({len(changes)}/{len({p for _, p in everything})})", "ghf:pick")])
        if changes or info.get("ahead"):
            kb.append([btn("✏️ Commit mesajı", "ghf:msg")])
        kb.append(nav("ghf:open"))
        return "\n".join(lines)[:4000], InlineKeyboardMarkup(inline_keyboard=kb)

    @dp.callback_query(F.data.startswith("ghf:"))
    async def gh_full_callback(cb: CallbackQuery):
        if cb.from_user.id != creator:
            await cb.answer("⛔ İcazə yoxdur", show_alert=True)
            return
        parts = cb.data.split(":")
        action = parts[1]
        new_msg = len(parts) > 2 and parts[2] == "new"

        if action in ("open", "fetch"):
            await cb.answer("GitHub yoxlanılır..." if action == "fetch" else None)
            gh_full.update(chat_id=cb.message.chat.id, panel_id=cb.message.message_id)
            gh_full["await"] = False
            text, kb = await status_view(fetch=(action == "fetch"))
            await edit_full_panel(text, kb)
            return

        if action == "prep":
            await cb.answer()
            gh_full["message"] = None
            gh_full["selected"] = None
            gh_full["await"] = False
            if new_msg:
                sent = await bot.send_message(creator, "⏳ <i>Hazırlanır...</i>", parse_mode="HTML")
                gh_full.update(chat_id=sent.chat.id if getattr(sent, "chat", None) else creator,
                               panel_id=sent.message_id)
            else:
                gh_full.update(chat_id=cb.message.chat.id, panel_id=cb.message.message_id)
            text, kb = await full_confirm_view()
            await edit_full_panel(text, kb)
            return

        if action == "noop":
            await cb.answer()
            return

        if action == "pick":
            await cb.answer()
            gh_full["await"] = False
            gh_full["page"] = 0
            if new_msg:
                sent = await bot.send_message(creator, "⏳ <i>Hazırlanır...</i>", parse_mode="HTML")
                gh_full.update(chat_id=sent.chat.id if getattr(sent, "chat", None) else creator,
                               panel_id=sent.message_id)
                gh_full["message"] = None
                p = await asyncio.to_thread(load_pending)
                gh_full["selected"] = (
                    {x for k in ("changed", "added", "removed") for x in p["files"].get(k, [])} if p else None
                )
            else:
                gh_full.update(chat_id=cb.message.chat.id, panel_id=cb.message.message_id)
            text, kb = await pick_view()
            await edit_full_panel(text, kb)
            return

        # ── Qovluq paneli və filtrlər ──
        if action == "fview":
            await cb.answer()
            gh_full.update(chat_id=cb.message.chat.id, panel_id=cb.message.message_id)
            text, kb = await folders_view()
            await edit_full_panel(text, kb)
            return

        if action == "fclr":
            await cb.answer("Filtr sıfırlandı")
            gh_full["folder_filter"] = None
            gh_full["page"] = 0
            text, kb = await pick_view()
            await edit_full_panel(text, kb)
            return

        if action == "fset" and len(parts) > 2:
            try:
                f_name = gh_full["folders"][int(parts[2])]
            except (IndexError, ValueError):
                await cb.answer("Məlumat köhnəlib")
                return
            gh_full["folder_filter"] = f_name
            gh_full["page"] = 0
            await cb.answer(f"Qovluq: {f_name}")
            text, kb = await pick_view()
            await edit_full_panel(text, kb)
            return

        if action == "fpg" and len(parts) > 2:
            try:
                gh_full["folder_page"] = int(parts[2])
            except (IndexError, ValueError):
                pass
            text, kb = await folders_view()
            await edit_full_panel(text, kb)
            await cb.answer()
            return

        if action == "ftog" and len(parts) > 2:
            try:
                folder_name = gh_full["folders"][int(parts[2])]
            except (IndexError, ValueError):
                await cb.answer("Məlumat köhnəlib")
                return
            info = await asyncio.to_thread(repo_status, root, False)
            all_items = [(k, p, False) for k, p in info["changes"]] + [(k, p, True) for k, p in info["excluded"]]
            f_items = [item for item in all_items if folder_of(item[1]) == folder_name]
            sel = gh_full["selected"] if gh_full["selected"] is not None else set()
            chosen_in_f = [p for _, p, _ in f_items if p in sel]

            alert = None
            if len(chosen_in_f) == len(f_items) and len(f_items) > 0:
                for _, p, _ in f_items:
                    sel.discard(p)
            else:
                non_sens = [p for _, p, s in f_items if not s]
                if not all(p in sel for p in non_sens) and non_sens:
                    sel.update(non_sens)
                else:
                    has_sens = False
                    for _, p, s in f_items:
                        sel.add(p)
                        if s:
                            has_sens = True
                    if has_sens:
                        alert = f"⚠️ {folder_name} qovluğundakı həssas fayllar da seçildi!"
            gh_full["selected"] = sel
            await cb.answer(alert, show_alert=bool(alert))
            text, kb = await folders_view()
            await edit_full_panel(text, kb)
            return

        if action in ("fall", "fnone"):
            info = await asyncio.to_thread(repo_status, root, False)
            all_items = [(k, p, False) for k, p in info["changes"]] + [(k, p, True) for k, p in info["excluded"]]
            if action == "fall":
                gh_full["selected"] = {p for _, p, s in all_items if not s}
            else:
                gh_full["selected"] = set()
            await cb.answer()
            text, kb = await folders_view()
            await edit_full_panel(text, kb)
            return

        # ── Tək-tək fayl seçimi və səhifələmə ──
        if action in ("t", "pg", "all", "none"):
            gh_full.update(chat_id=cb.message.chat.id, panel_id=cb.message.message_id)
            items = gh_full["items"]
            sel = gh_full["selected"] if gh_full["selected"] is not None else set()
            alert = None
            if action == "t":
                try:
                    kind, path, sens = items[int(parts[2])]
                except (IndexError, ValueError):
                    await cb.answer("Siyahı köhnəlib, yeniləndi")
                    text, kb = await pick_view()
                    await edit_full_panel(text, kb)
                    return
                if path in sel:
                    sel.discard(path)
                else:
                    sel.add(path)
                    if sens:
                        alert = (f"⚠️ {path} həssas fayldır!\n\nİçində token və ya şəxsi məlumat "
                                 "ola bilər. Göndərsəniz repoya düşəcək.")
            elif action == "pg":
                try:
                    gh_full["page"] = int(parts[2])
                except (IndexError, ValueError):
                    pass
            elif action == "all":
                sel |= {p for _, p, s in items if not s}
            elif action == "none":
                for _, p, _ in items:
                    sel.discard(p)
            gh_full["selected"] = sel
            await cb.answer(alert, show_alert=bool(alert))
            text, kb = await pick_view()
            await edit_full_panel(text, kb)
            return

        if action == "msg":
            await cb.answer()
            gh_full["await"] = True
            gh_full.update(chat_id=cb.message.chat.id, panel_id=cb.message.message_id)
            await edit_full_panel(
                "✏️ <b>Commit mesajını yazın</b>\n\n"
                + (f"Hazırkı:\n<pre>{escape(gh_full['message'][:600])}</pre>" if gh_full["message"]
                 else "<i>Hazırda avtomatik mesaj istifadə olunur (seçilən fayllardan yaradılır).</i>"),
                InlineKeyboardMarkup(inline_keyboard=[nav("ghf:back")]),
            )
            return

        if action == "back":
            await cb.answer()
            gh_full["await"] = False
            text, kb = await full_confirm_view()
            await edit_full_panel(text, kb)
            return

        if action == "go":
            if push_lock.locked():
                await cb.answer("Artıq göndərilir...")
                return
            await cb.answer("Göndərilir...")
            gh_full["await"] = False
            gh_full.update(chat_id=cb.message.chat.id, panel_id=cb.message.message_id)
            message = gh_full["message"]
            selected = set(gh_full["selected"]) if gh_full["selected"] is not None else None
            async with push_lock:
                await edit_full_panel("⏳ <i>GitHub-a göndərilir...</i>", None)
                try:
                    res = await asyncio.to_thread(push_all, root, message, selected)
                except Exception as e:
                    logger.error(f"Full push xətası: {e}", exc_info=True)
                    res = {"ok": False, "committed": False, "error": redact(str(e)), "hint": "", "excluded": []}

            if res["ok"]:
                gh_full["message"] = None
                gh_full["selected"] = None
                p = await asyncio.to_thread(load_pending)
                if p:
                    pend = {x for k in ("changed", "added", "removed") for x in p["files"].get(k, [])}
                    if not pend & set(res.get("remaining", [])):
                        await asyncio.to_thread(save_pending, None)
                        await set_report_kb(p, status_kb(f"✅ GitHub-da: {res['hash']}", res.get("url")))
                lines = [
                    ("✅ <b>Seçilən fayllar GitHub-a göndərildi</b>\n" if selected is not None
                     else "✅ <b>Tam yeniləmə GitHub-a göndərildi</b>\n"),
                    f"🌿 <code>{escape(res['branch'])}</code> · commit <code>{escape(res['hash'])}</code>",
                    f"📁 {len(res.get('files', []))} fayl"
                    + ("" if res["committed"] else " <i>(yeni commit lazım olmadı, yalnız push edildi)</i>"),
                ]
                if res.get("sensitive_sent"):
                    lines.append(f"🚨 {len(res['sensitive_sent'])} həssas fayl da göndərildi")
                if res.get("excluded"):
                    lines.append(f"🔒 {len(res['excluded'])} həssas fayl göndərilmədi")
                other = res.get("skipped", 0) - len(res.get("excluded", []))
                if other > 0:
                    lines.append(f"⏭ {other} fayl seçilmədiyi üçün lokalda qaldı")
                kb = []
                if res.get("url"):
                    kb.append([btn("🔗 Commit-ə bax", url=res["url"])])
                kb.append(nav("ghf:open"))
                await edit_full_panel("\n".join(lines), InlineKeyboardMarkup(inline_keyboard=kb))
                logger.info(f"📦 Full push: {res['hash']} ({len(res.get('files', []))} fayl)")
            else:
                note = "\n\n<i>Commit lokal olaraq yaradıldı, yalnız push alınmadı.</i>" if res.get("committed") else ""
                hint = f"\n\n💡 {res['hint']}" if res.get("hint") else ""
                await edit_full_panel(
                    f"❌ <b>Göndərilmədi</b>\n\n<code>{escape(res['error'])}</code>{hint}{note}",
                    InlineKeyboardMarkup(inline_keyboard=[
                        [btn("🔁 Yenidən cəhd et", "ghf:back")],
                        [btn("⚙️ GitHub-ı Quraşdır", "ghs:menu")],
                        nav("ghf:open")
                    ]),
                )
            return

        await cb.answer()

    # ═════════════ başlanğıcda yeniləmə yoxlanışı ═════════════
    async def check_updates():
        await asyncio.sleep(DELAY_AFTER_START)
        if not creator:
            logger.warning("CREATOR_ID yoxdur — yeniləmə bildirişi göndərilmir")
            return

        db = get_db()
        root = Path(BASE_DIR)
        try:
            new = await asyncio.to_thread(scan_files, root)
            old = await asyncio.to_thread(db.get_file_snapshot)
            env_new = await asyncio.to_thread(package_versions)
            git_data = await asyncio.to_thread(git_info, root)
        except Exception as e:
            logger.error(f"Yeniləmə yoxlanışı xətası: {e}", exc_info=True)
            return

        try:
            env_old = json.loads(db.get_setting("update_env") or "{}")
        except ValueError:
            env_old = {}
        git_old = db.get_setting("update_git_head")
        env = env_lines(env_old, env_new, git_data, git_old, ram_mb())

        plugin_status = getattr(context, "plugin_status", {}) or {}
        failed = any(v != "ok" for v in plugin_status.values())
        env_changed = bool(env_old) and any(env_old.get(k) != v for k, v in env_new.items() if k in env_old)
        diff_doc = None
        pending = None

        # GitHub quraşdırılma vəziyyətini yoxla
        is_conf = False
        if git_data:
            remote = os.getenv("GITHUB_REMOTE", "origin")
            rc, rurl, _ = git(root, "remote", "get-url", remote)
            is_conf = (rc == 0 and bool(rurl) and (bool(_token()) or not rurl.startswith("https://")))

        if not old:
            text = (
                "📸 <b>Yeniləmə izləməsi aktivdir</b>\n\n"
                f"İlk vəziyyət saxlandı: <b>{len(new)}</b> fayl, "
                f"<b>{fmt_size(sum(f['size'] for f in new.values()))}</b>, "
                f"<b>{sum(f['lines'] for f in new.values())}</b> sətir.\n"
                + "\n".join(env) +
                "\n\n<i>Növbəti dəfə kodu yeniləyib botu yenidən başladanda dəyişikliklər buraya göndəriləcək.</i>"
            )
            if failed:
                text += "\n\n" + build_failure_only(plugin_status, started)
        else:
            added, changed, removed = compare(old, new)
            if added or changed or removed:
                text = build_report(old, new, added, changed, removed, plugin_status, started, env)
                diff_doc = build_diff(old, new, added, changed, removed, started)
                if git_data:
                    files = {"changed": changed, "added": added, "removed": removed}
                    prev = await asyncio.to_thread(load_pending)
                    if prev:
                        for k in files:
                            files[k] = sorted(set(files[k]) | set(prev["files"].get(k, [])))
                    pending = {"id": str(int(started)), "files": files,
                               "message": default_commit_message(files, started), "prev": prev}
            elif env_changed:
                text = "🔄 <b>Kod dəyişməyib, amma mühit yeniləndi</b>\n" \
                       f"🕒 {fmt_time(started)}\n\n" + "\n".join(env)
            elif failed:
                text = build_failure_only(plugin_status, started)
            else:
                logger.info("Kodda dəyişiklik yoxdur, yeniləmə bildirişi göndərilmir")
                return

        try:
            sent = await context.bot.send_message(
                creator, text, parse_mode="HTML", disable_web_page_preview=True,
                reply_markup=gh_kb(pending["id"], configured=is_conf) if pending else None,
            )
            if diff_doc:
                stamp = datetime.fromtimestamp(started, get_tz()).strftime("%Y%m%d_%H%M")
                await context.bot.send_document(
                    creator,
                    BufferedInputFile(diff_doc.encode("utf-8"), filename=f"update_{stamp}.diff"),
                    caption="📄 Tam diff",
                )
        except Exception as e:
            logger.error(f"Yeniləmə bildirişi göndərilmədi: {e}")
            return

        await asyncio.to_thread(db.save_file_snapshot, new)
        if pending:
            prev = pending.pop("prev", None)
            pending["message_id"] = sent.message_id
            await asyncio.to_thread(save_pending, pending)
            if prev and prev.get("message_id"):
                await set_report_kb(prev, status_kb("↪️ Növbəti yeniləmə ilə birləşdi"))
        await asyncio.to_thread(db.set_setting, "update_env", json.dumps(env_new))
        if git_data:
            await asyncio.to_thread(db.set_setting, "update_git_head", git_data["hash"])
        logger.info("✅ Yeniləmə bildirişi göndərildi")

    context.update_check_task = asyncio.create_task(check_updates())
