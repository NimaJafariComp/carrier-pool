"""Generated correction scenarios remain auditable across present and historical views."""

import hashlib
import json
import os
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from carrier_pool.db.models import (
    CarrierRecommendation,
    Load,
    LoadVersion,
    SourceRateEntry,
    Stop,
    Tenant,
)
from carrier_pool.db.tenant import set_tenant_context
from carrier_pool.decisioning.backtest import RateBacktestHarness
from carrier_pool.decisioning.decision_runs import DecisionRunService
from carrier_pool.decisioning.pricing import HierarchicalRateEstimator
from carrier_pool.domain.types import FinancialSide, LoadStatus, SourceSystem
from carrier_pool.generator.scheduler import DAY11_SYNC_AT, write_sync_files
from carrier_pool.ingestion.base import SourceFile, TenantContext
from carrier_pool.ingestion.coordinator import (
    BrokerOSIngestionCoordinator,
    FreightFlowIngestionCoordinator,
    HaulDeskIngestionCoordinator,
    IngestionResult,
)
from carrier_pool.ingestion.discovery import (
    DiscoveredSync,
    FileIngestionOrchestrator,
    SourceBinding,
)
from carrier_pool.ingestion.rebuild import rebuild_current_projections

DATABASE_URL = os.getenv("DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="DATABASE_URL is required for PostgreSQL tests"
)


def _state_hash(session: Session, tenant_id: UUID) -> str:
    """Hash only rebuildable current projections for one tenant."""
    set_tenant_context(session, tenant_id)
    rows: list[tuple[object, ...]] = []
    for load in session.scalars(
        select(Load).where(Load.tenant_id == tenant_id).order_by(Load.external_id)
    ):
        stops = tuple(
            session.execute(
                select(Stop.sequence, Stop.postal_code)
                .where(Stop.tenant_id == tenant_id, Stop.load_id == load.id)
                .order_by(Stop.sequence)
            ).tuples()
        )
        rows.append(
            (
                load.external_id,
                load.status.value,
                str(load.customer_rate_amount),
                str(load.carrier_rate_amount),
                str(load.current_version_id),
                stops,
            )
        )
    return hashlib.sha256(json.dumps(rows, default=str).encode()).hexdigest()


def _seed_tenants(session: Session, sources: tuple[SourceSystem, ...]) -> dict[SourceSystem, UUID]:
    tenant_ids = {source: uuid4() for source in sources}
    session.add_all(
        Tenant(
            id=tenant_id,
            slug=f"generated-correction-{source.value.lower()}-{uuid4()}",
            name=f"Generated correction {source.value}",
            source_system=source,
        )
        for source, tenant_id in tenant_ids.items()
    )
    session.commit()
    return tenant_ids


def _ingest(session: Session, sync: DiscoveredSync) -> IngestionResult:
    coordinator = {
        SourceSystem.FREIGHTFLOW: FreightFlowIngestionCoordinator(),
        SourceSystem.HAULDESK: HaulDeskIngestionCoordinator(),
        SourceSystem.BROKEROS: BrokerOSIngestionCoordinator(),
    }[sync.binding.source_system]
    return coordinator.ingest(
        session,
        SourceFile(sync.path, sync.path.read_bytes()),
        TenantContext(sync.binding.tenant_id),
    )


def _external_load_id(sync: DiscoveredSync) -> str:
    """Read the stable source identifier from the generated correction payload."""
    payload = json.loads(sync.path.read_text())
    if sync.binding.source_system is SourceSystem.FREIGHTFLOW:
        return str(payload["loads"][0]["shipmentId"])
    if sync.binding.source_system is SourceSystem.BROKEROS:
        return str(payload["records"][0]["Id"])
    return str(payload["loads"][0]["load_num"])


def _external_load_id_with_carrier_rate(sync: DiscoveredSync, amount: Decimal) -> str:
    """Return the load that carries a correction, never merely the file's first load."""
    payload = json.loads(sync.path.read_text())
    expected = float(amount)
    if sync.binding.source_system is SourceSystem.FREIGHTFLOW:
        record = next(item for item in payload["loads"] if item["totalBuy"] == expected)
        return str(record["shipmentId"])
    if sync.binding.source_system is SourceSystem.BROKEROS:
        record = next(
            item for item in payload["records"] if item["bos__Carrier_Rate__c"] == expected
        )
        return str(record["Id"])
    raise AssertionError("HaulDesk corrections are append-only ledger rows.")


def _hauldesk_load_id_with_pay_rate(sync: DiscoveredSync, amount: Decimal) -> str:
    """Select the ledger row's load, not the first changed snapshot in its sync file."""
    payload = json.loads(sync.path.read_text())
    expected = float(amount)
    rate = next(
        item
        for item in payload["rates"]
        if item["side"] == "pay" and item["amount_usd"] == expected
    )
    return str(rate["load_num"])


