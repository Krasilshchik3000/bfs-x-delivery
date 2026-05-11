# BFD — Berlin Food Stories Delivery Check

**Status:** design
**Date:** 2026-05-10

## Problem

Berlin Food Stories (BFS) curates a list of ~378 recommended places to eat in Berlin, displayed on an interactive map at `https://www.berlinfoodstories.com/map`. Wolt, Lieferando, and Uber Eats each have their own delivery catalogs filtered by delivery zone. Manually checking each platform for every BFS recommendation against your delivery address is tedious.

Goal: a local web tool where the user enters a Berlin address and sees which BFS-recommended restaurants can deliver there, with deep links to the corresponding listing on Wolt / Lieferando / Uber Eats.

## Scope and constraints

- **Local single-user tool.** Runs on the user's Mac at `localhost`. No public hosting, no auth.
- **Berlin only.** All BFS places are in Berlin; we don't need to handle other cities.
- **Three delivery platforms** to check, in priority order: Wolt, Lieferando, Uber Eats.
- **Address input field**, not a fixed home address — user can check arbitrary Berlin addresses.
- **24-hour cache per `(address, platform)`**, with a manual refresh button. BFS list is cached separately and refreshed weekly (manual trigger acceptable).
- Out of scope: account integration, prices, menu contents, opening hours, restaurant ratings, ordering directly through the tool.

## Architecture

```
Browser (Vue 3 single-file in static/index.html)
        │
        │  GET /api/check?address=...
        ▼
FastAPI app (bfd/main.py)
        │
        ├─ bfd.bfs           BFS scraper (weekly run, populates SQLite)
        ├─ bfd.geocode       Address -> (lat, lng) via Nominatim
        ├─ bfd.adapters.*    Wolt / Lieferando / Uber Eats adapters
        ├─ bfd.matcher       Coordinate + name matching
        └─ bfd.cache         SQLite TTL cache
        │
        ▼
SQLite (data/bfd.sqlite)
```

The platform adapters share an interface so they can be swapped, parallelised, and degraded independently:

```python
class DeliveryAdapter(Protocol):
    name: str  # "wolt" | "lieferando" | "ubereats"
    async def list_deliverable(
        self, lat: float, lng: float, address: str
    ) -> list[DeliveryRestaurant]: ...
```

If an adapter raises, the request handler logs the error and the UI shows a per-platform "check failed" banner with a retry button. The other platforms still produce results.

## Data flow for a check

1. User enters an address, clicks **Check**.
2. Backend normalises the address string (whitespace, case) to use as a cache key.
3. Backend geocodes the address → `(lat, lng)`. Geocode results are cached for 30 days.
4. For each platform, the cache is consulted: `(address_normalised, platform)`. If fresh (< 24 h), use cached restaurants. Otherwise call the adapter.
5. Platform adapters run **in parallel** (`asyncio.gather` with `return_exceptions=True`).
6. Matcher cross-references the BFS list (378 places) with each platform's results, by coordinates with a name tie-breaker.
7. Response shape:
   ```json
   {
     "address": "Sonnenallee 100, 12045 Berlin",
     "coords": {"lat": 52.48, "lng": 13.43},
     "checked_at": "2026-05-10T18:42:00Z",
     "platforms": {
       "wolt": {"status": "ok", "matched": 21, "from_cache": false},
       "lieferando": {"status": "ok", "matched": 18, "from_cache": false},
       "ubereats": {"status": "error", "error": "captcha"}
     },
     "places": [
       {
         "bfs_name": "Standard Pizza",
         "bfs_url": "https://www.berlinfoodstories.com/map/standard",
         "image": "https://...",
         "neighborhood": "Prenzlauer Berg",
         "delivery": [
           {"platform": "wolt", "url": "https://wolt.com/.../standard-pizza"},
           {"platform": "lieferando", "url": "https://lieferando.de/..."}
         ]
       },
       ...
     ]
   }
   ```
   Places with empty `delivery` are still returned but rendered separately ("not deliverable").

## BFS scraper

Endpoint discovered: `GET https://www.berlinfoodstories.com/map.json` returns
```json
{ "placesList": "<HTML string>", "placesFilters": "...", "placesCount": 378 }
```

The `placesList` HTML is a sequence of `<li class="places__list__place" data-lat="..." data-lon="..." data-name="..." data-address="...">…</li>` entries. The scraper:

1. Fetches `map.json` with browser-like headers.
2. Parses each `<li>` with BeautifulSoup, extracting:
   - `name` (data-name, slug)
   - `display_name` (from inner `<h3>`)
   - `lat`, `lng` (data-lat, data-lon)
   - `address` (data-address — typically `Street 12, 10999`)
   - `bfs_url` (from inner `<a href>`)
   - `image_url` (from inner `<img src>`)
   - `categories` (parsed from teaser tags)
3. Upserts into `bfs_places` table by `name` (slug-like). Soft-delete entries no longer present in the latest fetch.

