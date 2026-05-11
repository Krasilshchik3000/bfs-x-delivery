import datetime as dt
import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

from . import db as _db
from .adapters.base import DeliveryRestaurant
from .config import DELIVERY_CACHE_TTL_HOURS


@dataclass
class CacheEntry:
    status: str
    results: list[DeliveryRestaurant]
    error: Optional[str]
    fetched_at: dt.datetime


def _now() -> dt.datetime:
    return dt.datetime.utcnow()


def _resolve_db_path(db_path: Optional[Path]) -> Path:
    if db_path is not None:
        return db_path
    from . import config
    return config.DB_PATH


def read(
    address_norm: str,
    platform: str,
    ttl_sec: Optional[int] = None,
    db_path: Optional[Path] = None,
) -> Optional[CacheEntry]:
    """Return a cached entry if fresher than `ttl_sec` (default: the global
    `DELIVERY_CACHE_TTL_HOURS`).  Pass `ttl_sec=0` to disable caching for
    this read (always returns None)."""
    if ttl_sec == 0:
        return None
    db_path = _resolve_db_path(db_path)
    if ttl_sec is None:
        ttl_sec = DELIVERY_CACHE_TTL_HOURS * 3600
    cutoff = _now() - dt.timedelta(seconds=ttl_sec)
    with _db.connect(db_path) as conn:
        row = conn.execute(
            "SELECT payload, status, error, fetched_at "
            "FROM delivery_cache WHERE address_norm=? AND platform=?",
            (address_norm, platform),
        ).fetchone()
    if row is None:
        return None
    fetched = dt.datetime.fromisoformat(row["fetched_at"])
    if fetched < cutoff:
        return None
    payload = json.loads(row["payload"])
    results = [DeliveryRestaurant(**r) for r in payload]
    return CacheEntry(
        status=row["status"],
        results=results,
        error=row["error"],
        fetched_at=fetched,
    )


def write(
    address_norm: str,
    platform: str,
    status: str,
    results: list[DeliveryRestaurant],
    error: Optional[str],
    db_path: Optional[Path] = None,
) -> None:
    db_path = _resolve_db_path(db_path)
    payload = json.dumps([asdict(r) for r in results])
    with _db.connect(db_path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO delivery_cache "
            "(address_norm, platform, payload, status, error, fetched_at) "
            "VALUES (?,?,?,?,?,?)",
            (address_norm, platform, payload, status, error, _now().isoformat()),
        )


def invalidate(address_norm: str, db_path: Optional[Path] = None) -> int:
    db_path = _resolve_db_path(db_path)
    with _db.connect(db_path) as conn:
        cur = conn.execute(
            "DELETE FROM delivery_cache WHERE address_norm=?", (address_norm,),
        )
        return cur.rowcount
