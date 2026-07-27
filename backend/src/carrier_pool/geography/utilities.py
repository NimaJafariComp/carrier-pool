"""Typed, local geography utilities for explainable lane comparison."""

from collections.abc import Callable
from dataclasses import dataclass
from importlib import import_module
from math import asin, cos, isfinite, pi, radians, sin, sqrt
from typing import cast

EARTH_RADIUS_MILES = 3958.7613
FINE_H3_RESOLUTION = 8
COARSE_H3_RESOLUTION = 6


@dataclass(frozen=True, slots=True)
class Coordinate:
    """Validated WGS84 coordinate used for exact endpoint distance."""

    latitude: float
    longitude: float

    def __post_init__(self) -> None:
        if not isfinite(self.latitude) or not -90 <= self.latitude <= 90:
            raise ValueError("latitude must be finite and within [-90, 90].")
        if not isfinite(self.longitude) or not -180 <= self.longitude <= 180:
            raise ValueError("longitude must be finite and within [-180, 180].")


@dataclass(frozen=True, slots=True)
class EndpointDistances:
    """Exact target-to-comparable distances kept separate by route endpoint."""

    origin_miles: float
    destination_miles: float


@dataclass(frozen=True, slots=True)
class RouteEndpoint:
    """Normalized endpoint identity used to preserve ordered route direction."""

    postal_code: str
    metro_group: str | None

    def __post_init__(self) -> None:
        if not self.postal_code.strip():
            raise ValueError("postal_code must not be empty.")


@dataclass(frozen=True, slots=True)
class MetroPairIdentity:
    """Ordered metro corridor; never an undirected set."""

    origin_metro: str | None
    destination_metro: str | None

    @property
    def value(self) -> str:
        return f"{self.origin_metro or 'UNKNOWN'}→{self.destination_metro or 'UNKNOWN'}"


@dataclass(frozen=True, slots=True)
class DirectionalRouteIdentity:
    """Ordered ZIP route identity used by exact and regional lanes."""

    origin_postal_code: str
    destination_postal_code: str

    @property
    def value(self) -> str:
        return f"{self.origin_postal_code}→{self.destination_postal_code}"


@dataclass(frozen=True, slots=True)
class H3Cells:
    """Optional retrieval cells; exact Haversine remains business distance."""

    fine: str | None
    coarse: str | None
    fine_resolution: int = FINE_H3_RESOLUTION
    coarse_resolution: int = COARSE_H3_RESOLUTION


def haversine_miles(first: Coordinate, second: Coordinate) -> float:
    """Return explainable great-circle distance in statute miles."""
    latitude_delta = radians(second.latitude - first.latitude)
    longitude_delta = radians(second.longitude - first.longitude)
    first_latitude = radians(first.latitude)
    second_latitude = radians(second.latitude)
    haversine = (
        sin(latitude_delta / 2) ** 2
        + cos(first_latitude) * cos(second_latitude) * sin(longitude_delta / 2) ** 2
    )
    return 2 * EARTH_RADIUS_MILES * asin(min(1.0, sqrt(haversine)))


def endpoint_distances(
    target_origin: Coordinate,
    target_destination: Coordinate,
    comparable_origin: Coordinate,
    comparable_destination: Coordinate,
) -> EndpointDistances:
    """Return target-to-comparable endpoint pairs without merging route direction."""
    return EndpointDistances(
        origin_miles=haversine_miles(target_origin, comparable_origin),
        destination_miles=haversine_miles(target_destination, comparable_destination),
    )


def metro_pair_identity(origin: RouteEndpoint, destination: RouteEndpoint) -> MetroPairIdentity:
    """Build an ordered metro pair for candidate retrieval."""
    return MetroPairIdentity(origin.metro_group, destination.metro_group)


def directional_route_identity(
    origin: RouteEndpoint, destination: RouteEndpoint
) -> DirectionalRouteIdentity:
    """Build an ordered ZIP route identity; reverse routes remain distinct."""
    return DirectionalRouteIdentity(origin.postal_code, destination.postal_code)


def h3_cells(point: Coordinate) -> H3Cells:
    """Return optional H3 candidate buckets, never a similarity or distance verdict."""
    try:
        h3 = import_module("h3")
    except ModuleNotFoundError:
        return H3Cells(None, None)
    function = cast(Callable[[float, float, int], str] | None, getattr(h3, "latlng_to_cell", None))
    if function is None:
        function = cast(Callable[[float, float, int], str] | None, getattr(h3, "geo_to_h3", None))
    if function is None:
        return H3Cells(None, None)
    return H3Cells(
        fine=str(function(point.latitude, point.longitude, FINE_H3_RESOLUTION)),
        coarse=str(function(point.latitude, point.longitude, COARSE_H3_RESOLUTION)),
    )


def maximum_haversine_miles() -> float:
    """Expose the antipodal upper bound for validation and diagnostics."""
    return pi * EARTH_RADIUS_MILES
