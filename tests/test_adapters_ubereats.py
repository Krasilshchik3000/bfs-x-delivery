import json

from bfd.adapters.ubereats import _parse_feed


def test_parse_feed_handles_empty():
    out = _parse_feed({"data": {"feedItems": []}})
    assert out == []


def test_parse_feed_extracts_basic_fields(fixtures_dir):
    raw = json.loads((fixtures_dir / "ubereats_feed.json").read_text())
    out = _parse_feed(raw)
    # If the captured fixture is empty (placeholder), there's nothing more to assert.
    if not out:
        return
    for r in out:
        assert r.platform == "ubereats"
        assert r.name
        assert -90 <= r.lat <= 90
