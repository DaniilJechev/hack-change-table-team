"""Offline H4 metadata, transactional and scoped by authenticated demo owner."""

import functools
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from buddy.data.base import DataError


def safe_sql(method):
    @functools.wraps(method)
    def wrapped(self, *args, **kwargs):
        try:
            return method(self, *args, **kwargs)
        except sqlite3.Error:
            self.db.rollback()
            raise DataError("unavailable") from None

    return wrapped


class DemoHistory:
    def __init__(self, path: str = ""):
        if path:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path or ":memory:", timeout=5)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS h4_history(owner TEXT, id TEXT, payload TEXT, PRIMARY KEY(owner,id))"
        )
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS h4_generation(owner TEXT PRIMARY KEY, generation INTEGER)"
        )
        self.db.commit()

    def _prune(self):
        now = datetime.now(UTC).isoformat()
        self.db.execute("DELETE FROM h4_history WHERE json_extract(payload,'$.expires_at')<=?", (now,))

    def _rows(self, owner):
        rows = self.db.execute("SELECT payload FROM h4_history WHERE owner=?", (owner,)).fetchall()
        return sorted([json.loads(row[0]) for row in rows], key=lambda r: r["created_at"], reverse=True)

    @safe_sql
    def history(self, owner):
        with self.db:
            self._prune()
            return self._rows(owner)

    def _generation(self, owner):
        row = self.db.execute("SELECT generation FROM h4_generation WHERE owner=?", (owner,)).fetchone()
        return row[0] if row else 0

    def _write(self, owner, record):
        self.db.execute(
            "INSERT OR REPLACE INTO h4_history VALUES (?,?,?)", (owner, record["id"], json.dumps(record))
        )

    @safe_sql
    def save(self, owner, record):
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            self._prune()
            record = dict(record)
            record["current"] = bool(record.get("current")) and record.get(
                "generation", 0
            ) == self._generation(owner)
            # A reset/invalidation may race a completed model call. Do not resurrect
            # a payload that never existed in the current history generation.
            exists = self.db.execute(
                "SELECT 1 FROM h4_history WHERE owner=? AND id=?", (owner, record["id"])
            ).fetchone()
            if record.get("generation", 0) == self._generation(owner) or exists:
                self._write(owner, record)
        return record

    @safe_sql
    def invalidate(self, owner):
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            self._prune()
            for record in self._rows(owner):
                record["current"] = False
                self._write(owner, record)
            self.db.execute(
                "INSERT INTO h4_generation VALUES (?,1) "
                "ON CONFLICT(owner) DO UPDATE SET generation=generation+1",
                (owner,),
            )
            return self._generation(owner)

    @safe_sql
    def clear_session(self, session):
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            prefix = f"demo:{session}:%"
            # Keep tombstones so an in-flight old check cannot resurrect after reset.
            self.db.execute("UPDATE h4_generation SET generation=generation+1 WHERE owner LIKE ?", (prefix,))
            self.db.execute("DELETE FROM h4_history WHERE owner LIKE ?", (prefix,))

    @safe_sql
    def claim(self, owner, check_id, part, *, retry_unknown=False):
        if part not in {"file", "report"}:
            raise DataError("invalid")
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            self._prune()
            record = next((r for r in self._rows(owner) if r["id"] == check_id), None)
            field = "delivery_" + part
            states = {"pending", "failed", "unknown"} if retry_unknown else {"pending", "failed"}
            if (
                record is None
                or not record.get("current")
                or not record.get("passed")
                or record.get("generation", 0) != self._generation(owner)
                or record.get(field, "pending") not in states
            ):
                raise DataError("conflict")
            record[field] = "sending"
            self._write(owner, record)
        return record
