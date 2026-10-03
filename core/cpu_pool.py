"""
⚙️ Performans — core/cpu_pool.py

Məqsəd: bot heç vaxt donmasın və bütün CPU nüvələrindən istifadə etsin.

1. 🧠 Proses hovuzları — yt-dlp (yükləmə, axtarış, playlist, Mix) və YT Music axtarışı ayrıca Python
   proseslərində (core/ytdl_worker.py) işləyir: hər proses öz nüvəsində, öz GIL-i ilə. Əsas proses yalnız
   Telegram ilə danışır → GIL-i yt-dlp tutmur, bot donmur.
     👤 İstifadəçi zolağı — axtarış + istifadəçilərin yükləmələri (həmişə ayrıca, depo onları gözlətmir)
     📦 Depo zolağı      — 📦 doldurucunun yükləmələri (UPLOAD_CTX ilə tanınır)
   Proseslər tənbəl yaranır, N tapşırıqdan sonra yenilənir (yaddaş sızmasın), çökəni avtomatik dəyişilir.
   Nəsə alınmasa tapşırıq thread-də işləyir — heç nə itmir.
2. 🧵 Thread hovuzu — asyncio.to_thread (baza, fayl, Spotify) üçün ölçü.
3. 🫀 Watchdog — event loop-un gecikməsini ölçür; loop 1 san.-dən çox donanda əsas thread-in stack-ini tutub
   harada ilişdiyini yazır (/menu → ⚙️ Performans).
4. 🐢 Prioritet — işçi proseslər və ffmpeg `nice` ilə işləyir, bot həmişə üstündür.
5. 🚀 Avto-miqyas — hər 3 san. CPU / RAM ölçülür (SysMon). Növbədə tapşırıq varsa və CPU hədəfdən
   aşağıdırsa zolaq böyüdülür (maks. 👤/📦 dəyərinə qədər), CPU hədəfi aşanda və ya RAM ehtiyatı
   azalanda kiçildilir (əvvəl 📦 depo). 2 dəq. boş qalan artıq proseslər bağlanır (RAM qayıdır).
   👤 / 📦 düymələri avto-miqyas açıqkən MAKSİMUM rolunu oynayır.

Ayarlar bazada (perf:*) — /menu → 🖥 Sistem → ⚙️ Performans.
"""
import asyncio
import itertools
import logging
import os
import pickle
import struct
import sys
import tempfile
import threading
import time
import traceback
from collections import deque
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)

CORES = os.cpu_count() or 1
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))     # layihə kökü (core/-un valideyni)
CANCEL_DIR = os.path.join(tempfile.gettempdir(), "dllmaster_cancel")

MODES = {"process": "🧠 Proses (bütün nüvələr)", "thread": "🧵 Thread (tək nüvə)"}
S_MODE, S_USER, S_DEPO, S_THREADS, S_NICE, S_WATCH = (
    "perf:mode", "perf:user_procs", "perf:depo_procs", "perf:threads", "perf:nice", "perf:watchdog")
USER_STEPS = [1, 2, 3, 4, 6, 8, 12, 16]
DEPO_STEPS = [1, 2, 4, 6, 8, 12, 16, 24, 32, 48, 64]
THREAD_STEPS = [16, 32, 48, 64, 96, 128, 192, 256, 384, 512]
NICE_STEPS = [0, 2, 5, 10, 15, 19]
RECYCLE_AFTER = 40              # proses bu qədər tapşırıqdan sonra yenilənir
EXTRACT_TIMEOUT = 180           # axtarış / playlist (san.)
DOWNLOAD_TIMEOUT = 1200         # bir yükləmə (san.)
CANCEL_KILL_AFTER = 15          # ⏹ basılıb, proses bu qədər vaxtda dayanmasa — öldürülür
FREEZE_AT = 1.0                 # watchdog: loop bu qədər cavab verməsə — donma sayılır (san.)

S_SCALE, S_TARGET, S_RESERVE = "perf:autoscale", "perf:cpu_target", "perf:ram_reserve"
TARGET_STEPS = [50, 60, 70, 80, 85, 90, 95, 100]
RESERVE_STEPS = [256, 512, 768, 1024, 1536, 2048, 4096, 8192]
SCALE_EVERY = 3.0               # ölçmə / miqyas intervalı (san.)
IDLE_KEEP = 120                 # bu qədər boş qalan artıq proses bağlanır (san.)
PROC_MB = 75                    # bir işçi prosesin təxmini RAM-ı
LAG_LIMIT = 0.25                # avto-miqyas: orta loop gecikməsi bundan çoxdursa böyütmə yox, kiçiltmə


