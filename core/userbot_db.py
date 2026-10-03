"""
🗄 Userbot bazası — core/userbot_db.py

Userbot-un (1️⃣ əsas hesab) öz ayrıca SQLite bazası: data/userbot.db (USERBOT_DB_PATH ilə dəyişir).
Botun əsas bazasından (data/bot.db) tam ayrıdır — userbot pluginləri nə qədər yazsa da botun bazasını
şişirtmir / kilidləmir, ayrıca yedəklənir, ayrıca silinə bilər.

Cədvəllər:
  kv       (ns, key)          → value          — sadə açar/dəyər (ayarlar, kiçik vəziyyət)
  records  (ns, chat_id, key) → data (JSON)    — çat üzrə strukturlu qeydlər (filtrlər, qeydlər, mute...)
  log      (id)               → ns, chat_id, user_id, data, ts — hadisə jurnalı (avtomatik kəsilir)

Userbot pluginlərində:
  ub.db.kv_get("afk", "text", "")           ub.db.kv_set("afk", "text", "...")
  ub.db.rec_put("notes", chat_id, "qayda", {"text": "..."})
  ub.db.rec_get("notes", chat_id, "qayda")  ub.db.rec_list("notes", chat_id)   ub.db.rec_del(...)
  ub.db.log_add("deleted", chat_id, user_id, {"text": "..."})   ub.db.log_list("deleted", chat_id, 20)

Uyğunluq: köhnə pluginlər `get_db().get_setting("userbot:...")` yazırdısa (core.userbot_api.get_db),
"userbot:" ilə başlayan açarlar avtomatik bu bazaya yönəlir. İlk açılışda əsas bazadakı bütün "userbot:*"
ayarları bura köçürülür (əsas bazada yedək kimi qalır).
"""
import contextlib
import json
import logging
import os
import sqlite3
import threading
import time

logger = logging.getLogger(__name__)

DB_PATH = os.getenv("USERBOT_DB_PATH", os.path.join("data", "userbot.db"))
LOG_KEEP = 20000                 # jurnalda hər ns üçün ən çox bu qədər sətir
COMPAT_PREFIX = "userbot:"

SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (
    ns      TEXT NOT NULL,
    key     TEXT NOT NULL,
    value   TEXT,
    updated INTEGER NOT NULL,
    PRIMARY KEY (ns, key)
);
CREATE TABLE IF NOT EXISTS records (
    ns      TEXT NOT NULL,
    chat_id INTEGER NOT NULL,
    key     TEXT NOT NULL,
    data    TEXT NOT NULL,
    created INTEGER NOT NULL,
    updated INTEGER NOT NULL,
    PRIMARY KEY (ns, chat_id, key)
);
CREATE INDEX IF NOT EXISTS idx_records_ns ON records(ns, updated);
CREATE TABLE IF NOT EXISTS log (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ns      TEXT NOT NULL,
    chat_id INTEGER,
    user_id INTEGER,
    data    TEXT,
    ts      INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_log_ns ON log(ns, chat_id, ts);
"""


def _dump(v):
    return json.dumps(v, ensure_ascii=False, separators=(",", ":"), default=str)


def _load(s, default=None):
    if s is None:
        return default
    try:
        return json.loads(s)
    except ValueError:
        return default


class UserbotDB:
    def __init__(self, path: str = DB_PATH):
        self.path = path
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False, timeout=30)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.executescript(SCHEMA)
            self._conn.commit()
        with contextlib.suppress(OSError):
            os.chmod(path, 0o600)
        self._log_adds = 0
        logger.info(f"🗄 Userbot bazası: {os.path.abspath(path)}")

    # ── kv ──
    def kv_get(self, ns: str, key: str, default=None):
        with self._lock:
            row = self._conn.execute("SELECT value FROM kv WHERE ns=? AND key=?", (ns, key)).fetchone()
        return default if row is None else _load(row[0], default)

    def kv_set(self, ns: str, key: str, value):
        with self._lock:
            self._conn.execute("INSERT INTO kv VALUES (?,?,?,?) ON CONFLICT(ns, key) DO UPDATE SET "
                               "value=excluded.value, updated=excluded.updated",
                               (ns, key, _dump(value), int(time.time())))
            self._conn.commit()

    def kv_del(self, ns: str, key: str) -> bool:
        with self._lock:
            n = self._conn.execute("DELETE FROM kv WHERE ns=? AND key=?", (ns, key)).rowcount
            self._conn.commit()
        return n > 0

    def kv_all(self, ns: str) -> dict:
        with self._lock:
            rows = self._conn.execute("SELECT key, value FROM kv WHERE ns=?", (ns,)).fetchall()
        return {r[0]: _load(r[1]) for r in rows}

    # ── records ──
    def rec_put(self, ns: str, chat_id: int, key: str, data):
        now = int(time.time())
        with self._lock:
            self._conn.execute("INSERT INTO records VALUES (?,?,?,?,?,?) ON CONFLICT(ns, chat_id, key) DO UPDATE SET "
                               "data=excluded.data, updated=excluded.updated",
                               (ns, int(chat_id), str(key), _dump(data), now, now))
            self._conn.commit()

    def rec_get(self, ns: str, chat_id: int, key: str, default=None):
        with self._lock:
            row = self._conn.execute("SELECT data FROM records WHERE ns=? AND chat_id=? AND key=?",
                                     (ns, int(chat_id), str(key))).fetchone()
        return default if row is None else _load(row[0], default)

    def rec_del(self, ns: str, chat_id: int, key: str = None) -> int:
        """key=None → həmin çatın bu ns-dəki bütün qeydləri."""
        with self._lock:
            if key is None:
                n = self._conn.execute("DELETE FROM records WHERE ns=? AND chat_id=?", (ns, int(chat_id))).rowcount
            else:
                n = self._conn.execute("DELETE FROM records WHERE ns=? AND chat_id=? AND key=?",
                                       (ns, int(chat_id), str(key))).rowcount
            self._conn.commit()
        return n

    def rec_list(self, ns: str, chat_id: int = None) -> list:
        """[(chat_id, key, data)] — əlavə olunma sırası ilə."""
        with self._lock:
            if chat_id is None:
                rows = self._conn.execute("SELECT chat_id, key, data FROM records WHERE ns=? ORDER BY rowid",
                                          (ns,)).fetchall()
            else:
                rows = self._conn.execute("SELECT chat_id, key, data FROM records WHERE ns=? AND chat_id=? "
                                          "ORDER BY rowid", (ns, int(chat_id))).fetchall()
        return [(r[0], r[1], _load(r[2])) for r in rows]

    def rec_replace(self, ns: str, items):
        """ns-in bütün qeydlərini atomik əvəz edir: items = [(chat_id, key, data)]."""
        now = int(time.time())
        with self._lock:
            old = {(r[0], r[1]): r[2] for r in self._conn.execute(
                "SELECT chat_id, key, created FROM records WHERE ns=?", (ns,)).fetchall()}
            self._conn.execute("DELETE FROM records WHERE ns=?", (ns,))
            self._conn.executemany("INSERT INTO records VALUES (?,?,?,?,?,?)",
                                   [(ns, int(c), str(k), _dump(d), old.get((int(c), str(k)), now), now)
                                    for c, k, d in items])
            self._conn.commit()

    # ── log ──
    def log_add(self, ns: str, chat_id=None, user_id=None, data=None):
        with self._lock:
            self._conn.execute("INSERT INTO log (ns, chat_id, user_id, data, ts) VALUES (?,?,?,?,?)",
                               (ns, chat_id, user_id, _dump(data), int(time.time())))
            self._log_adds += 1
            if self._log_adds % 500 == 0:
                self._conn.execute("DELETE FROM log WHERE ns=? AND id <= (SELECT id FROM log WHERE ns=? "
                                   "ORDER BY id DESC LIMIT 1 OFFSET ?)", (ns, ns, LOG_KEEP))
            self._conn.commit()

    def log_list(self, ns: str, chat_id=None, limit: int = 50) -> list:
        with self._lock:
            if chat_id is None:
                rows = self._conn.execute("SELECT chat_id, user_id, data, ts FROM log WHERE ns=? "
                                          "ORDER BY id DESC LIMIT ?", (ns, limit)).fetchall()
            else:
                rows = self._conn.execute("SELECT chat_id, user_id, data, ts FROM log WHERE ns=? AND chat_id=? "
                                          "ORDER BY id DESC LIMIT ?", (ns, chat_id, limit)).fetchall()
        return [{"chat_id": r[0], "user_id": r[1], "data": _load(r[2]), "ts": r[3]} for r in rows]

    def log_clear(self, ns: str = None) -> int:
        with self._lock:
            n = (self._conn.execute("DELETE FROM log WHERE ns=?", (ns,)) if ns
                 else self._conn.execute("DELETE FROM log")).rowcount
            self._conn.commit()
        return n

    # ── köhnə get_setting / set_setting uyğunluğu ("userbot:*" açarları) ──
    def get_setting(self, key, default=None):
        v = self.kv_get("settings", key, None)
        return default if v is None else v

    def set_setting(self, key, value):
        if value is None:
            self.kv_del("settings", key)
        else:
            self.kv_set("settings", key, str(value))

    # ── idarə ──
    def stats(self) -> dict:
        with self._lock:
            kv = self._conn.execute("SELECT ns, COUNT(*) FROM kv GROUP BY ns").fetchall()
            rec = self._conn.execute("SELECT ns, COUNT(*), COUNT(DISTINCT chat_id) FROM records GROUP BY ns").fetchall()
            lg = self._conn.execute("SELECT ns, COUNT(*) FROM log GROUP BY ns").fetchall()
        size = 0
        for suffix in ("", "-wal", "-shm"):
            with contextlib.suppress(OSError):
                size += os.path.getsize(self.path + suffix)
        return {"path": os.path.abspath(self.path), "size": size,
                "kv": {r[0]: r[1] for r in kv},
                "records": {r[0]: (r[1], r[2]) for r in rec},
                "log": {r[0]: r[1] for r in lg}}

    def backup(self, dst: str) -> str:
        """Canlı bazanın ardıcıl surəti (sqlite backup API — yazı gedərkən də təhlükəsiz)."""
        with self._lock:
            out = sqlite3.connect(dst)
            try:
                self._conn.backup(out)
            finally:
                out.close()
        return dst

    def vacuum(self) -> int:
        before = self.stats()["size"]
        with self._lock:
            self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            self._conn.execute("VACUUM")
        return before - self.stats()["size"]

    def migrate_from(self, main_db) -> int:
        """Əsas bazadakı "userbot:*" ayarlarını bir dəfə bura köçürür (əsas bazada yedək kimi qalır)."""
        if self.kv_get("meta", "migrated_v1"):
            return 0
        n = 0
        try:
            items = dict(getattr(main_db, "_settings", {}) or {})
        except Exception:
            items = {}
        for k, v in items.items():
            if isinstance(k, str) and k.startswith(COMPAT_PREFIX) and v is not None \
                    and self.kv_get("settings", k) is None:
                self.kv_set("settings", k, str(v))
                n += 1
        self.kv_set("meta", "migrated_v1", int(time.time()))
        if n:
            logger.info(f"🗄 Userbot bazası: əsas bazadan {n} ayar köçürüldü")
        return n


class CompatDB:
    """core.userbot_api.get_db() — "userbot:*" açarları userbot bazasına, qalanı botun əsas bazasına."""

    def __init__(self, udb: UserbotDB, main_getter):
        self._udb, self._main = udb, main_getter

    def get_setting(self, key, default=None):
        if isinstance(key, str) and key.startswith(COMPAT_PREFIX):
            return self._udb.get_setting(key, default)
        return self._main().get_setting(key, default)

    def set_setting(self, key, value):
        if isinstance(key, str) and key.startswith(COMPAT_PREFIX):
            return self._udb.set_setting(key, value)
        return self._main().set_setting(key, value)

    def __getattr__(self, name):
        return getattr(self._main(), name)


_udb = None
_compat = None
_init_lock = threading.Lock()


def get_udb() -> UserbotDB:
    global _udb
    if _udb is None:
        with _init_lock:
            if _udb is None:
                db = UserbotDB()
                try:
                    from core.database import get_db
                    db.migrate_from(get_db())
                except Exception as e:
                    logger.warning(f"🗄 Köçürmə alınmadı: {e}")
                _udb = db
    return _udb


def get_compat_db() -> CompatDB:
    global _compat
    if _compat is None:
        from core.database import get_db
        _compat = CompatDB(get_udb(), get_db)
    return _compat
