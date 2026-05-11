import asyncio
import datetime as dt
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import bfs, cache, geocode, matcher, normalize
from .adapters.wolt import WoltAdapter
from .adapters.ubereats import UberEatsAdapter
from .browser_pool import pool as browser_pool
from .config import (
    BERLIN_BBOX, DEFAULT_USER_AGENT, HTTP_TIMEOUT_SEC,
    PHOTON_URL, PLATFORM_TTL_SEC,
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Start the warm-Chromium pool on app startup, tear it down on shutdown.

    The Uber Eats adapter uses this pool instead of launching its own
    Playwright per request — saves ~5–7 s of cold-start per /api/check.
    """
    try:
        await browser_pool.start()
    except Exception as e:
        # Don't kill the app — UE will fail with AdapterUnavailable per
        # request and the rest of the app still works.
        logger.error("browser pool failed to start: %s", e)
    try:
        yield
    finally:
        await browser_pool.shutdown()


app = FastAPI(title="BFD", lifespan=_lifespan)

# Lieferando was removed (2026-05): every BFS place it covered was
# also on Wolt or Uber Eats, and Lieferando's Cloudflare-blocked feed
# forced an awkward residential-IP detour for any public deploy.
ADAPTERS = [WoltAdapter(), UberEatsAdapter()]
STATIC_DIR = Path(__file__).parent / "static"


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/api/settings")
async def settings() -> dict:
    """Client-side feature flags surfaced from env vars.

    - `home_address`: when set (BFD_HOME_ADDRESS env var), the UI shows a
      one-tap shortcut that pre-fills the address input. Used for personal
      deploys; unset on public ones so no address is hardcoded in the UI.
    """
    import os
    return {"home_address": os.environ.get("BFD_HOME_ADDRESS") or None}


@app.get("/api/suggest")
async def suggest(q: str) -> dict[str, Any]:
    """Address autocomplete via Photon (OSM-backed, free, no API key).

    Constrained to the Berlin bounding box so users don't see suggestions
    for streets in other German cities with the same name.
    """
    q = (q or "").strip()
    if len(q) < 2:
        return {"suggestions": []}

    import httpx
    params = {
        "q": q,
        "bbox": BERLIN_BBOX,
        "limit": "8",
        "lang": "de",
    }
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SEC) as client:
            resp = await client.get(
                PHOTON_URL,
                params=params,
                headers={"User-Agent": DEFAULT_USER_AGENT},
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        return {"suggestions": [], "error": repr(e)}

    out: list[dict] = []
    for feat in data.get("features", []):
        props = feat.get("properties") or {}
        geom = feat.get("geometry") or {}
        coords = geom.get("coordinates") or []
        if len(coords) < 2:
            continue
        lng, lat = coords[0], coords[1]
        # Skip results that escaped the bbox (Photon is loose with this).
        if not (52.3 <= lat <= 52.7 and 13.0 <= lng <= 13.8):
            continue
        # Build a clean one-line label. Photon's name/street/housenumber/postcode
        # are best assembled in our preferred order rather than trusting the
        # built-in "display_name".
        street = props.get("street") or props.get("name") or ""
        housenumber = props.get("housenumber") or ""
        postcode = props.get("postcode") or ""
        city = props.get("city") or props.get("locality") or "Berlin"
        line = " ".join(filter(None, [street, housenumber])).strip()
        rest = ", ".join(filter(None, [postcode, city])).strip()
        label = ", ".join(filter(None, [line, rest])) or props.get("name", "")
        if not label:
            continue
        out.append({"label": label, "lat": lat, "lng": lng})
    # Dedupe by label (Photon sometimes returns the same address with
    # different OSM ids).
    seen: set[str] = set()
    deduped: list[dict] = []
    for s in out:
        if s["label"] in seen:
            continue
        seen.add(s["label"])
        deduped.append(s)
    return {"suggestions": deduped[:8]}


@app.get("/api/check")
async def check(address: str) -> dict[str, Any]:
    try:
        loc = await geocode.geocode(address)
    except geocode.AddressNotFound as e:
        raise HTTPException(status_code=404, detail=str(e))

    addr_norm = normalize.address(address)
    bfs_places = bfs.load_active()
    bfs_records = _as_match_records(bfs_places)

    async def run(adapter):
        ttl = PLATFORM_TTL_SEC.get(adapter.name)  # None → default 24 h
        cached = cache.read(addr_norm, adapter.name, ttl_sec=ttl)
        if cached is not None:
            age = (dt.datetime.utcnow() - cached.fetched_at).total_seconds()
            if cached.status == "error":
                return adapter.name, None, cached.error, True, age
            return adapter.name, cached.results, None, True, age
        try:
            results = await adapter.list_deliverable(
                lat=loc.lat, lng=loc.lng, address=address, postcode=loc.postcode,
            )
            # Only persist when caching is enabled for this platform —
            # avoid the SQLite write churn for the "never cache" lane.
            if ttl is None or ttl > 0:
                cache.write(addr_norm, adapter.name, "ok", results, error=None)
            return adapter.name, results, None, False, 0.0
        except Exception as e:
            err = repr(e)
            if ttl is None or ttl > 0:
                cache.write(addr_norm, adapter.name, "error", [], error=err)
            return adapter.name, None, err, False, 0.0

    outcomes = await asyncio.gather(*(run(a) for a in ADAPTERS))

    platforms: dict[str, Any] = {}
    matches_per_platform: dict[str, dict] = {}
    for plat_name, results, err, from_cache, age_sec in outcomes:
        if err is not None:
            platforms[plat_name] = {
                "status": "error", "error": err,
                "from_cache": from_cache, "age_sec": int(age_sec),
            }
            continue
        matched = matcher.match(bfs_records, results)
        matches_per_platform[plat_name] = matched
        platforms[plat_name] = {
            "status": "ok",
            "matched": len(matched),
            "from_cache": from_cache,
            "age_sec": int(age_sec),
        }

    # Build per-pin records first; we'll dedupe by slug below so chains
    # (multiple BFS pins sharing the same slug) collapse to one card.
    pin_records: list[dict] = []
    for p in bfs_places:
        delivery: list[dict] = []
        for plat_name, matches in matches_per_platform.items():
            r = matches.get(_pin_key(p))
            if r is not None:
                delivery.append({
                    "platform": plat_name,
                    "url": r.url,
                    "is_open": r.is_open,
                })
        pin_records.append({
            "slug": p.slug,
            "bfs_name": p.name,
            "bfs_url": p.bfs_url,
            "image": p.image_url,
            "address": p.address,
            "description": p.description,
            "long_description": p.long_description,
            "neighborhood": p.neighborhood,
            "cuisines": list(p.cuisines),
            "delivery": delivery,
        })

    # Group pins by BFS slug (= one BFS entry, one card).  When a chain has
    # multiple physical locations, each pin may have matched a different
    # platform listing.  We merge the delivery options from ALL pins of the
    # same slug into one card, deduping by platform with this preference:
    #   open > closed > unknown
    # The card itself uses the first pin's metadata; addresses are joined
    # so the UI can show "X locations" or list them.
    by_slug: dict[str, dict] = {}
    for rec in pin_records:
        slug = rec["slug"]
        if slug not in by_slug:
            by_slug[slug] = {
                **rec,
                "addresses": [rec["address"]] if rec["address"] else [],
                "location_count": 1,
            }
            # explicit keys for the merge step below
            by_slug[slug]["long_description"] = rec.get("long_description", "")
        else:
            existing = by_slug[slug]
            existing["location_count"] += 1
            if rec["address"] and rec["address"] not in existing["addresses"]:
                existing["addresses"].append(rec["address"])
            # Merge delivery options
            for new_d in rec["delivery"]:
                # Find any existing entry for the same platform
                same_plat = next(
                    (d for d in existing["delivery"] if d["platform"] == new_d["platform"]),
                    None,
                )
                if same_plat is None:
                    existing["delivery"].append(new_d)
                else:
                    # Replace if the new one has better open status
                    # ranking: True (open) > None (unknown) > False (closed)
                    rank = {True: 2, None: 1, False: 0}
                    if rank[new_d["is_open"]] > rank[same_plat["is_open"]]:
                        same_plat.update(new_d)

    places_out: list[dict] = []
    for entry in by_slug.values():
        any_open = any(d["is_open"] is True for d in entry["delivery"])
        any_listed = bool(entry["delivery"])
        places_out.append({
            "slug": entry["slug"],
            "bfs_name": entry["bfs_name"],
            "bfs_url": entry["bfs_url"],
            "image": entry["image"],
            "address": entry["addresses"][0] if entry["addresses"] else entry["address"],
            "addresses": entry["addresses"],
            "location_count": entry["location_count"],
            "description": entry["description"],
            "long_description": entry["long_description"],
            "neighborhood": entry["neighborhood"],
            "cuisines": entry["cuisines"],
            "delivery": entry["delivery"],
            "any_open_now": any_open,
            "any_listed": any_listed,
        })

    # Sort: open-now first, then listed-but-closed, then unlisted.
    places_out.sort(
        key=lambda p: (not p["any_open_now"], not p["any_listed"], p["bfs_name"].lower())
    )

    return {
        "address": address,
        "coords": {"lat": loc.lat, "lng": loc.lng},
        "checked_at": dt.datetime.utcnow().isoformat() + "Z",
        "platforms": platforms,
        "places": places_out,
    }


def _pin_key(p: bfs.BFSPlace) -> str:
    """Stable identifier for a BFS pin (slug + coords) used as match key."""
    return f"{p.slug}@{p.lat},{p.lng}"


def _as_match_records(bfs_places: list[bfs.BFSPlace]):
    """Wrap BFSPlace so each has the `.id` attribute the matcher expects.

    BFS pins are uniquely identified by (slug, lat, lng) — multi-location
    restaurants share a slug. Use a composite string id.
    """
    class _R:
        __slots__ = ("id", "name", "lat", "lng")
        def __init__(self, p: bfs.BFSPlace):
            self.id = _pin_key(p)
            self.name = p.name
            self.lat = p.lat
            self.lng = p.lng
    return [_R(p) for p in bfs_places]


class RefreshRequest(BaseModel):
    address: str


@app.post("/api/refresh")
async def refresh_address(req: RefreshRequest) -> dict:
    """Invalidate cached delivery checks for an address. Next /api/check will
    hit the platforms live."""
    addr_norm = normalize.address(req.address)
    removed = cache.invalidate(addr_norm)
    return {"invalidated_entries": removed}


@app.post("/api/bfs/refresh")
async def bfs_refresh() -> dict:
    """Re-fetch the BFS map list and persist. Run weekly or so."""
    return await bfs.refresh()


# Static UI mount happens after API routes — order matters.
if STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
else:
    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return "<h1>BFD</h1><p>UI not yet built</p>"
