# database.py
import sqlite3
import os
from datetime import datetime
from typing import Optional, List, Dict, Any

class Database:
    def __init__(self):
        # Qovluqları yoxla və yoxdursa yarat
        self.logs_dir = "logs"
        self.stats_dir = "stats"
        os.makedirs(self.logs_dir, exist_ok=True)
        os.makedirs(self.stats_dir, exist_ok=True)

        # Stats verilənlər bazası (stats.db)
        self.stats_db_path = os.path.join(self.stats_dir, "stats.db")
        self.stats_conn = self._create_connection(self.stats_db_path)
        self._initialize_stats_tables()

        # Log verilənlər bazası (logs-il-ay-gün.db)
        self.logs_db_path = self._get_logs_db_path()
        self.logs_conn = self._create_connection(self.logs_db_path)
        self._initialize_logs_tables()

    def _create_connection(self, db_path: str) -> sqlite3.Connection:
        """SQLite verilənlər bazası ilə əlaqə yaradır."""
        return sqlite3.connect(db_path)

    def _get_logs_db_path(self) -> str:
        """Hər gün üçün yeni bir log verilənlər bazası yolu yaradır."""
        today = datetime.now().strftime("%Y-%m-%d")
        return os.path.join(self.logs_dir, f"logs-{today}.db")

    def _initialize_stats_tables(self):
        """Stats verilənlər bazası üçün cədvəlləri yaradır."""
        cursor = self.stats_conn.cursor()

        # users cədvəlini yaradın
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER UNIQUE,
                username TEXT,
                message_count INTEGER DEFAULT 0,
                song_download_count INTEGER DEFAULT 0
            )
        ''')

        # groups cədvəlini yaradın
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS groups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id INTEGER UNIQUE,
                group_name TEXT,
                bot_usage_count INTEGER DEFAULT 0
            )
        ''')

        # group_users cədvəlini yaradın
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS group_users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id INTEGER,
                user_id INTEGER,
                FOREIGN KEY(group_id) REFERENCES groups(group_id),
                FOREIGN KEY(user_id) REFERENCES users(user_id)
            )
        ''')

        # Sütunları avtomatik yoxla və əlavə et
        self._ensure_columns_exist("users", [
            ("message_count", "INTEGER DEFAULT 0"),
            ("song_download_count", "INTEGER DEFAULT 0")
        ])

        self._ensure_columns_exist("groups", [
            ("bot_usage_count", "INTEGER DEFAULT 0")
        ])

        self.stats_conn.commit()

    def _initialize_logs_tables(self):
        """Log verilənlər bazası üçün cədvəlləri yaradır."""
        cursor = self.logs_conn.cursor()

        # logs cədvəlini yaradın
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                level TEXT,
                message TEXT,
                user_id INTEGER,
                group_id INTEGER
            )
        ''')

        # Sütunları avtomatik yoxla və əlavə et
        self._ensure_columns_exist("logs", [
            ("user_id", "INTEGER"),
            ("group_id", "INTEGER")
        ])

        self.logs_conn.commit()

    def _ensure_columns_exist(self, table_name: str, columns: List[tuple]):
        """Verilən cədvəldə sütunların olub-olmadığını yoxlayır və yoxdursa əlavə edir."""
        cursor = self.stats_conn.cursor() if table_name in ["users", "groups", "group_users"] else self.logs_conn.cursor()
        
        cursor.execute(f"PRAGMA table_info({table_name})")
        existing_columns = [column[1] for column in cursor.fetchall()]

        for column_name, column_type in columns:
            if column_name not in existing_columns:
                cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")

    def add_group(self, group_id: int, group_name: str):
        """Qrupu stats verilənlər bazasına əlavə edir."""
        cursor = self.stats_conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO groups (group_id, group_name) VALUES (?, ?)", (group_id, group_name))
        self.stats_conn.commit()

    def add_user(self, user_id: int, username: str):
        """Useri stats verilənlər bazasına əlavə edir."""
        cursor = self.stats_conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)", (user_id, username))
        self.stats_conn.commit()

    def add_group_user(self, group_id: int, user_id: int):
        """Qrup və user arasında əlaqə yaradır."""
        cursor = self.stats_conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO group_users (group_id, user_id) VALUES (?, ?)", (group_id, user_id))
        self.stats_conn.commit()

    def increment_group_bot_usage(self, group_id: int):
        """Qrupda botun istifadə sayını artırır."""
        cursor = self.stats_conn.cursor()
        cursor.execute("UPDATE groups SET bot_usage_count = bot_usage_count + 1 WHERE group_id = ?", (group_id,))
        self.stats_conn.commit()

    def increment_user_message_count(self, user_id: int):
        """Userin mesaj sayını artırır."""
        cursor = self.stats_conn.cursor()
        cursor.execute("UPDATE users SET message_count = message_count + 1 WHERE user_id = ?", (user_id,))
        self.stats_conn.commit()

    def increment_user_song_download_count(self, user_id: int):
        """Userin mahnı yükləmə sayını artırır."""
        cursor = self.stats_conn.cursor()
        cursor.execute("UPDATE users SET song_download_count = song_download_count + 1 WHERE user_id = ?", (user_id,))
        self.stats_conn.commit()

    def add_log(self, level: str, message: str, user_id: Optional[int] = None, group_id: Optional[int] = None):
        """Botda baş verən hadisəni log verilənlər bazasına əlavə edir."""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor = self.logs_conn.cursor()
        cursor.execute("INSERT INTO logs (timestamp, level, message, user_id, group_id) VALUES (?, ?, ?, ?, ?)",
                       (timestamp, level, message, user_id, group_id))
        self.logs_conn.commit()

    def get_all_groups(self) -> List[Dict[str, Any]]:
        """Bütün qrupları stats verilənlər bazasından qaytarır."""
        cursor = self.stats_conn.cursor()
        cursor.execute("SELECT group_id, group_name, bot_usage_count FROM groups")
        return [{"group_id": row[0], "group_name": row[1], "bot_usage_count": row[2]} for row in cursor.fetchall()]

    def get_all_users(self) -> List[Dict[str, Any]]:
        """Bütün userləri stats verilənlər bazasından qaytarır."""
        cursor = self.stats_conn.cursor()
        cursor.execute("SELECT user_id, username, message_count, song_download_count FROM users")
        return [{"user_id": row[0], "username": row[1], "message_count": row[2], "song_download_count": row[3]} for row in cursor.fetchall()]

    def get_logs(self) -> List[Dict[str, Any]]:
        """Bütün logları log verilənlər bazasından qaytarır."""
        cursor = self.logs_conn.cursor()
        cursor.execute("SELECT timestamp, level, message, user_id, group_id FROM logs")
        return [{"timestamp": row[0], "level": row[1], "message": row[2], "user_id": row[3], "group_id": row[4]} for row in cursor.fetchall()]

    def close(self):
        """Bütün verilənlər bazası əlaqələrini bağlayır."""
        self.stats_conn.close()
        self.logs_conn.close()