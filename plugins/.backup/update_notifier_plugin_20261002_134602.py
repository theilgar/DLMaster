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
     (.gitignore və həssas fayllar istisna) bir commit-də göndərilir. Hesabatdan və ya
     /menu → 🌿 GitHub panelindən.

GitHub ayarları (config.env, hamısı könüllü):
  GITHUB_REMOTE=origin          push ediləcək remote (default: origin)
  GITHUB_TOKEN=ghp_...          HTTPS remote üçün token (SSH açarı varsa lazım deyil)
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

# Bütün plugin-lər yükləndikdən sonra işləsin ki, onların yüklənmə nəticəsini də görsün
PRIORITY = 1000

EXCLUDE_DIRS = {
    "__pycache__", ".git", ".venv", "venv", "env", ".env", "node_modules",
    "download", "data", "logs", ".idea", ".vscode", "site-packages",
}
PACKAGES = ["aiogram", "yt-dlp", "yt-dlp-ejs", "spotipy", "aiohttp"]
MAX_TEXT = 3900              # Telegram limiti 4096
MAX_NAMES = 8                # hər kateqoriyada göstərilən funksiya adı
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


def names_line(icon: str, label: str, names) -> str:
    names = sorted(names)
    shown = ", ".join(f"<code>{escape(n)}</code>" for n in names[:MAX_NAMES])
    more = f" +{len(names) - MAX_NAMES}" if len(names) > MAX_NAMES else ""
    return f"   {icon} {label}: {shown}{more}"


# ───────────────────────── fayl skanı ─────────────────────────
def scan_files(root: Path) -> dict:
    """{nisbi yol: {sha256, size, lines, mtime, content}} — layihədəki bütün .py faylları."""
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
    """
    ({funksiya/class adı: öz məzmununun hash-i}, {komandalar}, sintaksis xətası | None)
    İç-içə funksiyalar "setup.handler" kimi adlanır; valideynin hash-inə uşağın kodu daxil deyil,
    ona görə yalnız həqiqətən dəyişən funksiya "dəyişdi" sayılır.
    """
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
                own = [line for line in own if line]   # boş sətirlər hesaba alınmır
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
    """(mesaj bloku, sintaksis xətası | None)"""
    icon = kind_icon(path)
    if o is None:        # yeni fayl
        syms, cmds, err = analyze(n["content"])
        head = [f"🆕 {icon} <code>{escape(path)}</code>",
                f"   💾 <b>{fmt_size(n['size'])}</b> · 📃 {n['lines']} sətir · 🕒 {fmt_time(n['mtime'])}"]
        if syms:
            head.append(names_line("🧩", f"{len(syms)} funksiya/class", syms))
        if cmds:
            head.append(names_line("⌨️", "komandalar", {f'/{c}' for c in cmds}))
        return "\n".join(head), err

    if n is None:        # silinmiş fayl
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
def _token():
    return (os.getenv("GITHUB_TOKEN") or "").strip()


def redact(text: str) -> str:
    """Token heç vaxt mesaja/loga düşməsin."""
    tok = _token()
    if tok:
        text = text.replace(tok, "***")
        text = text.replace(base64.b64encode(f"x-access-token:{tok}".encode()).decode(), "***")
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
    """git çıxışından "hint:" sətirlərini atıb, əsas xəta sətirlərini saxlayır."""
    lines = [l for l in (err or "").splitlines() if l.strip() and not l.startswith("hint:")]
    return "\n".join(lines[-limit:])[-600:] or (err or "")[-300:]


