import datetime as dt
from pathlib import Path

from bfd import cache, db
from bfd.adapters.base import DeliveryRestaurant


def _sample():
    return [DeliveryRestaurant(
        platform="wolt", id="w1", name="X", lat=52.5, lng=13.4, url="https://...",
    )]


def test_cache_miss_returns_none(tmp_path: Path):
    db_path = tmp_path / "t.sqlite"
    db.init_db(db_path)
    assert cache.read("addr", "wolt", db_path=db_path) is None


def test_cache_write_and_read(tmp_path: Path):
    db_path = tmp_path / "t.sqlite"
    db.init_db(db_path)
    cache.write("addr", "wolt", "ok", _sample(), error=None, db_path=db_path)

    entry = cache.read("addr", "wolt", db_path=db_path)
    assert entry is not None
    assert entry.status == "ok"
    assert len(entry.results) == 1
    assert entry.results[0].name == "X"


def test_cache_expires_after_ttl(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "t.sqlite"
    db.init_db(db_path)
    cache.write("addr", "wolt", "ok", _sample(), error=None, db_path=db_path)

    # Advance "now" beyond TTL by patching the helper
    monkeypatch.setattr(cache, "_now", lambda: dt.datetime.utcnow() + dt.timedelta(hours=25))
    assert cache.read("addr", "wolt", db_path=db_path) is None


def test_invalidate_removes_entry(tmp_path: Path):
    db_path = tmp_path / "t.sqlite"
    db.init_db(db_path)
    cache.write("addr", "wolt", "ok", _sample(), error=None, db_path=db_path)
    cache.invalidate("addr", db_path=db_path)
    assert cache.read("addr", "wolt", db_path=db_path) is None


def test_cache_stores_error_status(tmp_path: Path):
    db_path = tmp_path / "t.sqlite"
    db.init_db(db_path)
    cache.write("addr", "wolt", "error", [], error="captcha", db_path=db_path)
    entry = cache.read("addr", "wolt", db_path=db_path)
    assert entry is not None
    assert entry.status == "error"
    assert entry.error == "captcha"
    assert entry.results == []
