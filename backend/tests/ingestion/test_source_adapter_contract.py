from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from carrier_pool.domain.models import (
    CanonicalCustomerSnapshot,
    CanonicalLoadSnapshot,
    CanonicalSourceIdentity,
    CanonicalStop,
    NormalizedSync,
    SourceFinancialEntry,
    SyncMetadata,
)
from carrier_pool.domain.types import FinancialSide, LoadStatus, Money, SourceSystem
from carrier_pool.ingestion.base import ParsedSync, SourceAdapter, SourceFile, TenantContext


class FakeAdapter:
    """Test-only adapter proving the contract without modeling a real TMS."""

    source_system = SourceSystem.FREIGHTFLOW

    def parse_file(self, source_file: SourceFile, tenant: TenantContext) -> ParsedSync:
        return ParsedSync(
            source_system=self.source_system,
            source_file=source_file,
            payload={"tenant_id": tenant.tenant_id},
        )

    def normalize(self, parsed_sync: ParsedSync, tenant: TenantContext) -> NormalizedSync:
        timestamp = datetime(2026, 7, 6, 6, tzinfo=UTC)
        load_identity = CanonicalSourceIdentity(
            tenant_id=tenant.tenant_id,
            source_system=parsed_sync.source_system,
            external_id="load-1",
        )
        customer = CanonicalCustomerSnapshot(
            identity=CanonicalSourceIdentity(
                tenant_id=tenant.tenant_id,
                source_system=parsed_sync.source_system,
                external_id="customer-1",
            ),
            name="Demo Customer",
        )
        load = CanonicalLoadSnapshot(
            identity=load_identity,
            status=LoadStatus.ACTIVE,
            customer=customer,
            stops=(
                CanonicalStop(
                    sequence=1,
                    is_pickup=True,
                    is_dropoff=False,
                    city="Dallas",
                    state="TX",
                    postal_code="75201",
                ),
            ),
            source_created_at=timestamp,
            source_modified_at=timestamp,
        )
        rate_entry = SourceFinancialEntry(
            identity=CanonicalSourceIdentity(
                tenant_id=tenant.tenant_id,
                source_system=parsed_sync.source_system,
                external_id="rate-1",
            ),
            load_identity=load_identity,
            side=FinancialSide.PAY,
            code="LINEHAUL",
            amount=Money(amount=Decimal("1000.00")),
            source_created_at=timestamp,
        )
        return NormalizedSync(
            metadata=SyncMetadata(
                tenant_id=tenant.tenant_id,
                source_system=parsed_sync.source_system,
                source_file_name=parsed_sync.source_file.path.name,
                sync_at=timestamp,
                observed_at=timestamp,
            ),
            loads=(load,),
            customers=(customer,),
            source_financial_entries=(rate_entry,),
        )


def test_fake_adapter_produces_normalized_sync_without_persistence() -> None:
    adapter: SourceAdapter = FakeAdapter()
    tenant = TenantContext(tenant_id="demo-broker")
    source_file = SourceFile(path=Path("demo.json"), content=b"{}")

    parsed_sync = adapter.parse_file(source_file, tenant)
    normalized_sync = adapter.normalize(parsed_sync, tenant)

    assert normalized_sync.metadata.tenant_id == "demo-broker"
    assert normalized_sync.loads[0].identity.external_id == "load-1"
    assert normalized_sync.source_financial_entries[0].amount == Money.from_value("1000.00")
