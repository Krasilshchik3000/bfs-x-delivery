"""End-to-end tests for the FastAPI app.

We monkeypatch UberEatsAdapter.list_deliverable because the real
adapter uses Playwright and isn't suitable for unit tests. Wolt and
Nominatim are stubbed via respx.
"""
from pathlib import Path

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from bfd import bfs
from bfd.adapters.ubereats import UberEatsAdapter
from bfd.adapters.wolt import WOLT_API_URL
from bfd.config import NOMINATIM_URL


@pytest.fixture
def app(tmp_path: Path, monkeypatch, fixtures_dir):
    """A fresh FastAPI app pointed at a temp DB, pre-loaded with BFS data."""
    db_path = tmp_path / "e2e.sqlite"
    monkeypatch.setattr("bfd.config.DB_PATH", db_path)
    monkeypatch.setattr("bfd.bfs.DB_PATH", db_path)
    monkeypatch.setattr("bfd.geocode.DB_PATH", db_path)
    monkeypatch.setattr("bfd.main.DB_PATH", db_path, raising=False)

    # Pre-seed BFS data
    import json
    raw = (fixtures_dir / "bfs_map_sample.json").read_text()
    places = bfs.parse_places(json.loads(raw)["placesList"])
    bfs.persist(places, db_path=db_path)

    # Stub Uber Eats with empty results so tests don't try to launch Playwright.
    async def _empty_ue(*args, **kwargs):
        return []
    monkeypatch.setattr(UberEatsAdapter, "list_deliverable", _empty_ue)

    from bfd.main import app
    return app


@respx.mock
def test_check_returns_matched_places(app, fixtures_dir):
    nom_sample = (fixtures_dir / "nominatim_response.json").read_text()
    wolt_sample = (fixtures_dir / "wolt_response.json").read_text()
    respx.get(NOMINATIM_URL).mock(return_value=httpx.Response(200, text=nom_sample))
    respx.get(WOLT_API_URL).mock(return_value=httpx.Response(200, text=wolt_sample))

    with TestClient(app) as client:
        resp = client.get("/api/check", params={"address": "Sonnenallee 100, 12045 Berlin"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["address"]
    assert body["coords"]["lat"]
    assert "platforms" in body
    assert body["platforms"]["wolt"]["status"] == "ok"
    assert isinstance(body["places"], list)
    matched = [p for p in body["places"] if p["delivery"]]
    assert len(matched) > 0, "expected at least one BFS place matched to a Wolt restaurant"


@respx.mock
def test_check_runs_adapters_in_parallel(app, fixtures_dir):
    """Both Wolt and Uber Eats adapters report 'ok' status, even when UE
    returns no matches."""
    nom = (fixtures_dir / "nominatim_response.json").read_text()
    wolt = (fixtures_dir / "wolt_response.json").read_text()
    respx.get(NOMINATIM_URL).mock(return_value=httpx.Response(200, text=nom))
    respx.get(WOLT_API_URL).mock(return_value=httpx.Response(200, text=wolt))

    with TestClient(app) as client:
        resp = client.get("/api/check", params={"address": "Sonnenallee 100, 12045 Berlin"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["platforms"]["wolt"]["status"] == "ok"
    assert body["platforms"]["ubereats"]["status"] == "ok"


def test_check_one_adapter_failure_does_not_break_others(
    app, fixtures_dir, monkeypatch,
):
    """An UberEats failure (raises AdapterUnavailable) does not break Wolt."""
    from bfd.adapters.base import AdapterUnavailable

    async def _stub_fail(*args, **kwargs):
        raise AdapterUnavailable("stubbed failure")
    monkeypatch.setattr(UberEatsAdapter, "list_deliverable", _stub_fail)

    @respx.mock
    def _run():
        nom = (fixtures_dir / "nominatim_response.json").read_text()
        wolt = (fixtures_dir / "wolt_response.json").read_text()
        respx.get(NOMINATIM_URL).mock(return_value=httpx.Response(200, text=nom))
        respx.get(WOLT_API_URL).mock(return_value=httpx.Response(200, text=wolt))

        with TestClient(app) as client:
            resp = client.get("/api/check", params={"address": "Sonnenallee 100, 12045 Berlin"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["platforms"]["wolt"]["status"] == "ok"
        assert body["platforms"]["ubereats"]["status"] == "error"
        assert body["platforms"]["wolt"]["matched"] >= 0
    _run()
