import os
from pathlib import Path

# Load .env if present — used for local development (e.g. setting
# BFD_HOME_ADDRESS without committing it). On Railway the platform
# supplies env vars directly, so load_dotenv is a no-op there.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# DATA_DIR / DB_PATH can be overridden via env vars so deployment
# targets (Railway volumes etc.) can place the SQLite file outside
# the source tree.
DATA_DIR = Path(os.environ.get("BFD_DATA_DIR") or
                (Path(__file__).parent.parent / "data"))
DB_PATH = Path(os.environ.get("BFD_DB_PATH") or (DATA_DIR / "bfd.sqlite"))

# BFS
BFS_MAP_JSON_URL = "https://www.berlinfoodstories.com/map.json"
BFS_MAP_PAGE_URL = "https://www.berlinfoodstories.com/map"

# Caching TTLs
DELIVERY_CACHE_TTL_HOURS = 24    # default if a platform isn't in PLATFORM_TTL_SEC
GEOCODE_CACHE_TTL_DAYS = 30

# Per-platform delivery-cache TTL in seconds. Choices made 2026-05:
#  - wolt: 0 (never cache) — Wolt's JSON API call costs ~1 s and gives a
#          per-venue `online: true|false`, so caching would only buy
#          milliseconds while making open/closed status stale.
#  - ubereats: 60 s — UE via Playwright costs 5–15 s. 60 s lets repeat
#          requests for the same address (e.g. UI refresh button)
#          coalesce, but is short enough that "currently open" is fresh.
PLATFORM_TTL_SEC: dict[str, int] = {
    "wolt": 0,
    "ubereats": 60,
}

# Matching thresholds
MATCH_MAX_DISTANCE_M = 80
MATCH_MIN_NAME_SIMILARITY = 0.5

# HTTP
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"
)
HTTP_TIMEOUT_SEC = 30.0

# Nominatim
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_USER_AGENT = "bfs-x-delivery/0.1"

# Photon (also OSM-backed, but designed for typeahead autocomplete).
# Free, no API key needed, hosted by Komoot.
PHOTON_URL = "https://photon.komoot.io/api"
# Rough Berlin bounding box: SW corner to NE corner.
BERLIN_BBOX = "13.0884,52.3382,13.7611,52.6755"