# ───────────────────────── ayarlar ─────────────────────────
def _db():
    from core.database import get_db
    return get_db()


def _get(key, default=None):
    try:
        v = _db().get_setting(key)
        return default if v in (None, "") else v
    except Exception:
        return default


def _set(key, value):
    _db().set_setting(key, str(value))


def _int(key, default, lo, hi):
    try:
        return max(lo, min(hi, int(_get(key, default))))
    except (TypeError, ValueError):
        return default


def mode() -> str:
    m = _get(S_MODE, "process" if CORES > 1 else "thread")
    return m if m in MODES else "process"


def user_procs() -> int:
    return _int(S_USER, max(2, min(4, CORES)), 1, USER_STEPS[-1])


def depo_procs() -> int:
    return _int(S_DEPO, max(2, CORES * 2), 1, DEPO_STEPS[-1])


def threads() -> int:
    return _int(S_THREADS, max(64, CORES * 8), THREAD_STEPS[0], THREAD_STEPS[-1])


def nice() -> int:
    return _int(S_NICE, 5, 0, 19)


def watchdog_on() -> bool:
    return _get(S_WATCH, "on") != "off"


def _step(cur, steps, direction):
    if direction > 0:
        return next((x for x in steps if x > cur), steps[-1])
    return next((x for x in reversed(steps) if x < cur), steps[0])


def set_mode(m: str):
    _set(S_MODE, m)
    if m == "thread":
        asyncio.ensure_future(shutdown_pools())


def step_user(d):
    v = _step(user_procs(), USER_STEPS, d)
    _set(S_USER, v)
    LANES["user"].set_max(v)
    return v


def step_depo(d):
    v = _step(depo_procs(), DEPO_STEPS, d)
    _set(S_DEPO, v)
    LANES["depo"].set_max(v)
    return v


def step_threads(d):
    v = _step(threads(), THREAD_STEPS, d)
    _set(S_THREADS, v)
    apply_threads(force=True)
    return v


def step_nice(d):
    v = _step(nice(), NICE_STEPS, d)
    _set(S_NICE, v)
    return v                    # yeni proseslərə tətbiq olunur (♻️ ilə hamısına)


def autoscale_on() -> bool:
    return _get(S_SCALE, "on") != "off"


def cpu_target() -> int:
    return _int(S_TARGET, 90, 10, 100)


def ram_reserve() -> int:
    return _int(S_RESERVE, 512, 0, 1 << 20)


def toggle_autoscale() -> bool:
    on = not autoscale_on()
    _set(S_SCALE, "on" if on else "off")
    for pool in LANES.values():
        pool.set_max(pool.max_size)          # rejimə görə effektiv ölçünü yenidən qurur
    return on


def step_target(d):
    v = _step(cpu_target(), TARGET_STEPS, d)
    _set(S_TARGET, v)
    return v


def step_reserve(d):
    v = _step(ram_reserve(), RESERVE_STEPS, d)
    _set(S_RESERVE, v)
    return v


def toggle_watchdog() -> bool:
    on = not watchdog_on()
    _set(S_WATCH, "on" if on else "off")
    WATCHDOG.enabled = on
    return on


# ───────────────────────── 🧠 proses hovuzu ─────────────────────────
class WorkerError(Exception):
    """İşçi prosesdə yt-dlp xətası (mətn olduğu kimi — 403 və s. yoxlamaları işləyir)."""


class WorkerCrashed(Exception):
    pass


class _Worker:
    def __init__(self, proc):
        self.proc = proc
        self.tasks = 0
        self.busy_since = None
        self.idle_since = None

    @property
    def alive(self) -> bool:
        return self.proc.returncode is None

    async def _recv(self):
        size = struct.unpack(">I", await self.proc.stdout.readexactly(4))[0]
        return pickle.loads(await self.proc.stdout.readexactly(size))

    def kill(self):
        if self.alive:
            try:
                self.proc.kill()
            except ProcessLookupError:
                pass


