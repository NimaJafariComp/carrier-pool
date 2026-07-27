"""Initial transactional ingestion coordinator for FreightFlow files."""

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import cast
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from carrier_pool.db.models import (
    Carrier,
    Customer,
    IngestionFile,
    IngestionStatus,
    Load,
    LoadVersion,
    SourceRateEntry,
    Stop,
)
from carrier_pool.db.tenant import set_tenant_context
from carrier_pool.domain.models import (
    CanonicalCarrierSnapshot,
    CanonicalCustomerSnapshot,
    CanonicalLoadSnapshot,
    CanonicalStop,
    NormalizedSync,
    SourceFinancialEntry,
)
from carrier_pool.domain.types import FinancialSide, Money, SourceSystem
from carrier_pool.geography.enrichment import enrich_stop
from carrier_pool.ingestion.base import InvalidSourceFileError, SourceFile, TenantContext
from carrier_pool.ingestion.brokeros import normalize_brokeros, parse_brokeros_file
from carrier_pool.ingestion.freightflow import FreightFlowAdapter
from carrier_pool.ingestion.hauldesk import (
    HaulDeskAssembly,
    normalize_hauldesk,
    parse_hauldesk_file,
)
from carrier_pool.ingestion.precedence import VersionTiming, choose_current_version
from carrier_pool.ingestion.reporting import IngestionReport


@dataclass(frozen=True, slots=True)
class IngestionResult:
    duplicate: bool
    versions_created: int
    report: IngestionReport | None = None


class FreightFlowIngestionCoordinator:
    """Persist one normalized FreightFlow sync atomically and idempotently."""

    source_system = SourceSystem.FREIGHTFLOW

    def ingest(
        self, session: Session, source_file: SourceFile, tenant: TenantContext
    ) -> IngestionResult:
        """Run one file transaction and persist an isolated failure record on fatal error."""
        try:
            result = self._ingest(session, source_file, tenant)
            if result.report is not None:
                result.report.log()
            return result
        except Exception as error:
            session.rollback()
            self._record_failure(session, source_file, tenant, error).log(failed=True)
            raise

    def _ingest(
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
                return _result(existing, no_op=True)
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
                loads_seen=len(normalized.loads),
                warnings_count=len(normalized.warnings),
            )
            session.add(ingestion)
            session.flush()
            versions = 0
            projections = 0
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
                if not self._becomes_current(load, version, normalized.metadata.sync_at, ingestion):
                    versions += 1
                    continue
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
                    _current_stop(tenant.tenant_id, load, stop) for stop in snapshot.stops
                )
                projections += 1
                versions += 1
            ingestion.status, ingestion.completed_at, ingestion.versions_created = (
                IngestionStatus.COMPLETED,
                normalized.metadata.observed_at,
                versions,
            )
            ingestion.projections_updated = projections
            return _result(ingestion, no_op=False)

    def _record_failure(
        self, session: Session, source_file: SourceFile, tenant: TenantContext, error: Exception
    ) -> IngestionReport:
        """Store only non-sensitive failure classification after file facts are rolled back."""
        checksum = hashlib.sha256(source_file.content).hexdigest()
        now = datetime.now(UTC)
        tenant_id = _uuid(tenant.tenant_id)
        with session.begin():
            set_tenant_context(session, tenant_id)
            existing = session.scalar(
                select(IngestionFile).where(
                    IngestionFile.tenant_id == tenant_id, IngestionFile.sha256 == checksum
                )
            )

            if existing is not None:
                return IngestionReport.from_file(existing, no_op=False)
            failed = IngestionFile(
                tenant_id=tenant_id,
                source_system=self.source_system,
                relative_path=str(source_file.path.parent),
                file_name=source_file.path.name,
                sha256=checksum,
                raw_payload={"failure": {"code": _failure_code(error)}},
                sync_at=now,
                observed_at=now,
                status=IngestionStatus.FAILED,
                started_at=now,
                completed_at=now,
                errors_count=1,
                error_details={
                    "code": _failure_code(error),
                    "category": type(error).__name__,
                },
            )
            session.add(failed)
            return IngestionReport.from_file(failed, no_op=False)

    def _becomes_current(
        self, load: Load, candidate: LoadVersion, source_sync_at: datetime, ingestion: IngestionFile
    ) -> bool:
        current = load.current_version
        current_timing = (
            None
            if current is None
            else VersionTiming(
                current.ingestion_file.sync_at,
                current.source_modified_at,
                current.observed_at,
                current.status,
            )
        )
        decision = choose_current_version(
            current_timing,
            VersionTiming(
                source_sync_at,
                candidate.source_modified_at,
                candidate.observed_at,
                candidate.status,
            ),
        )
        if decision.anomaly_code is not None:
            self._record_anomaly(ingestion, decision.anomaly_code)
        return decision.becomes_current

    @staticmethod
    def _record_anomaly(ingestion: IngestionFile, code: str) -> None:
        details = dict(ingestion.warning_details or {})
        anomalies = list(details.get("anomalies", []))
        anomalies.append({"code": code})
        ingestion.warning_details = {"anomalies": anomalies}
        ingestion.warnings_count += 1

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


