from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any

from core.db import Database


SYNC_TABLE_KEYS: dict[str, tuple[str, ...]] = {
    "tracks": ("track_id",),
    "artists": ("artist_id",),
    "track_artists": ("track_id", "artist_id"),
    "albums": ("album_id",),
    "saved_tracks": ("track_id",),
    "play_events": ("event_uid",),
    "playlists": ("playlist_id",),
    "playlist_tracks": ("playlist_id", "position"),
    "rules": ("rule_key",),
    "source_preferences": ("playlist_id",),
    "top_track_affinity": ("track_id", "time_range"),
    "top_artist_affinity": ("artist_id", "time_range"),
    "avoid_tracks": ("track_id",),
}


class CloudSyncEngine:
    def __init__(self, db: Database) -> None:
        self.db = db
        self._columns_cache: dict[str, list[str]] = {}

    def _table_columns(self, table_name: str) -> list[str]:
        cached = self._columns_cache.get(table_name)
        if cached is not None:
            return cached

        rows = self.db.query_all(f"PRAGMA table_info({table_name})")
        columns = [str(row["name"]) for row in rows if row.get("name")]
        self._columns_cache[table_name] = columns
        return columns

    def bootstrap_snapshot_if_empty(self) -> dict[str, int]:
        row = self.db.query_one("SELECT COUNT(*) AS c FROM sync_log") or {"c": 0}
        if int(row.get("c") or 0) > 0:
            return {"inserted": 0}

        inserted = 0
        for table_name, key_columns in SYNC_TABLE_KEYS.items():
            keys_sql = ", ".join(key_columns)
            rows = self.db.query_all(f"SELECT {keys_sql} FROM {table_name}")
            payload_rows: list[tuple[Any, ...]] = []
            for key_row in rows:
                pk = {key: key_row.get(key) for key in key_columns}
                pk_json = json.dumps(pk, separators=(",", ":"), sort_keys=True)
                payload_rows.append((table_name, pk_json, "upsert"))

            if payload_rows:
                self.db.executemany(
                    "INSERT INTO sync_log(table_name, pk_json, op) VALUES (?, ?, ?)",
                    payload_rows,
                )
                inserted += len(payload_rows)

        return {"inserted": inserted}

    def _where_clause(
        self,
        table_name: str,
        pk: dict[str, Any],
    ) -> tuple[str, tuple[Any, ...]]:
        keys = [key for key in SYNC_TABLE_KEYS[table_name] if key in pk]
        clause = " AND ".join([f"{key} = ?" for key in keys])
        params = tuple(pk[key] for key in keys)
        return clause, params

    def _fetch_row(self, table_name: str, pk: dict[str, Any]) -> dict[str, Any] | None:
        where_clause, params = self._where_clause(table_name, pk)
        return self.db.query_one(
            f"SELECT * FROM {table_name} WHERE {where_clause} LIMIT 1",
            params,
        )

    def _normalize_row_for_sync(
        self,
        table_name: str,
        row: dict[str, Any],
    ) -> dict[str, Any]:
        normalized = dict(row)
        if table_name == "play_events":
            normalized.pop("event_id", None)
        return normalized

    def export_changes(
        self,
        since_seq: int,
        limit: int = 500,
    ) -> dict[str, Any]:
        rows = self.db.query_all(
            """
      SELECT seq, table_name, pk_json, op, changed_at
      FROM sync_log
      WHERE seq > ?
      ORDER BY seq ASC
      LIMIT ?
      """,
            (since_seq, max(1, min(limit, 2000))),
        )

        changes: list[dict[str, Any]] = []
        last_seq = since_seq
        for entry in rows:
            seq = int(entry["seq"])
            last_seq = max(last_seq, seq)

            table_name = str(entry.get("table_name") or "")
            if table_name not in SYNC_TABLE_KEYS:
                continue

            try:
                pk = json.loads(str(entry.get("pk_json") or "{}"))
            except json.JSONDecodeError:
                continue
            if not isinstance(pk, dict):
                continue

            op = str(entry.get("op") or "upsert")
            if op == "delete":
                changes.append(
                    {
                        "seq": seq,
                        "table": table_name,
                        "op": "delete",
                        "pk": pk,
                    }
                )
                continue

            row = self._fetch_row(table_name, pk)
            if row is None:
                changes.append(
                    {
                        "seq": seq,
                        "table": table_name,
                        "op": "delete",
                        "pk": pk,
                    }
                )
                continue

            changes.append(
                {
                    "seq": seq,
                    "table": table_name,
                    "op": "upsert",
                    "pk": pk,
                    "row": self._normalize_row_for_sync(table_name, row),
                }
            )

        return {
            "changes": changes,
            "last_seq": last_seq,
        }

    def _set_suppress_logging(self, enabled: bool) -> None:
        self.db.execute(
            "UPDATE sync_context SET suppress_logging = ? WHERE id = 1",
            (1 if enabled else 0,),
        )

    def _upsert(self, table_name: str, row: dict[str, Any]) -> None:
        keys = SYNC_TABLE_KEYS[table_name]
        columns = self._table_columns(table_name)
        payload = {key: value for key, value in row.items() if key in columns}
        if not payload:
            return

        if table_name == "play_events":
            payload.pop("event_id", None)

        ordered_columns = sorted(payload.keys())
        placeholders = ", ".join(["?" for _ in ordered_columns])
        conflict_target = ", ".join(keys)

        update_columns = [column for column in ordered_columns if column not in keys]
        if update_columns:
            updates = ", ".join(
                [f"{column} = excluded.{column}" for column in update_columns]
            )
            sql = (
                f"INSERT INTO {table_name} ({', '.join(ordered_columns)}) "
                f"VALUES ({placeholders}) "
                f"ON CONFLICT({conflict_target}) DO UPDATE SET {updates}"
            )
        else:
            sql = (
                f"INSERT INTO {table_name} ({', '.join(ordered_columns)}) "
                f"VALUES ({placeholders}) "
                f"ON CONFLICT({conflict_target}) DO NOTHING"
            )

        params = tuple(payload[column] for column in ordered_columns)
        self.db.execute(sql, params)

    def _delete(self, table_name: str, pk: dict[str, Any]) -> None:
        where_clause, params = self._where_clause(table_name, pk)
        self.db.execute(f"DELETE FROM {table_name} WHERE {where_clause}", params)

    def apply_changes(self, changes: list[dict[str, Any]]) -> dict[str, int]:
        applied = 0
        skipped = 0

        self._set_suppress_logging(True)
        try:
            for change in changes:
                table_name = str(change.get("table") or "")
                if table_name not in SYNC_TABLE_KEYS:
                    skipped += 1
                    continue

                op = str(change.get("op") or "upsert")
                pk = change.get("pk")
                if not isinstance(pk, dict):
                    skipped += 1
                    continue

                key_columns = set(SYNC_TABLE_KEYS[table_name])
                if not key_columns.issubset(pk.keys()):
                    skipped += 1
                    continue

                if op == "delete":
                    self._delete(table_name, pk)
                    applied += 1
                    continue

                row = change.get("row")
                if not isinstance(row, dict):
                    skipped += 1
                    continue

                self._upsert(table_name, row)
                applied += 1
        finally:
            self._set_suppress_logging(False)

        return {"applied": applied, "skipped": skipped}

    def load_checkpoint(self, remote_name: str = "default") -> dict[str, int]:
        row = self.db.query_one(
            """
      SELECT last_pushed_seq, last_pulled_seq
      FROM cloud_sync_checkpoint
      WHERE remote_name = ?
      """,
            (remote_name,),
        )
        if row is None:
            return {"last_pushed_seq": 0, "last_pulled_seq": 0}
        return {
            "last_pushed_seq": int(row.get("last_pushed_seq") or 0),
            "last_pulled_seq": int(row.get("last_pulled_seq") or 0),
        }

    def save_checkpoint(
        self,
        remote_name: str,
        *,
        last_pushed_seq: int,
        last_pulled_seq: int,
    ) -> None:
        self.db.execute(
            """
      INSERT INTO cloud_sync_checkpoint(
        remote_name,
        last_pushed_seq,
        last_pulled_seq,
        updated_at
      ) VALUES (?, ?, ?, ?)
      ON CONFLICT(remote_name)
      DO UPDATE SET
        last_pushed_seq = excluded.last_pushed_seq,
        last_pulled_seq = excluded.last_pulled_seq,
        updated_at = excluded.updated_at
      """,
            (
                remote_name,
                max(0, int(last_pushed_seq)),
                max(0, int(last_pulled_seq)),
                datetime.now(tz=timezone.utc).isoformat(),
            ),
        )