def _load(session: Session, tenant_id: UUID, external_id: str) -> Load:
    set_tenant_context(session, tenant_id)
    load = session.scalar(
        select(Load).where(Load.tenant_id == tenant_id, Load.external_id == external_id)
    )
    assert load is not None
    return load


def _versions(session: Session, tenant_id: UUID, load_id: UUID) -> tuple[LoadVersion, ...]:
    set_tenant_context(session, tenant_id)
    return tuple(
        session.scalars(
            select(LoadVersion)
            .where(LoadVersion.tenant_id == tenant_id, LoadVersion.load_id == load_id)
            .order_by(LoadVersion.observed_at, LoadVersion.id)
        )
    )


def test_generated_replacement_and_ledger_corrections_update_current_state_once(
    tmp_path: Path,
) -> None:
    """The generated FF/BO restatements and HD ledger adjustment retain their source semantics."""
    assert DATABASE_URL is not None
    data_root = tmp_path / "data"
    write_sync_files(data_root)
    engine = create_engine(DATABASE_URL)
    sources = (SourceSystem.FREIGHTFLOW, SourceSystem.HAULDESK, SourceSystem.BROKEROS)
    directories = {
        SourceSystem.FREIGHTFLOW: "tms_a_freightflow",
        SourceSystem.HAULDESK: "tms_b_hauldesk",
        SourceSystem.BROKEROS: "tms_c_brokeros",
    }
    try:
        with Session(engine) as session:
            tenant_ids = _seed_tenants(session, sources)
            orchestrator = FileIngestionOrchestrator(
                tuple(
                    SourceBinding(data_root / directories[source], str(tenant_ids[source]), source)
                    for source in sources
                )
            )
            first = orchestrator.ingest_all(lambda sync: _ingest(session, sync))
            assert len(first) == 123

            ff_correction = next(
                sync
                for sync in orchestrator.discover()
                if sync.binding.source_system is SourceSystem.FREIGHTFLOW
                and '"totalBuy": 1265' in sync.path.read_text()
            )
            brokeros_correction = next(
                sync
                for sync in orchestrator.discover()
                if sync.binding.source_system is SourceSystem.BROKEROS
                and '"bos__Carrier_Rate__c": 1660' in sync.path.read_text()
            )
            for source, external_id, booking, corrected in (
                (
                    SourceSystem.FREIGHTFLOW,
                    _external_load_id_with_carrier_rate(ff_correction, Decimal("1265")),
                    Decimal("1210"),
                    Decimal("1265"),
                ),
                (
                    SourceSystem.BROKEROS,
                    _external_load_id_with_carrier_rate(brokeros_correction, Decimal("1660")),
                    Decimal("1630"),
                    Decimal("1660"),
                ),
            ):
                load = _load(session, tenant_ids[source], external_id)
                versions = _versions(session, tenant_ids[source], load.id)
                amounts = tuple(
                    version.carrier_rate_amount
                    for version in versions
                    if version.carrier_rate_amount is not None
                )
                assert booking in amounts
                assert corrected in amounts
                assert len({version.id for version in versions}) >= 2
                assert load.current_version_id == versions[-1].id
                assert load.carrier_rate_amount == corrected

            hauldesk_tenant = tenant_ids[SourceSystem.HAULDESK]
            hauldesk_adjustment = next(
                sync
                for sync in orchestrator.discover()
                if sync.binding.source_system is SourceSystem.HAULDESK
                and '"amount_usd": 35' in sync.path.read_text()
            )
            hauldesk_load = _load(
                session,
                hauldesk_tenant,
                _hauldesk_load_id_with_pay_rate(hauldesk_adjustment, Decimal("35")),
            )
            entries = tuple(
                session.scalars(
                    select(SourceRateEntry)
                    .where(
                        SourceRateEntry.tenant_id == hauldesk_tenant,
                        SourceRateEntry.load_id == hauldesk_load.id,
                        SourceRateEntry.side == FinancialSide.PAY,
                    )
                    .order_by(SourceRateEntry.observed_at, SourceRateEntry.id)
                )
            )
            assert tuple(entry.amount for entry in entries) == (Decimal("1190"), Decimal("35"))
            assert sum((entry.amount for entry in entries), Decimal(0)) == Decimal("1225")
            assert hauldesk_load.carrier_rate_amount == Decimal("1225")

            # Read-only assertions opened an ORM transaction; each source file owns the next one.
            session.rollback()
            second = orchestrator.ingest_all(lambda sync: _ingest(session, sync))
            assert all(result.duplicate for result in second)
            repeated_entries = tuple(
                session.scalars(
                    select(SourceRateEntry)
                    .where(
                        SourceRateEntry.tenant_id == hauldesk_tenant,
                        SourceRateEntry.load_id == hauldesk_load.id,
                        SourceRateEntry.side == FinancialSide.PAY,
                    )
                    .order_by(SourceRateEntry.observed_at, SourceRateEntry.id)
                )
            )
            assert [(entry.external_id, entry.amount) for entry in repeated_entries] == [
                (entry.external_id, entry.amount) for entry in entries
            ]
    finally:
        engine.dispose()


