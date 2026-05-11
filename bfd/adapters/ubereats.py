import asyncio
from typing import Any

from playwright.async_api import Page, Response

from ..browser_pool import pool as _browser_pool
from ..config import HTTP_TIMEOUT_SEC
from .base import AdapterUnavailable, DeliveryRestaurant


UBEREATS_HOME = "https://www.ubereats.com/de"
UBEREATS_BASE = "https://www.ubereats.com"
FEED_ENDPOINT_FRAGMENT = "getFeedV1"  # part of the request URL we intercept

# Selectors tried in order when dismissing the cookie/consent banner.
# We try both aria-label and text-content variants to be resilient to
# future wording changes (German "Annehmen" = Accept).
_CONSENT_SELECTORS = [
    'button[aria-label="Annehmen"]',
    'button[aria-label="Accept"]',
    'button[aria-label="Accept All"]',
    'button[aria-label="Alle akzeptieren"]',
    'button:has-text("Annehmen")',
    'button:has-text("Accept All")',
    'button:has-text("Akzeptieren")',
    '#onetrust-accept-btn-handler',
    '[data-testid*="accept"]',
]

# Selectors tried in order for the address / delivery-location typeahead.
# The German placeholder "Lieferadresse eingeben" is the current one (verified
# 2026-05); the English variants are kept as a fallback.
_ADDRESS_SELECTORS = [
    'input[name="searchTerm"]',
    'input[placeholder*="Lieferadresse"]',
    'input[placeholder*="Adresse"]',
    'input[placeholder*="Address"]',
    'input[name="address"]',
]


async def _dismiss_consent_banner(page: Page) -> None:
    """Try every known cookie/consent selector and click the first one visible.

    Silently does nothing if no banner is present — that is the normal case
    when Uber already has a stored consent cookie.
    """
    for selector in _CONSENT_SELECTORS:
        try:
            await page.click(selector, timeout=3_000)
            return  # one click is enough
        except Exception:
            continue


async def _fill_address(page: Page, address: str) -> None:
    """Fill the delivery-address typeahead and confirm the first suggestion."""
    for selector in _ADDRESS_SELECTORS:
        try:
            await page.fill(selector, address, timeout=10_000)
            await asyncio.sleep(1.5)  # let autocomplete populate
            await page.keyboard.press("ArrowDown")
            await page.keyboard.press("Enter")
            return
        except Exception:
            continue
    raise AdapterUnavailable(
        f"address input not found — tried selectors: {_ADDRESS_SELECTORS}"
    )


class UberEatsAdapter:
    name = "ubereats"

    async def list_deliverable(
        self,
        lat: float,
        lng: float,
        address: str,
        postcode: str | None = None,
    ) -> list[DeliveryRestaurant]:
        feed_payload: dict[str, Any] | None = None
        feed_event = asyncio.Event()

        # Use the long-lived Chromium from bfd.browser_pool. Lock is held
        # for the whole navigation: UE detects parallel sessions from one
        # IP as bot-like, and the per-context geolocation has to stick.
        async with _browser_pool.acquire() as browser:
            context = await browser.new_context(
                locale="de-DE",
                geolocation={"latitude": lat, "longitude": lng},
                permissions=["geolocation"],
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
                    "Version/17.0 Safari/605.1.15"
                ),
            )
            try:
                page = await context.new_page()

                async def on_response(resp: Response) -> None:
                    nonlocal feed_payload
                    if FEED_ENDPOINT_FRAGMENT in resp.url:
                        try:
                            feed_payload = await resp.json()
                            feed_event.set()
                        except Exception:
                            pass

                page.on("response", on_response)
                await page.goto(UBEREATS_HOME, wait_until="domcontentloaded")

                # Give the page a moment to render consent modals before trying
                # to interact with anything.
                await asyncio.sleep(2)

                # 1. Dismiss cookie/consent banner (no-op if absent).
                await _dismiss_consent_banner(page)
                await asyncio.sleep(1)

                # 2. Type the delivery address and pick the first autocomplete
                #    suggestion.  Raises AdapterUnavailable on hard failure.
                try:
                    await _fill_address(page, address)
                except AdapterUnavailable:
                    raise
                except Exception as e:
                    raise AdapterUnavailable(f"address input error: {e}") from e

                # 3. Wait for the feed API response.
                try:
                    await asyncio.wait_for(
                        feed_event.wait(), timeout=HTTP_TIMEOUT_SEC
                    )
                except asyncio.TimeoutError as e:
                    raise AdapterUnavailable(
                        "getFeedV1 not observed within timeout"
                    ) from e
            finally:
                # Drop only the context; the browser stays warm for the
                # next request.
                try:
                    await context.close()
                except Exception:
                    pass

        if feed_payload is None:
            raise AdapterUnavailable("no feed response captured")
        return _parse_feed(feed_payload)


