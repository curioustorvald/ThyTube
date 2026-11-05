"""SQLite access: connections, migrations, and small helpers shared by both processes."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from importlib import resources
from pathlib import Path
from typing import Any

EVENT_RETENTION_DAYS = 90


def now() -> datetime:
    return datetime.now(UTC)


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def now_iso() -> str:
    return iso(now())


def in_iso(**delta: float) -> str:
    return iso(now() + timedelta(**delta))


def parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def connect(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=5.0, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


@contextmanager
def tx(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """An explicit write transaction (BEGIN IMMEDIATE so writers queue on busy_timeout)."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def _migrations() -> list[tuple[int, str, str]]:
    out = []
    for entry in resources.files("thytube.migrations").iterdir():
        name = entry.name
        if name.endswith(".sql") and name[:4].isdigit():
            out.append((int(name[:4]), name, entry.read_text()))
    return sorted(out)


def migrate(conn: sqlite3.Connection) -> list[str]:
    applied = []
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    for version, name, sql in _migrations():
        if version <= current:
            continue
        conn.execute("BEGIN IMMEDIATE")
        try:
            # executescript would COMMIT our transaction; run statements one by one instead.
            for stmt in _split_sql(sql):
                conn.execute(stmt)
            conn.execute(f"PRAGMA user_version={version}")
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        applied.append(name)
    return applied


def _split_sql(sql: str) -> list[str]:
    stmts, buf = [], []
    for line in sql.splitlines():
        stripped = line.strip()
        if stripped.startswith("--") or not stripped:
            continue
        buf.append(line)
        if sqlite3.complete_statement("\n".join(buf)):
            stmts.append("\n".join(buf))
            buf = []
    if buf and "".join(buf).strip():
        stmts.append("\n".join(buf))
    return stmts


def open_db(path: str | Path) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = connect(path)
    migrate(conn)
    return conn


# ---- kv -----------------------------------------------------------------


def kv_get(conn: sqlite3.Connection, key: str, default: Any = None) -> Any:
    row = conn.execute("SELECT value_json FROM kv WHERE key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else default


def kv_set(conn: sqlite3.Connection, key: str, value: Any) -> None:
    conn.execute(
        "INSERT INTO kv(key, value_json) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
        (key, json.dumps(value)),
    )


def kv_delete(conn: sqlite3.Connection, key: str) -> None:
    conn.execute("DELETE FROM kv WHERE key=?", (key,))


# ---- events -------------------------------------------------------------


def add_event(
    conn: sqlite3.Connection, level: str, kind: str, message: str, data: dict[str, Any] | None = None
) -> None:
    conn.execute(
        "INSERT INTO events(ts, level, kind, message, data_json) VALUES(?,?,?,?,?)",
        (now_iso(), level, kind, message, json.dumps(data) if data else None),
    )


def prune_events(conn: sqlite3.Connection) -> int:
    cutoff = in_iso(days=-EVENT_RETENTION_DAYS)
    return conn.execute("DELETE FROM events WHERE ts < ?", (cutoff,)).rowcount


# ---- commands (web → worker) --------------------------------------------


def enqueue_command(conn: sqlite3.Connection, kind: str, payload: dict[str, Any] | None = None) -> int:
    cur = conn.execute(
        "INSERT INTO commands(kind, payload_json, created_at) VALUES(?,?,?)",
        (kind, json.dumps(payload or {}), now_iso()),
    )
    return int(cur.lastrowid or 0)


def take_commands(conn: sqlite3.Connection, limit: int = 20) -> list[sqlite3.Row]:
    with tx(conn):
        rows = conn.execute(
            "SELECT * FROM commands WHERE taken_at IS NULL ORDER BY id LIMIT ?", (limit,)
        ).fetchall()
        if rows:
            ts = now_iso()
            conn.executemany("UPDATE commands SET taken_at=? WHERE id=?", [(ts, r["id"]) for r in rows])
    return rows


def finish_command(conn: sqlite3.Connection, cmd_id: int, result: dict[str, Any] | None = None) -> None:
    conn.execute(
        "UPDATE commands SET done_at=?, result_json=? WHERE id=?",
        (now_iso(), json.dumps(result or {}), cmd_id),
    )


def prune_commands(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM commands WHERE done_at IS NOT NULL AND done_at < ?", (in_iso(days=-7),))


def row_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None
