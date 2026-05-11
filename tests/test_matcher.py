from dataclasses import dataclass

from bfd import matcher


@dataclass
class FakePlace:
    id: int
    name: str
    lat: float
    lng: float


def test_haversine_zero_for_same_point():
    assert matcher.haversine(52.5, 13.4, 52.5, 13.4) == 0


def test_haversine_known_distance():
    # ~1.11 km between (52.5, 13.4) and (52.51, 13.4)
    d = matcher.haversine(52.5, 13.4, 52.51, 13.4)
    assert 1100 < d < 1120


def test_match_finds_close_with_matching_name():
    bfs = [FakePlace(id=1, name="Standard Pizza", lat=52.500, lng=13.400)]
    plat = [FakePlace(id="w1", name="Standard Pizza Berlin", lat=52.5001, lng=13.4001)]
    matches = matcher.match(bfs, plat)
    assert matches == {1: plat[0]}


def test_match_skips_when_too_far():
    bfs = [FakePlace(id=1, name="Standard Pizza", lat=52.500, lng=13.400)]
    plat = [FakePlace(id="w1", name="Standard Pizza", lat=52.510, lng=13.400)]
    assert matcher.match(bfs, plat) == {}


def test_match_skips_when_name_too_different():
    bfs = [FakePlace(id=1, name="Standard Pizza", lat=52.500, lng=13.400)]
    plat = [FakePlace(id="w1", name="Burger King", lat=52.5001, lng=13.4001)]
    assert matcher.match(bfs, plat) == {}


def test_match_picks_best_name_among_close():
    bfs = [FakePlace(id=1, name="Standard Pizza", lat=52.500, lng=13.400)]
    plat = [
        FakePlace(id="w1", name="Some Other Place", lat=52.5001, lng=13.4001),
        FakePlace(id="w2", name="Standard Pizza", lat=52.5002, lng=13.4002),
    ]
    matches = matcher.match(bfs, plat)
    assert matches[1] is plat[1]


# --- name-only fallback (sentinel coords lat=0, lng=0) ---

def test_match_name_only_fallback_matches_high_similarity():
    """A sentinel-coord restaurant (lat=0, lng=0) is matched via name only."""
    bfs_places = [FakePlace(id=1, name="Standard Pizza", lat=52.500, lng=13.400)]
    # Sentinel: lat=0, lng=0 means coords are unknown
    plat = [FakePlace(id="lf1", name="Standard Pizza", lat=0.0, lng=0.0)]
    matches = matcher.match(bfs_places, plat)
    assert 1 in matches
    assert matches[1] is plat[0]


def test_match_name_only_fallback_rejects_low_similarity():
    """A sentinel-coord restaurant with a different name is not matched."""
    bfs_places = [FakePlace(id=1, name="Standard Pizza", lat=52.500, lng=13.400)]
    plat = [FakePlace(id="lf1", name="Burger King", lat=0.0, lng=0.0)]
    matches = matcher.match(bfs_places, plat)
    assert matches == {}


def test_match_name_only_does_not_contaminate_coord_matching():
    """Sentinel results only participate in fallback; they never appear as
    coord-based candidates."""
    bfs_places = [FakePlace(id=1, name="Standard Pizza", lat=52.500, lng=13.400)]
    coord_match = FakePlace(id="w1", name="Standard Pizza", lat=52.5001, lng=13.4001)
    sentinel = FakePlace(id="lf1", name="Standard Pizza", lat=0.0, lng=0.0)
    matches = matcher.match(bfs_places, [coord_match, sentinel])
    # Coord match wins; sentinel should not replace it
    assert matches[1] is coord_match


def test_match_name_only_fallback_is_used_when_no_coord_nearby():
    """When no coord-based match is close enough, the sentinel fallback activates."""
    bfs_places = [FakePlace(id=1, name="Standard Pizza", lat=52.500, lng=13.400)]
    # coord restaurant is too far away
    far_coord = FakePlace(id="w1", name="Standard Pizza", lat=52.600, lng=13.400)
    sentinel = FakePlace(id="lf1", name="Standard Pizza", lat=0.0, lng=0.0)
    matches = matcher.match(bfs_places, [far_coord, sentinel])
    assert matches[1] is sentinel


def test_match_name_only_threshold_is_strict():
    """The name-only fallback requires similarity >= 0.85, stricter than coord-based."""
    bfs_places = [FakePlace(id=1, name="Standard Pizza Berlin", lat=52.500, lng=13.400)]
    # "Schnitzel Hut" has very low similarity (~0.18) — should not match even via fallback
    plat = [FakePlace(id="lf1", name="Schnitzel Hut", lat=0.0, lng=0.0)]
    matches = matcher.match(bfs_places, plat)
    assert matches == {}


def test_is_sentinel():
    """_is_sentinel correctly identifies lat=0, lng=0 records."""
    sentinel = FakePlace(id="x", name="A", lat=0.0, lng=0.0)
    normal = FakePlace(id="y", name="B", lat=52.5, lng=13.4)
    assert matcher._is_sentinel(sentinel) is True
    assert matcher._is_sentinel(normal) is False