class ProcPool:
    def __init__(self, name: str, label: str, size: int):
        self.name, self.label, self.size = name, label, size
        self.max_size = size                      # istifadəçinin qoyduğu (avto-miqyasda — maksimum)
        self.workers = []
        self.idle = deque()
        self.waiters = deque()
        self._ids = itertools.count(1)
        self.stats = {"done": 0, "err": 0, "crash": 0, "fallback": 0, "spawned": 0, "secs": 0.0}
        self.waiting = 0

    @property
    def running(self) -> int:
        return sum(1 for w in self.workers if getattr(w, "busy_since", None))

    async def _spawn(self) -> _Worker:
        env = dict(os.environ)
        env["PYTHONPATH"] = ROOT + os.pathsep + env.get("PYTHONPATH", "")
        env["PYTHONUNBUFFERED"] = "1"
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "core.ytdl_worker", str(nice()),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=None,
            cwd=os.getcwd(), env=env, limit=64 * 1024 * 1024)
        w = _Worker(proc)
        try:
            hello = await asyncio.wait_for(w._recv(), 60)
            if hello[2] != "ready":
                raise RuntimeError(hello)
        except Exception as e:
            w.kill()
            raise WorkerCrashed(f"işçi proses başlamadı: {e}")
        self.stats["spawned"] += 1
        return w

    async def _acquire(self) -> _Worker:
        while True:
            while self.idle:
                w = self.idle.popleft()
                if w.alive:
                    return w
                self._forget(w)
            if len(self.workers) < self.size:
                placeholder = object()
                self.workers.append(placeholder)          # yer tutulur (paralel spawn həddi aşmasın)
                try:
                    w = await self._spawn()
                except BaseException:
                    self.workers.remove(placeholder)
                    raise
                self.workers[self.workers.index(placeholder)] = w
                return w
            fut = asyncio.get_running_loop().create_future()
            self.waiters.append(fut)
            self.waiting += 1
            try:
                w = await fut
            except asyncio.CancelledError:
                if fut.done() and not fut.cancelled() and fut.result() is not None:
                    self._release(fut.result(), True)        # verilmiş prosesi geri qaytar
                raise
            finally:
                self.waiting -= 1
            if w is not None and w.alive:
                return w

    def _forget(self, w):
        if w in self.workers:
            self.workers.remove(w)

    def _release(self, w: _Worker, healthy: bool):
        w.busy_since = None
        if not healthy or not w.alive or w.tasks >= RECYCLE_AFTER or len(self.workers) > self.size:
            w.kill()
            self._forget(w)
            w = None                       # gözləyən varsa — o özü yenisini yaradacaq
        while self.waiters:
            fut = self.waiters.popleft()
            if not fut.done():
                fut.set_result(w)
                return
        if w is not None:
            w.idle_since = time.monotonic()
            self.idle.append(w)

    # ── 🚀 avto-miqyas ──
    def set_max(self, v: int):
        self.max_size = v
        if autoscale_on() and mode() == "process":
            alive = sum(1 for w in self.workers if isinstance(w, _Worker))
            # başlanğıc: nüvə sayı (və ya hazırda açıq proses sayı) — sonra yükə görə böyüyür / kiçilir
            self.size = max(1, min(v, max(alive, CORES)))
        else:
            self.size = v
        self.kick()

    def kick(self):
        """Ölçü böyüyəndə növbədəkiləri oyadır — onlar yeni proses açır."""
        free = self.size - len(self.workers)
        while free > 0 and self.waiters:
            fut = self.waiters.popleft()
            if not fut.done():
                fut.set_result(None)
                free -= 1

    def reap_idle(self, keep: int = 1) -> int:
        """IDLE_KEEP-dən çox boş qalan artıq prosesləri bağlayır (ən azı `keep` boş qalır)."""
        now = time.monotonic()
        idle = [w for w in self.idle if w.alive]
        old = [w for w in idle if w.idle_since and now - w.idle_since > IDLE_KEEP]
        victims = old[:max(0, len(idle) - keep)]
        for w in victims:
            try:
                self.idle.remove(w)
            except ValueError:
                pass
            w.kill()
            self._forget(w)
        return len(victims)

    async def call(self, name: str, args: tuple, timeout: float, cancel_event=None, cancel_path=None):
        w = await self._acquire()
        healthy = False
        recv = None
        t0 = time.monotonic()
        w.busy_since, w.tasks = time.time(), w.tasks + 1
        try:
            tid = next(self._ids)
            data = pickle.dumps((tid, name, args), protocol=pickle.HIGHEST_PROTOCOL)
            w.proc.stdin.write(struct.pack(">I", len(data)) + data)
            await w.proc.stdin.drain()
            recv = asyncio.ensure_future(w._recv())
            deadline, cancel_seen = t0 + timeout, None
            while True:
                done, _ = await asyncio.wait({recv}, timeout=0.5)
                if done:
                    break
                now = time.monotonic()
                if cancel_event is not None and cancel_event.is_set():
                    if cancel_path and not os.path.exists(cancel_path):
                        open(cancel_path, "w").close()       # proses növbəti hissədə özü dayanır
                    cancel_seen = cancel_seen or now
                    if now - cancel_seen > CANCEL_KILL_AFTER:
                        recv.cancel()
                        from yt_dlp.utils import DownloadCancelled
                        raise DownloadCancelled("İstifadəçi yükləməni dayandırdı")
                if now > deadline:
                    recv.cancel()
                    raise WorkerError(f"vaxt bitdi ({timeout:.0f} san.)")
            _, status, payload = recv.result()
            healthy = True
            if status == "ok":
                self.stats["done"] += 1
                return payload
            if status == "cancel":
                from yt_dlp.utils import DownloadCancelled
                raise DownloadCancelled(payload)
            self.stats["err"] += 1
            raise WorkerError(payload)
        except (asyncio.IncompleteReadError, BrokenPipeError, ConnectionResetError, EOFError) as e:
            self.stats["crash"] += 1
            raise WorkerCrashed(f"işçi proses çökdü: {type(e).__name__}")
        finally:
            if recv is not None and not recv.done():
                recv.cancel()
            self.stats["secs"] += time.monotonic() - t0
            # ləğv / timeout / çöküş — prosesin vəziyyəti məlum deyil → öldürülür, yerinə yenisi gəlir
            self._release(w, healthy)

    async def shutdown(self):
        for w in list(self.workers):
            if isinstance(w, _Worker):
                w.kill()
        self.workers.clear()
        self.idle.clear()
        for fut in self.waiters:
            if not fut.done():
                fut.set_result(None)
        self.waiters.clear()

    def snapshot(self) -> dict:
        alive = [w for w in self.workers if isinstance(w, _Worker) and w.alive]
        return {"size": self.size, "max": self.max_size, "alive": len(alive), "running": self.running,
                "waiting": self.waiting, "pids": [w.proc.pid for w in alive], **self.stats}


