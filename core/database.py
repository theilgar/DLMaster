"""
Bot verilənlər bazası (SQLite, əlavə kitabxana tələb etmir).

Fayl: core/database.py
Yol:  DB_PATH env dəyişəni (default: data/bot.db)

Cədvəllər:
  users      — botla şəxsi çatda əlaqə quran istifadəçilər
  downloads  — göndərilən hər mahnı (kim, nə, haradan, nə vaxt)
  chats      — botun olduğu qruplar və kanallar (broadcast üçün)
  settings   — bot ayarları (məs. creator-un caption seçimi)
  premium    — premium istifadəçilər (until NULL = ömürlük)
  payments   — Telegram Stars (⭐) ilə premium ödənişləri
  files      — kod fayllarının son vəziyyəti (yeniləmə bildirişi üçün)

İstifadə (istənilən plugin-dən):
    from core.database import log_download
    await log_download(user_id, title, url, "music")
"""
import asyncio
import logging
import os
import sqlite3
import threading
import time

logger = logging.getLogger(__name__)


class Database:
    def __init__(self, path: str):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id    INTEGER PRIMARY KEY,
                    username   TEXT,
                    first_name TEXT,
                    last_name  TEXT,
                    first_seen INTEGER NOT NULL,
                    last_seen  INTEGER NOT NULL,
                    blocked    INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS downloads (
                    id      INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    title   TEXT,
                    url     TEXT,
                    source  TEXT,
                    ts      INTEGER NOT NULL,
                    chat_id INTEGER
                );
                CREATE INDEX IF NOT EXISTS idx_downloads_ts ON downloads(ts);
                CREATE INDEX IF NOT EXISTS idx_downloads_user ON downloads(user_id, ts);
                CREATE TABLE IF NOT EXISTS payments (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id    INTEGER NOT NULL,
                    plan       TEXT NOT NULL,
                    days       INTEGER,
                    stars      INTEGER NOT NULL,
                    charge_id  TEXT UNIQUE,
                    ts         INTEGER NOT NULL,
                    refunded   INTEGER NOT NULL DEFAULT 0,
                    refund_ts  INTEGER
                );
                CREATE INDEX IF NOT EXISTS idx_payments_ts ON payments(ts);
                CREATE TABLE IF NOT EXISTS premium (
                    user_id    INTEGER PRIMARY KEY,
                    until      INTEGER,
                    granted_at INTEGER NOT NULL,
                    granted_by INTEGER
                );
                CREATE TABLE IF NOT EXISTS files (
                    path   TEXT PRIMARY KEY,
                    sha256 TEXT NOT NULL,
                    size   INTEGER NOT NULL,
                    lines  INTEGER NOT NULL,
                    mtime  INTEGER NOT NULL,
                    content TEXT
                );
                CREATE TABLE IF NOT EXISTS settings (
                    key   TEXT PRIMARY KEY,
                    value TEXT
                );
                CREATE TABLE IF NOT EXISTS chats (
                    chat_id    INTEGER PRIMARY KEY,
                    type       TEXT NOT NULL,
                    title      TEXT,
                    username   TEXT,
                    active     INTEGER NOT NULL DEFAULT 1,
                    first_seen INTEGER NOT NULL,
                    last_seen  INTEGER NOT NULL
                );
            """)
            self._conn.commit()
            # Köhnə bazalar üçün: files cədvəlinə content sütunu (diff üçün faylın məzmunu)
            cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(files)")}
            if "content" not in cols:
                self._conn.execute("ALTER TABLE files ADD COLUMN content TEXT")
                self._conn.commit()
            # Köhnə bazalar üçün: yükləmənin hansı çatda olduğu (qrup statistikası)
            cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(downloads)")}
            if "chat_id" not in cols:
                self._conn.execute("ALTER TABLE downloads ADD COLUMN chat_id INTEGER")
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_downloads_chat ON downloads(chat_id, ts)")
            self._conn.commit()
            self._settings = {
                r["key"]: r["value"] for r in self._conn.execute("SELECT key, value FROM settings")
            }

    # ───────────── ayarlar (yaddaşda keşlənir, hər oxunuşda bazaya getmir) ─────────────
    def get_setting(self, key, default=None):
        return self._settings.get(key, default)

    def set_setting(self, key, value):
        value = None if value is None else str(value)
        with self._lock:
            self._conn.execute(
                "INSERT INTO settings(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
            self._conn.commit()
            self._settings[key] = value

    # ───────────── istifadəçilər ─────────────
    def upsert_user(self, user_id, username=None, first_name=None, last_name=None):
        """İstifadəçi yazdı/düymə basdı → qeyd et (bloklamayıb deməkdir)."""
        now = int(time.time())
        with self._lock:
            self._conn.execute(
                """INSERT INTO users(user_id, username, first_name, last_name, first_seen, last_seen, blocked)
                   VALUES(?,?,?,?,?,?,0)
                   ON CONFLICT(user_id) DO UPDATE SET
                       username=excluded.username, first_name=excluded.first_name,
                       last_name=excluded.last_name, last_seen=excluded.last_seen, blocked=0""",
                (user_id, username, first_name, last_name, now, now),
            )
            self._conn.commit()

    def set_blocked(self, user_id, blocked: bool):
        with self._lock:
            self._conn.execute("UPDATE users SET blocked=? WHERE user_id=?", (1 if blocked else 0, user_id))
            self._conn.commit()

    def user_counts(self) -> dict:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS total, COALESCE(SUM(blocked),0) AS blocked FROM users"
            ).fetchone()
        return {"total": row["total"], "blocked": row["blocked"], "active": row["total"] - row["blocked"]}

    def users_page(self, offset: int, limit: int) -> list:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM users ORDER BY first_seen DESC, user_id DESC LIMIT ? OFFSET ?", (limit, offset)
            ).fetchall()
        return [dict(r) for r in rows]

    def all_users(self) -> list:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM users ORDER BY first_seen ASC, user_id ASC").fetchall()
        return [dict(r) for r in rows]

    def get_user(self, user_id):
        with self._lock:
            row = self._conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
        return dict(row) if row else None

    def find_user_by_username(self, username: str):
        username = (username or "").lstrip("@").strip()
        if not username:
            return None
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM users WHERE username = ? COLLATE NOCASE ORDER BY last_seen DESC LIMIT 1",
                (username,),
            ).fetchone()
        return dict(row) if row else None

    # ───────────── premium ─────────────
    _ACTIVE_PREMIUM = "(p.until IS NULL OR p.until > ?)"

    def get_premium(self, user_id):
        """Aktiv premium varsa {"until": ts | None(ömürlük)}, yoxdursa None."""
        with self._lock:
            row = self._conn.execute(
                "SELECT until FROM premium p WHERE user_id=? AND " + self._ACTIVE_PREMIUM,
                (user_id, int(time.time())),
            ).fetchone()
        return {"until": row["until"]} if row else None

    def is_premium(self, user_id) -> bool:
        return self.get_premium(user_id) is not None

    def grant_premium(self, user_id, days, granted_by=None):
        """days=None → ömürlük. Aktiv müddətli premium varsa üstünə əlavə olunur. Yeni until qaytarır."""
        now = int(time.time())
        current = self.get_premium(user_id)
        if days is None:
            until = None
        elif current and current["until"] is None:
            until = None                      # ömürlük onsuz da ömürlükdür
        else:
            start = max(now, current["until"]) if current else now
            until = start + int(days) * 86400
        with self._lock:
            self._conn.execute(
                """INSERT INTO premium(user_id, until, granted_at, granted_by) VALUES(?,?,?,?)
                   ON CONFLICT(user_id) DO UPDATE SET
                       until=excluded.until, granted_at=excluded.granted_at, granted_by=excluded.granted_by""",
                (user_id, until, now, granted_by),
            )
            self._conn.commit()
        return until

    def revoke_premium(self, user_id) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM premium WHERE user_id=?", (user_id,))
            self._conn.commit()
        return cur.rowcount > 0

    def shorten_premium(self, user_id, days):
        """Geri qaytarılan ödəniş üçün: müddətli premiumdan `days` gün çıxır (days=None → premium silinir)."""
        now = int(time.time())
        cur = self.get_premium(user_id)
        if not cur:
            return None
        if days is None:
            self.revoke_premium(user_id)
            return "revoked"
        if cur["until"] is None:
            return "lifetime"          # başqa yolla verilmiş ömürlük premium — toxunulmur
        until = cur["until"] - int(days) * 86400
        if until <= now:
            self.revoke_premium(user_id)
            return "revoked"
        with self._lock:
            self._conn.execute("UPDATE premium SET until=? WHERE user_id=?", (until, user_id))
            self._conn.commit()
        return until

    # ───────────── ⭐ Stars ödənişləri ─────────────
    def add_payment(self, user_id, plan, days, stars, charge_id) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO payments(user_id, plan, days, stars, charge_id, ts) VALUES(?,?,?,?,?,?)",
                (user_id, plan, days, stars, charge_id, int(time.time())),
            )
            self._conn.commit()
        return cur.lastrowid if cur.rowcount else 0

    def get_payment(self, payment_id: int):
        with self._lock:
            row = self._conn.execute(
                """SELECT p.*, u.username, u.first_name, u.last_name FROM payments p
                   LEFT JOIN users u ON u.user_id = p.user_id WHERE p.id=?""", (payment_id,)).fetchone()
        return dict(row) if row else None

    def mark_refunded(self, payment_id: int):
        with self._lock:
            self._conn.execute("UPDATE payments SET refunded=1, refund_ts=? WHERE id=?", (int(time.time()), payment_id))
            self._conn.commit()

    def payments_page(self, offset: int, limit: int):
        with self._lock:
            total = self._conn.execute("SELECT COUNT(*) FROM payments").fetchone()[0]
            rows = self._conn.execute(
                """SELECT p.*, u.username, u.first_name, u.last_name FROM payments p
                   LEFT JOIN users u ON u.user_id = p.user_id ORDER BY p.ts DESC LIMIT ? OFFSET ?""",
                (limit, offset)).fetchall()
        return [dict(r) for r in rows], total

    def payments_summary(self, now: int) -> dict:
        with self._lock:
            row = self._conn.execute(
                """SELECT COUNT(*) AS n, COALESCE(SUM(stars),0) AS stars,
                          COALESCE(SUM(CASE WHEN ts >= ? THEN stars END),0) AS stars_30d,
                          COALESCE(SUM(ts >= ?),0) AS n_30d, COUNT(DISTINCT user_id) AS buyers
                   FROM payments WHERE refunded=0""", (now - 30 * 86400, now - 30 * 86400)).fetchone()
            refunded = self._conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(stars),0) FROM payments WHERE refunded=1").fetchone()
        out = dict(row)
        out["refunded_n"], out["refunded_stars"] = refunded[0], refunded[1]
        return out

    def premium_count(self) -> int:
        with self._lock:
            return self._conn.execute(
                "SELECT COUNT(*) FROM premium p WHERE " + self._ACTIVE_PREMIUM, (int(time.time()),)
            ).fetchone()[0]

    def premium_page(self, offset: int, limit: int) -> list:
        with self._lock:
            rows = self._conn.execute(
                """SELECT p.user_id, p.until, p.granted_at, u.username, u.first_name, u.last_name
                   FROM premium p LEFT JOIN users u ON u.user_id = p.user_id
                   WHERE """ + self._ACTIVE_PREMIUM + """
                   ORDER BY p.granted_at DESC LIMIT ? OFFSET ?""",
                (int(time.time()), limit, offset),
            ).fetchall()
        return [dict(r) for r in rows]

    def premium_ids(self, user_ids) -> set:
        user_ids = list(user_ids)
        if not user_ids:
            return set()
        marks = ",".join("?" * len(user_ids))
        with self._lock:
            rows = self._conn.execute(
                f"SELECT user_id FROM premium p WHERE user_id IN ({marks}) AND " + self._ACTIVE_PREMIUM,
                (*user_ids, int(time.time())),
            ).fetchall()
        return {r[0] for r in rows}

    # ───────────── kod faylları (yeniləmə bildirişi) ─────────────
    def get_file_snapshot(self) -> dict:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM files").fetchall()
        return {r["path"]: dict(r) for r in rows}

    def save_file_snapshot(self, files: dict):
        """files: {path: {sha256, size, lines, mtime, content}} — köhnə snapshot tam əvəz olunur."""
        with self._lock:
            self._conn.execute("DELETE FROM files")
            self._conn.executemany(
                "INSERT INTO files(path, sha256, size, lines, mtime, content) VALUES(?,?,?,?,?,?)",
                [(p, f["sha256"], f["size"], f["lines"], f["mtime"], f.get("content"))
                 for p, f in files.items()],
            )
            self._conn.commit()

    # ───────────── qruplar / kanallar ─────────────
    def upsert_chat(self, chat_id, chat_type, title=None, username=None, active=True):
        now = int(time.time())
        with self._lock:
            self._conn.execute(
                """INSERT INTO chats(chat_id, type, title, username, active, first_seen, last_seen)
                   VALUES(?,?,?,?,?,?,?)
                   ON CONFLICT(chat_id) DO UPDATE SET
                       type=excluded.type, title=excluded.title, username=excluded.username,
                       active=excluded.active, last_seen=excluded.last_seen""",
                (chat_id, chat_type, title, username, 1 if active else 0, now, now),
            )
            self._conn.commit()

    def set_chat_active(self, chat_id, active: bool):
        with self._lock:
            self._conn.execute("UPDATE chats SET active=? WHERE chat_id=?", (1 if active else 0, chat_id))
            self._conn.commit()

    def migrate_chat(self, old_id, new_id):
        """Qrup supergroup-a çevriləndə ID dəyişir."""
        with self._lock:
            row = self._conn.execute("SELECT * FROM chats WHERE chat_id=?", (old_id,)).fetchone()
            self._conn.execute("UPDATE chats SET active=0 WHERE chat_id=?", (old_id,))
            self._conn.commit()
        if row:
            self.upsert_chat(new_id, "supergroup", row["title"], row["username"], True)

    def chat_counts(self) -> dict:
        with self._lock:
            row = self._conn.execute(
                """SELECT
                       COALESCE(SUM(type IN ('group','supergroup')), 0) AS groups,
                       COALESCE(SUM(type = 'channel'), 0)               AS channels
                   FROM chats WHERE active=1"""
            ).fetchone()
        return dict(row)

    def broadcast_targets(self, target: str) -> list:
        """[(növ, chat_id)] — target: users | groups | channels | all"""
        out = []
        with self._lock:
            if target in ("users", "all"):
                out += [("user", r[0]) for r in self._conn.execute(
                    "SELECT user_id FROM users WHERE blocked=0 ORDER BY user_id")]
            if target in ("groups", "all"):
                out += [("group", r[0]) for r in self._conn.execute(
                    "SELECT chat_id FROM chats WHERE active=1 AND type IN ('group','supergroup') ORDER BY chat_id")]
            if target in ("channels", "all"):
                out += [("channel", r[0]) for r in self._conn.execute(
                    "SELECT chat_id FROM chats WHERE active=1 AND type='channel' ORDER BY chat_id")]
        return out

    # ───────────── yükləmələr ─────────────
    def log_download(self, user_id, title=None, url=None, source=None, ts=None, chat_id=None):
        with self._lock:
            self._conn.execute(
                "INSERT INTO downloads(user_id, title, url, source, ts, chat_id) VALUES(?,?,?,?,?,?)",
                (user_id, title, url, source, int(ts or time.time()), chat_id),
            )
            self._conn.commit()

    # ───────────── qrup / kanal statistikası ─────────────
    _CHAT_FILTERS = {
        "groups":   "c.active = 1 AND c.type IN ('group','supergroup')",
        "channels": "c.active = 1 AND c.type = 'channel'",
        "left":     "c.active = 0",
    }
    _CHAT_SELECT = """
        SELECT c.*, COALESCE(d.n, 0) AS dl_total, COALESCE(d.m, 0) AS dl_30d, d.last_ts AS last_download
        FROM chats c
        LEFT JOIN (SELECT chat_id, COUNT(*) AS n, SUM(ts >= :month) AS m, MAX(ts) AS last_ts
                   FROM downloads WHERE chat_id IS NOT NULL GROUP BY chat_id) d ON d.chat_id = c.chat_id
    """

    def chats_filtered(self, flt: str, offset: int, limit: int, now: int):
        where = self._CHAT_FILTERS.get(flt, self._CHAT_FILTERS["groups"])
        params = {"month": now - 30 * 86400, "limit": limit, "offset": offset}
        with self._lock:
            total = self._conn.execute(f"SELECT COUNT(*) FROM chats c WHERE {where}").fetchone()[0]
            rows = self._conn.execute(
                f"{self._CHAT_SELECT} WHERE {where} ORDER BY dl_30d DESC, c.last_seen DESC LIMIT :limit OFFSET :offset",
                params).fetchall()
        return [dict(r) for r in rows], total

    def chat_filter_counts(self) -> dict:
        with self._lock:
            return {k: self._conn.execute(f"SELECT COUNT(*) FROM chats c WHERE {w}").fetchone()[0]
                    for k, w in self._CHAT_FILTERS.items()}

    def get_chat(self, chat_id: int):
        with self._lock:
            row = self._conn.execute("SELECT * FROM chats WHERE chat_id=?", (chat_id,)).fetchone()
        return dict(row) if row else None

    def update_chat_info(self, chat_id: int, title=None, username=None, member_count=None):
        """Canlı yoxlamadan sonra adı / username-i yeniləyir, üzv sayını saxlayır."""
        with self._lock:
            if title is not None:
                self._conn.execute("UPDATE chats SET title=?, username=? WHERE chat_id=?", (title, username, chat_id))
            self._conn.commit()
        if member_count is not None:
            self.set_setting(f"chat_members:{chat_id}", str(member_count))

    def chat_detail(self, chat_id: int, now: int) -> dict:
        with self._lock:
            c = self._conn
            dl = dict(c.execute(
                """SELECT COUNT(*) AS total, COALESCE(SUM(ts >= ?),0) AS week, COALESCE(SUM(ts >= ?),0) AS month,
                          COUNT(DISTINCT user_id) AS users, MIN(ts) AS first_ts, MAX(ts) AS last_ts
                   FROM downloads WHERE chat_id=?""", (now - 7 * 86400, now - 30 * 86400, chat_id)).fetchone())
            top_users = [dict(r) for r in c.execute(
                """SELECT d.user_id, COUNT(*) AS n, u.username, u.first_name, u.last_name
                   FROM downloads d LEFT JOIN users u ON u.user_id = d.user_id
                   WHERE d.chat_id=? GROUP BY d.user_id ORDER BY n DESC LIMIT 5""", (chat_id,))]
            recent = [dict(r) for r in c.execute(
                "SELECT title, url, user_id, ts FROM downloads WHERE chat_id=? ORDER BY ts DESC LIMIT 5", (chat_id,))]
        members = self.get_setting(f"chat_members:{chat_id}")
        return {"dl": dl, "top_users": top_users, "recent": recent,
                "members": int(members) if members and members.isdigit() else None}

    def top_chats(self, since: int, limit: int = 5) -> list:
        with self._lock:
            rows = self._conn.execute(
                """SELECT c.chat_id, c.title, c.type, c.username, COUNT(*) AS n, COUNT(DISTINCT d.user_id) AS users
                   FROM downloads d JOIN chats c ON c.chat_id = d.chat_id
                   WHERE d.ts >= ? AND c.type IN ('group','supergroup')
                   GROUP BY d.chat_id ORDER BY n DESC LIMIT ?""", (since, limit)).fetchall()
        return [dict(r) for r in rows]

    def group_download_count(self, since: int) -> int:
        with self._lock:
            return self._conn.execute(
                """SELECT COUNT(*) FROM downloads d JOIN chats c ON c.chat_id = d.chat_id
                   WHERE d.ts >= ? AND c.type IN ('group','supergroup')""", (since,)).fetchone()[0]

    # ───────────── detallı statistika ─────────────
    def stats_overview(self, day_start: int, now: int, tz_offset: int, days: int = 14) -> dict:
        """
        /menu → 📊 Statistika üçün bütün rəqəmlər bir yerdə.
        day_start — bu günün 00:00-ı (yerli vaxt), tz_offset — UTC-dən fərq (saniyə).
        """
        week, month = now - 7 * 86400, now - 30 * 86400
        chart_start = day_start - (days - 1) * 86400
        with self._lock:
            c = self._conn
            dl = dict(c.execute(
                """SELECT COALESCE(SUM(ts >= ?),0) AS today, COALESCE(SUM(ts >= ?),0) AS week,
                          COALESCE(SUM(ts >= ?),0) AS month, COALESCE(SUM(ts >= ? AND ts < ?),0) AS prev_week,
                          COUNT(*) AS total
                   FROM downloads""", (day_start, week, month, now - 14 * 86400, week)).fetchone())
            dl_users = dict(c.execute(
                """SELECT COUNT(DISTINCT CASE WHEN ts >= ? THEN user_id END) AS today,
                          COUNT(DISTINCT CASE WHEN ts >= ? THEN user_id END) AS week,
                          COUNT(DISTINCT CASE WHEN ts >= ? THEN user_id END) AS month
                   FROM downloads""", (day_start, week, month)).fetchone())
            users = dict(c.execute(
                """SELECT COUNT(*) AS total, COALESCE(SUM(blocked),0) AS blocked,
                          COALESCE(SUM(first_seen >= ?),0) AS new_today, COALESCE(SUM(first_seen >= ?),0) AS new_week,
                          COALESCE(SUM(first_seen >= ?),0) AS new_month, COALESCE(SUM(first_seen >= ? AND first_seen < ?),0) AS new_prev_week,
                          COALESCE(SUM(last_seen >= ?),0) AS active_today, COALESCE(SUM(last_seen >= ?),0) AS active_week,
                          COALESCE(SUM(last_seen >= ?),0) AS active_month
                   FROM users""",
                (day_start, week, month, now - 14 * 86400, week, day_start, week, month)).fetchone())
            sources = {r[0] or "digər": r[1] for r in c.execute(
                "SELECT source, COUNT(*) FROM downloads WHERE ts >= ? GROUP BY source ORDER BY 2 DESC", (month,))}
            daily_dl = {r[0]: r[1] for r in c.execute(
                "SELECT CAST((ts - ?) / 86400 AS INTEGER), COUNT(*) FROM downloads WHERE ts >= ? GROUP BY 1",
                (chart_start, chart_start))}
            daily_new = {r[0]: r[1] for r in c.execute(
                "SELECT CAST((first_seen - ?) / 86400 AS INTEGER), COUNT(*) FROM users WHERE first_seen >= ? GROUP BY 1",
                (chart_start, chart_start))}
            hours = {int(r[0]): r[1] for r in c.execute(
                "SELECT strftime('%H', ts + ?, 'unixepoch'), COUNT(*) FROM downloads WHERE ts >= ? GROUP BY 1",
                (tz_offset, month))}
            top_songs = [dict(r) for r in c.execute(
                """SELECT MAX(title) AS title, url, COUNT(*) AS n, COUNT(DISTINCT user_id) AS users
                   FROM downloads WHERE ts >= ? GROUP BY COALESCE(url, title) ORDER BY n DESC LIMIT 7""", (month,))]
            top_users = [dict(r) for r in c.execute(
                """SELECT d.user_id, COUNT(*) AS n, u.username, u.first_name, u.last_name
                   FROM downloads d LEFT JOIN users u ON u.user_id = d.user_id
                   WHERE d.ts >= ? AND d.user_id IS NOT NULL
                   GROUP BY d.user_id ORDER BY n DESC LIMIT 7""", (month,))]
        return {
            "dl": dl, "dl_users": dl_users, "users": users, "sources": sources,
            "daily_dl": [daily_dl.get(i, 0) for i in range(days)],
            "daily_new": [daily_new.get(i, 0) for i in range(days)],
            "chart_start": chart_start,
            "hours": [hours.get(h, 0) for h in range(24)],
            "top_songs": top_songs, "top_users": top_users,
        }

    def user_detail(self, user_id: int, now: int) -> dict:
        """Bir istifadəçi haqqında hər şey: profil, yükləmə sayları, mənbələr, son mahnılar."""
        with self._lock:
            c = self._conn
            row = c.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
            dl = dict(c.execute(
                """SELECT COUNT(*) AS total, COALESCE(SUM(ts >= ?),0) AS week, COALESCE(SUM(ts >= ?),0) AS month,
                          MIN(ts) AS first_ts, MAX(ts) AS last_ts
                   FROM downloads WHERE user_id=?""", (now - 7 * 86400, now - 30 * 86400, user_id)).fetchone())
            sources = {r[0] or "digər": r[1] for r in c.execute(
                "SELECT source, COUNT(*) FROM downloads WHERE user_id=? GROUP BY source ORDER BY 2 DESC", (user_id,))}
            recent = [dict(r) for r in c.execute(
                "SELECT title, url, source, ts FROM downloads WHERE user_id=? ORDER BY ts DESC LIMIT 5", (user_id,))]
            rank = c.execute(
                """SELECT COUNT(*) + 1 FROM (SELECT user_id, COUNT(*) AS n FROM downloads GROUP BY user_id)
                   WHERE n > (SELECT COUNT(*) FROM downloads WHERE user_id=?)""", (user_id,)).fetchone()[0]
        return {"user": dict(row) if row else None, "dl": dl, "sources": sources, "recent": recent,
                "rank": rank if dl["total"] else None}

    # ───────────── istifadəçi siyahısı: filtr + axtarış ─────────────
    _USER_FILTERS = {
        "all":     ("1=1", "u.first_seen DESC"),
        "active":  ("u.last_seen >= :week AND u.blocked = 0", "u.last_seen DESC"),
        "new":     ("u.first_seen >= :week", "u.first_seen DESC"),
        "top":     ("1=1", "dl_total DESC, u.last_seen DESC"),
        "prem":    ("p.user_id IS NOT NULL", "u.first_seen DESC"),
        "blocked": ("u.blocked = 1", "u.last_seen DESC"),
    }
    _USER_SELECT = """
        SELECT u.*, COALESCE(d.n, 0) AS dl_total, p.user_id IS NOT NULL AS premium
        FROM users u
        LEFT JOIN (SELECT user_id, COUNT(*) AS n FROM downloads GROUP BY user_id) d ON d.user_id = u.user_id
        LEFT JOIN premium p ON p.user_id = u.user_id AND (p.until IS NULL OR p.until > :now)
    """

    def users_filtered(self, flt: str, offset: int, limit: int, now: int):
        """(sətirlər, cəmi say)"""
        where, order = self._USER_FILTERS.get(flt, self._USER_FILTERS["all"])
        params = {"now": now, "week": now - 7 * 86400, "limit": limit, "offset": offset}
        with self._lock:
            total = self._conn.execute(
                f"SELECT COUNT(*) FROM ({self._USER_SELECT} WHERE {where}) AS t", params).fetchone()[0]
            rows = self._conn.execute(
                f"{self._USER_SELECT} WHERE {where} ORDER BY {order} LIMIT :limit OFFSET :offset", params).fetchall()
        return [dict(r) for r in rows], total

    def filter_counts(self, now: int) -> dict:
        params = {"now": now, "week": now - 7 * 86400}
        out = {}
        with self._lock:
            for key, (where, _) in self._USER_FILTERS.items():
                if key == "top":
                    continue
                out[key] = self._conn.execute(
                    f"SELECT COUNT(*) FROM ({self._USER_SELECT} WHERE {where}) AS t", params).fetchone()[0]
        out["top"] = out["all"]
        return out

    def search_users(self, query: str, now: int, limit: int = 10) -> list:
        """ID (tam), @username və ya ad üzrə axtarış."""
        q = (query or "").strip().lstrip("@")
        if not q:
            return []
        params = {"now": now, "id": int(q) if q.isdigit() else -1, "like": f"%{q}%", "limit": limit}
        with self._lock:
            rows = self._conn.execute(
                f"""{self._USER_SELECT}
                    WHERE u.user_id = :id OR u.username LIKE :like COLLATE NOCASE
                       OR u.first_name LIKE :like COLLATE NOCASE OR u.last_name LIKE :like COLLATE NOCASE
                    ORDER BY (u.user_id = :id) DESC, dl_total DESC LIMIT :limit""", params).fetchall()
        return [dict(r) for r in rows]

    def users_export(self, now: int) -> list:
        with self._lock:
            rows = self._conn.execute(
                """SELECT u.*, COALESCE(d.n,0) AS dl_total, COALESCE(d.m,0) AS dl_30d, d.last_ts AS last_download,
                          p.user_id IS NOT NULL AS premium, p.until AS premium_until
                   FROM users u
                   LEFT JOIN (SELECT user_id, COUNT(*) AS n, SUM(ts >= :month) AS m, MAX(ts) AS last_ts
                              FROM downloads GROUP BY user_id) d ON d.user_id = u.user_id
                   LEFT JOIN premium p ON p.user_id = u.user_id AND (p.until IS NULL OR p.until > :now)
                   ORDER BY u.first_seen ASC""", {"now": now, "month": now - 30 * 86400}).fetchall()
        return [dict(r) for r in rows]

    def download_stats(self, day_start: int, now: int) -> dict:
        """Bu gün (day_start-dan), son 7 gün, son 30 gün və cəmi."""
        with self._lock:
            row = self._conn.execute(
                """SELECT
                       COALESCE(SUM(ts >= ?), 0) AS today,
                       COALESCE(SUM(ts >= ?), 0) AS week,
                       COALESCE(SUM(ts >= ?), 0) AS month,
                       COUNT(*)                  AS total
                   FROM downloads""",
                (day_start, now - 7 * 86400, now - 30 * 86400),
            ).fetchone()
        return dict(row)


_db = None
_db_lock = threading.Lock()


def get_db() -> Database:
    global _db
    with _db_lock:
        if _db is None:
            _db = Database(os.getenv("DB_PATH", "data/bot.db"))
        return _db


_TRUE = ("1", "on", "true", "yes")


def is_premium(user_id) -> bool:
    """Digər plugin-lər üçün: istifadəçi premiumdurmu. Creator həmişə premiumdur. Xəta olsa free sayılır."""
    if is_creator(user_id):
        return True
    try:
        return get_db().is_premium(user_id)
    except Exception as e:
        logger.warning(f"Premium yoxlanışı xətası: {e}")
        return False


# ───────────── Rollar və caption qaydası ─────────────
#   👑 Creator → hər şey açıqdır, caption-u /menu-dan özü açıb-bağlayır (default: bağlı)
#   💎 Premium → caption həmişə bağlı (təmiz audio)
#   🆓 Free    → caption həmişə açıq
def creator_id() -> int:
    try:
        return int(os.getenv("CREATOR_ID") or 0)
    except ValueError:
        return 0


def is_creator(user_id) -> bool:
    cid = creator_id()
    return bool(cid) and user_id == cid


def creator_caption_enabled() -> bool:
    try:
        value = get_db().get_setting("creator_caption")
    except Exception as e:
        logger.warning(f"Creator caption ayarı oxunmadı: {e}")
        value = None
    return (value or "off").strip().lower() in _TRUE


def set_creator_caption(enabled: bool):
    get_db().set_setting("creator_caption", "on" if enabled else "off")


def caption_for_user(user_id) -> bool:
    """Bu istifadəçiyə göndərilən mahnının altında "via @dllmasterbot" yazısı olsunmu."""
    if is_creator(user_id):
        return creator_caption_enabled()
    if is_premium(user_id):
        return False
    return True


async def log_download(user_id, title=None, url=None, source=None, chat_id=None):
    """Mahnı göndərildikdən sonra çağırın. chat_id — mahnının göndərildiyi çat (qrup statistikası üçün)."""
    try:
        await asyncio.to_thread(get_db().log_download, user_id, title, url, source, None, chat_id)
    except Exception as e:
        logger.warning(f"Yükləmə qeydə alınmadı: {e}")