class HaulDeskIngestionCoordinator(FreightFlowIngestionCoordinator):
    """Persist HaulDesk snapshots and append-only financial ledger rows."""

    source_system = SourceSystem.HAULDESK

    def _ingest(
        self, session: Session, source_file: SourceFile, tenant: TenantContext
    ) -> IngestionResult:
        checksum = hashlib.sha256(source_file.content).hexdigest()
        raw_payload = json.loads(source_file.content)
        tenant_id = _uuid(tenant.tenant_id)

        with session.begin():
            set_tenant_context(session, tenant_id)
            existing = session.scalar(
                select(IngestionFile).where(
                    IngestionFile.tenant_id == tenant.tenant_id, IngestionFile.sha256 == checksum
                )
            )
            if existing is not None and existing.status is IngestionStatus.COMPLETED:
                return _result(existing, no_op=True)

            known_carrier_ids = {
                int(external_id)
                for external_id in session.scalars(
                    select(Carrier.external_id).where(
                        Carrier.tenant_id == tenant.tenant_id,
                        Carrier.source_system == SourceSystem.HAULDESK,
                    )
                )
                if external_id.isdigit()
            }
            assembly = parse_hauldesk_file(source_file, known_carrier_ids)
            normalized = normalize_hauldesk(assembly, tenant.tenant_id, source_file.path.name)
            ingestion = IngestionFile(
                tenant_id=tenant.tenant_id,
                source_system=SourceSystem.HAULDESK,
                relative_path=str(source_file.path.parent),
                file_name=source_file.path.name,
                sha256=checksum,
                raw_payload=raw_payload,
                sync_at=normalized.metadata.sync_at,
                observed_at=normalized.metadata.observed_at,
                status=IngestionStatus.PROCESSING,
                started_at=normalized.metadata.observed_at,
                loads_seen=len(normalized.loads),
                warnings_count=len(normalized.warnings),
            )
            session.add(ingestion)
            session.flush()

            raw_loads = {str(load["load_num"]): load for load in raw_payload.get("loads", [])}
            raw_rates = {str(rate["rate_id"]): rate for rate in raw_payload.get("rates", [])}
            snapshots_by_external_id = {
                str(snapshot.identity.external_id): snapshot for snapshot in normalized.loads
            }
            loads_by_external_id: dict[str, Load] = {}
            for snapshot in normalized.loads:
                load = self._upsert_hauldesk_load(session, snapshot, assembly, normalized)
                loads_by_external_id[str(snapshot.identity.external_id)] = load

            affected_loads = dict(loads_by_external_id)
            for entry in normalized.source_financial_entries:
                external_load_id = str(entry.load_identity.external_id)
                load = affected_loads.get(external_load_id)
                if load is None:
                    load = session.scalar(
                        select(Load).where(
                            Load.tenant_id == tenant.tenant_id,
                            Load.source_system == SourceSystem.HAULDESK,
                            Load.external_id == external_load_id,
                        )
                    )
                    if load is None:
                        raise InvalidSourceFileError(
                            source_file.path,
                            f"Rate {entry.identity.external_id!s} references unknown load "
                            f"{external_load_id!r}.",
                        )
                    affected_loads[external_load_id] = load
                self._insert_rate_entry(
                    session,
                    ingestion,
                    load,
                    entry,
                    raw_rates[str(entry.identity.external_id)],
                    normalized.metadata.observed_at,
                )
            session.flush()

            versions = 0
            projections = 0
            for external_load_id, load in affected_loads.items():
                bill_total, pay_total = self._ledger_totals(
                    session, load, normalized.metadata.observed_at
                )
                snapshot = snapshots_by_external_id.get(external_load_id)
                if snapshot is None:
                    version = self._persist_ledger_only_version(
                        session, ingestion, load, bill_total, pay_total, normalized
                    )
                else:
                    version = self._persist_hauldesk_version(
                        session,
                        ingestion,
                        load,
                        snapshot,
                        raw_loads[external_load_id],
                        bill_total,
                        pay_total,
                        normalized,
                        assembly,
                    )
                if version is not None:
                    versions += 1
                    projections += int(load.current_version_id == version.id)

            ingestion.status = IngestionStatus.COMPLETED
            ingestion.completed_at = normalized.metadata.observed_at
            ingestion.versions_created = versions
            ingestion.projections_updated = projections
            return _result(ingestion, no_op=False)

    def _upsert_hauldesk_load(
        self,
        session: Session,
        snapshot: CanonicalLoadSnapshot,
        assembly: HaulDeskAssembly,
        normalized: NormalizedSync,
    ) -> Load:
        customer = self._customer(session, snapshot.customer, normalized.metadata.observed_at)
        carrier = self._hauldesk_carrier(
            session, snapshot, assembly, normalized.metadata.observed_at
        )
        load = session.scalar(
            select(Load).where(
                Load.tenant_id == snapshot.identity.tenant_id,
                Load.source_system == SourceSystem.HAULDESK,
                Load.external_id == snapshot.identity.external_id,
            )
        )
        if load is not None:
            return load
        load = Load(
            tenant_id=snapshot.identity.tenant_id,
            source_system=SourceSystem.HAULDESK,
            external_id=snapshot.identity.external_id,
            load_number=snapshot.load_number,
            customer=customer,
            carrier=carrier,
            status=snapshot.status,
            equipment=snapshot.equipment,
            weight_lbs=snapshot.weight_lbs,
            distance_miles=snapshot.distance_miles,
            source_created_at=snapshot.source_created_at,
            source_modified_at=snapshot.source_modified_at,
            observed_at=normalized.metadata.observed_at,
        )
        session.add(load)
        session.flush()
        return load

    def _hauldesk_carrier(
        self,
        session: Session,
        snapshot: CanonicalLoadSnapshot,
        assembly: HaulDeskAssembly,
        observed_at: datetime,
    ) -> Carrier | None:
        if snapshot.carrier is not None:
            return self._carrier(session, snapshot.carrier, observed_at)
        source_load = next(
            load for load in assembly.sync.loads if load.load_num == snapshot.identity.external_id
        )
        if source_load.carrier_ref is None:
            return None
        return session.scalar(
            select(Carrier).where(
                Carrier.tenant_id == snapshot.identity.tenant_id,
                Carrier.source_system == SourceSystem.HAULDESK,
                Carrier.external_id == str(source_load.carrier_ref),
            )
        )

    def _insert_rate_entry(
        self,
        session: Session,
        ingestion: IngestionFile,
        load: Load,
        entry: SourceFinancialEntry,
        raw_snapshot: dict[str, object],
        observed_at: datetime,
    ) -> None:
        existing = session.scalar(
            select(SourceRateEntry).where(
                SourceRateEntry.tenant_id == entry.identity.tenant_id,
                SourceRateEntry.source_system == SourceSystem.HAULDESK,
                SourceRateEntry.external_id == entry.identity.external_id,
            )
        )
        if existing is not None:
            return
        session.add(
            SourceRateEntry(
                tenant_id=entry.identity.tenant_id,
                load_id=load.id,
                ingestion_file_id=ingestion.id,
                source_system=SourceSystem.HAULDESK,
                external_id=entry.identity.external_id,
                side=entry.side,
                code=entry.code,
                amount=entry.amount.amount,
                source_created_at=entry.source_created_at,
                observed_at=observed_at,
                raw_snapshot=raw_snapshot,
            )
        )

    def _ledger_totals(
        self, session: Session, load: Load, as_of: datetime
    ) -> tuple[Decimal, Decimal]:
        rows = session.execute(
            select(SourceRateEntry.side, func.coalesce(func.sum(SourceRateEntry.amount), 0))
            .where(
                SourceRateEntry.tenant_id == load.tenant_id,
                SourceRateEntry.load_id == load.id,
                SourceRateEntry.observed_at <= as_of,
            )
            .group_by(SourceRateEntry.side)
        )
        totals = {side: Decimal(amount) for side, amount in rows}
        return (
            totals.get(FinancialSide.BILL, Decimal("0")),
            totals.get(FinancialSide.PAY, Decimal("0")),
        )

    def _persist_hauldesk_version(
        self,
        session: Session,
        ingestion: IngestionFile,
        load: Load,
        snapshot: CanonicalLoadSnapshot,
        raw_snapshot: dict[str, object],
        bill_total: Decimal,
        pay_total: Decimal,
        normalized: NormalizedSync,
        assembly: HaulDeskAssembly,
    ) -> LoadVersion | None:
        customer = self._customer(session, snapshot.customer, normalized.metadata.observed_at)
        carrier = self._hauldesk_carrier(
            session, snapshot, assembly, normalized.metadata.observed_at
        )
        canonical_snapshot = _hauldesk_canonical(snapshot, carrier, bill_total, pay_total)
        snapshot_hash = _snapshot_hash(canonical_snapshot)
        if self._version_exists(session, load, snapshot_hash):
            return None
        version = LoadVersion(
            tenant_id=load.tenant_id,
            load=load,
            ingestion_file=ingestion,
            source_modified_at=snapshot.source_modified_at,
            observed_at=normalized.metadata.observed_at,
            status=snapshot.status,
            equipment=snapshot.equipment,
            customer=customer,
            carrier=carrier,
            customer_rate_amount=bill_total,
            carrier_rate_amount=pay_total,
            weight_lbs=snapshot.weight_lbs,
            distance_miles=snapshot.distance_miles,
            canonical_snapshot=canonical_snapshot,
            raw_snapshot=raw_snapshot,
            snapshot_hash=snapshot_hash,
            supersedes_id=load.current_version_id,
        )
        session.add(version)
        session.flush()
        if not self._becomes_current(load, version, normalized.metadata.sync_at, ingestion):
            return version
        self._update_hauldesk_projection(
            load, version, snapshot, customer, carrier, bill_total, pay_total
        )
        session.execute(
            delete(Stop).where(Stop.tenant_id == load.tenant_id, Stop.load_id == load.id)
        )
        session.add_all(_current_stop(load.tenant_id, load, stop) for stop in snapshot.stops)
        return version

    def _persist_ledger_only_version(
        self,
        session: Session,
        ingestion: IngestionFile,
        load: Load,
        bill_total: Decimal,
        pay_total: Decimal,
        normalized: NormalizedSync,
    ) -> LoadVersion | None:
        previous = load.current_version
        if previous is None:
            raise ValueError("HaulDesk rate-only sync requires a prior load version.")
        canonical_snapshot = dict(previous.canonical_snapshot)
        canonical_snapshot["customer_rate_amount"] = str(bill_total)
        canonical_snapshot["carrier_rate_amount"] = str(pay_total)
        snapshot_hash = _snapshot_hash(canonical_snapshot)
        if self._version_exists(session, load, snapshot_hash):
            return None
        version = LoadVersion(
            tenant_id=load.tenant_id,
            load=load,
            ingestion_file=ingestion,
            source_modified_at=previous.source_modified_at,
            observed_at=normalized.metadata.observed_at,
            status=previous.status,
            equipment=previous.equipment,
            customer=previous.customer,
            carrier=previous.carrier,
            customer_rate_amount=bill_total,
            carrier_rate_amount=pay_total,
            weight_lbs=previous.weight_lbs,
            distance_miles=previous.distance_miles,
            canonical_snapshot=canonical_snapshot,
            raw_snapshot=previous.raw_snapshot,
            snapshot_hash=snapshot_hash,
            supersedes_id=previous.id,
        )
        session.add(version)
        session.flush()
        if not self._becomes_current(load, version, normalized.metadata.sync_at, ingestion):
            return version
        load.customer_rate_amount = bill_total
        load.carrier_rate_amount = pay_total
        load.observed_at = normalized.metadata.observed_at
        load.current_version = version
        return version

    def _version_exists(self, session: Session, load: Load, snapshot_hash: str) -> bool:
        return (
            session.scalar(
                select(LoadVersion.id).where(
                    LoadVersion.tenant_id == load.tenant_id,
                    LoadVersion.load_id == load.id,
                    LoadVersion.snapshot_hash == snapshot_hash,
                )
            )
            is not None
        )

    def _update_hauldesk_projection(
        self,
        load: Load,
        version: LoadVersion,
        snapshot: CanonicalLoadSnapshot,
        customer: Customer,
        carrier: Carrier | None,
        bill_total: Decimal,
        pay_total: Decimal,
    ) -> None:
        load.load_number = snapshot.load_number
        load.customer = customer
        load.carrier = carrier
        load.status = snapshot.status
        load.equipment = snapshot.equipment
        load.customer_rate_amount = bill_total
        load.carrier_rate_amount = pay_total
        load.weight_lbs = snapshot.weight_lbs
        load.distance_miles = snapshot.distance_miles
        load.source_created_at = snapshot.source_created_at
        load.source_modified_at = snapshot.source_modified_at
        load.observed_at = version.observed_at
        load.current_version = version


