from decimal import Decimal

from test_brokeros_parser import _parse, _payload

from carrier_pool.domain.types import EquipmentType
from carrier_pool.ingestion.brokeros import normalize_brokeros


def test_brokeros_normalizes_resolved_records_and_restated_rates() -> None:
    payload = _payload()
    records = payload["records"]
    assert isinstance(records, list)
    records[0]["bos__Equipment_Type__c"] = None
    records[0]["bos__Line_Items__r"].append(
        {
            "bos__Commodity__c": "Frozen goods",
            "bos__Weight__c": 100,
            "bos__Weight_Units__c": "kg",
            "bos__Pallet_Count__c": 1,
        }
    )
    normalized = normalize_brokeros(_parse(payload), "tenant", "brokeros.json")
    load = normalized.loads[0]

    assert load.customer.name == "Gulf Coast Foods"
    assert load.equipment is EquipmentType.UNKNOWN
    assert load.customer_rate is not None and load.customer_rate.amount == Decimal("1720")
    assert load.carrier_rate is None
    assert load.weight_lbs is not None and load.weight_lbs > Decimal("14660")
    assert [stop.sequence for stop in load.stops] == [1, 2]
    assert load.stops[0].facility_name == "Sugar Land Cold Storage"
    assert normalized.raw_loads[0]["bos__Line_Items__r"][0]["bos__Weight_Units__c"] == "lbs"
