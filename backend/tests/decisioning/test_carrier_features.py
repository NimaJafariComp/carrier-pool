"""Carrier historical-delivery feature contracts."""

from datetime import UTC, datetime
from uuid import UUID

from carrier_pool.db.models import LoadVersion
from carrier_pool.decisioning.carrier_features import _endpoint, _version_precedence


def test_completed_history_breaks_equal_observation_times_by_version_id() -> None:
    observed_at = datetime(2026, 7, 10, 18, tzinfo=UTC)
    lower_id = LoadVersion(id=UUID(int=1), observed_at=observed_at)
    higher_id = LoadVersion(id=UUID(int=2), observed_at=observed_at)

    assert sorted((higher_id, lower_id), key=_version_precedence)[-1] is higher_id


def test_endpoint_uses_valid_zip_when_city_is_missing() -> None:
    version = LoadVersion(
        canonical_snapshot={
            "stops": [
                {
                    "sequence": 1,
                    "is_pickup": True,
                    "is_dropoff": False,
                    "postal_code": "75050",
                    "state": "TX",
                }
            ]
        }
    )

    endpoint = _endpoint(version, pickup=True)

    assert endpoint is not None
    assert endpoint.latitude == 32.745964
    assert endpoint.longitude == -96.997785
