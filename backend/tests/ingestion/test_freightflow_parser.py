"""FreightFlow parsing tests derived from supplied schema examples."""

import json
from pathlib import Path

import pytest

from carrier_pool.ingestion.base import InvalidSourceFileError, SourceFile
from carrier_pool.ingestion.freightflow import parse_freightflow_file


def _payload(*, booked: bool) -> dict[str, object]:
    return {
        "syncedAt": "2026-07-06T12:00:00-05:00" if booked else "2026-07-06T06:00:00-05:00",
        "loads": [
            {
                "shipmentId": 127472397,
                "status": "Dispatched" if booked else "Booking",
                "mileage": 242.1,
                "totalSell": 1450.0,
                "totalBuy": 1180.0 if booked else None,
                "customer": {"customerId": 889264, "name": "Lone Star Beverages"},
                "carrier": {
                    "carrierMasterId": 835692,
                    "name": "IBRAHIM TRANSPORT INC",
                    "mcNumber": "1346382",
                    "dotNumber": "3771394",
                    "phoneNumber": "+15714906959",
                }
                if booked
                else None,
                "equipment": "53 ft Van | Dry",
                "weightTotal": 24000.0,
                "stops": [
                    {
                        "stopType": "First Pickup",
                        "city": "GRAND PRAIRIE",
                        "state": "TX",
                        "zipCode": "75050",
                        "estimatedReadyDateTime": "2026-07-07T08:00:00-05:00",
                        "estimatedCloseDateTime": "2026-07-07T16:00:00-05:00",
                        "actualDepartureDateTime": None,
                    },
                    {
                        "stopType": "Last Drop",
                        "city": "KATY",
                        "state": "TX",
                        "zipCode": "77449",
                        "estimatedReadyDateTime": "2026-07-08T08:00:00-05:00",
                        "estimatedCloseDateTime": "2026-07-08T16:00:00-05:00",
                        "actualDepartureDateTime": None,
                    },
                ],
                "createdDate": "2026-07-06T04:12:44-05:00",
                "lastModifiedDate": "2026-07-06T10:03:17-05:00"
                if booked
                else "2026-07-06T04:12:44-05:00",
            }
        ],
    }


@pytest.mark.parametrize("booked", [False, True])
def test_parses_provided_freightflow_example_shapes(booked: bool) -> None:
    source_file = SourceFile(Path("fixture.json"), json.dumps(_payload(booked=booked)).encode())
    sync = parse_freightflow_file(source_file)
    assert sync.loads[0].carrier is not None if booked else sync.loads[0].carrier is None
    assert sync.loads[0].total_buy == (1180.0 if booked else None)
    assert [stop.city for stop in sync.loads[0].stops] == ["GRAND PRAIRIE", "KATY"]


def test_reports_precise_path_for_naive_timestamp() -> None:
    payload = _payload(booked=False)
    payload["loads"][0]["createdDate"] = "2026-07-06T04:12:44"  # type: ignore[index]
    with pytest.raises(InvalidSourceFileError, match="loads.0.createdDate"):
        parse_freightflow_file(SourceFile(Path("invalid.json"), json.dumps(payload).encode()))
