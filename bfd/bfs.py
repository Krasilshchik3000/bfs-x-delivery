import asyncio
import datetime as dt
import json
import logging
from dataclasses import dataclass, replace
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

from .config import (
    BFS_MAP_JSON_URL, BFS_MAP_PAGE_URL, DB_PATH,
    DEFAULT_USER_AGENT, HTTP_TIMEOUT_SEC,
)
from . import db as _db

logger = logging.getLogger(__name__)

# Concurrency cap for fetching individual place pages (long descriptions).
# BFS is served by Plausible-static infra and tolerates this fine.
_LONG_DESC_CONCURRENCY = 12


@dataclass(frozen=True)
class BFSPlace:
    slug: str
    name: str
    lat: float
    lng: float
    address: str
    bfs_url: str
    image_url: str
    description: str = ""          # short "top tip" from map.json
    long_description: str = ""     # full review from individual page
    neighborhood: str = ""
    cuisines: tuple[str, ...] = ()


def parse_places(places_html: str) -> list[BFSPlace]:
    """Parse the `placesList` HTML chunk from BFS map.json."""
    soup = BeautifulSoup(places_html, "html.parser")
    out: list[BFSPlace] = []
    for li in soup.select("li.places__list__place"):
        slug = li.get("data-name") or ""
        lat = float(li["data-lat"])
        lng = float(li["data-lon"])
        address = li.get("data-address", "")

        anchor = li.select_one("a[href]")
        bfs_url = anchor["href"] if anchor else ""

        h3 = li.select_one("h3")
        display_name = h3.get_text(strip=True) if h3 else slug

        img = li.select_one("img[src]")
        image_url = img["src"] if img else ""

        # Description ("top tip" paragraph inside the expandable card)
        tip = li.select_one(".top-tip__text")
        description = tip.get_text(" ", strip=True) if tip else ""

        # Categories: t-tag--sq is the neighborhood, plain t-tag is cuisine.
        neighborhood = ""
        cuisines: list[str] = []
        for tag in li.select(".article-teaser__categories .t-tag"):
            text = tag.get_text(strip=True)
            if "t-tag--sq" in tag.get("class", []):
                if not neighborhood:
                    neighborhood = text
            elif text:
                cuisines.append(text)

        out.append(BFSPlace(
            slug=slug,
            name=display_name or slug,
            lat=lat,
            lng=lng,
            address=address,
            bfs_url=bfs_url,
            image_url=image_url,
            description=description,
            neighborhood=neighborhood,
            cuisines=tuple(cuisines),
        ))
    return out


async def _fetch_with_httpx(client: httpx.AsyncClient, url: str) -> tuple[int, str]:
    """GET a URL with browser-y headers. Returns (status, body)."""
    headers = {
        "User-Agent": DEFAULT_USER_AGENT,
        "Referer": BFS_MAP_PAGE_URL,
        "Accept": "application/json,text/html,*/*",
    }
    resp = await client.get(url, headers=headers)
    return resp.status_code, resp.text


def _looks_like_captcha(body: str) -> bool:
    """BFS serves SiteGround's sgcaptcha challenge to datacenter IPs.
    The challenge body is a tiny HTML page with a meta-refresh to
    /.well-known/sgcaptcha/.  Detect that so we know to fall back to a
    cookie-bearing browser session."""
    return "sgcaptcha" in body or "meta http-equiv=\"refresh\"" in body[:300]


