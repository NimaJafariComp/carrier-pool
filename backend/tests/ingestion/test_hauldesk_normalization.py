import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from carrier_pool.ingestion.base import InvalidSourceFileError, SourceFile
from carrier_pool.ingestion.hauldesk import normalize_hauldesk, parse_hauldesk_file


def test_normalizes_units_and_preserves_negative_adjustment() -> None:
    payload = {
        "synced_at": "2026-07-06 06:00:00",
        "loads": [
            {
                "load_num": "HD-1",
                "status_code": 20,
                "customer_code": "C",
                "customer_name": "C",
                "carrier_ref": 1,
                "equip": "V",
                "weight_kg": 10,
                "dist_km": 10,
                "pu_city": "A",
                "pu_state": "TX",
                "pu_zip": "1",
                "pu_date": "x",
                "pu_departed_at": "2026-07-06 07:00:00",
                "del_city": "B",
                "del_state": "TX",
                "del_zip": "2",
                "del_date": "x",
                "del_arrived_at": "2026-07-06 12:00:00",
                "entered_at": "2026-07-05 14:00:00",
                "updated_at": "2026-07-06 03:00:00",
            }
        ],
        "carriers": [
            {
                "carrier_id": 1,
                "carrier_name": "Carrier",
                "mc_no": "1",
                "dot_no": "2",
                "home_city": "A",
                "home_state": "TX",
                "phone": "x",
            }
        ],
        "rates": [
            {
                "rate_id": 2,
                "load_num": "HD-1",
                "side": "pay",
                "code": "FUEL",
                "amount_usd": 15,
                "created_at": "2026-07-06 03:00:00",
            },
            {
                "rate_id": 1,
                "load_num": "HD-1",
                "side": "pay",
                "code": "ADJUSTMENT",
                "amount_usd": -10,
                "created_at": "2026-07-06 03:00:00",
            },
        ],
    }
    result = normalize_hauldesk(
        parse_hauldesk_file(SourceFile(Path("x.json"), json.dumps(payload).encode())),
        "tenant",
        "x.json",
    )
    assert result.loads[0].weight_lbs is not None and result.loads[0].weight_lbs > 22
    assert result.loads[0].carrier is not None
    assert result.loads[0].stops[0].actual_departure_at == datetime(
        2026, 7, 6, 12, tzinfo=UTC
    )
    assert result.loads[0].stops[1].actual_arrival_at == datetime(2026, 7, 6, 17, tzinfo=UTC)
    assert [entry.amount.amount for entry in result.source_financial_entries] == [
        Decimal("15"),
        Decimal("-10"),
    ]
    assert result.metadata.sync_at.tzinfo is UTC


def test_rejects_ambiguous_hauldesk_timestamp() -> None:
    payload = {"synced_at": "2026-11-01 01:30:00", "loads": [], "carriers": [], "rates": []}
    with pytest.raises(InvalidSourceFileError, match="daylight-saving transition"):
        parse_hauldesk_file(SourceFile(Path("invalid.json"), json.dumps(payload).encode()))


def test_preserves_exact_decimal_financial_amount() -> None:
    source_file = SourceFile(
        Path("exact.json"),
        b'{"synced_at":"2026-07-06 06:00:00","loads":[],"carriers":[],"rates":['
        b'{"rate_id":1,"load_num":"HD-1","side":"pay","code":"LINEHAUL",'
        b'"amount_usd":0.100000000000000005,"created_at":"2026-07-06 03:00:00"}]}',
    )
    result = normalize_hauldesk(parse_hauldesk_file(source_file), "tenant", "exact.json")
    assert result.source_financial_entries[0].amount.amount == Decimal("0.100000000000000005")
