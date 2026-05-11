import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import httpx

from . import db as _db
from . import normalize
from .config import (
    DB_PATH, GEOCODE_CACHE_TTL_DAYS, HTTP_TIMEOUT_SEC,
    NOMINATIM_URL, NOMINATIM_USER_AGENT,
)


class AddressNotFound(Exception):
    pass


@dataclass(frozen=True)
class GeocodeResult:
    lat: float
    lng: float
    postcode: Optional[str]


async def geocode(address: str, db_path: Path | None = None) -> GeocodeResult:
    if db_path is None:
        from . import config
        db_path = config.DB_PATH
    addr_norm = normalize.address(address)
    cached = _read_cache(addr_norm, db_path)
    if cached:
        return cached

    params = {
        "q": address,
        "format": "jsonv2",
        "addressdetails": "1",
        "countrycodes": "de",
        "limit": "1",
    }
    headers = {"User-Agent": NOMINATIM_USER_AGENT}

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SEC) as client:
        resp = await client.get(NOMINATIM_URL, params=params, headers=headers)
        resp.raise_for_status()
        results = resp.json()

    if not results:
        raise AddressNotFound(f"Could not geocode: {address!r}")

    top = results[0]
    result = GeocodeResult(
        lat=float(top["lat"]),
        lng=float(top["lon"]),
        postcode=(top.get("address") or {}).get("postcode"),
    )
    _write_cache(addr_norm, result, db_path)
    return result


def _read_cache(addr_norm: str, db_path: Path) -> Optional[GeocodeResult]:
    cutoff = (
        dt.datetime.utcnow() - dt.timedelta(days=GEOCODE_CACHE_TTL_DAYS)
    ).isoformat()
    with _db.connect(db_path) as conn:
        row = conn.execute(
            "SELECT lat, lng, postcode FROM geocode_cache "
            "WHERE address_norm=? AND fetched_at>=?",
            (addr_norm, cutoff),
        ).fetchone()
    if row is None:
        return None
    return GeocodeResult(lat=row["lat"], lng=row["lng"], postcode=row["postcode"])


def _write_cache(addr_norm: str, r: GeocodeResult, db_path: Path) -> None:
    now = dt.datetime.utcnow().isoformat()
    with _db.connect(db_path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO geocode_cache(address_norm, lat, lng, postcode, fetched_at) "
            "VALUES (?,?,?,?,?)",
            (addr_norm, r.lat, r.lng, r.postcode, now),
        )
