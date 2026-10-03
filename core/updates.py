"""
DLLMaster Bot — yeniləmə yoxlayıcısı.

Üç mənbəni yoxlayır:
  🤖 bot      — git upstream-də yeni commit varmı
  📦 pip      — requirements.txt paketlərinin PyPI-dakı son versiyası
  🐧 system   — deps/<pm>.txt paketləri (checkupdates / apt list --upgradable)

Nəticə keşdə saxlanır: /menu dərhal cavab verir, şəbəkəyə getmir.
"""
from __future__ import annotations

import asyncio
import html
import logging
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from importlib import metadata
from typing import Awaitable, Callable

import aiohttp
from packaging.requirements import Requirement
from packaging.version import InvalidVersion, Version

from core.bootstrap import ROOT, color, detect_pm, parse_requirements, read_system_packages

log = logging.getLogger("dlm.updates")

CHECK_INTERVAL = 6 * 3600   # 6 saat


@dataclass(frozen=True)
class Update:
    kind: str            # "bot" | "pip" | "system"
    name: str
    current: str
    latest: str
    blocked: bool = False   # requirements.txt-dəki məhdudiyyət buna imkan vermir


@dataclass
class Report:
    items: list[Update] = field(default_factory=list)
    checked_at: float = 0.0
    errors: list[str] = field(default_factory=list)


_report = Report()
_notified: set[Update] = set()
_lock = asyncio.Lock()


def get_report() -> Report:
    return _report


async def _run(*cmd: str) -> tuple[int, str]:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"}
    p = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL, env=env)
    out, _ = await p.communicate()
    return p.returncode or 0, out.decode(errors="replace")


# ── bot (git) ────────────────────────────────────────────────
async def _check_git(errors: list[str]) -> list[Update]:
    if not (ROOT / ".git").exists() or not shutil.which("git"):
        return []
    g = ("git", "-C", str(ROOT))
    code, _ = await _run(*g, "fetch", "--quiet")
    if code != 0:
        errors.append("git fetch alınmadı")
        return []
    code, behind = await _run(*g, "rev-list", "--count", "HEAD..@{u}")
    if code != 0 or not behind.strip().isdigit() or int(behind) == 0:
        return []
    _, cur = await _run(*g, "rev-parse", "--short", "HEAD")
    _, new = await _run(*g, "rev-parse", "--short", "@{u}")
    return [Update("bot", f"DLLMaster Bot (+{int(behind)} commit)", cur.strip(), new.strip())]


# ── pip (PyPI) ───────────────────────────────────────────────
async def _check_pip(errors: list[str]) -> list[Update]:
    reqs = []
    for line in parse_requirements():
        r = Requirement(line)
        if not r.marker or r.marker.evaluate():
            reqs.append(r)

    out: list[Update] = []
    sem = asyncio.Semaphore(8)

    async def one(session: aiohttp.ClientSession, r: Requirement) -> None:
        try:
            cur = Version(metadata.version(r.name))
        except (metadata.PackageNotFoundError, InvalidVersion):
            return
        async with sem:
            try:
                async with session.get(f"https://pypi.org/pypi/{r.name}/json") as resp:
                    if resp.status != 200:
                        return
                    data = await resp.json()
            except Exception as e:
                errors.append(f"PyPI {r.name}: {e!r}")
                return
        try:
            latest = Version(data["info"]["version"])
        except (KeyError, InvalidVersion):
            return
        if latest > cur:
            out.append(Update("pip", r.name, str(cur), str(latest),
                              blocked=not r.specifier.contains(latest, prereleases=True)))

    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(timeout=timeout) as s:
        await asyncio.gather(*(one(s, r) for r in reqs))
    return sorted(out, key=lambda u: u.name.lower())


# ── sistem paketləri ─────────────────────────────────────────
_APT_RE = re.compile(r"^([^/\s]+)/\S+\s+(\S+)\s+\S+\s+\[upgradable from: ([^\]]+)\]")