async def _solve_captcha_via_browser() -> dict[str, str]:
    """Drive a Playwright session through the BFS captcha challenge and
    return a dict of cookies suitable for handing to httpx.  Chromium
    follows the challenge's meta-refresh and the server sets a session
    cookie that lets later (cookie-bearing) httpx requests through."""
    from .browser_pool import pool as _pool
    async with _pool.acquire() as browser:
        context = await browser.new_context(
            user_agent=DEFAULT_USER_AGENT,
        )
        try:
            page = await context.new_page()
            # Visit the human-facing map page; this triggers the same
            # challenge but completes the JS hop end-to-end. Use
            # `networkidle` so we wait through the meta-refresh hop.
            try:
                await page.goto(
                    BFS_MAP_PAGE_URL,
                    wait_until="networkidle",
                    timeout=30_000,
                )
            except Exception as e:
                logger.warning("BFS captcha page goto failed: %s", e)
            await asyncio.sleep(3)  # belt-and-braces — let post-challenge JS settle
            # Grab ALL cookies (no domain filter) and keep just the
            # berlinfoodstories ones. Filtering by URL up front sometimes
            # misses cookies that Chromium normalised to a different host.
            all_cookies = await context.cookies()
            kept = [
                c for c in all_cookies
                if "berlinfoodstories.com" in (c.get("domain") or "")
            ]
            logger.info(
                "captcha solve: page url=%s, %d cookies total, %d kept (%s)",
                page.url, len(all_cookies), len(kept),
                [c["name"] for c in kept],
            )
        finally:
            try:
                await context.close()
            except Exception:
                pass
    return {c["name"]: c["value"] for c in kept}


async def _ensure_unblocked(client: httpx.AsyncClient) -> None:
    """Probe BFS once; if the IP is captcha-gated, solve once and stick the
    session cookies onto `client` so all subsequent requests on this client
    go straight through.
    """
    status, body = await _fetch_with_httpx(client, BFS_MAP_JSON_URL)
    if status == 200 and not _looks_like_captcha(body):
        return
    logger.warning(
        "BFS captcha challenge from IP (status=%s); solving via Playwright",
        status,
    )
    cookies = await _solve_captcha_via_browser()
    if not cookies:
        raise RuntimeError("BFS captcha solve returned no cookies")
    client.cookies.update(cookies)
    logger.info("BFS captcha solved, %d cookies attached", len(cookies))


async def fetch_places(client: httpx.AsyncClient | None = None) -> list[BFSPlace]:
    """Fetch BFS map.json, handling the SiteGround captcha on datacenter IPs.

    If `client` is provided (e.g. by refresh()), captcha cookies are
    persisted on it so subsequent individual-page fetches don't have to
    solve the challenge again.
    """
    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(timeout=HTTP_TIMEOUT_SEC)
    try:
        await _ensure_unblocked(client)
        status, body = await _fetch_with_httpx(client, BFS_MAP_JSON_URL)
        if status != 200 or _looks_like_captcha(body):
            raise RuntimeError(
                f"BFS still serves captcha after solve "
                f"(status={status}, head={body[:200]!r})"
            )
    finally:
        if own_client:
            await client.aclose()

    try:
        data = json.loads(body)
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"BFS returned non-JSON ({len(body)} bytes, head: {body[:200]!r})"
        ) from e
    return parse_places(data["placesList"])


def extract_long_description(html: str) -> str:
    """Pull the full review text from an individual BFS place page.

    The review lives in <div class="place__description"> (verified
    against goldadeluxe, ~2026-05).  We strip whitespace and join
    paragraphs with single newlines so the UI can render it as a
    natural paragraph block.
    """
    soup = BeautifulSoup(html, "html.parser")
    block = soup.select_one(".place__description")
    if block is None:
        return ""
    # Each <p> is one paragraph; join with blank line.
    paras = [p.get_text(" ", strip=True) for p in block.find_all("p")]
    if not paras:
        paras = [block.get_text(" ", strip=True)]
    return "\n\n".join(p for p in paras if p)