class BrokerOSIngestionCoordinator(FreightFlowIngestionCoordinator):
    """Persist BrokerOS snapshots, treating rates as restated totals."""

    source_system = SourceSystem.BROKEROS

    def _ingest(
        self, session: Session, source_file: SourceFile, tenant: TenantContext
    ) -> IngestionResult:
        checksum = hashlib.sha256(source_file.content).hexdigest()
        raw_payload = json.loads(source_file.content)
        with session.begin():
            set_tenant_context(session, _uuid(tenant.tenant_id))
            existing = session.scalar(
                select(IngestionFile).where(
                    IngestionFile.tenant_id == tenant.tenant_id, IngestionFile.sha256 == checksum
                )
            )
            if existing is not None and existing.status is IngestionStatus.COMPLETED:
                return _result(existing, no_op=True)

            normalized = normalize_brokeros(
                parse_brokeros_file(source_file), tenant.tenant_id, source_file.path.name
            )
            ingestion = IngestionFile(
                tenant_id=tenant.tenant_id,
                source_system=SourceSystem.BROKEROS,
                relative_path=str(source_file.path.parent),
                file_name=source_file.path.name,
                sha256=checksum,
                raw_payload=raw_payload,
                sync_at=normalized.metadata.sync_at,
                observed_at=normalized.metadata.observed_at,
                status=IngestionStatus.PROCESSING,
                started_at=normalized.metadata.observed_at,
                loads_seen=len(normalized.loads),
            )
            session.add(ingestion)
            session.flush()
            raw_loads = {str(record["Id"]): record for record in raw_payload.get("records", [])}

            versions = 0
            projections = 0
            for snapshot in normalized.loads:
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
                        Load.source_system == SourceSystem.BROKEROS,
                        Load.external_id == snapshot.identity.external_id,
                    )
                )
                if load is None:
                    load = Load(
                        tenant_id=tenant.tenant_id,
                        source_system=SourceSystem.BROKEROS,
                        external_id=snapshot.identity.external_id,
                        load_number=snapshot.load_number,
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

                canonical_snapshot = _brokeros_canonical(snapshot)
                snapshot_hash = _snapshot_hash(canonical_snapshot)
                existing_version = session.scalar(
                    select(LoadVersion.id).where(
                        LoadVersion.tenant_id == tenant.tenant_id,
                        LoadVersion.load_id == load.id,
                        LoadVersion.snapshot_hash == snapshot_hash,
                    )
                )
                if existing_version is not None:
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
                    canonical_snapshot=canonical_snapshot,
                    raw_snapshot=raw_loads[str(snapshot.identity.external_id)],
                    snapshot_hash=snapshot_hash,
                    supersedes_id=load.current_version_id,
                )
                session.add(version)
                session.flush()
                if not self._becomes_current(load, version, normalized.metadata.sync_at, ingestion):
                    versions += 1
                    continue
                self._update_brokeros_projection(load, version, snapshot, customer, carrier)
                session.execute(
                    delete(Stop).where(Stop.tenant_id == tenant.tenant_id, Stop.load_id == load.id)
                )
                session.add_all(
                    _current_stop(tenant.tenant_id, load, stop) for stop in snapshot.stops
                )
                projections += 1
                versions += 1

            ingestion.status = IngestionStatus.COMPLETED
            ingestion.completed_at = normalized.metadata.observed_at
            ingestion.versions_created = versions
            ingestion.projections_updated = projections
            return _result(ingestion, no_op=False)

    def _update_brokeros_projection(
        self,
        load: Load,
        version: LoadVersion,
        snapshot: CanonicalLoadSnapshot,
        customer: Customer,
        carrier: Carrier | None,
    ) -> None:
        load.load_number = snapshot.load_number
        load.customer = customer
        load.carrier = carrier
        load.status = snapshot.status
        load.equipment = snapshot.equipment
        load.customer_rate_amount = _amount(snapshot.customer_rate)
        load.carrier_rate_amount = _amount(snapshot.carrier_rate)
        load.weight_lbs = snapshot.weight_lbs
        load.distance_miles = snapshot.distance_miles
        load.source_created_at = snapshot.source_created_at
        load.source_modified_at = snapshot.source_modified_at
        load.observed_at = version.observed_at
        load.current_version = version


