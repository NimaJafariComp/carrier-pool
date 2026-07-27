"""Initial transactional ingestion coordinator for FreightFlow files."""

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from carrier_pool.db.models import (
    Carrier,
    Customer,
    IngestionFile,
    IngestionStatus,
    Load,
    LoadVersion,
    Stop,
)
from carrier_pool.db.tenant import set_tenant_context
from carrier_pool.domain.models import (
    CanonicalCarrierSnapshot,
    CanonicalCustomerSnapshot,
    CanonicalLoadSnapshot,
)
from carrier_pool.domain.types import Money
from carrier_pool.ingestion.base import SourceFile, TenantContext
from carrier_pool.ingestion.freightflow import FreightFlowAdapter


@dataclass(frozen=True, slots=True)
class IngestionResult:
    duplicate: bool
    versions_created: int


class FreightFlowIngestionCoordinator:
    """Persist one normalized FreightFlow sync atomically and idempotently."""

    def ingest(
        self, session: Session, source_file: SourceFile, tenant: TenantContext
    ) -> IngestionResult:
        adapter = FreightFlowAdapter()
        parsed = adapter.parse_file(source_file, tenant)
        normalized = adapter.normalize(parsed, tenant)
        checksum = hashlib.sha256(source_file.content).hexdigest()
        with session.begin():
            set_tenant_context(session, _uuid(tenant.tenant_id))
            existing = session.scalar(
                select(IngestionFile).where(
                    IngestionFile.tenant_id == tenant.tenant_id, IngestionFile.sha256 == checksum
                )
            )
            if existing is not None and existing.status is IngestionStatus.COMPLETED:
                return IngestionResult(True, 0)
            ingestion = IngestionFile(
                tenant_id=tenant.tenant_id,
                source_system=adapter.source_system,
                relative_path=str(source_file.path.parent),
                file_name=source_file.path.name,
                sha256=checksum,
                raw_payload=json.loads(source_file.content),
                sync_at=normalized.metadata.sync_at,
                observed_at=normalized.metadata.observed_at,
                status=IngestionStatus.PROCESSING,
                started_at=normalized.metadata.observed_at,
            )
            session.add(ingestion)
            session.flush()
            versions = 0
            for snapshot, raw_load in zip(normalized.loads, normalized.raw_loads, strict=True):
                customer = self._customer(
                    session, snapshot.customer, normalized.metadata.observed_at
                )
                carrier = (
                    None
                    if snapshot.carrier is None
                    else self._carrier(session, snapshot.carrier, normalized.metadata.observed_at)
                )
                load = session.scalar(
                    select(Load).where(
                        Load.tenant_id == tenant.tenant_id,
                        Load.source_system == adapter.source_system,
                        Load.external_id == snapshot.identity.external_id,
                    )
                )
                if load is None:
                    load = Load(
                        tenant_id=tenant.tenant_id,
                        source_system=adapter.source_system,
                        external_id=snapshot.identity.external_id,
                        customer=customer,
                        carrier=carrier,
                        status=snapshot.status,
                        equipment=snapshot.equipment,
                        customer_rate_amount=_amount(snapshot.customer_rate),
                        carrier_rate_amount=_amount(snapshot.carrier_rate),
                        weight_lbs=snapshot.weight_lbs,
                        distance_miles=snapshot.distance_miles,
                        source_created_at=snapshot.source_created_at,
                        source_modified_at=snapshot.source_modified_at,
                        observed_at=normalized.metadata.observed_at,
                    )
                    session.add(load)
                    session.flush()
                snapshot_hash = hashlib.sha256(
                    json.dumps(raw_load, sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest()
                old = session.scalar(
                    select(LoadVersion).where(
                        LoadVersion.tenant_id == tenant.tenant_id,
                        LoadVersion.load_id == load.id,
                        LoadVersion.snapshot_hash == snapshot_hash,
                    )
                )
                if old is not None:
                    continue
                version = LoadVersion(
                    tenant_id=tenant.tenant_id,
                    load=load,
                    ingestion_file=ingestion,
                    source_modified_at=snapshot.source_modified_at,
                    observed_at=normalized.metadata.observed_at,
                    status=snapshot.status,
                    equipment=snapshot.equipment,
                    customer=customer,
                    carrier=carrier,
                    customer_rate_amount=_amount(snapshot.customer_rate),
                    carrier_rate_amount=_amount(snapshot.carrier_rate),
                    weight_lbs=snapshot.weight_lbs,
                    distance_miles=snapshot.distance_miles,
                    canonical_snapshot=_canonical(snapshot),
                    raw_snapshot=raw_load,
                    snapshot_hash=snapshot_hash,
                    supersedes_id=load.current_version_id,
                )
                session.add(version)
                session.flush()
                load.customer, load.carrier, load.status, load.equipment = (
                    customer,
                    carrier,
                    snapshot.status,
                    snapshot.equipment,
                )
                load.customer_rate_amount, load.carrier_rate_amount = (
                    _amount(snapshot.customer_rate),
                    _amount(snapshot.carrier_rate),
                )
                load.weight_lbs, load.distance_miles = snapshot.weight_lbs, snapshot.distance_miles
                (
                    load.source_created_at,
                    load.source_modified_at,
                    load.observed_at,
                    load.current_version,
                ) = (
                    snapshot.source_created_at,
                    snapshot.source_modified_at,
                    normalized.metadata.observed_at,
                    version,
                )
                session.execute(
                    delete(Stop).where(Stop.tenant_id == tenant.tenant_id, Stop.load_id == load.id)
                )
                session.add_all(
                    Stop(
                        tenant_id=tenant.tenant_id,
                        load=load,
                        sequence=stop.sequence,
                        is_pickup=stop.is_pickup,
                        is_dropoff=stop.is_dropoff,
                        city=stop.city,
                        state=stop.state,
                        postal_code=stop.postal_code,
                        scheduled_start_at=stop.scheduled_start_at,
                        scheduled_end_at=stop.scheduled_end_at,
                        actual_arrival_at=stop.actual_arrival_at,
                        actual_departure_at=stop.actual_departure_at,
                    )
                    for stop in snapshot.stops
                )
                versions += 1
            ingestion.status, ingestion.completed_at, ingestion.versions_created = (
                IngestionStatus.COMPLETED,
                normalized.metadata.observed_at,
                versions,
            )
            return IngestionResult(False, versions)

    def _customer(
        self, session: Session, value: CanonicalCustomerSnapshot, observed_at: datetime
    ) -> Customer:
        result = session.scalar(
            select(Customer).where(
                Customer.tenant_id == value.identity.tenant_id,
                Customer.source_system == value.identity.source_system,
                Customer.external_id == value.identity.external_id,
            )
        )
        if result is None:
            result = Customer(
                tenant_id=value.identity.tenant_id,
                source_system=value.identity.source_system,
                external_id=value.identity.external_id,
                name=value.name,
                first_observed_at=observed_at,
                last_observed_at=observed_at,
            )
            session.add(result)
        else:
            result.name, result.last_observed_at = value.name, observed_at
        return result

    def _carrier(
        self, session: Session, value: CanonicalCarrierSnapshot, observed_at: datetime
    ) -> Carrier:
        result = session.scalar(
            select(Carrier).where(
                Carrier.tenant_id == value.identity.tenant_id,
                Carrier.source_system == value.identity.source_system,
                Carrier.external_id == value.identity.external_id,
            )
        )
        if result is None:
            result = Carrier(
                tenant_id=value.identity.tenant_id,
                source_system=value.identity.source_system,
                external_id=value.identity.external_id,
                name=value.name,
                normalized_name=value.name.upper(),
                mc_number=value.mc_number,
                dot_number=value.dot_number,
                phone_number=value.phone_number,
                home_city=value.home_city,
                home_state=value.home_state,
                first_observed_at=observed_at,
                last_observed_at=observed_at,
            )
            session.add(result)
        else:
            result.name, result.last_observed_at = value.name, observed_at
        return result


def _amount(value: Money | None) -> Decimal | None:
    return None if value is None else value.amount


def _uuid(value: str):
    from uuid import UUID

    return UUID(value)


def _canonical(value: CanonicalLoadSnapshot) -> dict[str, object]:
    return {
        "external_id": str(value.identity.external_id),
        "status": value.status.value,
        "stops": [asdict(stop) for stop in value.stops],
    }
