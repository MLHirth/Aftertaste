from __future__ import annotations

from datetime import datetime, timezone
import shutil
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable


class Database:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON;")

    def migrate(self, migrations_path: Path) -> None:
        with self._lock:
            self._conn.execute(
                """
        CREATE TABLE IF NOT EXISTS schema_migrations (
          filename TEXT PRIMARY KEY,
          applied_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """
            )
            applied = {
                row["filename"]
                for row in self._conn.execute("SELECT filename FROM schema_migrations")
            }

            pending = [
                migration
                for migration in sorted(migrations_path.glob("*.sql"))
                if migration.name not in applied
            ]
            if pending:
                stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                backup_path = self.db_path.with_name(
                    f"{self.db_path.stem}.pre-migrate-{stamp}{self.db_path.suffix}"
                )
                shutil.copy2(self.db_path, backup_path)

            for migration in pending:
                sql = migration.read_text(encoding="utf-8")
                self._conn.executescript(sql)
                self._conn.execute(
                    "INSERT INTO schema_migrations(filename) VALUES (?)",
                    (migration.name,),
                )
            self._conn.commit()

    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            cursor = self._conn.execute(sql, tuple(params))
            self._conn.commit()
            return cursor

    def executemany(self, sql: str, rows: Iterable[Iterable[Any]]) -> None:
        with self._lock:
            self._conn.executemany(sql, rows)
            self._conn.commit()

    def query_one(self, sql: str, params: Iterable[Any] = ()) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(sql, tuple(params)).fetchone()
        if row is None:
            return None
        return dict(row)

    def query_all(self, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(sql, tuple(params)).fetchall()
        return [dict(row) for row in rows]

    def close(self) -> None:
        with self._lock:
            self._conn.close()