def _extract_store_entry(
    store: dict,
    seen: set[str],
) -> "DeliveryRestaurant | None":
    """Convert one store object (from either REGULAR_STORE or carousel) into a
    DeliveryRestaurant, or return None if data is incomplete or already seen.

    Current Uber Eats schema (verified 2026-05):
      store.storeUuid          — unique identifier
      store.title.text         — display name
      store.mapMarker.latitude — latitude
      store.mapMarker.longitude— longitude
      store.actionUrl          — relative path like "/store/<slug>/<b64uuid>"
    """
    store_id = store.get("storeUuid", "")
    if not store_id or store_id in seen:
        return None

    map_marker = store.get("mapMarker") or {}
    lat = map_marker.get("latitude")
    lng = map_marker.get("longitude")
    if lat is None or lng is None:
        return None

    title_obj = store.get("title") or {}
    name = title_obj.get("text") or store.get("name", "")

    action_url = store.get("actionUrl", "")
    # Strip query params so the URL is a stable canonical link.
    url_path = action_url.split("?")[0]
    url = f"{UBEREATS_BASE}{url_path}" if url_path else f"{UBEREATS_BASE}/de"

    # Open/closed signal: UE marks closed stores with `meta[].badgeType="CLOSED"`
    # (text "Geschlossen"). Open stores carry an "ETD" badge with the delivery
    # ETA instead. If we see no `meta` at all, we don't know — return None.
    is_open: bool | None = None
    meta = store.get("meta")
    if isinstance(meta, list) and meta:
        is_open = not any(
            (m or {}).get("badgeType") == "CLOSED" for m in meta if isinstance(m, dict)
        )

    seen.add(store_id)
    return DeliveryRestaurant(
        platform="ubereats",
        id=store_id,
        name=name,
        lat=float(lat),
        lng=float(lng),
        url=url,
        address="",  # feed does not include a formatted address string
        is_open=is_open,
    )


def _parse_feed(payload: dict) -> list[DeliveryRestaurant]:
    """Walk the Uber Eats feed JSON and return a list of restaurants.

    The feed contains items of various types.  We extract stores from:
      - REGULAR_STORE items   (item.store)
      - REGULAR_CAROUSEL items (item.carousel.stores[])

    All other types (DIVIDER, SECTION_HEADER, MARKUP_TEXT, …) are ignored.
    """
    out: list[DeliveryRestaurant] = []
    items: list[dict] = (
        payload.get("data", {}).get("feedItems")
        or payload.get("feedItems")
        or []
    )
    seen: set[str] = set()

    for item in items:
        item_type = item.get("type", "")

        if item_type == "REGULAR_STORE":
            store = item.get("store") or {}
            result = _extract_store_entry(store, seen)
            if result is not None:
                out.append(result)

        elif item_type == "REGULAR_CAROUSEL":
            carousel_stores = (item.get("carousel") or {}).get("stores") or []
            for store in carousel_stores:
                result = _extract_store_entry(store, seen)
                if result is not None:
                    out.append(result)

    return out
