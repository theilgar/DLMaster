"""
DLLMaster Bot — başlanğıc asılılıq yoxlaması.

YALNIZ standart kitabxanadan istifadə edir, çünki aiogram/telethon hələ
quraşdırılmamış ola bilər. Giriş faylında HƏR ŞEYDƏN ƏVVƏL çağır:

    from core.bootstrap import ensure_dependencies
    ensure_dependencies()

Ardıcıllıq: sistem paketləri (pacman/apt) → .venv → pip paketləri.

Mühit dəyişənləri:
    DLM_AUTO_INSTALL=1   soruşmadan quraşdır (systemd/docker üçün)
    DLM_SKIP_DEPS=1      yoxlamanı tamamilə keç
    DLM_NO_VENV=1        .venv tələb etmə
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REQ_FILE = ROOT / "requirements.txt"
DEPS_DIR = ROOT / "deps"
VENV_DIR = ROOT / ".venv"

_PASS_ENV = "DLM_BOOTSTRAP_PASS"

# ── terminal ─────────────────────────────────────────────────
_TTY = sys.stdout.isatty()
_COLORS = {"red": "31", "green": "32", "yellow": "33", "cyan": "36", "bold": "1"}


def color(text: str, name: str) -> str:
    return f"\033[{_COLORS[name]}m{text}\033[0m" if _TTY else text


def info(msg: str) -> None:
    print(color("[deps]", "cyan"), msg, flush=True)


def warn(msg: str) -> None:
    print(color("[deps]", "yellow"), msg, flush=True)


def error(msg: str) -> None:
    print(color("[deps]", "red"), msg, file=sys.stderr, flush=True)


def ask(question: str) -> bool:
    if os.environ.get("DLM_AUTO_INSTALL") == "1":
        return True
    if not sys.stdin.isatty():          # systemd və s. — soruşa bilmirik
        return False
    try:
        ans = input(f"{color('[deps]', 'yellow')} {question} [Y/n]: ").strip().lower()
    except EOFError:
        return False
    return ans in ("", "y", "yes", "h", "hə", "he")


def run(cmd: list[str]) -> bool:
    info("→ " + " ".join(cmd))
    return subprocess.run(cmd).returncode == 0


def _sudo() -> list[str]:
    if os.geteuid() == 0:
        return []
    for tool in ("sudo", "doas"):
        if shutil.which(tool):
            return [tool]
    return []


def _reexec(python: str) -> None:
    """Prosesi (yeni) Python ilə eyni arqumentlərlə yenidən başlat."""
    env = os.environ.copy()
    n = int(env.get(_PASS_ENV, "0")) + 1
    if n > 3:
        error("Bootstrap dövrəyə düşdü — asılılıqları əl ilə yoxla.")
        sys.exit(1)
    env[_PASS_ENV] = str(n)
    args = getattr(sys, "orig_argv", [sys.executable, *sys.argv])[1:]  # "-m bot" da işləyir
    os.execve(python, [python, *args], env)


# ── sistem paketləri ─────────────────────────────────────────
def detect_pm() -> str | None:
    if shutil.which("pacman"):
        return "pacman"
    if shutil.which("apt-get"):
        return "apt"
    return None


def read_system_packages(pm: str) -> tuple[list[str], list[str]]:
    """deps/<pm>.txt → (vacib, könüllü). '?' ilə başlayan sətir könüllüdür."""
    path = DEPS_DIR / f"{pm}.txt"
    required: list[str] = []
    optional: list[str] = []
    if not path.exists():
        return required, optional
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("?"):
            optional.append(line[1:].strip())
        else:
            required.append(line)
    return required, optional


def missing_system(pm: str, pkgs: list[str]) -> list[str]:
    if not pkgs:
        return []
    if pm == "pacman":
        # -T "provides"-i də nəzərə alır (məs. ffmpeg-full → ffmpeg)
        r = subprocess.run(["pacman", "-T", *pkgs], capture_output=True, text=True)
        return r.stdout.split()
    missing = []
    for p in pkgs:
        r = subprocess.run(["dpkg-query", "-W", "-f=${Status}", p],
                           capture_output=True, text=True)
        if "install ok installed" not in r.stdout:
            missing.append(p)
    return missing


def _install_cmds(pm: str, pkgs: list[str]) -> list[list[str]]:
    s = _sudo()
    if pm == "pacman":
        return [[*s, "pacman", "-S", "--needed", "--noconfirm", *pkgs]]
    return [[*s, "apt-get", "update"], [*s, "apt-get", "install", "-y", *pkgs]]


def _check_system() -> None:
    pm = detect_pm()
    if pm is None:
        warn("pacman/apt tapılmadı — sistem paketləri yoxlanmır.")
        if not shutil.which("ffmpeg"):
            warn("ffmpeg PATH-də yoxdur!")
        return

    required, optional = read_system_packages(pm)
    miss_opt = missing_system(pm, optional)
    if miss_opt:
        warn("Könüllü paketlər yoxdur: " + ", ".join(miss_opt))

    miss = missing_system(pm, required)
    if not miss:
        return

    error("Lazım olan sistem paketləri yoxdur: " + color(", ".join(miss), "bold"))
    cmds = _install_cmds(pm, miss)
    if ask(f"{pm} ilə indi quraşdırılsın?") and all(run(c) for c in cmds):
        info(color("Sistem paketləri quraşdırıldı.", "green"))
        return
    error("Əl ilə quraşdır:\n    " + "\n    ".join(" ".join(c) for c in cmds))
    sys.exit(1)


# ── virtual mühit ────────────────────────────────────────────
def _ensure_venv() -> None:
    if sys.prefix != sys.base_prefix or os.environ.get("DLM_NO_VENV") == "1":
        return
    py = VENV_DIR / "bin" / "python"
    if not py.exists():
        warn("Virtual mühit (.venv) yoxdur. Arch/Debian sistem Python-una "
             "pip ilə yazmağa icazə vermir (PEP 668).")
        if not ask(f"{VENV_DIR} yaradılsın?"):
            error(f"Əl ilə: python -m venv {VENV_DIR}")
            sys.exit(1)
        if not run([sys.executable, "-m", "venv", str(VENV_DIR)]):
            sys.exit(1)
    info(".venv-ə keçilir...")
    _reexec(str(py))


# ── pip paketləri ────────────────────────────────────────────
def parse_requirements() -> list[str]:
    if not REQ_FILE.exists():
        return []
    out = []
    for raw in REQ_FILE.read_text(encoding="utf-8").splitlines():
        line = re.split(r"\s+#", raw, 1)[0].strip()
        if line and not line.startswith(("#", "-")):
            out.append(line)
    return out


def missing_python() -> list[str]:
    from importlib import metadata
    try:
        from packaging.requirements import Requirement
    except ImportError:
        Requirement = None  # type: ignore[assignment]

    missing = []
    for line in parse_requirements():
        spec = None
        if Requirement is not None:
            req = Requirement(line)
            if req.marker and not req.marker.evaluate():
                continue
            name, spec = req.name, req.specifier
        else:
            name = re.split(r"[\s\[<>=!~;]", line, 1)[0]
        try:
            ver = metadata.version(name)
        except metadata.PackageNotFoundError:
            missing.append(f"{line}  (yoxdur)")
            continue
        if spec is not None and not spec.contains(ver, prereleases=True):
            missing.append(f"{line}  (quraşdırılıb: {ver})")
    return missing


def _check_python() -> None:
    miss = missing_python()
    if not miss:
        return
    error("Python paketləri çatışmır / versiya uyğun deyil:")
    for m in miss:
        print("   •", m)
    cmd = [sys.executable, "-m", "pip", "install", "-r", str(REQ_FILE)]
    if not ask("pip ilə indi quraşdırılsın?"):
        error("Əl ilə: " + " ".join(cmd))
        sys.exit(1)
    if not run(cmd):
        sys.exit(1)
    info(color("Quraşdırıldı, bot yenidən başladılır...", "green"))
    _reexec(sys.executable)


# ── giriş nöqtəsi ────────────────────────────────────────────
def ensure_dependencies() -> None:
    if os.environ.get("DLM_SKIP_DEPS") == "1":
        return
    if sys.version_info < (3, 10):
        error(f"Python 3.10+ lazımdır, səndə {sys.version.split()[0]}")
        sys.exit(1)
    _check_system()
    _ensure_venv()
    _check_python()
    info(color("Bütün asılılıqlar yerindədir ✓", "green"))
