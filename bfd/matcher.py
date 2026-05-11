import math
from typing import Iterable, Protocol, TypeVar

from .config import MATCH_MAX_DISTANCE_M, MATCH_MIN_NAME_SIMILARITY
from .normalize import name_similarity

# Minimum name-similarity score required when falling back to name-only
# matching (used when a platform restaurant has sentinel coords lat=0, lng=0).
MATCH_NAME_ONLY_MIN_SIMILARITY = 0.85


class HasCoords(Protocol):
    name: str
    lat: float
    lng: float


T = TypeVar("T", bound=HasCoords)
B = TypeVar("B", bound=HasCoords)


def haversine(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance in metres."""
    r = 6_371_000  # Earth radius in metres
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _is_sentinel(r) -> bool:
    """Return True for platform results with sentinel coords (lat=0, lng=0).

    Adapters that cannot retrieve coordinates (e.g. DOM-only scrape) emit
    lat=0/lng=0 as a sentinel.  These records must be matched by name alone.
    """
    return r.lat == 0.0 and r.lng == 0.0


def match(
    bfs_places: Iterable[B],
    platform_results: Iterable[T],
    max_distance_m: float = MATCH_MAX_DISTANCE_M,
    min_name_sim: float = MATCH_MIN_NAME_SIMILARITY,
    name_only_min_sim: float = MATCH_NAME_ONLY_MIN_SIMILARITY,
) -> dict[int | str, T]:
    """Map each BFS place's `id` to its best platform match, if any.

    Primary path (coordinates available):
    - The platform restaurant is within `max_distance_m` of the BFS coords, AND
    - among the close candidates the one with highest name similarity passes
      `min_name_sim`.

    Fallback path (sentinel coords lat=0, lng=0):
    - Used when an adapter cannot retrieve coordinates (e.g. DOM-only scrape).
    - Only the name is compared; the similarity threshold is stricter
      (`name_only_min_sim`, default 0.85) to compensate for the lack of a
      geographic filter.
    - A sentinel result only participates in name-only matching — it is never
      considered a "nearby" restaurant for coord-based matching.
    """
    results: dict[int | str, T] = {}
    plat_list = list(platform_results)

    coord_plat = [r for r in plat_list if not _is_sentinel(r)]
    name_only_plat = [r for r in plat_list if _is_sentinel(r)]

    for bfs in bfs_places:
        # --- coordinate-based matching (primary) ---
        nearby = [
            r for r in coord_plat
            if haversine(bfs.lat, bfs.lng, r.lat, r.lng) <= max_distance_m
        ]
        if nearby:
            best = max(nearby, key=lambda r: name_similarity(bfs.name, r.name))
            if name_similarity(bfs.name, best.name) >= min_name_sim:
                results[bfs.id] = best
                continue

        # --- name-only fallback (sentinel coords) ---
        if name_only_plat:
            best_no = max(name_only_plat, key=lambda r: name_similarity(bfs.name, r.name))
            if name_similarity(bfs.name, best_no.name) >= name_only_min_sim:
                results[bfs.id] = best_no

    return results
