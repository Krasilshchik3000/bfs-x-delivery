# BFD — Berlin Food Stories Delivery Check

A local (or Railway-deployable) web tool: enter a Berlin address, see
which Berlin Food Stories recommendations can be delivered there via
Wolt or Uber Eats.

## Quickstart

```bash
# 1. Install dependencies (one time)
uv sync
uv run playwright install chromium   # for the Uber Eats adapter

# 2. Populate the BFS list (run weekly or whenever you remember)
uv run python -m bfd refresh-bfs

# 3. Start the app
uv run python -m bfd serve
# Open http://localhost:8765
```

Type a Berlin address into the input, hit **Check**. First request takes
~5–15 seconds (live calls to Wolt + Uber Eats). Subsequent requests for
the same address are instant — results are cached for 24 h. Hit **Refresh**
to drop the cache for the current address and re-check.

## What works

| Platform | Status |
|----------|--------|
| **Wolt** | ✅ Public JSON API, fast and reliable |
| **Uber Eats** | ✅ Browser-driven via Playwright with cookie-consent dismiss. Occasionally hits captcha → graceful "error" status; the rest of the response still works. |

Lieferando was removed in 2026-05: it added a Cloudflare-blocked feed
that required a residential-IP detour for any public deploy, and every
BFS place it covered was also on Wolt or Uber Eats.

## Tests

```bash
uv run pytest          # ~40 unit + replay tests, all mocked HTTP, no internet
```

Replay fixtures live under `tests/fixtures/`. To capture a fresh BFS fixture
(e.g. after their site changes):

```bash
curl -sL "https://www.berlinfoodstories.com/map.json" \
  -H "User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15" \
  -H "Referer: https://www.berlinfoodstories.com/map" \
  -o tests/fixtures/bfs_map_sample.json
```

## Architecture

- `bfd/bfs.py` — scrapes BFS `map.json` (HTML chunks with `data-lat`/`data-lon` attributes), upserts into SQLite. ~448 pins (some restaurants have multiple locations).
- `bfd/geocode.py` — wraps OpenStreetMap Nominatim. 30-day SQLite cache.
- `bfd/adapters/` — one module per platform, all implementing the same `DeliveryAdapter` Protocol. Adapters run in parallel via `asyncio.gather`.
- `bfd/matcher.py` — pairs each BFS pin to a delivery-platform restaurant by coordinate proximity (≤80 m) with a name-similarity tie-breaker (`rapidfuzz`).
- `bfd/cache.py` — `(address_norm, platform) → results | error` with 24-hour TTL. Errors are also cached, so a busted Uber Eats won't be re-tried for 24 h. (TTL is being reconsidered — see DEPLOY.md / open work.)
- `bfd/main.py` — FastAPI app, mounts the static UI at `/`.
- `bfd/static/index.html` — Vue 3 single-file SPA, no build step.

Full design: [docs/superpowers/specs/2026-05-10-bfd-design.md](docs/superpowers/specs/2026-05-10-bfd-design.md).
Implementation plan: [docs/superpowers/plans/2026-05-10-bfd.md](docs/superpowers/plans/2026-05-10-bfd.md).

## Configuration

All knobs in `bfd/config.py`:
- `MATCH_MAX_DISTANCE_M = 80` — max distance between BFS pin and platform listing
- `MATCH_MIN_NAME_SIMILARITY = 0.5` — minimum fuzzy name match (0–1)
- `DELIVERY_CACHE_TTL_HOURS = 24`
- `GEOCODE_CACHE_TTL_DAYS = 30`

## Notes

- The BFS source list (`map.json`) is scraped, not licensed. Don't republish.
- All caches live in `data/bfd.sqlite` (gitignored). Delete to start fresh.
- Nominatim has a 1-req/s soft limit; the geocode cache means we essentially never hit it.