def _amount(value: Money | None) -> Decimal | None:
    return None if value is None else value.amount


def _current_stop(tenant_id: str | UUID, load: Load, stop: CanonicalStop) -> Stop:
    geography = enrich_stop(stop.city, stop.state, stop.postal_code)
    return Stop(
        tenant_id=tenant_id,
        load=load,
        sequence=stop.sequence,
        is_pickup=stop.is_pickup,
        is_dropoff=stop.is_dropoff,
        facility_name=stop.facility_name,
        city=stop.city,
        state=stop.state,
        postal_code=stop.postal_code,
        latitude=geography.latitude,
        longitude=geography.longitude,
        metro_group=geography.metro_group,
        geography_quality_flags=geography.quality_flags,
        scheduled_start_at=stop.scheduled_start_at,
        scheduled_end_at=stop.scheduled_end_at,
        actual_arrival_at=stop.actual_arrival_at,
        actual_departure_at=stop.actual_departure_at,
    )


def _result(ingestion: IngestionFile, *, no_op: bool) -> IngestionResult:
    return IngestionResult(
        duplicate=no_op,
        versions_created=ingestion.versions_created,
        report=IngestionReport.from_file(ingestion, no_op=no_op),
    )


def _failure_code(error: Exception) -> str:
    return (
        "SOURCE_VALIDATION_FAILED"
        if isinstance(error, InvalidSourceFileError)
        else "INGESTION_FAILED"
    )


