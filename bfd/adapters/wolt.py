import httpx

from ..config import DEFAULT_USER_AGENT, HTTP_TIMEOUT_SEC
from .base import DeliveryRestaurant


WOLT_API_URL = "https://restaurant-api.wolt.com/v1/pages/restaurants"


class WoltAdapter:
    name = "wolt"

    async def list_deliverable(
        self,
        lat: float,
        lng: float,
        address: str,
        postcode: str | None = None,
    ) -> list[DeliveryRestaurant]:
        params = {"lat": str(lat), "lon": str(lng)}
        headers = {
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept": "application/json",
        }
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SEC) as client:
            resp = await client.get(WOLT_API_URL, params=params, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        return list(_extract_restaurants(data))


def _extract_restaurants(data: dict):
    """Walk the Wolt API response and yield unique DeliveryRestaurants.

    The Wolt API groups restaurants into 'sections', each containing 'items'.
    Each item has a 'venue' dict with:
      - id: str
      - name: str
      - slug: str  (used to build the URL)
      - location: [lng, lat]  (GeoJSON order)
      - address: str

    The same restaurant can appear in multiple sections — dedupe by venue id.
    """
    seen: set[str] = set()
    for section in data.get("sections", []):
        for item in section.get("items", []):
            venue = item.get("venue")
            if not venue:
                continue

            venue_id = venue.get("id", "")
            if not venue_id or venue_id in seen:
                continue
            seen.add(venue_id)

            # location is GeoJSON [lng, lat]
            location = venue.get("location")
            if not location or len(location) < 2:
                continue
            lng_v, lat_v = location[0], location[1]

            slug = venue.get("slug", "")
            name = venue.get("name", "") or item.get("title", "")
            # Wolt occasionally uses a list of {lang, value} dicts for name
            if isinstance(name, list):
                en = next((x["value"] for x in name if x.get("lang") == "en"), None)
                name = en or (name[0]["value"] if name else "")

            yield DeliveryRestaurant(
                platform="wolt",
                id=str(venue_id),
                name=name,
                lat=float(lat_v),
                lng=float(lng_v),
                url=f"https://wolt.com/en/deu/berlin/restaurant/{slug}" if slug else "",
                address=venue.get("address", ""),
                # Wolt exposes the per-venue online state on `venue.online`
                is_open=bool(venue["online"]) if "online" in venue else None,
            )