Refresh trigger: `python -m bfd.bfs refresh` CLI, plus a button in the UI. Not on every request — BFS adds ~a few places a month, not hourly.

## Geocoding

Use **Nominatim** (`https://nominatim.openstreetmap.org/search`) with `User-Agent: BFD/0.1 (...)` and `countrycodes=de`, `viewbox` constrained to Berlin. Free, no API key.

- Cache geocode results in SQLite for 30 days, keyed by normalised address.
- If geocoding fails or returns a non-Berlin result, surface "Address not found in Berlin" to the user.

Fallback: if Nominatim rate-limits, fall back to `geopy.geocoders.Photon` (also free, OSM-backed).

## Platform adapters

### Wolt
- Endpoint: `GET https://restaurant-api.wolt.com/v1/pages/restaurants?lat={lat}&lon={lng}` (public, no auth required).
- Returns sections of restaurants delivering to the location. Walk all sections, dedupe by `slug`.
- Each item gives `name`, `location.coordinates`, `slug` → URL: `https://wolt.com/en/deu/berlin/restaurant/{slug}`.
- Pure HTTP via `httpx`. No browser.

### Lieferando
- Endpoint: `GET https://cw-api.takeaway.com/api/v33/restaurants?postalCode={plz}&latitude={lat}&longitude={lng}&country=de` (Just Eat / Takeaway).
- Postal code derived from the geocoder result (Nominatim returns `postcode`).
- Returns restaurant list with `name`, `latitude`, `longitude`, `primarySlug` → URL: `https://lieferando.de/menu/{slug}`.
- May require a `Cookie` / `X-Country-Code: de` header. If a plain request returns 403, retry with a single Playwright session to harvest cookies, then HTTP from there. (Adapter handles its own bootstrap.)

### Uber Eats
- No clean public API. Use **Playwright** (Chromium):
  1. Launch with realistic UA, `de-DE` locale, Berlin geolocation.
  2. Navigate to `https://www.ubereats.com/de`.
  3. Type address into the address autocomplete, pick the first suggestion.
  4. Wait for the feed page; intercept the `getFeedV1` GraphQL response and parse it.
- Returns store list with name, location, `slug`. URL: `https://www.ubereats.com/de/store/{slug}/{uuid}`.
- This is the most fragile adapter. If we hit a captcha or layout change, the adapter raises `AdapterUnavailable`; the request handler reports `status: "error"` for Uber and the UI degrades gracefully.

All adapters share a common HTTP client (`httpx.AsyncClient`) with sensible timeouts (10 s connect, 30 s total) and retries (1 retry on 5xx).

## Matcher

Coordinate-first matching with name tie-breaker:

```python
def match(bfs_places, platform_results, max_distance_m=80, min_name_sim=0.5):
    matches = {}
    for bfs in bfs_places:
        nearby = [
            r for r in platform_results
            if haversine(bfs.coords, r.coords) <= max_distance_m
        ]
        if not nearby:
            continue
        best = max(nearby, key=lambda r: name_sim(bfs.name, r.name))
        if name_sim(bfs.name, best.name) >= min_name_sim:
            matches[bfs.id] = best
    return matches
```

- `haversine` from `geopy.distance.distance` or hand-rolled.
- `name_sim` via `rapidfuzz.fuzz.token_set_ratio` after normalising (lowercase, strip umlauts, drop punctuation).
- Thresholds (`80 m`, `0.5`) are picked conservatively and validated against fixture pairs in tests. They're constants in `bfd/matcher.py` so tuning is easy if mismatches show up in practice.

Edge cases handled:
- **Chains** (Cocolo Ramen has 3 locations): platform returns multiple within 80 m of an unrelated BFS pin only if they're geographically clustered, which is unusual. Closest wins.
- **Same building, different restaurant**: name-sim threshold rejects unrelated neighbours.
- **Slightly off coordinates** (platforms occasionally use parcel centroids): 80 m tolerates this; the threshold is reviewed against real data during initial testing.

## Caching

SQLite, single file at `data/bfd.sqlite`. Tables:

```sql
CREATE TABLE bfs_places (
    id          INTEGER PRIMARY KEY,
    slug        TEXT UNIQUE NOT NULL,
    name        TEXT NOT NULL,
    lat         REAL NOT NULL,
    lng         REAL NOT NULL,
    address     TEXT,
    bfs_url     TEXT,
    image_url   TEXT,
    categories  TEXT,           -- JSON array
    last_seen   TIMESTAMP NOT NULL,
    deleted_at  TIMESTAMP        -- soft delete
);

CREATE TABLE geocode_cache (
    address_norm TEXT PRIMARY KEY,
    lat REAL NOT NULL,
    lng REAL NOT NULL,
    postcode TEXT,
    fetched_at TIMESTAMP NOT NULL
);

CREATE TABLE delivery_cache (
    address_norm TEXT NOT NULL,
    platform TEXT NOT NULL,
    payload TEXT NOT NULL,        -- JSON list of restaurants
    status TEXT NOT NULL,         -- "ok" | "error"
    error TEXT,
    fetched_at TIMESTAMP NOT NULL,
    PRIMARY KEY (address_norm, platform)
);
```