LANES = {
    "user": ProcPool("user", "👤 İstifadəçi", user_procs()),
    "depo": ProcPool("depo", "📦 Depo", depo_procs()),
}


async def shutdown_pools():
    for p in LANES.values():
        await p.shutdown()


async def restart_pools():
    await shutdown_pools()
    LANES["user"].set_max(user_procs())
    LANES["depo"].set_max(depo_procs())


def _lane() -> str:
    try:
        from core.depo_helpers import UPLOAD_CTX
        return "depo" if UPLOAD_CTX.get() else "user"
    except Exception:
        return "user"


async def _run(name: str, args: tuple, timeout: float, cancel_event=None, cancel_path=None):
    from core import ytdl_worker
    if mode() == "process":
        pool = LANES[_lane()]
        try:
            return await pool.call(name, args, timeout, cancel_event, cancel_path)
        except WorkerCrashed as e:
            pool.stats["fallback"] += 1
            logger.warning(f"⚙️ {pool.label} prosesi: {e} — tapşırıq thread-də işlənir")
    return await asyncio.to_thread(ytdl_worker.TASKS[name], *args)


# ── ictimai API (youtube_handler istifadə edir) ──
async def ytdl_extract(url: str, opts: dict, timeout: float = EXTRACT_TIMEOUT):
    opts = {k: v for k, v in opts.items() if k not in ("progress_hooks", "logger")}
    return await _run("ytdl_extract", (url, opts), timeout)


