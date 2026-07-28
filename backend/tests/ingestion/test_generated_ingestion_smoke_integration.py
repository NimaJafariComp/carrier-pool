"""Phase 7 smoke: generated data remains ingestible, idempotent, and rebuildable."""

import hashlib
import json
import os
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from carrier_pool.db.models import (
    Carrier,
    CarrierRecommendation,
    DecisionRun,
    IngestionFile,
    Load,
    LoadVersion,
    SourceRateEntry,
    Stop,
    Tenant,
)
from carrier_pool.db.tenant import set_tenant_context
from carrier_pool.decisioning.backtest import RateBacktestHarness
from carrier_pool.decisioning.carrier_explanations import explain_rankings
from carrier_pool.decisioning.carrier_features import CarrierFeatureService
from carrier_pool.decisioning.carrier_scoring import CarrierHistoricalFitScorer
from carrier_pool.decisioning.decision_runs import DecisionRunService
from carrier_pool.decisioning.pricing import HierarchicalRateEstimator
from carrier_pool.decisioning.ranking_evaluation import (
    RankingBacktestHarness,
    ranking_acceptance_failures,
)
from carrier_pool.domain.types import LoadStatus, SourceSystem
from carrier_pool.generator.manifest import write_scenarios_manifest
from carrier_pool.generator.scheduler import DAY11_SYNC_AT, write_sync_files
from carrier_pool.generator.validator import validate_generated_data
from carrier_pool.geography.comparables import ComparableLoadRepository, LaneTier
from carrier_pool.ingestion.base import SourceFile, TenantContext
from carrier_pool.ingestion.coordinator import (
    BrokerOSIngestionCoordinator,
    FreightFlowIngestionCoordinator,
    HaulDeskIngestionCoordinator,
)
from carrier_pool.ingestion.discovery import FileIngestionOrchestrator, SourceBinding
from carrier_pool.ingestion.rebuild import rebuild_current_projections

DATABASE_URL = os.getenv("DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="DATABASE_URL is required for PostgreSQL tests"
)


def _state_hash(session: Session, tenant_id: UUID) -> str:
    loads = session.execute(
        select(Load).where(Load.tenant_id == tenant_id).order_by(Load.external_id)
    ).scalars()
    state = []
    for load in loads:
        stops = session.execute(
            select(Stop).where(Stop.load_id == load.id).order_by(Stop.sequence)
        ).scalars()
        state.append(
            (
                load.external_id,
                load.status.value,
                str(load.customer_rate_amount),
                str(load.carrier_rate_amount),
                str(load.current_version_id),
                [(stop.sequence, stop.postal_code) for stop in stops],
            )
        )
    return hashlib.sha256(json.dumps(state).encode()).hexdigest()