def push_hint(err: str) -> str:
    e = err.lower()
    if "non-fast-forward" in e or "fetch first" in e or "rejected" in e:
        return "Uzaq repoda sizdə olmayan commit-lər var. Serverdə <code>git pull --rebase</code> edib yenidən cəhd edin."
    if "permission denied" in e or "authentication" in e or "could not read username" in e or "403" in e:
        return "Giriş alınmadı: serverdə SSH açarı qurun və ya <code>config.env</code>-ə <code>GITHUB_TOKEN</code> yazın."
    if "could not resolve host" in e or "unable to access" in e:
        return "GitHub-a qoşulmaq alınmadı (şəbəkə)."
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
    """Yalnız verilən faylları commit edib push edir. Nəticə: dict (ok, hash, url, ...)."""
    res = {"ok": False, "committed": False, "error": "", "hint": ""}

    rc, _, _ = git(root, "rev-parse", "--is-inside-work-tree")
    if rc != 0:
        res["error"] = "Layihə git repo deyil."
        return res
    remote = os.getenv("GITHUB_REMOTE", "origin")
    rc, remote_url, _ = git(root, "remote", "get-url", remote)
    if rc != 0:
        res["error"] = f"'{remote}' remote-u tapılmadı."
        res["hint"] = "Serverdə: <code>git remote add origin git@github.com:USER/REPO.git</code>"
        return res
    rc, branch, _ = git(root, "rev-parse", "--abbrev-ref", "HEAD")
    if rc != 0 or branch == "HEAD":
        res["error"] = "Repo heç bir branch-da deyil (detached HEAD)."
        return res
    res["branch"] = branch

    # Yalnız mövcud və ya izlənən, .gitignore-a düşməyən fayllar
    use, to_add = [], []
    for p in paths:
        tracked = git(root, "ls-files", "--error-unmatch", "--", p)[0] == 0
        in_head = bool(git(root, "ls-tree", "--name-only", "HEAD", "--", p)[1])   # silinmiş / adı dəyişmiş fayl
        if not tracked and not in_head and not (root / p).exists():
            continue
        if git(root, "check-ignore", "-q", "--", p)[0] == 0:
            continue
        use.append(p)
        if tracked or (root / p).exists():
            to_add.append(p)        # silinməsi artıq indeksdə olan (git mv / git rm) fayl üçün add lazım deyil
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


# ───────────────────────── Full update (hamısını göndər) ─────────────────────────
# .gitignore-da olmasa belə BU fayllar heç vaxt avtomatik göndərilmir (token, baza, cookies ...)
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
    """Commit edilməmiş bütün dəyişikliklər → ([(növ, yol)], [həssas olduğu üçün çıxarılan yollar])."""
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
            # köhnə ad da commit-ə düşməlidir ki, repoda silinsin
            if old and "R" in xy:
                if is_sensitive(old):
                    excluded.append(old)
                else:
                    changes.append(("deleted", old))
        elif xy == "??":
            kind = "new"
        elif "D" in xy:
            kind = "deleted"
        else:
            kind = "modified"
        if is_sensitive(path):
            excluded.append(path)
        else:
            changes.append((kind, path))
    return changes, excluded


def repo_status(root: Path, fetch: bool = False) -> dict:
    """GitHub paneli üçün: branch, remote, son commit, ahead/behind, dəyişikliklər."""
    info = {}
    if git(root, "rev-parse", "--is-inside-work-tree")[0] != 0:
        info["error"] = "Layihə git repo deyil."
        return info
    remote = os.getenv("GITHUB_REMOTE", "origin")
    info["remote"] = remote
    rc, url, _ = git(root, "remote", "get-url", remote)
    info["remote_url"] = url if rc == 0 else None
    info["web"] = github_web_url(url) if rc == 0 else None
    info["branch"] = git(root, "rev-parse", "--abbrev-ref", "HEAD")[1]
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


def full_commit_message(changes, started: float) -> str:
    groups = {}
    for kind, path in changes:
        groups.setdefault(kind, []).append(Path(path).name)
    title = f"Tam yeniləmə ({datetime.fromtimestamp(started, get_tz()).strftime('%d.%m.%Y %H:%M')})"
    labels = [("modified", "Dəyişən"), ("new", "Yeni"), ("deleted", "Silinən"), ("renamed", "Adı dəyişən")]
    body = []
    for key, label in labels:
        names = groups.get(key)
        if names:
            shown = ", ".join(names[:15]) + (f" və daha {len(names) - 15}" if len(names) > 15 else "")
            body.append(f"{label} ({len(names)}): {shown}")
    return title + ("\n\n" + "\n".join(body) if body else "")


