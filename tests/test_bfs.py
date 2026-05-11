import json
import sqlite3
from pathlib import Path

import httpx
import respx

from bfd import bfs, db
from bfd.config import BFS_MAP_JSON_URL


def test_parse_extracts_known_fields(fixtures_dir):
    raw = json.loads((fixtures_dir / "bfs_map_sample.json").read_text())
    places = bfs.parse_places(raw["placesList"])

    # placesCount=378 unique slugs, but multi-location restaurants produce
    # more than one <li> each; accept a wider window to accommodate both
    # the multi-location expansion and ±10% drift on fixture refreshes.
    assert 340 < len(places) < 500

    for p in places:
        assert isinstance(p.slug, str) and p.slug
        assert isinstance(p.name, str) and p.name
        assert -90 <= p.lat <= 90
        assert -180 <= p.lng <= 180
        assert p.bfs_url.startswith("https://www.berlinfoodstories.com/map/")


def test_parse_specific_known_place(fixtures_dir):
    raw = json.loads((fixtures_dir / "bfs_map_sample.json").read_text())
    places = bfs.parse_places(raw["placesList"])
    by_slug = {p.slug: p for p in places}
    # "goldadeluxe" is in the sample fixture
    assert "goldadeluxe" in by_slug
    p = by_slug["goldadeluxe"]
    assert p.lat == 52.4977785
    assert p.lng == 13.4134444
    assert "Erkelenzdamm" in p.address


@respx.mock
async def test_fetch_returns_places_list(fixtures_dir):
    sample = (fixtures_dir / "bfs_map_sample.json").read_text()
    respx.get(BFS_MAP_JSON_URL).mock(
        return_value=httpx.Response(200, text=sample)
    )
    places = await bfs.fetch_places()
    assert len(places) > 100


def test_persist_inserts_and_marks_deleted(tmp_path: Path):
    db_path = tmp_path / "t.sqlite"
    db.init_db(db_path)

    p1 = bfs.BFSPlace(slug="a", name="A", lat=1.0, lng=2.0, address="", bfs_url="", image_url="")
    p2 = bfs.BFSPlace(slug="b", name="B", lat=3.0, lng=4.0, address="", bfs_url="", image_url="")

    stats = bfs.persist([p1, p2], db_path=db_path)
    assert stats == {"added": 2, "updated": 0, "removed": 0, "total": 2}

    # Re-run with only p1 — p2 should be soft-deleted
    stats = bfs.persist([p1], db_path=db_path)
    assert stats == {"added": 0, "updated": 1, "removed": 1, "total": 1}

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT slug, deleted_at FROM bfs_places ORDER BY slug"
        ).fetchall()
    assert rows[0][0] == "a" and rows[0][1] is None
    assert rows[1][0] == "b" and rows[1][1] is not None


def test_persist_handles_multiple_locations_same_slug(tmp_path: Path):
    """A restaurant with two physical locations should produce two rows."""
    db_path = tmp_path / "t.sqlite"
    db.init_db(db_path)

    loc1 = bfs.BFSPlace(slug="multi", name="Multi", lat=1.0, lng=2.0, address="A", bfs_url="", image_url="")
    loc2 = bfs.BFSPlace(slug="multi", name="Multi", lat=5.0, lng=6.0, address="B", bfs_url="", image_url="")

    stats = bfs.persist([loc1, loc2], db_path=db_path)
    assert stats == {"added": 2, "updated": 0, "removed": 0, "total": 2}


def test_load_active_excludes_soft_deleted(tmp_path: Path):
    db_path = tmp_path / "t.sqlite"
    db.init_db(db_path)
    p1 = bfs.BFSPlace(slug="a", name="A", lat=1.0, lng=2.0, address="", bfs_url="", image_url="")
    p2 = bfs.BFSPlace(slug="b", name="B", lat=3.0, lng=4.0, address="", bfs_url="", image_url="")
    bfs.persist([p1, p2], db_path=db_path)
    bfs.persist([p1], db_path=db_path)

    active = bfs.load_active(db_path=db_path)
    assert len(active) == 1
    assert active[0].slug == "a"
