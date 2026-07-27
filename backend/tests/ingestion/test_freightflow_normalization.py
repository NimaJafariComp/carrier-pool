import json
from pathlib import Path

from test_freightflow_parser import _payload

from carrier_pool.ingestion.base import SourceFile, TenantContext
from carrier_pool.ingestion.freightflow import FreightFlowAdapter


def test_initial_freightflow_snapshot_is_active_without_carrier_rate() -> None:
    adapter = FreightFlowAdapter()
    parsed = adapter.parse_file(
        SourceFile(Path("initial.json"), json.dumps(_payload(booked=False)).encode()),
        TenantContext("tenant-a"),
    )
    result = adapter.normalize(parsed, TenantContext("tenant-a"))
    assert result.loads[0].status.value == "ACTIVE"
    assert result.loads[0].carrier_rate is None
    assert result.raw_loads[0]["shipmentId"] == 127472397


def test_later_freightflow_snapshot_is_covered_with_carrier_rate() -> None:
    adapter = FreightFlowAdapter()
    parsed = adapter.parse_file(
        SourceFile(Path("later.json"), json.dumps(_payload(booked=True)).encode()),
        TenantContext("tenant-a"),
    )
    result = adapter.normalize(parsed, TenantContext("tenant-a"))
    assert result.loads[0].status.value == "COVERED"
    assert result.loads[0].carrier is not None
    assert result.loads[0].carrier_rate is not None
    assert result.loads[0].carrier_rate.amount == 1180