async def fetch_long_descriptions(
    places: list[BFSPlace],
    concurrency: int = _LONG_DESC_CONCURRENCY,
    client: httpx.AsyncClient | None = None,
) -> list[BFSPlace]:
    """For each place, fetch its individual BFS page and extract the long
    review text.  Returns a new list of BFSPlace with `long_description`
    populated.  Failures (404, timeouts, captcha pages) leave
    long_description as "" and are logged at WARNING.

    If `client` is provided, its cookies are reused — important on
    captcha-gated IPs where _ensure_unblocked has already attached a
    session cookie that lets bare httpx pass through.
    """
    headers = {
        "User-Agent": DEFAULT_USER_AGENT,
        "Referer": BFS_MAP_PAGE_URL,
    }
    sem = asyncio.Semaphore(concurrency)
    results: list[BFSPlace] = [None] * len(places)  # type: ignore
    own_client = client is None

    if own_client:
        client = httpx.AsyncClient(timeout=HTTP_TIMEOUT_SEC, headers=headers)
    else:
        # Apply the headers on the existing client for this call;
        # they'll stay set, which is harmless for the next call too.
        client.headers.update(headers)

    try:
        async def one(idx: int, place: BFSPlace) -> None:
            async with sem:
                try:
                    resp = await client.get(place.bfs_url)
                    resp.raise_for_status()
                    text = resp.text
                    if _looks_like_captcha(text):
                        # Cookie expired or stripped — note and skip; the
                        # caller's next refresh() will re-solve.
                        raise RuntimeError("captcha challenge on individual page")
                    long_desc = extract_long_description(text)
                except Exception as e:
                    logger.warning("long-desc fetch failed for %s: %s", place.slug, e)
                    long_desc = ""
                results[idx] = replace(place, long_description=long_desc)

        await asyncio.gather(*(one(i, p) for i, p in enumerate(places)))
    finally:
        if own_client:
            await client.aclose()

    return results


