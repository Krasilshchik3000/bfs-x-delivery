import httpx
import respx

from bfd.adapters.wolt import WoltAdapter, WOLT_API_URL


@respx.mock
async def test_wolt_lists_restaurants(fixtures_dir):
    sample = (fixtures_dir / "wolt_response.json").read_text()
    respx.get(WOLT_API_URL).mock(return_value=httpx.Response(200, text=sample))

    adapter = WoltAdapter()
    results = await adapter.list_deliverable(lat=52.4977, lng=13.4134, address="x")

    assert len(results) > 0
    for r in results:
        assert r.platform == "wolt"
        assert r.name
        assert r.url.startswith("https://wolt.com/")
        assert -90 <= r.lat <= 90


@respx.mock
async def test_wolt_dedupes_by_id(fixtures_dir):
    """Same restaurant appearing in multiple sections should appear once."""
    sample = (fixtures_dir / "wolt_response.json").read_text()
    respx.get(WOLT_API_URL).mock(return_value=httpx.Response(200, text=sample))

    adapter = WoltAdapter()
    results = await adapter.list_deliverable(lat=52.4977, lng=13.4134, address="x")
    ids = [r.id for r in results]
    assert len(ids) == len(set(ids))