def push_all(root: Path, message: str) -> dict:
    """Bütün (həssas olmayan) dəyişiklikləri commit edib push edir."""
    changes, excluded = working_changes(root)
    paths = sorted({p for _, p in changes})
    res = push_update(root, paths, message)
    res["excluded"] = excluded
    res["changes"] = changes
    return res


def gh_kb(pid: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ GitHub-a göndər", callback_data=f"gh:push:{pid}"),
         InlineKeyboardButton(text="❌ İmtina", callback_data=f"gh:skip:{pid}")],
        [InlineKeyboardButton(text="✏️ Commit mesajı", callback_data=f"gh:msg:{pid}"),
         InlineKeyboardButton(text="📦 Hamısını göndər", callback_data="ghf:prep:new")],
    ])


def status_kb(text: str, url: str = None) -> InlineKeyboardMarkup:
    btn = InlineKeyboardButton(text=text, url=url) if url else InlineKeyboardButton(text=text, callback_data="gh:noop")
    return InlineKeyboardMarkup(inline_keyboard=[[btn]])


# ───────────────────────── plugin ─────────────────────────
def setup(context):
    creator = context.creator_id
    started = time.time()
    dp = context.dp
    bot = context.bot
    root = Path(BASE_DIR)
    push_lock = asyncio.Lock()
    gh_state = {"await_msg": None}      # commit mesajı gözlənilirsə: pending id
    gh_full = {"message": None, "await": False, "chat_id": None, "panel_id": None}   # 📦 full update

    # menu_plugin-dəki "creator mətn yazır" yoxlamasına əlavə et (music_plugin axtarış etməsin)
    prev_waiting = getattr(context, "menu_waiting_text", None)

    def waiting_text(user_id: int) -> bool:
        if user_id == creator and (gh_state["await_msg"] or gh_full["await"]):
            return True
        return bool(prev_waiting and prev_waiting(user_id))

    context.menu_waiting_text = waiting_text

    # ── gözləyən (təsdiq olunmamış) yeniləmə bazada saxlanılır: restartdan sonra da düymələr işləyir ──
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
                    await set_report_kb(p, gh_kb(p["id"]))
                    note = "\n\n<i>Commit lokal olaraq yaradıldı, yalnız push alınmadı. " \
                           "\"GitHub-a göndər\" yenidən basılsa, push təkrarlanacaq.</i>" if res["committed"] else ""
                    hint = f"\n\n💡 {res['hint']}" if res.get("hint") else ""
                    await bot.send_message(
                        creator,
                        f"❌ <b>GitHub-a göndərilmədi</b>\n\n<code>{escape(res['error'])}</code>{hint}{note}",
                        parse_mode="HTML",
                    )
            return

        await cb.answer()

    async def waiting_commit_msg(message: Message) -> bool:
        return bool(gh_state["await_msg"] or gh_full["await"]) and message.from_user \
            and message.from_user.id == creator

    @dp.message(F.chat.type == "private", F.text, ~F.text.startswith("/"), waiting_commit_msg)
    async def gh_commit_message(message: Message):
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

    # ═════════════ 📦 Full update / 🌿 GitHub paneli ═════════════
    def nav(back: str):
        """menu_plugin-in naviqasiyası ilə eyni: ⬅️ Geri · 🏠 Menyu · ❌ Ləğv et"""
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

    def changes_block(changes, excluded, limit=25) -> str:
        counts = {}
        for kind, _ in changes:
            counts[kind] = counts.get(kind, 0) + 1
        summary = " · ".join(f"{STATUS_ICONS[k]} {v}" for k, v in counts.items()) or "—"
        lines = [f"📁 <b>Dəyişikliklər:</b> {len(changes)} ({summary})"]
        for kind, path in changes[:limit]:
            lines.append(f"   {STATUS_ICONS[kind]} <code>{escape(path)}</code>")
        if len(changes) > limit:
            lines.append(f"   <i>… və daha {len(changes) - limit} fayl</i>")
        if excluded:
            lines.append(f"\n🔒 <b>Göndərilməyəcək (həssas):</b> {len(excluded)}")
            lines += [f"   🔒 <code>{escape(p)}</code>" for p in excluded[:10]]
        return "\n".join(lines)

    async def status_view(fetch: bool = False):
        info = await asyncio.to_thread(repo_status, root, fetch)
        if info.get("error"):
            return f"🌿 <b>GitHub</b>\n\n❌ {escape(info['error'])}", InlineKeyboardMarkup(inline_keyboard=[nav("menu:sys")])
        lines = ["🌿 <b>GitHub</b>\n"]
        repo = f'<a href="{info["web"]}">{escape(info["web"].replace("https://github.com/", ""))}</a>' \
            if info.get("web") else escape(info.get("remote_url") or "remote yoxdur")
        lines.append(f"📦 Repo: {repo}")
        lines.append(f"🌿 Branch: <code>{escape(info['branch'])}</code>")
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
        lines.append(changes_block(info["changes"], info["excluded"], limit=15))
        if not info["changes"] and not info.get("ahead"):
            lines.append("\n✅ <i>Göndəriləcək heç nə yoxdur.</i>")

        kb = []
        if info["changes"] or info.get("ahead"):
            kb.append([btn("📦 Hamısını göndər", "ghf:prep")])
        kb.append([btn("🔄 Yenilə", "ghf:open"), btn("📡 GitHub-la yoxla", "ghf:fetch")])
        kb.append(nav("menu:sys"))
        return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=kb)

    async def full_confirm_view():
        info = await asyncio.to_thread(repo_status, root, False)
        if info.get("error"):
            return f"❌ {escape(info['error'])}", InlineKeyboardMarkup(inline_keyboard=[nav("ghf:open")])
        changes = info["changes"]
        if not gh_full["message"]:
            gh_full["message"] = full_commit_message(changes, time.time())
        lines = ["📦 <b>Tam yeniləmə — GitHub-a göndərilsin?</b>\n",
                 f"🌿 <code>{escape(info['branch'])}</code> → <code>{escape(info['remote'])}</code>"]
        if info.get("ahead"):
            lines.append(f"⬆️ Əvvəldən göndərilməmiş {info['ahead']} commit də push olunacaq")
        if info.get("behind"):
            lines.append(f"⚠️ GitHub-da sizdə olmayan {info['behind']} commit var — push rədd oluna bilər")
        lines.append("")
        lines.append(changes_block(changes, info["excluded"]))
        lines.append(f"\n📝 <b>Commit mesajı:</b>\n<pre>{escape(gh_full['message'][:600])}</pre>")
        kb = []
        if changes or info.get("ahead"):
            kb.append([btn("✅ Təsdiqlə və göndər", "ghf:go")])
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
            gh_full["await"] = False
            if new_msg:
                # hesabatın altından: hesabat qalsın, yeni mesaj göndərilsin
                sent = await bot.send_message(creator, "⏳ <i>Hazırlanır...</i>", parse_mode="HTML")
                gh_full.update(chat_id=sent.chat.id if getattr(sent, "chat", None) else creator,
                               panel_id=sent.message_id)
            else:
                gh_full.update(chat_id=cb.message.chat.id, panel_id=cb.message.message_id)
            text, kb = await full_confirm_view()
            await edit_full_panel(text, kb)
            return

        if action == "msg":
            await cb.answer()
            gh_full["await"] = True
            gh_full.update(chat_id=cb.message.chat.id, panel_id=cb.message.message_id)
            await edit_full_panel(
                "✏️ <b>Commit mesajını yazın</b>\n\n"
                f"Hazırkı:\n<pre>{escape((gh_full['message'] or '')[:600])}</pre>",
                InlineKeyboardMarkup(inline_keyboard=[nav("ghf:back")]),
            )
            return

        if action == "back":          # commit mesajı yazmaqdan imtina → təsdiq pəncərəsi
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
            message = gh_full["message"] or full_commit_message([], time.time())
            async with push_lock:
                await edit_full_panel("⏳ <i>GitHub-a göndərilir...</i>", None)
                try:
                    res = await asyncio.to_thread(push_all, root, message)
                except Exception as e:
                    logger.error(f"Full push xətası: {e}", exc_info=True)
                    res = {"ok": False, "committed": False, "error": redact(str(e)), "hint": "", "excluded": []}

            if res["ok"]:
                gh_full["message"] = None
                # gözləyən hesabat da bu commit-ə daxil oldu
                p = await asyncio.to_thread(load_pending)
                if p:
                    await asyncio.to_thread(save_pending, None)
                    await set_report_kb(p, status_kb(f"✅ GitHub-da: {res['hash']}", res.get("url")))
                lines = [
                    "✅ <b>Tam yeniləmə GitHub-a göndərildi</b>\n",
                    f"🌿 <code>{escape(res['branch'])}</code> · commit <code>{escape(res['hash'])}</code>",
                    f"📁 {len(res.get('files', []))} fayl"
                    + ("" if res["committed"] else " <i>(yeni commit lazım olmadı, yalnız push edildi)</i>"),
                ]
                if res.get("excluded"):
                    lines.append(f"🔒 {len(res['excluded'])} həssas fayl göndərilmədi")
                kb = []
                if res.get("url"):
                    kb.append([btn("🔗 Commit-ə bax", url=res["url"])])
                kb.append(nav("ghf:open"))
                await edit_full_panel("\n".join(lines), InlineKeyboardMarkup(inline_keyboard=kb))
                logger.info(f"📦 Full push: {res['hash']} ({len(res.get('files', []))} fayl)")
            else:
                note = "\n\n<i>Commit lokal olaraq yaradıldı, yalnız push alınmadı — yenidən cəhd edə bilərsən.</i>" \
                    if res.get("committed") else ""
                hint = f"\n\n💡 {res['hint']}" if res.get("hint") else ""
                await edit_full_panel(
                    f"❌ <b>Göndərilmədi</b>\n\n<code>{escape(res['error'])}</code>{hint}{note}",
                    InlineKeyboardMarkup(inline_keyboard=[[btn("🔁 Yenidən cəhd et", "ghf:prep")], nav("ghf:open")]),
                )
            return

        await cb.answer()

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
            git = await asyncio.to_thread(git_info, root)
        except Exception as e:
            logger.error(f"Yeniləmə yoxlanışı xətası: {e}", exc_info=True)
            return

        try:
            env_old = json.loads(db.get_setting("update_env") or "{}")
        except ValueError:
            env_old = {}
        git_old = db.get_setting("update_git_head")
        env = env_lines(env_old, env_new, git, git_old, ram_mb())

        plugin_status = getattr(context, "plugin_status", {}) or {}
        failed = any(v != "ok" for v in plugin_status.values())
        env_changed = bool(env_old) and any(env_old.get(k) != v for k, v in env_new.items() if k in env_old)
        diff_doc = None
        pending = None

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
                if git:
                    files = {"changed": changed, "added": added, "removed": removed}
                    prev = await asyncio.to_thread(load_pending)
                    if prev:
                        # təsdiqlənməmiş əvvəlki yeniləmə ilə birləşdir ki, heç nə kənarda qalmasın
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
                reply_markup=gh_kb(pending["id"]) if pending else None,
            )
            if diff_doc:
                stamp = datetime.fromtimestamp(started, get_tz()).strftime("%Y%m%d_%H%M")
                await context.bot.send_document(
                    creator,
                    BufferedInputFile(diff_doc.encode("utf-8"), filename=f"update_{stamp}.diff"),
                    caption="📄 Tam diff",
                )
        except Exception as e:
            # Göndərilməsə snapshot yenilənmir → növbəti başlanğıcda eyni dəyişikliklər yenə bildiriləcək
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
        if git:
            await asyncio.to_thread(db.set_setting, "update_git_head", git["hash"])
        logger.info("✅ Yeniləmə bildirişi göndərildi")

    context.update_check_task = asyncio.create_task(check_updates())
