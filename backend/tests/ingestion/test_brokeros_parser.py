import json
from datetime import UTC
from pathlib import Path

import pytest

from carrier_pool.ingestion.base import InvalidSourceFileError, SourceFile
from carrier_pool.ingestion.brokeros import parse_brokeros_file

LOAD_ID = "a0jO900000YgsYJIAZ"
CUSTOMER_ID = "0011I00000NMUrPQAX"
PICKUP_ID = "0011I00000HAeJnQAL"
DROPOFF_ID = "0011I00000NMha6QAD"


def _payload() -> dict[str, object]:
    return {
        "synced_at": "2026-07-06T11:00:00.000+0000",
        "records": [
            {
                "Id": LOAD_ID,
                "Name": "SHP6743062",
                "bos__Load_Status__c": "Ready to Book",
                "bos__Distance_Miles__c": 197.4,
                "bos__Customer__c": CUSTOMER_ID,
                "bos__Carrier__c": None,
                "bos__Equipment_Type__c": "Reefer",
                "bos__Customer_Rate__c": 1720.00,
                "bos__Carrier_Rate__c": None,
                "bos__Stops__r": [
                    {
                        "bos__Number__c": 2.0,
                        "bos__Is_Pickup__c": False,
                        "bos__Is_Dropoff__c": True,
                        "bos__Location__c": DROPOFF_ID,
                        "bos__Scheduled_Date__c": "2026-07-08",
                        "bos__Arrival_Time__c": None,
                    },
                    {
                        "bos__Number__c": 1.0,
                        "bos__Is_Pickup__c": True,
                        "bos__Is_Dropoff__c": False,
                        "bos__Location__c": PICKUP_ID,
                        "bos__Scheduled_Date__c": "2026-07-07",
                        "bos__Arrival_Time__c": None,
                    },
                ],
                "bos__Line_Items__r": [
                    {
                        "bos__Commodity__c": "Packaged foods",
                        "bos__Weight__c": 14440.0,
                        "bos__Weight_Units__c": "lbs",
                        "bos__Pallet_Count__c": 18.0,
                    }
                ],
                "CreatedDate": "2026-07-06T09:40:02.000+0000",
                "LastModifiedDate": "2026-07-06T09:40:02.000+0000",
            }
        ],
        "referenced_records": {
            PICKUP_ID: {
                "type": "Location",
                "Name": "Sugar Land Cold Storage",
                "bos__City__c": "Sugar Land",
                "bos__State__c": "TX",
                "bos__Postal_Code__c": "77478",
            },
            DROPOFF_ID: {
                "type": "Location",
                "Name": "Schertz Distribution Ctr",
                "bos__City__c": "Schertz",
                "bos__State__c": "TX",
                "bos__Postal_Code__c": "78154",
            },
            CUSTOMER_ID: {"type": "Account", "record_type": "Customer", "Name": "Gulf Coast Foods"},
        },
    }


def _parse(payload: dict[str, object]):
    return parse_brokeros_file(SourceFile(Path("brokeros.json"), json.dumps(payload).encode()))


def test_brokeros_parses_provided_example_shape_and_orders_stops() -> None:
    result = _parse(_payload())
    load = result.loads[0]

    assert result.sync.synced_at.tzinfo is UTC
    assert load.load.Id == LOAD_ID
    assert load.customer.Name == "Gulf Coast Foods"
    assert [stop.stop.bos__Number__c for stop in load.stops] == [1, 2]
    assert load.stops[0].stop.bos__Is_Pickup__c is True
    assert load.stops[1].stop.bos__Is_Dropoff__c is True
    assert load.load.bos__Line_Items__r[0].bos__Commodity__c == "Packaged foods"
    assert load.load.bos__Line_Items__r[0].bos__Weight_Units__c == "lbs"


def test_brokeros_rejects_missing_location_reference() -> None:
    payload = _payload()
    records = payload["records"]
    assert isinstance(records, list)
    records[0]["bos__Stops__r"][0]["bos__Location__c"] = "0011I00000ZZzzQAAZ"

    with pytest.raises(InvalidSourceFileError, match="Missing Location reference"):
        _parse(payload)


def test_brokeros_rejects_wrong_reference_record_type() -> None:
    payload = _payload()
    records = payload["records"]
    assert isinstance(records, list)
    records[0]["bos__Customer__c"] = PICKUP_ID

    with pytest.raises(InvalidSourceFileError, match="Missing Account reference"):
        _parse(payload)


def test_brokeros_rejects_wrong_account_record_type() -> None:
    payload = _payload()
    references = payload["referenced_records"]
    assert isinstance(references, dict)
    references[CUSTOMER_ID]["record_type"] = "Carrier"

    with pytest.raises(InvalidSourceFileError, match="Wrong Account record_type"):
        _parse(payload)


def test_brokeros_preserves_independent_stop_flags_and_multi_stop_order() -> None:
    payload = _payload()
    records = payload["records"]
    assert isinstance(records, list)
    stops = records[0]["bos__Stops__r"]
    assert isinstance(stops, list)
    stops.append(
        {
            "bos__Number__c": 3.0,
            "bos__Is_Pickup__c": True,
            "bos__Is_Dropoff__c": True,
            "bos__Location__c": PICKUP_ID,
            "bos__Scheduled_Date__c": "2026-07-08",
            "bos__Arrival_Time__c": "2026-07-08T18:00:00.000+0000",
        }
    )
    result = _parse(payload)

    flags = [
        (stop.stop.bos__Is_Pickup__c, stop.stop.bos__Is_Dropoff__c)
        for stop in result.loads[0].stops
    ]
    assert flags == [
        (True, False),
        (False, True),
        (True, True),
    ]
