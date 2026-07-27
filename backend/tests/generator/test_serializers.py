"""Phase 6.4 serializer contracts against the existing source adapters."""

import json
import re
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from carrier_pool.domain.types import FinancialSide, LoadStatus, Money, SourceSystem
from carrier_pool.generator.catalog import build_catalog
from carrier_pool.generator.lifecycle import LifecycleEngine
from carrier_pool.generator.models import FinancialEvent, LifecycleEvent, ScheduledSync
from carrier_pool.generator.serializers import serialize_sync
from carrier_pool.ingestion.base import SourceFile, TenantContext
from carrier_pool.ingestion.brokeros import BrokerOSAdapter
from carrier_pool.ingestion.freightflow import FreightFlowAdapter
from carrier_pool.ingestion.hauldesk import HaulDeskAdapter


def _at(hour: int) -> datetime:
    return datetime(2026, 7, 11, hour, tzinfo=UTC)


def _serialized(
    source: SourceSystem,
    load_id: str,
    events: tuple[LifecycleEvent | FinancialEvent, ...],
):
    catalog = build_catalog()
    sync = ScheduledSync(
        sync_id=f"{source.value}-D11-06",
        tenant_id={
            SourceSystem.FREIGHTFLOW: "ff-broker",
            SourceSystem.HAULDESK: "hd-broker",
            SourceSystem.BROKEROS: "bo-broker",
        }[source],
        source_system=source,
        sync_at=_at(6),
        events=events,
    )
    state = LifecycleEngine(catalog).apply((sync,))
    return catalog, sync, serialize_sync(catalog, sync, state)


def test_freightflow_serializer_emits_complete_replacement_snapshot_and_round_trips() -> None:
    catalog, sync, payload = _serialized(
        SourceSystem.FREIGHTFLOW,
        "FF-9001",
        (
            LifecycleEvent(
                "FF-9001",
                _at(6),
                status=LoadStatus.COVERED,
                carrier_id="FF-C-201",
                customer_rate=Money(Decimal("1450")),
                carrier_rate=Money(Decimal("1180")),
            ),
        ),
    )

    encoded = json.dumps(payload)
    load = payload["loads"][0]
    assert "//" not in encoded
    assert set(load) == {
        "shipmentId",
        "status",
        "mileage",
        "totalSell",
        "totalBuy",
        "customer",
        "carrier",
        "equipment",
        "weightTotal",
        "stops",
        "createdDate",
        "lastModifiedDate",
    }
    assert load["carrier"] is not None
    adapter = FreightFlowAdapter()
    normalized = adapter.normalize(
        adapter.parse_file(
            SourceFile(Path("ff.json"), encoded.encode()), TenantContext("ff-broker")
        ),
        TenantContext("ff-broker"),
    )
    assert normalized.loads[0].status is LoadStatus.COVERED
    assert normalized.loads[0].carrier_rate == Money(Decimal("1180"))
    assert sync.source_system is SourceSystem.FREIGHTFLOW
    assert catalog.load("FF-9001").day11_target is True


def test_hauldesk_serializer_emits_only_financial_events_from_its_sync() -> None:
    _, _, payload = _serialized(
        SourceSystem.HAULDESK,
        "HD-2101",
        (
            LifecycleEvent("HD-2101", _at(6), status=LoadStatus.ACTIVE),
            FinancialEvent(
                "HD-2101",
                _at(6),
                entry_id="HD-RATE-9001",
                side=FinancialSide.PAY,
                code="FUEL",
                amount=Money(Decimal("75")),
            ),
        ),
    )

    assert len(payload["loads"]) == 1
    assert payload["rates"] == [
        {
            "rate_id": payload["rates"][0]["rate_id"],
            "load_num": "HD-2101",
            "side": "pay",
            "code": "FUEL",
            "amount_usd": 75,
            "created_at": payload["rates"][0]["created_at"],
        }
    ]
    adapter = HaulDeskAdapter()
    normalized = adapter.normalize(
        adapter.parse_file(
            SourceFile(Path("hd.json"), json.dumps(payload).encode()), TenantContext("hd-broker")
        ),
        TenantContext("hd-broker"),
    )
    assert normalized.loads[0].status is LoadStatus.ACTIVE
    assert normalized.source_financial_entries[0].amount == Money(Decimal("75"))


def test_brokeros_serializer_includes_all_referenced_records_and_round_trips() -> None:
    _, _, payload = _serialized(
        SourceSystem.BROKEROS,
        "BO-9001",
        (LifecycleEvent("BO-9001", _at(6), status=LoadStatus.ACTIVE),),
    )

    record = payload["records"][0]
    references = payload["referenced_records"]
    required_ids = {
        record["bos__Customer__c"],
        *(stop["bos__Location__c"] for stop in record["bos__Stops__r"]),
    }
    assert required_ids <= set(references)
    assert len(record["Id"]) == 18
    assert all(len(reference_id) == 18 for reference_id in references)
    adapter = BrokerOSAdapter()
    normalized = adapter.normalize(
        adapter.parse_file(
            SourceFile(Path("bo.json"), json.dumps(payload).encode()), TenantContext("bo-broker")
        ),
        TenantContext("bo-broker"),
    )
    assert normalized.loads[0].status is LoadStatus.ACTIVE
    assert [stop.postal_code for stop in normalized.loads[0].stops] == ["77449", "78205"]


@pytest.mark.parametrize(
    ("source", "load_id", "events", "directory"),
    [
        (
            SourceSystem.FREIGHTFLOW,
            "FF-9001",
            (LifecycleEvent("FF-9001", _at(6), status=LoadStatus.ACTIVE),),
            "tms_a_freightflow",
        ),
        (
            SourceSystem.HAULDESK,
            "HD-2101",
            (
                LifecycleEvent("HD-2101", _at(6), status=LoadStatus.COVERED, carrier_id="HD-C-401"),
                FinancialEvent(
                    "HD-2101",
                    _at(6),
                    entry_id="HD-RATE-SHAPE",
                    side=FinancialSide.PAY,
                    code="LINEHAUL",
                    amount=Money(Decimal("1000")),
                ),
            ),
            "tms_b_hauldesk",
        ),
        (
            SourceSystem.BROKEROS,
            "BO-9001",
            (
                LifecycleEvent(
                    "BO-9001",
                    _at(6),
                    status=LoadStatus.ACTIVE,
                    customer_rate=Money(Decimal("1720")),
                ),
            ),
            "tms_c_brokeros",
        ),
    ],
)
def test_serializer_structural_snapshot_matches_supplied_jsonc_example(
    source: SourceSystem,
    load_id: str,
    events: tuple[LifecycleEvent | FinancialEvent, ...],
    directory: str,
) -> None:
    _, _, payload = _serialized(source, load_id, events)
    example_path = Path(__file__).parents[3] / "data" / directory / "example_sync.jsonc"
    example = json.loads(re.sub(r"//.*$", "", example_path.read_text(), flags=re.MULTILINE))

    assert _shape(payload) == _shape(example)


def _shape(value: object) -> object:
    if isinstance(value, dict):
        if value and all(isinstance(key, str) and len(key) == 18 for key in value):
            return sorted(json.dumps(_shape(item), sort_keys=True) for item in value.values())
        return {key: _shape(item) for key, item in value.items()}
    if isinstance(value, list):
        return [] if not value else [_shape(value[0])]
    if isinstance(value, bool) or value is None:
        return type(value).__name__
    if isinstance(value, str):
        return "string"
    if isinstance(value, int | float):
        return "number"
    raise TypeError(f"Unexpected JSON value: {value!r}")
