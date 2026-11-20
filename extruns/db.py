"""Storage. SQLite for the skeleton (one file, nothing shared touched); the schema is plain SQL so it moves to
Postgres unchanged when this goes to a server."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager

from . import config

SCHEMA = """
create table if not exists books (
  id            integer primary key autoincrement,
  linear_id     text not null,
  language      text not null,
  title         text not null default '',
  until         text not null,                 -- narratable | speakable | audio
  stage         text not null,                 -- queued | narratable | speakable | audio | delivered
  status        text not null,                 -- waiting | running | stopped | failed | done
  attempt       integer not null default 1,
  position      real not null,                 -- order inside its place (lower runs first)
  claimed_by    text,
  lease_until   real,
  heartbeat_at  real,
  stage_started real,
  error         text,
  narratable    text,                          -- json: chapters, fixes
  speakable     text,                          -- json: chapters as returned
  audio_path    text,
  report        text,                          -- json from the audio stage
  created_by    text not null default 'someone',
  created_at    real not null,
  updated_at    real not null
);
create index if not exists books_place on books(stage, status, position);
create table if not exists events (
  id       integer primary key autoincrement,
  book_id  integer not null,
  at       real not null,
  actor    text not null,                      -- in-house | external | person name/id
  kind     text not null,
  stage    text,
  detail   text                                -- json
);
create index if not exists events_book on events(book_id, at);
create table if not exists owners (stage text primary key, owner text not null);
"""

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None


def conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(config.DB_PATH, check_same_thread=False, isolation_level=None)
        _conn.row_factory = sqlite3.Row
        _conn.execute("pragma journal_mode=wal")
        _conn.executescript(SCHEMA)
        for stage, owner in config.DEFAULT_OWNERS.items():
            _conn.execute("insert or ignore into owners(stage, owner) values (?, ?)", (stage, owner))
    return _conn


def reset_for_tests(path) -> None:
    global _conn
    if _conn is not None:
        _conn.close()
    _conn = None
    config.DB_PATH = path


@contextmanager
def tx():
    """One write transaction at a time (claims must never hand the same book to two workers)."""
    with _lock:
        c = conn()
        c.execute("begin immediate")
        try:
            yield c
            c.execute("commit")
        except BaseException:
            c.execute("rollback")
            raise


def rows(sql: str, args=()) -> list[dict]:
    with _lock:
        return [dict(r) for r in conn().execute(sql, args).fetchall()]


def one(sql: str, args=()) -> dict | None:
    r = rows(sql, args)
    return r[0] if r else None


def event(c: sqlite3.Connection, book_id: int, actor: str, kind: str, stage: str | None = None, **detail) -> None:
    c.execute("insert into events(book_id, at, actor, kind, stage, detail) values (?,?,?,?,?,?)",
              (book_id, time.time(), actor, kind, stage, json.dumps(detail, ensure_ascii=False) if detail else None))


def owners() -> dict[str, str]:
    return {r["stage"]: r["owner"] for r in rows("select stage, owner from owners")}
