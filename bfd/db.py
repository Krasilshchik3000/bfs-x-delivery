import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS bfs_places (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    slug              TEXT NOT NULL,
    name              TEXT NOT NULL,
    lat               REAL NOT NULL,
    lng               REAL NOT NULL,
    address           TEXT,
    bfs_url           TEXT,
    image_url         TEXT,
    description       TEXT,             -- short "top tip" from map.json
    long_description  TEXT,             -- full review from individual page
    neighborhood      TEXT,
    categories        TEXT,             -- JSON array of cuisine tags
    last_seen         TIMESTAMP NOT NULL,
    deleted_at        TIMESTAMP,
    UNIQUE (slug, lat, lng)
);

CREATE TABLE IF NOT EXISTS geocode_cache (
    address_norm TEXT PRIMARY KEY,
    lat          REAL NOT NULL,
    lng          REAL NOT NULL,
    postcode     TEXT,
    fetched_at   TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS delivery_cache (
    address_norm TEXT NOT NULL,
    platform     TEXT NOT NULL,
    payload      TEXT NOT NULL,
    status       TEXT NOT NULL,
    error        TEXT,
    fetched_at   TIMESTAMP NOT NULL,
    PRIMARY KEY (address_norm, platform)
);
"""


def init_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.executescript(SCHEMA)
        _apply_migrations(conn)


def _apply_migrations(conn: sqlite3.Connection) -> None:
    """Idempotent column-add migrations.

    `CREATE TABLE IF NOT EXISTS` doesn't add new columns to an existing
    table, so we manually ALTER TABLE when a column is missing. SQLite
    has no `ADD COLUMN IF NOT EXISTS`, so we check the schema first.
    """
    existing_cols = {
        row[1] for row in conn.execute("PRAGMA table_info(bfs_places)")
    }
    if "long_description" not in existing_cols:
        conn.execute("ALTER TABLE bfs_places ADD COLUMN long_description TEXT")


@contextmanager
def connect(path: Path) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()