async def ytdl_download(url: str, opts: dict, cancel_event=None, timeout: float = DOWNLOAD_TIMEOUT) -> dict:
    """→ {"path", "info"}. ⏹ — cancel_event; proses / thread fərqi yoxdur (fayl bayrağı ilə)."""
    opts = {k: v for k, v in opts.items() if k not in ("progress_hooks", "logger")}
    os.makedirs(CANCEL_DIR, exist_ok=True)
    cancel_path = os.path.join(CANCEL_DIR, f"{os.getpid()}_{next(_cancel_ids)}")
    watcher = None
    if cancel_event is not None:
        async def watch():                       # ⏹ → bayraq faylı (proses də, thread də onu yoxlayır)
            while True:
                if cancel_event.is_set():
                    open(cancel_path, "w").close()
                    return
                await asyncio.sleep(0.3)
        watcher = asyncio.ensure_future(watch())
    try:
        return await _run("ytdl_download", (url, opts, cancel_path), timeout, cancel_event, cancel_path)
    finally:
        if watcher:
            watcher.cancel()
        try:
            os.remove(cancel_path)
        except OSError:
            pass


_cancel_ids = itertools.count(1)


async def ytm_search(query: str, limit: int):
    return await _run("ytm_search", (query, limit), EXTRACT_TIMEOUT)


# ───────────────────────── 🧵 thread hovuzu ─────────────────────────
def apply_threads(force: bool = False):
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    want = threads()
    ex = getattr(loop, "_default_executor", None)
    cur = getattr(ex, "_max_workers", 0) if ex else min(32, CORES + 4)
    if cur == want or (cur > want and not force):
        return
    loop.set_default_executor(ThreadPoolExecutor(max_workers=want, thread_name_prefix="asyncio"))
    if ex is not None:
        ex.shutdown(wait=False)                  # köhnədəki işlər bitir, yenisi qəbul olunmur
    logger.info(f"🧵 Thread hovuzu: {cur} → {want}")


def thread_pool_size() -> int:
    try:
        ex = getattr(asyncio.get_running_loop(), "_default_executor", None)
        return getattr(ex, "_max_workers", 0) if ex else min(32, CORES + 4)
    except RuntimeError:
        return 0


def ffmpeg_prefix() -> list:
    """ffmpeg-i aşağı prioritetlə işə salmaq üçün (bot donmasın)."""
    n = nice()
    import shutil
    return ["nice", "-n", str(n)] if n > 0 and shutil.which("nice") else []


# ───────────────────────── 📊 sistem monitoru + 🚀 avto-miqyas ─────────────────────────
def _read(path) -> str:
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return ""


def _proc_ticks(pid) -> int:
    """utime + stime (taktla)."""
    raw = _read(f"/proc/{pid}/stat")
    if not raw:
        return 0
    try:
        f = raw.rsplit(")", 1)[1].split()
        return int(f[11]) + int(f[12])
    except (IndexError, ValueError):
        return 0


def _tree(pid, depth=0) -> list:
    """pid + bütün övladları (ffmpeg və s. — yt-dlp-nin açdığı proseslər də sayılsın)."""
    out = [pid]
    if depth > 4:
        return out
    try:
        tids = os.listdir(f"/proc/{pid}/task")
    except OSError:
        return out
    for tid in tids:
        for c in _read(f"/proc/{pid}/task/{tid}/children").split():
            if c.isdigit():
                out += _tree(int(c), depth + 1)
    return out


class SysMon:
    def __init__(self):
        self.hz = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100
        self.prev_cpu = None
        self.prev_ticks = {}
        self.prev_t = None
        self.cpu = 0.0                 # ümumi %
        self.cores = []                # hər nüvə %
        self.bot_cpu = 0.0             # botun özü (maşının %-i)
        self.work_cpu = 0.0            # işçi proseslər + övladları (maşının %-i)
        self.avail_mb = 0
        self.total_mb = 0
        self.hist = deque(maxlen=60)   # ~3 dəq.
        self.events = deque(maxlen=8)  # (ts, mətn) — miqyas dəyişiklikləri

    def sample(self):
        cur = []
        for line in _read("/proc/stat").splitlines():
            if not line.startswith("cpu"):
                break
            vals = [int(x) for x in line.split()[1:]]
            cur.append((vals[3] + (vals[4] if len(vals) > 4 else 0), sum(vals)))
        if self.prev_cpu and len(self.prev_cpu) == len(cur):
            pct = [100.0 * (1 - (i1 - i0) / (t1 - t0)) if t1 > t0 else 0.0
                   for (i0, t0), (i1, t1) in zip(self.prev_cpu, cur)]
            self.cpu, self.cores = pct[0], pct[1:]
            self.hist.append(self.cpu)
        self.prev_cpu = cur

        now = time.monotonic()
        groups = {"bot": [os.getpid()], "work": []}
        for pool in LANES.values():
            for w in pool.workers:
                if isinstance(w, _Worker) and w.alive:
                    groups["work"] += _tree(w.proc.pid)
        ticks = {}
        for g, pids in groups.items():
            ticks[g] = {p: _proc_ticks(p) for p in pids}
        if self.prev_t:
            dt = max(0.001, now - self.prev_t) * self.hz * CORES
            for g in ticks:
                used = sum(max(0, t - self.prev_ticks.get(p, t)) for p, t in ticks[g].items())
                setattr(self, "bot_cpu" if g == "bot" else "work_cpu", min(100.0, 100.0 * used / dt))
        self.prev_ticks = {p: t for g in ticks.values() for p, t in g.items()}
        self.prev_t = now

        mem = {}
        for line in _read("/proc/meminfo").splitlines():
            k, _, v = line.partition(":")
            if v.split():
                mem[k] = int(v.split()[0]) // 1024
        self.total_mb = mem.get("MemTotal", 0)
        self.avail_mb = mem.get("MemAvailable", mem.get("MemFree", 0))

    def event(self, text):
        self.events.append((time.time(), text))
        logger.info(f"🚀 Avto-miqyas: {text}")


