"""Projection-ready local geography values for canonical stops."""

from dataclasses import dataclass
from decimal import Decimal

from carrier_pool.geography.lookup import GeographyLookup


@dataclass(frozen=True, slots=True)
class StopGeography:
    """Coordinate and quality fields persisted on a mutable stop projection."""

    latitude: Decimal | None
    longitude: Decimal | None
    metro_group: str | None
    quality_flags: list[str]


def enrich_stop(city: str, state: str, postal_code: str) -> StopGeography:
    """Use only packaged reference data to enrich one source stop."""
    result = GeographyLookup.default().lookup(postal_code, city, state)
    return StopGeography(
        latitude=result.latitude,
        longitude=result.longitude,
        metro_group=result.metro_group,
        quality_flags=[flag.value for flag in result.quality_flags],
    )