TTLs: `geocode_cache` 30 days, `delivery_cache` 24 hours. The "Refresh" button in the UI invalidates `delivery_cache` for the current address.

## API surface

- `GET /api/check?address={addr}` → JSON shape from "Data flow" above.
- `POST /api/refresh` body `{address}` → invalidates cache for that address, returns nothing.
- `POST /api/bfs/refresh` → triggers BFS scrape, returns `{added, removed, total}`.
- `GET /` → serves the Vue 3 single-file UI.

## Frontend

Single HTML file with Vue 3 (CDN, no build step). State:
- `address` (input)
- `loading` (bool)
- `result` (response object) or `error`

Layout: address input + Check + Refresh, then a results table. Per-platform error banners shown above the table when applicable. "Not deliverable" places shown collapsed at the bottom.

No styling framework — minimal CSS in a `<style>` block. The point is utility, not visual polish.

## Error handling

| Failure mode | Behaviour |
|--------------|-----------|
| Adapter raises | log; mark platform `status: "error"`; UI shows banner with retry. Other platforms unaffected. |
| Geocoder fails | 4xx with "Address not found in Berlin"; UI shows error in input area. |
| BFS scrape fails | Use last known data; UI shows stale-data banner with last refresh time. |
| All three adapters fail | Request returns 200 with all platforms in error state; UI is honest about what we know. |

No retries in the request path beyond what's inside each adapter (1 retry on 5xx). The user pressing Refresh is the explicit retry mechanism.

## Testing

- **Replay tests for HTTP-based components** (BFS, Wolt, Lieferando, geocode): record one real response per fixture, store under `tests/fixtures/`, mock `httpx` to replay. Fast, deterministic, runs in CI.
- **Matcher tests**: table of ~10 hand-picked pairs from real data — exact match, umlaut differences, chain disambiguation, near-miss negatives. Pure-function, no I/O.
- **Uber Eats**: live smoke test marked `@pytest.mark.live`, off by default. Asserts that a known address near a major restaurant returns a non-empty list.
- **End-to-end**: one integration test with all adapters mocked, hitting the real FastAPI app via `httpx.AsyncClient`, verifying shape and degradation behaviour.

## Project layout

```
BFD/
├── pyproject.toml
├── README.md
├── bfd/
│   ├── __init__.py
│   ├── main.py              # FastAPI app + routes
│   ├── config.py            # constants (TTLs, thresholds)
│   ├── bfs.py               # scraper + CLI entry
│   ├── geocode.py
│   ├── matcher.py
│   ├── cache.py             # SQLite helpers
│   ├── adapters/
│   │   ├── __init__.py
│   │   ├── base.py
│   │   ├── wolt.py
│   │   ├── lieferando.py
│   │   └── ubereats.py
│   └── static/
│       └── index.html
├── tests/
│   ├── fixtures/
│   ├── test_bfs.py
│   ├── test_matcher.py
│   ├── test_adapters.py
│   ├── test_cache.py
│   └── test_e2e.py
└── data/
    └── bfd.sqlite           # gitignored
```

## Implementation milestones

Ordered by dependency, not by calendar — the user paces this themselves.

1. **Skeleton + BFS scraper.** `pyproject.toml`, FastAPI hello-world at `/`, `bfd.bfs.refresh()` populates SQLite from `map.json`. Verifies our parse handles all 378 places.
2. **Geocoder + matcher + Wolt adapter.** Smallest end-to-end slice: address → coords → Wolt → matched BFS list → JSON response. UI is bare but shows a real table.
3. **Lieferando adapter, parallel adapter execution.** Adapters run with `asyncio.gather`. Per-platform error handling formalised. UI gains the per-platform banners.
4. **Uber Eats via Playwright.** Most fragile piece, isolated by interface so a failure doesn't block the rest. Live smoke test added.
5. **24-hour cache + Refresh button.** Cache layer wraps each adapter call. UI refresh invalidates entry for the current address.
6. **Polish.** README with run instructions, "not deliverable" collapsible section in UI, weekly BFS refresh hooked to a CLI command the user can cron locally.

After milestone 2 the tool is genuinely useful for the user; milestones 3-6 are quality-of-life on top of that.

## Open questions / decisions deferred

- **Whether to store a JSON snapshot of each `map.json` fetch** for diffing over time. Probably no — keep simple, rely on `last_seen` / `deleted_at`.
- **Whether BFS list refresh should be automated** (cron) or stay manual. Default: manual; revisit after a month of use.
- **Whether to expose the matched-but-low-confidence candidates to the user** (e.g., name-sim 0.4–0.5). Default: hide, log internally; revisit if users report missing matches.
