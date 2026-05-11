import datetime as dt
from pathlib import Path

import httpx
import pytest
import respx

from bfd import db, geocode
from bfd.config import NOMINATIM_URL


@respx.mock
async def test_geocode_calls_nominatim_and_caches(tmp_path: Path, fixtures_dir):
    sample = (fixtures_dir / "nominatim_response.json").read_text()
    respx.get(NOMINATIM_URL).mock(return_value=httpx.Response(200, text=sample))

    db_path = tmp_path / "t.sqlite"
    db.init_db(db_path)

    result = await geocode.geocode("Sonnenallee 100, 12045 Berlin", db_path=db_path)
    assert 52.0 < result.lat < 53.0
    assert 13.0 < result.lng < 14.0
    assert result.postcode  # Nominatim returns one for this address

    # Second call should hit cache (no extra HTTP request)
    respx.calls.clear()
    result2 = await geocode.geocode("Sonnenallee 100, 12045 Berlin", db_path=db_path)
    assert result2.lat == result.lat
    assert respx.calls.call_count == 0


@respx.mock
async def test_geocode_raises_when_not_found(tmp_path: Path):
    respx.get(NOMINATIM_URL).mock(return_value=httpx.Response(200, json=[]))
    db_path = tmp_path / "t.sqlite"
    db.init_db(db_path)
    with pytest.raises(geocode.AddressNotFound):
        await geocode.geocode("not a real address xyzzy", db_path=db_path)