def test_generated_correction_preserves_history_changes_later_estimate_and_rebuilds(
    tmp_path: Path,
) -> None:
    """A late FF correction changes current Day 11 evidence, never an earlier stored decision."""
    assert DATABASE_URL is not None
    data_root = tmp_path / "data"
    write_sync_files(data_root)
    engine = create_engine(DATABASE_URL)
    tenant_id: UUID | None = None
    try:
        with Session(engine) as session:
            tenant_id = _seed_tenants(session, (SourceSystem.FREIGHTFLOW,))[
                SourceSystem.FREIGHTFLOW
            ]
            binding = SourceBinding(
                data_root / "tms_a_freightflow", str(tenant_id), SourceSystem.FREIGHTFLOW
            )
            syncs = FileIngestionOrchestrator((binding,)).discover()
            correction_sync = next(
                sync for sync in syncs if '"totalBuy": 1265' in sync.path.read_text()
            )
            correction_at = correction_sync.sync_at
            day11_sync = next(sync for sync in syncs if sync.sync_at == DAY11_SYNC_AT)
            for sync in syncs:
                if sync.sync_at < correction_at:
                    _ingest(session, sync)

            corrected_load = _load(
                session,
                tenant_id,
                _external_load_id_with_carrier_rate(correction_sync, Decimal("1265")),
            )
            corrected_load_id = corrected_load.id
            corrected_external_id = corrected_load.external_id
            active_version = next(
                version
                for version in _versions(session, tenant_id, corrected_load.id)
                if version.status is LoadStatus.ACTIVE
            )
            active_at = active_version.observed_at
            persisted = DecisionRunService().run(session, tenant_id, corrected_load_id, active_at)
            session.commit()
            decision_before = (
                persisted.run.input_version_id,
                dict(persisted.run.price_estimate),
                dict(persisted.run.confidence),
                dict(persisted.run.evidence_summary),
                tuple(
                    (row.rank, row.adjusted_score, tuple(row.evidence_ids))
                    for row in persisted.recommendations
                ),
            )

            # Model a correction delivered after Day 11. Its source time is still earlier than
            # Day 11, so current evidence may change while the old decision cannot.
            session.rollback()
            _ingest(session, day11_sync)
            target = _load(session, tenant_id, _external_load_id(day11_sync))
            target_id = target.id
            before = HierarchicalRateEstimator().estimate(
                session, tenant_id, target_id, DAY11_SYNC_AT
            )
            assert all(
                item.load_external_id != corrected_external_id for item in before.comparables
            )

            session.rollback()
            _ingest(session, correction_sync)
            corrected_load = _load(
                session,
                tenant_id,
                _external_load_id_with_carrier_rate(correction_sync, Decimal("1265")),
            )
            assert corrected_load.carrier_rate_amount == Decimal("1265")
            after = HierarchicalRateEstimator().estimate(
                session, tenant_id, target_id, DAY11_SYNC_AT
            )
            assert any(item.load_external_id == corrected_external_id for item in after.comparables)
            assert after.point_estimate_usd != before.point_estimate_usd

            session.refresh(persisted.run)
            persisted_rows = tuple(
                session.scalars(
                    select(CarrierRecommendation)
                    .where(
                        CarrierRecommendation.tenant_id == tenant_id,
                        CarrierRecommendation.decision_run_id == persisted.run.id,
                    )
                    .order_by(CarrierRecommendation.rank)
                )
            )
            assert decision_before == (
                persisted.run.input_version_id,
                persisted.run.price_estimate,
                persisted.run.confidence,
                persisted.run.evidence_summary,
                tuple(
                    (row.rank, row.adjusted_score, tuple(row.evidence_ids))
                    for row in persisted_rows
                ),
            )

            report = RateBacktestHarness().run(session, (tenant_id,))
            case = next(
                result for result in report.cases if result.case.load_id == corrected_load_id
            )
            assert case.case.first_active_at == active_at
            assert case.actual_carrier_rate_usd == Decimal("1265")
            assert all(
                comparable.load_external_id != corrected_external_id
                and comparable.load_version_id
                in {
                    version.id
                    for version in session.scalars(
                        select(LoadVersion).where(
                            LoadVersion.tenant_id == tenant_id,
                            LoadVersion.observed_at <= active_at,
                        )
                    )
                }
                for comparable in case.estimate.comparables
            )

            expected = _state_hash(session, tenant_id)
            session.rollback()
            rebuild_current_projections(session, tenant_id)
            assert _state_hash(session, tenant_id) == expected
    finally:
        engine.dispose()