def persist(places: list[BFSPlace], db_path: Path | None = None) -> dict:
    if db_path is None:
        from . import config
        db_path = config.DB_PATH
    """Upsert places (keyed on slug+lat+lng), soft-delete those no longer present."""
    _db.init_db(db_path)
    now = dt.datetime.now(dt.UTC).isoformat()

    # Deduplicate incoming list — keep the last occurrence of each (slug, lat, lng).
    # The BFS HTML occasionally has the same pin twice; taking the last entry is fine.
    deduped: dict[tuple, BFSPlace] = {}
    for p in places:
        deduped[(p.slug, p.lat, p.lng)] = p
    places = list(deduped.values())

    # Use (slug, lat, lng) as the natural key — each pin is identified
    # by restaurant + location, since multi-location restaurants share a slug.
    seen_keys = {(p.slug, p.lat, p.lng) for p in places}

    added = updated = 0
    with _db.connect(db_path) as conn:
        existing = {
            (row["slug"], row["lat"], row["lng"]): row["id"]
            for row in conn.execute("SELECT id, slug, lat, lng FROM bfs_places")
        }
        for p in places:
            key = (p.slug, p.lat, p.lng)
            cuisines_json = json.dumps(list(p.cuisines))
            if key in existing:
                # Preserve the existing long_description if the new payload
                # has none (refresh() only re-fetches long descriptions for
                # places missing one — see refresh()).
                if p.long_description:
                    conn.execute(
                        """
                        UPDATE bfs_places
                           SET name=?, address=?, bfs_url=?, image_url=?,
                               description=?, long_description=?,
                               neighborhood=?, categories=?,
                               last_seen=?, deleted_at=NULL
                         WHERE slug=? AND lat=? AND lng=?
                        """,
                        (p.name, p.address, p.bfs_url, p.image_url,
                         p.description, p.long_description,
                         p.neighborhood, cuisines_json,
                         now, p.slug, p.lat, p.lng),
                    )
                else:
                    conn.execute(
                        """
                        UPDATE bfs_places
                           SET name=?, address=?, bfs_url=?, image_url=?,
                               description=?, neighborhood=?, categories=?,
                               last_seen=?, deleted_at=NULL
                         WHERE slug=? AND lat=? AND lng=?
                        """,
                        (p.name, p.address, p.bfs_url, p.image_url,
                         p.description, p.neighborhood, cuisines_json,
                         now, p.slug, p.lat, p.lng),
                    )
                updated += 1
            else:
                conn.execute(
                    """
                    INSERT INTO bfs_places(
                        slug, name, lat, lng, address, bfs_url, image_url,
                        description, long_description, neighborhood, categories, last_seen
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (p.slug, p.name, p.lat, p.lng, p.address, p.bfs_url, p.image_url,
                     p.description, p.long_description, p.neighborhood, cuisines_json, now),
                )
                added += 1

        # Soft-delete: any active row whose key isn't in seen_keys
        cursor = conn.execute(
            "SELECT id, slug, lat, lng FROM bfs_places WHERE deleted_at IS NULL"
        )
        to_delete = [
            row["id"] for row in cursor
            if (row["slug"], row["lat"], row["lng"]) not in seen_keys
        ]
        if to_delete:
            placeholders = ",".join("?" * len(to_delete))
            conn.execute(
                f"UPDATE bfs_places SET deleted_at=? WHERE id IN ({placeholders})",
                (now, *to_delete),
            )
        removed = len(to_delete)

    return {
        "added": added,
        "updated": updated,
        "removed": removed,
        "total": added + updated,
    }


def load_active(db_path: Path | None = None) -> list[BFSPlace]:
    if db_path is None:
        from . import config
        db_path = config.DB_PATH
    """Read all active (non-deleted) BFS places from SQLite."""
    _db.init_db(db_path)
    with _db.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT slug, name, lat, lng, address, bfs_url, image_url, "
            "description, long_description, neighborhood, categories "
            "FROM bfs_places WHERE deleted_at IS NULL"
        ).fetchall()
    out: list[BFSPlace] = []
    for r in rows:
        d = dict(r)
        cats_raw = d.pop("categories", None) or "[]"
        try:
            cuisines = tuple(json.loads(cats_raw))
        except (TypeError, ValueError):
            cuisines = ()
        out.append(BFSPlace(
            slug=d["slug"],
            name=d["name"],
            lat=d["lat"],
            lng=d["lng"],
            address=d["address"] or "",
            bfs_url=d["bfs_url"] or "",
            image_url=d["image_url"] or "",
            description=d.get("description") or "",
            long_description=d.get("long_description") or "",
            neighborhood=d.get("neighborhood") or "",
            cuisines=cuisines,
        ))
    return out


def _slugs_missing_long_desc(db_path: Path) -> set[str]:
    """Return slugs whose long_description is NULL or empty.  Used by
    refresh() to avoid re-fetching all 448 individual pages when most are
    already populated."""
    _db.init_db(db_path)
    with _db.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT slug FROM bfs_places "
            "WHERE deleted_at IS NULL AND (long_description IS NULL OR long_description='')"
        ).fetchall()
    return {r["slug"] for r in rows}


async def refresh(
    db_path: Path | None = None,
    fetch_long_descs: bool = True,
) -> dict:
    """Fetch the BFS list, optionally enrich with long descriptions, persist.

    We drive one httpx.AsyncClient through both fetch steps so that any
    captcha session cookie picked up while fetching map.json is reused
    for the ~448 individual /map/<slug> pages instead of solving the
    challenge again per page.

    Long-description fetching is idempotent — only places whose
    long_description is currently empty are re-fetched.
    """
    if db_path is None:
        from . import config
        db_path = config.DB_PATH

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SEC) as client:
        places = await fetch_places(client=client)

        if fetch_long_descs:
            # Persist what we have so far so the DB stays consistent
            # even if the long-desc fetch is interrupted halfway.
            persist(places, db_path=db_path)
            missing = _slugs_missing_long_desc(db_path)
            to_enrich = [p for p in places if p.slug in missing]
            if to_enrich:
                logger.info(
                    "fetching long descriptions for %d places", len(to_enrich),
                )
                enriched = await fetch_long_descriptions(to_enrich, client=client)
                persist(enriched, db_path=db_path)

    return persist(places, db_path=db_path)