MON = SysMon()


def _scale_step():
    cpu, avail = MON.cpu, MON.avail_mb
    target, reserve = cpu_target(), ram_reserve()
    user, depo = LANES["user"], LANES["depo"]
    # bot ləngiyirsə (event loop gecikməsi) — CPU boş olsa da böyütmə; darboğaz başqa yerdədir (baza kilidi, şəbəkə)
    w = WATCHDOG.snapshot() if WATCHDOG.enabled else {"lag_avg": 0.0, "now_frozen": False}
    laggy = w["now_frozen"] or w["lag_avg"] > LAG_LIMIT
    over = cpu > min(100, target + 5) or (avail and avail < reserve) or laggy
    room = cpu < target - 3 and (not avail or avail - PROC_MB > reserve) and w["lag_avg"] < LAG_LIMIT / 2
    step = max(1, CORES // 4)
    why = f"CPU {cpu:.0f}%, boş RAM {avail} MB" + (f", bot gecikməsi {w['lag_avg'] * 1000:.0f} ms" if laggy else "")
    if over:
        for pool in (depo, user):                  # əvvəl depo kiçilir — istifadəçi qorunur
            if pool.size > 1:
                old = pool.size
                pool.size -= 1
                MON.event(f"{pool.label} {old}→{pool.size} ({why})")
                break
    elif room:
        for pool in (user, depo):                  # əvvəl istifadəçi böyüyür
            if pool.waiting and pool.size < pool.max_size:
                old = pool.size
                ram_cap = (avail - reserve) // PROC_MB if avail else step
                grow = max(1, min(step, pool.waiting, ram_cap))
                pool.size = min(pool.max_size, pool.size + grow)
                pool.kick()
                MON.event(f"{pool.label} {old}→{pool.size} ({why}, növbə {pool.waiting})")
                break
    for pool in (user, depo):
        n = pool.reap_idle()
        if n:
            logger.info(f"🚀 {pool.label}: {n} boş proses bağlandı (RAM qaytarıldı)")


async def _scaler():
    while True:
        await asyncio.sleep(SCALE_EVERY)
        try:
            await asyncio.to_thread(MON.sample)
            if mode() == "process" and autoscale_on():
                _scale_step()
        except Exception as e:
            logger.debug(f"avto-miqyas: {e}")


_scaler_task = None


# ───────────────────────── 🫀 event loop watchdog ─────────────────────────
class LoopWatchdog:
    def __init__(self):
        self.enabled = True
        self.loop = None
        self.loop_tid = None
        self.beat = time.monotonic()
        self.lags = deque(maxlen=1500)         # (ts, gecikmə san.) — ~5 dəq.
        self.freezes = deque(maxlen=50)        # (ts, müddət, harada)
        self._cur = None                       # gedən donma: (başlanğıc, stack)
        self._task = None
        self._thread = None

    def start(self, loop):
        if self._task and not self._task.done():
            return
        self.loop = loop
        self.enabled = watchdog_on()
        self._task = loop.create_task(self._ticker())
        if not (self._thread and self._thread.is_alive()):
            self._thread = threading.Thread(target=self._watch, name="loop-watchdog", daemon=True)
            self._thread.start()

    async def _ticker(self):
        self.loop_tid = threading.get_ident()
        while True:
            t = time.monotonic()
            self.beat = t
            await asyncio.sleep(0.2)
            lag = max(0.0, time.monotonic() - t - 0.2)
            self.lags.append((time.time(), lag))
            if self._cur and lag < FREEZE_AT:
                self._finish()

    def _finish(self):
        start, where = self._cur
        dur = time.monotonic() - start
        self._cur = None
        self.freezes.append((time.time(), dur, where))
        if dur >= 2:
            logger.warning(f"🫀 Bot {dur:.1f} san. dondu — {where}")

    def _watch(self):
        while True:
            time.sleep(0.25)
            if not self.enabled or self.loop_tid is None:
                continue
            age = time.monotonic() - self.beat
            if age > FREEZE_AT + 0.2 and self._cur is None:
                self._cur = (self.beat, self._where())

    def _where(self) -> str:
        """Event loop thread-i hazırda harada ilişib — layihə fayllarından ən dərin sətir."""
        frame = sys._current_frames().get(self.loop_tid)
        if frame is None:
            return "?"
        stack = traceback.extract_stack(frame)
        own = [f for f in stack if ROOT in os.path.abspath(f.filename) and "cpu_pool" not in f.filename
               and "site-packages" not in f.filename]
        f = (own or stack)[-1]
        rel = os.path.relpath(f.filename, ROOT) if ROOT in os.path.abspath(f.filename) else os.path.basename(f.filename)
        deeper = stack[-1]
        extra = "" if deeper is f else f" → {os.path.basename(deeper.filename)}:{deeper.lineno} {deeper.name}"
        return f"{rel}:{f.lineno} {f.name}(){extra}"

    def snapshot(self) -> dict:
        now = time.time()
        recent = [l for t, l in self.lags if now - t <= 300]
        hour = [f for f in self.freezes if now - f[0] <= 3600]
        return {
            "lag": self.lags[-1][1] if self.lags else 0.0,
            "lag_avg": sum(recent) / len(recent) if recent else 0.0,
            "lag_max": max(recent, default=0.0),
            "freezes_hour": len(hour),
            "last": self.freezes[-1] if self.freezes else None,
            "now_frozen": bool(self._cur),
        }


WATCHDOG = LoopWatchdog()


def health() -> str:
    """'' — hər şey qaydasındadır, əks halda qısa xəbərdarlıq (menyu düyməsi üçün)."""
    s = WATCHDOG.snapshot()
    if s["freezes_hour"]:
        return f"{s['freezes_hour']} donma/saat"
    if s["lag_max"] > 0.5:
        return f"gecikmə {s['lag_max'] * 1000:.0f} ms"
    return ""


# ───────────────────────── başlatma ─────────────────────────
_started = False


def init(context=None):
    """menu_plugin.setup çağırır: loop işləyirsə dərhal, yoxdursa dp.startup-da."""
    global _started

    def _go():
        global _started
        if _started:
            return
        loop = asyncio.get_running_loop()
        _started = True
        apply_threads()
        WATCHDOG.start(loop)
        global _scaler_task
        for pool, v in ((LANES["user"], user_procs()), (LANES["depo"], depo_procs())):
            pool.set_max(v)
        if _scaler_task is None or _scaler_task.done():
            _scaler_task = loop.create_task(_scaler())
        logger.info(f"⚙️ Performans: {MODES[mode()]} · {CORES} nüvə · 👤 {user_procs()} + 📦 {depo_procs()} proses"
                    f" · 🧵 {threads()} thread · nice +{nice()}"
                    + (f" · 🚀 avto-miqyas: CPU hədəfi {cpu_target()}%" if autoscale_on() else ""))

    try:
        asyncio.get_running_loop()
        _go()
    except RuntimeError:
        dp = getattr(context, "dp", None)
        if dp is not None:
            async def _startup():
                _go()
            dp.startup.register(_startup)
            dp.shutdown.register(shutdown_pools)
            return
    if context is not None and getattr(context, "dp", None) is not None \
            and not getattr(context, "_cpu_pool_shutdown", False):
        context._cpu_pool_shutdown = True
        context.dp.shutdown.register(shutdown_pools)