def test_generated_data_ingests_idempotently_and_rebuilds(tmp_path: Path) -> None:
    assert DATABASE_URL is not None
    data_root = tmp_path / "data"
    assert len(write_sync_files(data_root)) == 123
    write_scenarios_manifest(data_root)
    assert validate_generated_data(data_root).sync_file_count == 123

    engine = create_engine(DATABASE_URL)
    tenant_ids = {source: uuid4() for source in SourceSystem}
    try:
        with Session(engine) as session:
            session.add_all(
                Tenant(
                    id=tenant_id,
                    slug=f"smoke-{source.value.lower()}-{uuid4()}",
                    name=f"Smoke {source.value}",
                    source_system=source,
                )
                for source, tenant_id in tenant_ids.items()
            )
            session.commit()
            coordinators = {
                SourceSystem.FREIGHTFLOW: FreightFlowIngestionCoordinator(),
                SourceSystem.HAULDESK: HaulDeskIngestionCoordinator(),
                SourceSystem.BROKEROS: BrokerOSIngestionCoordinator(),
            }
            directories = {
                SourceSystem.FREIGHTFLOW: "tms_a_freightflow",
                SourceSystem.HAULDESK: "tms_b_hauldesk",
                SourceSystem.BROKEROS: "tms_c_brokeros",
            }
            orchestrator = FileIngestionOrchestrator(
                tuple(
                    SourceBinding(data_root / directories[source], str(tenant_id), source)
                    for source, tenant_id in tenant_ids.items()
                )
            )

            def ingest(sync):
                return coordinators[sync.binding.source_system].ingest(
                    session,
                    SourceFile(sync.path, sync.path.read_bytes()),
                    TenantContext(sync.binding.tenant_id),
                )

            first = orchestrator.ingest_all(ingest)
            assert len(first) == 123
            assert any(result.versions_created > 0 for result in first)
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(IngestionFile)
                    .where(IngestionFile.tenant_id.in_(tenant_ids.values()))
                )
                == 123
            )
            day11_target = session.scalar(
                select(Load).where(
                    Load.tenant_id == tenant_ids[SourceSystem.HAULDESK],
                    Load.external_id == "HD-9001",
                )
            )
            assert day11_target is not None
            assert day11_target.current_version is not None
            day11_evidence = ComparableLoadRepository().retrieve(
                session,
                tenant_ids[SourceSystem.HAULDESK],
                day11_target.id,
                day11_target.current_version.id,
                day11_target.observed_at,
            )
            assert any(item.load_external_id == "HD-2101" for item in day11_evidence)
            assert all(item.tier is not LaneTier.TENANT_ALL_EQUIPMENT for item in day11_evidence)
            hd2101 = session.scalar(
                select(Load).where(
                    Load.tenant_id == tenant_ids[SourceSystem.HAULDESK],
                    Load.external_id == "HD-2101",
                )
            )
            assert hd2101 is not None
            hd2101_entries = session.scalars(
                select(SourceRateEntry).where(
                    SourceRateEntry.tenant_id == hd2101.tenant_id,
                    SourceRateEntry.load_id == hd2101.id,
                )
            ).all()
            assert [entry.amount for entry in hd2101_entries] == [Decimal("1150")]
            day11_estimate = HierarchicalRateEstimator().estimate(
                session,
                tenant_ids[SourceSystem.HAULDESK],
                day11_target.id,
                day11_target.observed_at,
            )
            displayed_hd2101_rate = next(
                comparable.carrier_rate_usd
                for comparable in day11_estimate.comparables
                if comparable.load_external_id == "HD-2101"
            )
            assert displayed_hd2101_rate == Decimal("1150")

            ff_day11_target = session.scalar(
                select(Load).where(
                    Load.tenant_id == tenant_ids[SourceSystem.FREIGHTFLOW],
                    Load.observed_at == DAY11_SYNC_AT,
                )
            )
            assert ff_day11_target is not None
            ff_features = CarrierFeatureService().retrieve(
                session,
                ff_day11_target.tenant_id,
                ff_day11_target.id,
                ff_day11_target.current_version_id,
                ff_day11_target.observed_at,
            )
            by_carrier = {item.carrier_external_id: item for item in ff_features}
            delivery_observed_at = sorted(
                item.last_delivery_observed_at
                for item in by_carrier.values()
                if item.last_delivery_observed_at is not None
            )
            assert delivery_observed_at[-1] > delivery_observed_at[0]
            assert all(item.target_equipment_unknown is False for item in ff_features)
            fixed_ranking = CarrierHistoricalFitScorer().score(ff_features)
            rich_demo_carrier = session.scalar(
                select(Carrier).where(
                    Carrier.tenant_id == ff_day11_target.tenant_id,
                    Carrier.name == "Lone Star Van",
                )
            )
            assert rich_demo_carrier is not None
            rich_demo_fit = next(
                item
                for item in CarrierHistoricalFitScorer(history_mode="identity").score(ff_features)
                if item.carrier_external_id == rich_demo_carrier.external_id
            )
            serving_demo_fit = next(
                item
                for item in fixed_ranking
                if item.carrier_external_id == rich_demo_carrier.external_id
            )
            assert rich_demo_fit.effective_history >= Decimal(9)
            assert rich_demo_fit.adjusted_score >= Decimal(75)
            assert rich_demo_fit.confidence == "HIGH"
            assert serving_demo_fit.adjusted_score >= Decimal(75)
            other_tenant_target = session.scalar(
                select(Load).where(
                    Load.tenant_id == tenant_ids[SourceSystem.BROKEROS],
                    Load.observed_at == DAY11_SYNC_AT,
                )
            )
            assert other_tenant_target is not None
            # Reading another tenant's history cannot change this tenant-local ranking.
            CarrierFeatureService().retrieve(
                session,
                other_tenant_target.tenant_id,
                other_tenant_target.id,
                other_tenant_target.current_version_id,
                other_tenant_target.observed_at,
            )
            assert (
                CarrierHistoricalFitScorer().score(
                    CarrierFeatureService().retrieve(
                        session,
                        ff_day11_target.tenant_id,
                        ff_day11_target.id,
                        ff_day11_target.current_version_id,
                        ff_day11_target.observed_at,
                    )
                )
                == fixed_ranking
            )

            estimator = HierarchicalRateEstimator()
            decision_service = DecisionRunService()
            for source_system, tenant_id in tenant_ids.items():
                target = session.scalar(
                    select(Load).where(
                        Load.tenant_id == tenant_id,
                        Load.source_system == source_system,
                        Load.observed_at == DAY11_SYNC_AT,
                    )
                )
                assert target is not None
                estimate = estimator.estimate(
                    session,
                    target.tenant_id,
                    target.id,
                    target.observed_at,
                )
                assert estimate.point_estimate_usd is not None
                assert estimate.historical_comparison_lower_usd is not None
                assert estimate.historical_comparison_upper_usd is not None
                assert estimate.historical_comparison_lower_usd <= estimate.point_estimate_usd
                assert estimate.point_estimate_usd <= estimate.historical_comparison_upper_usd
                assert estimate.confidence.level is not None
                assert estimate.comparables
                candidate_features = CarrierFeatureService().retrieve(
                    session,
                    target.tenant_id,
                    target.id,
                    target.current_version_id,
                    target.observed_at,
                )
                ranking = CarrierHistoricalFitScorer().score(candidate_features)
                assert ranking
                assert CarrierHistoricalFitScorer().score(candidate_features) == ranking
                explanations = explain_rankings(ranking, candidate_features)
                assert all(item.supporting_load_ids for item in explanations)
                first_decision = decision_service.run(
                    session, target.tenant_id, target.id, target.observed_at
                )
                reused_decision = decision_service.run(
                    session, target.tenant_id, target.id, target.observed_at
                )
                assert first_decision.reused is False
                assert reused_decision.reused is True
                assert reused_decision.run.id == first_decision.run.id
                assert first_decision.run.input_version_id == target.current_version_id
                assert first_decision.run.price_estimate["point_estimate_usd"] is not None
                assert first_decision.recommendations
                assert all(row.evidence_ids for row in first_decision.recommendations)
                assert all(row.explanation_reason_codes for row in first_decision.recommendations)
                assert (
                    session.scalar(
                        select(func.count())
                        .select_from(DecisionRun)
                        .where(
                            DecisionRun.tenant_id == target.tenant_id,
                            DecisionRun.load_id == target.id,
                        )
                    )
                    == 1
                )
                assert session.scalar(
                    select(func.count())
                    .select_from(CarrierRecommendation)
                    .where(CarrierRecommendation.decision_run_id == first_decision.run.id)
                ) == len(first_decision.recommendations)

            set_tenant_context(session, tenant_ids[SourceSystem.FREIGHTFLOW])
            inactive_load = session.scalar(
                select(Load).where(
                    Load.tenant_id == tenant_ids[SourceSystem.FREIGHTFLOW],
                    Load.status != LoadStatus.ACTIVE,
                )
            )
            assert inactive_load is not None
            with pytest.raises(ValueError, match="ACTIVE"):
                decision_service.run(
                    session,
                    inactive_load.tenant_id,
                    inactive_load.id,
                    inactive_load.observed_at,
                )
            correction_load = inactive_load
            active_version = session.scalar(
                select(LoadVersion)
                .where(
                    LoadVersion.load_id == correction_load.id,
                    LoadVersion.status == LoadStatus.ACTIVE,
                )
                .order_by(LoadVersion.observed_at)
            )
            assert active_version is not None
            later_version_count = session.scalar(
                select(func.count())
                .select_from(LoadVersion)
                .where(
                    LoadVersion.load_id == correction_load.id,
                    LoadVersion.observed_at > active_version.observed_at,
                )
            )
            assert later_version_count is not None and later_version_count > 0
            historical_features = CarrierFeatureService().retrieve(
                session,
                correction_load.tenant_id,
                correction_load.id,
                active_version.id,
                active_version.observed_at,
            )
            historical_ranking = CarrierHistoricalFitScorer().score(historical_features)
            feature_version_ids = {
                UUID(version_id)
                for feature in historical_features
                for version_id in feature.raw_evidence_ids
            }
            feature_observed_at = {
                version_id: observed_at
                for version_id, observed_at in session.execute(
                    select(LoadVersion.id, LoadVersion.observed_at).where(
                        LoadVersion.id.in_(feature_version_ids)
                    )
                ).tuples()
            }
            assert all(
                observed_at <= active_version.observed_at
                for observed_at in feature_observed_at.values()
            )
            # The already-ingested later correction exists, but cannot change this cutoff ranking.
            assert (
                CarrierHistoricalFitScorer().score(
                    CarrierFeatureService().retrieve(
                        session,
                        correction_load.tenant_id,
                        correction_load.id,
                        active_version.id,
                        active_version.observed_at,
                    )
                )
                == historical_ranking
            )
            historical_decision = decision_service.run(
                session,
                correction_load.tenant_id,
                correction_load.id,
                active_version.observed_at,
            )
            historical_snapshot = (
                historical_decision.run.input_version_id,
                dict(historical_decision.run.price_estimate),
                tuple(row.adjusted_score for row in historical_decision.recommendations),
            )
            session.refresh(historical_decision.run)
            assert historical_snapshot == (
                historical_decision.run.input_version_id,
                historical_decision.run.price_estimate,
                tuple(row.adjusted_score for row in historical_decision.recommendations),
            )

            report = RateBacktestHarness().run(session, tuple(tenant_ids.values()))
            assert report.scored_case_count > 0
            assert report.metrics.mae_usd is not None
            assert report.metrics.median_absolute_error_usd is not None
            assert report.metrics.wape is not None
            for tenant_id in tenant_ids.values():
                assert (
                    sum(
                        result.case.tenant_id == tenant_id and result.absolute_error_usd is not None
                        for result in report.cases
                    )
                    >= 5
                )
            assert report.by_history_depth["RICH"].case_count >= 1
            assert report.by_history_depth["SPARSE"].case_count >= 1
            assert set(report.baseline_models) == {
                "tenant_wide_median",
                "equipment_distance_band_median",
                "unshrunk_nearest_lane_weighted_median",
                "robust_huber_regression",
                "quantile_regression",
            }
            assert (
                report.baseline_models["tenant_wide_median"].case_count == report.scored_case_count
            )
            assert all(
                model.case_count <= report.case_count for model in report.baseline_models.values()
            )
            assert set(report.same_population_comparisons) == set(report.baseline_models)
            for name, comparison in report.same_population_comparisons.items():
                assert comparison.case_count <= report.baseline_models[name].case_count
                if comparison.case_count:
                    assert comparison.production_metrics.mae_usd is not None
                    assert comparison.baseline_metrics.mae_usd is not None
                    assert comparison.by_tier
                    assert comparison.by_history_depth
            ranking_report = RankingBacktestHarness().run(session, tuple(tenant_ids.values()))
            assert (
                ranking_report.with_deadhead.case_count
                == ranking_report.without_deadhead.case_count
            )
            assert ranking_report.with_deadhead.case_count > 0
            assert ranking_report.with_deadhead.scored_case_count > 0
            assert ranking_report.with_deadhead.by_history_depth["RICH"].case_count >= 1
            assert ranking_report.with_deadhead.by_history_depth["SPARSE"].case_count >= 1
            identity_ranking_report = RankingBacktestHarness(
                scorer=CarrierHistoricalFitScorer(history_mode="identity")
            ).run(session, tuple(tenant_ids.values()))
            assert identity_ranking_report.with_deadhead.by_history_depth["RICH"].case_count >= 1
            assert identity_ranking_report.with_deadhead.by_history_depth["SPARSE"].case_count >= 1
            assert ranking_report.all_candidates_with_deadhead.case_count == (
                ranking_report.with_deadhead.case_count
            )
            assert sum(ranking_report.with_deadhead.no_rank_reason_counts.values()) == (
                ranking_report.with_deadhead.no_rank_count
            )
            assert set(ranking_report.component_ablations) == {
                "without_lane",
                "without_equipment",
                "without_recency",
            }
            assert all(
                ablation.case_count == ranking_report.with_deadhead.case_count
                for ablation in ranking_report.component_ablations.values()
            )
            assert ranking_report.weight_tuning_eligible is False
            assert not ranking_acceptance_failures(ranking_report)
            for model in report.baseline_models.values():
                if model.case_count:
                    assert model.metrics.mae_usd is not None
                    assert model.metrics.median_absolute_error_usd is not None
                    assert model.metrics.wape is not None
            evidence_version_ids = {
                comparable.load_version_id
                for result in report.cases
                for comparable in result.estimate.comparables
            }
            evidence_observed_at: dict[UUID, datetime] = {}
            for version_id, observed_at in session.execute(
                select(LoadVersion.id, LoadVersion.observed_at).where(
                    LoadVersion.id.in_(evidence_version_ids)
                )
            ).tuples():
                evidence_observed_at[version_id] = observed_at
            for result in report.cases:
                assert all(
                    evidence_observed_at[comparable.load_version_id] <= result.case.first_active_at
                    for comparable in result.estimate.comparables
                )
            before = {
                tenant_id: _state_hash(session, tenant_id) for tenant_id in tenant_ids.values()
            }
            session.rollback()

            second = orchestrator.ingest_all(ingest)
            assert len(second) == 123
            assert all(result.duplicate for result in second)
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(IngestionFile)
                    .where(IngestionFile.tenant_id.in_(tenant_ids.values()))
                )
                == 123
            )
            session.rollback()

            for tenant_id, expected in before.items():
                rebuild_current_projections(session, tenant_id)
                assert _state_hash(session, tenant_id) == expected
                session.rollback()
    finally:
        engine.dispose()
