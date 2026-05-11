import sqlite3

from bfd import db


def test_init_db_creates_tables(tmp_path):
    db_path = tmp_path / "test.sqlite"
    db.init_db(db_path)

    with sqlite3.connect(db_path) as conn:
        names = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
                " AND name NOT LIKE 'sqlite_%'"
            )
        }
    assert names == {"bfs_places", "geocode_cache", "delivery_cache"}


def test_init_db_is_idempotent(tmp_path):
    db_path = tmp_path / "test.sqlite"
    db.init_db(db_path)
    db.init_db(db_path)  # must not raise


def test_connect_returns_row_factory(tmp_path):
    db_path = tmp_path / "test.sqlite"
    db.init_db(db_path)
    with db.connect(db_path) as conn:
        row = conn.execute("SELECT 1 AS x").fetchone()
    assert row["x"] == 1
