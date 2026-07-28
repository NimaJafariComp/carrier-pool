import json
from pathlib import Path

import pytest

from carrier_pool.ingestion.base import InvalidSourceFileError, SourceFile
from carrier_pool.ingestion.hauldesk import parse_hauldesk_file


def test_hauldesk_assembly_parses_provided_example_shape() -> None:
    payload = {
        "synced_at": "2026-07-06 06:00:00",
        "loads": [
            {
                "load_num": "HD-2026-004417",
                "status_code": 30,
                "customer_code": "C-0031",
                "customer_name": "Alamo Building Supply",
                "carrier_ref": 66861,
                "equip": "V",
                "weight_kg": 10886.2,
                "dist_km": 389.6,
                "pu_city": "New Braunfels",
                "pu_state": "TX",
                "pu_zip": "78130",
                "pu_date": "2026-07-07",
                "pu_departed_at": None,
                "del_city": "Pasadena",
                "del_state": "TX",
                "del_zip": "77502",
                "del_date": "2026-07-08",
                "del_arrived_at": None,
                "entered_at": "2026-07-05 14:00:00",
                "updated_at": "2026-07-06 03:00:00",
            }
        ],
        "carriers": [
            {
                "carrier_id": 66861,
                "carrier_name": "DELTA PRIME LLC",
                "mc_no": "884201",
                "dot_no": "2551377",
                "home_city": "Seguin",
                "home_state": "TX",
                "phone": "x",
            }
        ],
        "rates": [
            {
                "rate_id": 910233,
                "load_num": "HD-2026-004417",
                "side": "pay",
                "code": "LINEHAUL",
                "amount_usd": 1035.00,
                "created_at": "2026-07-06 03:00:00",
            },
            {
                "rate_id": 910234,
                "load_num": "HD-2026-004417",
                "side": "bill",
                "code": "LINEHAUL",
                "amount_usd": 1310.00,
                "created_at": "2026-07-06 03:00:00",
            },
        ],
    }
    result = parse_hauldesk_file(SourceFile(Path("hauldesk.json"), json.dumps(payload).encode()))
    assert result.carriers_by_id[66861].carrier_name == "DELTA PRIME LLC"
    assert result.carriers_by_load_num["HD-2026-004417"] is result.carriers_by_id[66861]
    assert len(result.rates_by_load_num["HD-2026-004417"]) == 2


def test_hauldesk_missing_carrier_is_warning() -> None:
    payload = {
        "synced_at": "2026-07-06 06:00:00",
        "loads": [
            {
                "load_num": "HD-1",
                "status_code": 20,
                "customer_code": "C",
                "customer_name": "C",
                "carrier_ref": 9,
                "equip": "V",
                "weight_kg": 1,
                "dist_km": 1,
                "pu_city": "A",
                "pu_state": "TX",
                "pu_zip": "1",
                "pu_date": "2026-07-06",
                "pu_departed_at": None,
                "del_city": "B",
                "del_state": "TX",
                "del_zip": "2",
                "del_date": "2026-07-07",
                "del_arrived_at": None,
                "entered_at": "2026-07-05 14:00:00",
                "updated_at": "2026-07-06 03:00:00",
            }
        ],
        "carriers": [],
        "rates": [],
    }
    assert parse_hauldesk_file(SourceFile(Path("x.json"), json.dumps(payload).encode())).warnings


def test_hauldesk_known_prior_carrier_does_not_warn() -> None:
    payload = {
        "synced_at": "2026-07-06 06:00:00",
        "loads": [
            {
                "load_num": "HD-1",
                "status_code": 30,
                "customer_code": "C",
                "customer_name": "C",
                "carrier_ref": 9,
                "equip": "V",
                "weight_kg": 1,
                "dist_km": 1,
                "pu_city": "A",
                "pu_state": "TX",
                "pu_zip": "1",
                "pu_date": "2026-07-06",
                "pu_departed_at": None,
                "del_city": "B",
                "del_state": "TX",
                "del_zip": "2",
                "del_date": "2026-07-07",
                "del_arrived_at": None,
                "entered_at": "2026-07-05 14:00:00",
                "updated_at": "2026-07-06 03:00:00",
            }
        ],
        "carriers": [],
        "rates": [],
    }
    result = parse_hauldesk_file(
        SourceFile(Path("x.json"), json.dumps(payload).encode()), known_carrier_ids={9}
    )
    assert result.warnings == ()
    assert result.carriers_by_load_num["HD-1"] is None


@pytest.mark.parametrize(
    ("section", "field", "value"),
    [("loads", "load_num", "HD-1"), ("carriers", "carrier_id", 1), ("rates", "rate_id", 1)],
)
def test_hauldesk_rejects_duplicate_stable_ids(
    section: str, field: str, value: str | int
) -> None:
    payload = {
        "synced_at": "2026-07-06 06:00:00",
        "loads": [],
        "carriers": [],
        "rates": [],
    }
    if section == "loads":
        payload[section] = [
            {
                "load_num": value,
                "status_code": 20,
                "customer_code": "C",
                "customer_name": "C",
                "carrier_ref": None,
                "equip": "V",
                "weight_kg": 1,
                "dist_km": 1,
                "pu_city": "A",
                "pu_state": "TX",
                "pu_zip": "1",
                "pu_date": "2026-07-06",
                "pu_departed_at": None,
                "del_city": "B",
                "del_state": "TX",
                "del_zip": "2",
                "del_date": "2026-07-07",
                "del_arrived_at": None,
                "entered_at": "2026-07-05 14:00:00",
                "updated_at": "2026-07-06 03:00:00",
            }
        ] * 2
    elif section == "carriers":
        payload[section] = [
            {
                "carrier_id": value,
                "carrier_name": "Carrier",
                "mc_no": "1",
                "dot_no": "2",
                "home_city": "A",
                "home_state": "TX",
                "phone": "x",
            }
        ] * 2
    else:
        payload[section] = [
            {
                "rate_id": value,
                "load_num": "HD-1",
                "side": "pay",
                "code": "LINEHAUL",
                "amount_usd": 100,
                "created_at": "2026-07-06 03:00:00",
            }
        ] * 2

    with pytest.raises(InvalidSourceFileError, match=f"Duplicate HaulDesk {field}"):
        parse_hauldesk_file(SourceFile(Path("invalid.json"), json.dumps(payload).encode()))


def test_hauldesk_rejects_undocumented_status_code() -> None:
    payload = {
        "synced_at": "2026-07-06 06:00:00",
        "loads": [
            {
                "load_num": "HD-1",
                "status_code": 99,
                "customer_code": "C",
                "customer_name": "C",
                "carrier_ref": None,
                "equip": "V",
                "weight_kg": 1,
                "dist_km": 1,
                "pu_city": "A",
                "pu_state": "TX",
                "pu_zip": "1",
                "pu_date": "2026-07-06",
                "pu_departed_at": None,
                "del_city": "B",
                "del_state": "TX",
                "del_zip": "2",
                "del_date": "2026-07-07",
                "del_arrived_at": None,
                "entered_at": "2026-07-05 14:00:00",
                "updated_at": "2026-07-06 03:00:00",
            }
        ],
        "carriers": [],
        "rates": [],
    }
    with pytest.raises(InvalidSourceFileError, match="status_code"):
        parse_hauldesk_file(SourceFile(Path("invalid.json"), json.dumps(payload).encode()))
