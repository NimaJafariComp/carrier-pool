"""Explainable geography utility contracts."""

import math

import pytest

from carrier_pool.geography.utilities import (
    COARSE_H3_RESOLUTION,
    FINE_H3_RESOLUTION,
    Coordinate,
    RouteEndpoint,
    directional_route_identity,
    endpoint_distances,
    h3_cells,
    haversine_miles,
    metro_pair_identity,
)

DALLAS = Coordinate(32.787000, -96.799000)
HOUSTON = Coordinate(29.756000, -95.365000)
SAN_ANTONIO = Coordinate(29.424000, -98.494000)


@pytest.mark.parametrize(
    ("first", "second"),
    [(DALLAS, HOUSTON), (HOUSTON, SAN_ANTONIO), (DALLAS, SAN_ANTONIO)],
)
def test_haversine_is_symmetric_nonnegative_and_globally_bounded(
    first: Coordinate, second: Coordinate
) -> None:
    forward = haversine_miles(first, second)
    reverse = haversine_miles(second, first)

    assert forward == pytest.approx(reverse)
    assert 0 <= forward <= math.pi * 3958.7613
    assert haversine_miles(first, first) == pytest.approx(0)


def test_haversine_matches_known_approximate_texas_distances() -> None:
    assert haversine_miles(DALLAS, HOUSTON) == pytest.approx(225, abs=8)
    assert haversine_miles(HOUSTON, SAN_ANTONIO) == pytest.approx(190, abs=8)


def test_endpoint_distances_keep_origin_and_destination_separate() -> None:
    target = (DALLAS, HOUSTON)
    comparable = (Coordinate(32.746, -96.998), Coordinate(29.835, -95.730))

    distances = endpoint_distances(*target, *comparable)

    assert 10 < distances.origin_miles < 20
    assert 20 < distances.destination_miles < 30


def test_metro_and_route_identities_preserve_direction() -> None:
    origin = RouteEndpoint("75050", "DFW")
    destination = RouteEndpoint("77449", "HOUSTON")

    assert metro_pair_identity(origin, destination).value == "DFW→HOUSTON"
    assert directional_route_identity(origin, destination).value == "75050→77449"
    assert directional_route_identity(destination, origin).value == "77449→75050"


def test_h3_is_optional_and_never_used_as_distance() -> None:
    cells = h3_cells(DALLAS)

    assert (cells.fine_resolution, cells.coarse_resolution) == (
        FINE_H3_RESOLUTION,
        COARSE_H3_RESOLUTION,
    )
    assert cells.fine is None or isinstance(cells.fine, str)
    assert cells.coarse is None or isinstance(cells.coarse, str)
