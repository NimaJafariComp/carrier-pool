"""Contract tests against comment-stripped source schema examples."""

import json
import re
from pathlib import Path

import pytest

from carrier_pool.domain.types import LoadStatus, SourceSystem
from carrier_pool.ingestion.base import SourceFile, TenantContext
from carrier_pool.ingestion.brokeros import BrokerOSAdapter
from carrier_pool.ingestion.freightflow import FreightFlowAdapter
from carrier_pool.ingestion.hauldesk import HaulDeskAdapter


def _source_example(directory: str, name: str) -> SourceFile:
    path = Path(__file__).parents[3] / "data" / directory / name
    content = re.sub(r"//.*$", "", path.read_text(), flags=re.MULTILINE)
    return SourceFile(path, json.dumps(json.loads(content)).encode())


@pytest.mark.parametrize(
    ("adapter", "directory", "expected_source", "expected_status"),
    [
        (FreightFlowAdapter(), "tms_a_freightflow", SourceSystem.FREIGHTFLOW, LoadStatus.ACTIVE),
        (HaulDeskAdapter(), "tms_b_hauldesk", SourceSystem.HAULDESK, LoadStatus.COVERED),
        (BrokerOSAdapter(), "tms_c_brokeros", SourceSystem.BROKEROS, LoadStatus.ACTIVE),
    ],
)
def test_supplied_schema_example_normalizes_through_its_adapter(
    adapter: FreightFlowAdapter | HaulDeskAdapter | BrokerOSAdapter,
    directory: str,
    expected_source: SourceSystem,
    expected_status: LoadStatus,
) -> None:
    source_file = _source_example(directory, "example_sync.jsonc")
    tenant = TenantContext("schema-example-tenant")

    normalized = adapter.normalize(adapter.parse_file(source_file, tenant), tenant)

    assert normalized.metadata.source_system is expected_source
    assert len(normalized.loads) == 1
    assert normalized.loads[0].status is expected_status
