from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class DeliveryRestaurant:
    """A restaurant returned by a delivery platform for a given location."""
    platform: str           # "wolt" | "lieferando" | "ubereats"
    id: str                 # platform-specific id/slug, used as dict key
    name: str
    lat: float
    lng: float
    url: str                # deeplink to the restaurant on the platform
    address: str = ""
    is_open: bool | None = None  # currently accepting orders. None = unknown.


class DeliveryAdapter(Protocol):
    name: str

    async def list_deliverable(
        self,
        lat: float,
        lng: float,
        address: str,
        postcode: str | None = None,
    ) -> list[DeliveryRestaurant]:
        """Return all restaurants on this platform that deliver to (lat, lng).

        `address` and `postcode` are passed through for platforms that need
        them (Lieferando uses postcode, UE pastes the address into autocomplete).
        Adapters MUST raise an exception on any error; the caller catches it.
        """
        ...


class AdapterUnavailable(RuntimeError):
    """Raised when an adapter cannot complete a check (captcha, layout change,
    etc.) — distinct from transient HTTP errors."""