def _uuid(value: str):
    from uuid import UUID

    return UUID(value)


def _canonical(value: CanonicalLoadSnapshot) -> dict[str, object]:
    return _json_safe_snapshot(
        {
            "external_id": str(value.identity.external_id),
            "status": value.status.value,
            "stops": [asdict(stop) for stop in value.stops],
        }
    )


def _hauldesk_canonical(
    value: CanonicalLoadSnapshot,
    carrier: Carrier | None,
    bill_total: Decimal,
    pay_total: Decimal,
) -> dict[str, object]:
    return _json_safe_snapshot(
        {
            "external_id": str(value.identity.external_id),
            "load_number": value.load_number,
            "status": value.status.value,
            "equipment": None if value.equipment is None else value.equipment.value,
            "customer": {
                "external_id": str(value.customer.identity.external_id),
                "name": value.customer.name,
            },
            "carrier": None
            if carrier is None
            else {
                "external_id": carrier.external_id,
                "name": carrier.name,
                "mc_number": carrier.mc_number,
                "dot_number": carrier.dot_number,
            },
            "stops": [asdict(stop) for stop in value.stops],
            "source_created_at": value.source_created_at,
            "source_modified_at": value.source_modified_at,
            "weight_lbs": value.weight_lbs,
            "distance_miles": value.distance_miles,
            "customer_rate_amount": str(bill_total),
            "carrier_rate_amount": str(pay_total),
        }
    )