async def _check_system(errors: list[str]) -> list[Update]:
    pm = detect_pm()
    if pm is None:
        return []
    req, opt = read_system_packages(pm)
    ours = set(req) | set(opt)
    out: list[Update] = []

    if pm == "pacman":
        if not shutil.which("checkupdates"):
            errors.append("checkupdates yoxdur — pacman-contrib quraşdır")
            return []
        _, text = await _run("checkupdates")          # root lazım deyil
        for line in text.splitlines():
            p = line.split()                          # pkg old -> new
            if len(p) >= 4 and p[0] in ours:
                out.append(Update("system", p[0], p[1], p[3]))
    else:
        _, text = await _run("apt", "list", "--upgradable")  # son `apt update`-ə əsaslanır
        for line in text.splitlines():
            m = _APT_RE.match(line)
            if m and m.group(1) in ours:
                out.append(Update("system", m.group(1), m.group(3), m.group(2)))
    return out


# ── ümumi yoxlama ────────────────────────────────────────────
async def check_updates() -> Report:
    global _report
    async with _lock:
        errors: list[str] = []
        results = await asyncio.gather(
            _check_git(errors), _check_pip(errors), _check_system(errors),
            return_exceptions=True)
        items: list[Update] = []
        for r in results:
            if isinstance(r, BaseException):
                errors.append(repr(r))
            else:
                items.extend(r)
        _report = Report(items, time.time(), errors)
        return _report


def _hints(items: list[Update]) -> list[str]:
    kinds = {u.kind for u in items}
    hints = []
    if "bot" in kinds:
        hints.append("git pull")
    if "pip" in kinds:
        hints.append(".venv/bin/pip install -U -r requirements.txt")
    if "system" in kinds:
        hints.append("sudo pacman -Syu" if detect_pm() == "pacman"
                     else "sudo apt update && sudo apt upgrade")
    return hints


# ── terminal ─────────────────────────────────────────────────
def print_report(report: Report | None = None) -> None:
    r = report or _report
    tag = color("[update]", "yellow")
    if not r.items:
        print(color("[update]", "green"), "Hər şey ən son versiyadadır ✓", flush=True)
        return
    print(tag, color(f"{len(r.items)} yeniləmə mövcuddur:", "bold"))
    for u in r.items:
        lock = color("  (requirements.txt məhdudlaşdırır)", "red") if u.blocked else ""
        print(f"   [{u.kind:^6}] {u.name}: {u.current} → {color(u.latest, 'green')}{lock}")
    for h in _hints(r.items):
        print(tag, "→", h)
    print(flush=True)


# ── Telegram (/menu) ─────────────────────────────────────────
_ICONS = {"bot": "🤖", "pip": "📦", "system": "🐧"}


def menu_notice(max_items: int = 8) -> str | None:
    """/menu-nun sonuna əlavə ediləcək HTML blok; yeniləmə yoxdursa None."""
    r = _report
    if not r.items:
        return None
    lines = [f"🔔 <b>Yeniləmə mövcuddur ({len(r.items)})</b>"]
    for u in r.items[:max_items]:
        lock = " 🔒" if u.blocked else ""
        lines.append(f"{_ICONS[u.kind]} <code>{html.escape(u.name)}</code> "
                     f"{html.escape(u.current)} → <b>{html.escape(u.latest)}</b>{lock}")
    if len(r.items) > max_items:
        lines.append(f"… və daha {len(r.items) - max_items}")
    lines += [f"<code>{html.escape(h)}</code>" for h in _hints(r.items)]
    return "\n".join(lines)


# ── fon tapşırığı ────────────────────────────────────────────
Notifier = Callable[[str], Awaitable[None]]


async def update_loop(interval: int = CHECK_INTERVAL,
                      notify: Notifier | None = None,
                      first_delay: int = 5) -> None:
    """Başlanğıcda və hər `interval` saniyədən bir yoxlayır.
    Terminala hər dəfə yeni tapılan yeniləmədə yazır, `notify` ilə sahibə göndərir."""
    await asyncio.sleep(first_delay)
    first = True
    while True:
        try:
            rep = await check_updates()
            new = [u for u in rep.items if u not in _notified]
            if first or new:
                print_report(rep)
            _notified.update(new)
            if new and notify:
                text = menu_notice()
                if text:
                    await notify(text)
            for e in rep.errors:
                log.warning("update check: %s", e)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Yeniləmə yoxlaması alınmadı")
        first = False
        await asyncio.sleep(interval)
