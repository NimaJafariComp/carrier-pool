"""Readable evidence API response contracts."""

from carrier_pool.main import _comparable_load_response


def test_comparable_evidence_preserves_endpoint_distances_once() -> None:
    response = _comparable_load_response(
        {
            "load_external_id": "FF-1101",
            "route": "Grand Prairie, TX → Katy, TX",
            "origin_distance_miles": 11.9,
            "destination_distance_miles": 22.6,
            "delivery_to_pickup_miles": 18.2,
            "delivery_to_pickup_gap_days": 1.75,
            "carrier_rate_usd": "1200.00",
        }
    )

    assert response.origin_distance_miles == 11.9
    assert response.destination_distance_miles == 22.6
    assert response.delivery_to_pickup_miles == 18.2
    assert response.delivery_to_pickup_gap_days == 1.75