def _brokeros_canonical(value: CanonicalLoadSnapshot) -> dict[str, object]:
    return _json_safe_snapshot(
        {
            "external_id": str(value.identity.external_id),
            "load_number": value.load_number,
            "status": value.status.value,
            "equipment": None if value.equipment is None else value.equipment.value,
            "customer": {
                "external_id": str(value.customer.identity.external_id),
                "name": value.customer.name,
            },
            "carrier": None
            if value.carrier is None
            else {
                "external_id": str(value.carrier.identity.external_id),
                "name": value.carrier.name,
            },
            "stops": [asdict(stop) for stop in value.stops],
            "source_created_at": value.source_created_at,
            "source_modified_at": value.source_modified_at,
            "weight_lbs": value.weight_lbs,
            "distance_miles": value.distance_miles,
            "customer_rate_amount": None
            if value.customer_rate is None
            else str(value.customer_rate.amount),
            "carrier_rate_amount": None
            if value.carrier_rate is None
            else str(value.carrier_rate.amount),
            "cargo_items": [asdict(item) for item in value.cargo_items],
        }
    )


def _json_safe_snapshot(snapshot: dict[str, object]) -> dict[str, object]:
    return {key: _json_safe_value(value) for key, value in snapshot.items()}


def _json_safe_value(value: object) -> object:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        dictionary = cast(dict[object, object], value)
        return {str(key): _json_safe_value(item) for key, item in dictionary.items()}
    if isinstance(value, (list, tuple)):
        sequence = cast(list[object] | tuple[object, ...], value)
        return [_json_safe_value(item) for item in sequence]
    return value


def _snapshot_hash(snapshot: dict[str, object]) -> str:
    def default(value: object) -> str:
        if isinstance(value, Decimal):
            return str(value)
        if isinstance(value, (date, datetime)):
            return value.isoformat()
        raise TypeError(f"Unsupported canonical snapshot value: {type(value)!r}")

    serialized = json.dumps(snapshot, default=default, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode()).hexdigest()
