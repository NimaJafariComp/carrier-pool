"""Leakage-safe rolling backtests for hierarchical carrier-rate estimation."""

import csv
import json
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from carrier_pool.db.models import Load, LoadVersion, SourceRateEntry, Tenant
from carrier_pool.db.tenant import set_tenant_context
from carrier_pool.decisioning.baselines import (
    BaselineObservation,
    BaselinePrediction,
    BaselineTarget,
    equipment_distance_band_median,
    quantile_regression,
    robust_huber_regression,
    tenant_wide_median,
    unshrunk_nearest_lane_weighted_median,
)
from carrier_pool.decisioning.pricing import (
    MODEL_VERSION,
    HierarchicalRateEstimator,
    RateEstimate,
    comparable_weight,
)
from carrier_pool.domain.types import EquipmentType, FinancialSide, LoadStatus, SourceSystem
from carrier_pool.geography.comparables import ComparableLoadRepository


@dataclass(frozen=True, slots=True)
class HistoricalRateCase:
    """One target whose input cutoff and later corrected label are kept separate."""

    tenant_id: UUID
    load_id: UUID
    first_active_at: datetime
    final_carrier_rate_usd: Decimal
    equipment: EquipmentType | None


@dataclass(frozen=True, slots=True)
class ErrorMetrics:
    """Aggregate error and historical-range coverage for scored cases."""

    mae_usd: Decimal | None
    median_absolute_error_usd: Decimal | None
    wape: Decimal | None
    range_coverage: Decimal | None


@dataclass(frozen=True, slots=True)
class BacktestCaseResult:
    """One reproducible historical prediction and its eventual outcome."""

    case: HistoricalRateCase
    estimate: RateEstimate
    absolute_error_usd: Decimal | None
    range_contains_actual: bool | None

    @property
    def actual_carrier_rate_usd(self) -> Decimal:
        """Expose eventual corrected label without making it an estimator input."""
        return self.case.final_carrier_rate_usd


@dataclass(frozen=True, slots=True)
class Breakdown:
    """Metrics for one tier, equipment type, or evidence-depth segment."""

    case_count: int
    metrics: ErrorMetrics


BASELINE_MODEL_NAMES = (
    "tenant_wide_median",
    "equipment_distance_band_median",
    "unshrunk_nearest_lane_weighted_median",
    "robust_huber_regression",
    "quantile_regression",
)
MIN_SAME_POPULATION_CASES_FOR_PROMOTION = 30
MIN_RELATIVE_MAE_IMPROVEMENT_FOR_PROMOTION = Decimal("0.05")


def _empty_baseline_models() -> dict[str, "BaselineModelReport"]:
    return {}


@dataclass(frozen=True, slots=True)
class BaselineModelReport:
    """Metrics for one baseline on its explicitly displayed eligible population."""

    case_count: int
    metrics: ErrorMetrics


@dataclass(frozen=True, slots=True)
class BaselineCaseResult:
    """One baseline outcome tied to its exact historical target case."""

    case: HistoricalRateCase
    prediction: BaselinePrediction


@dataclass(frozen=True, slots=True)
class ComparisonBreakdown:
    """Production and challenger metrics over one identical case population."""

    case_count: int
    production_metrics: ErrorMetrics
    baseline_metrics: ErrorMetrics


@dataclass(frozen=True, slots=True)
class SamePopulationComparison:
    """A challenger comparison that never mixes denominator populations."""

    case_count: int
    production_metrics: ErrorMetrics
    baseline_metrics: ErrorMetrics
    by_tier: dict[str, ComparisonBreakdown]
    by_history_depth: dict[str, ComparisonBreakdown]


@dataclass(frozen=True, slots=True)
class BacktestReport:
    """Backtest summary plus individual auditable case outcomes."""

    case_count: int
    scored_case_count: int
    metrics: ErrorMetrics
    cases: tuple[BacktestCaseResult, ...]
    by_tier: dict[str, Breakdown]
    by_equipment: dict[str, Breakdown]
    by_history_depth: dict[str, Breakdown]
    baseline_models: dict[str, BaselineModelReport] = field(default_factory=_empty_baseline_models)
    same_population_comparisons: dict[str, SamePopulationComparison] = field(
        default_factory=lambda: dict[str, SamePopulationComparison]()
    )


EstimatorCall = Callable[[UUID, UUID, datetime], RateEstimate]


class RateBacktestHarness:
    """Discover historical targets and score each using only its first ACTIVE cutoff."""

    def __init__(self, estimator: HierarchicalRateEstimator | None = None) -> None:
        self._estimator = estimator or HierarchicalRateEstimator()
        self._comparable_repository = ComparableLoadRepository()

    def run(self, session: Session, tenant_ids: Sequence[UUID] | None = None) -> BacktestReport:
        """Run rolling predictions against eventual corrected carrier-pay totals."""
        selected_tenants = (
            tuple(tenant_ids)
            if tenant_ids is not None
            else tuple(session.scalars(select(Tenant.id).order_by(Tenant.id)).all())
        )
        cases: list[HistoricalRateCase] = []
        for tenant_id in selected_tenants:
            cases.extend(self._cases_for_tenant(session, tenant_id))
        ordered_cases = tuple(sorted(cases, key=lambda case: (case.first_active_at, case.load_id)))
        report = self.evaluate_cases(
            ordered_cases,
            lambda tenant_id, load_id, as_of: self._estimator.estimate(
                session, tenant_id, load_id, as_of
            ),
        )
        baseline_cases = self._baseline_cases(session, ordered_cases)
        return replace(
            report,
            baseline_models=_baseline_reports(baseline_cases),
            same_population_comparisons=_same_population_comparisons(report.cases, baseline_cases),
        )

    def _cases_for_tenant(
        self, session: Session, tenant_id: UUID
    ) -> tuple[HistoricalRateCase, ...]:
        set_tenant_context(session, tenant_id)
        loads = session.scalars(select(Load).where(Load.tenant_id == tenant_id)).all()
        cases: list[HistoricalRateCase] = []
        for load in loads:
            versions = tuple(
                session.scalars(
                    select(LoadVersion)
                    .where(LoadVersion.tenant_id == tenant_id, LoadVersion.load_id == load.id)
                    .order_by(LoadVersion.observed_at, LoadVersion.id)
                ).all()
            )
            first_active = next(
                (version for version in versions if version.status is LoadStatus.ACTIVE), None
            )
            if first_active is None or not any(
                version.status is LoadStatus.COMPLETED for version in versions
            ):
                continue
            final_rate = _final_carrier_rate(session, tenant_id, load, versions)
            if final_rate is None:
                continue
            cases.append(
                HistoricalRateCase(
                    tenant_id=tenant_id,
                    load_id=load.id,
                    first_active_at=first_active.observed_at,
                    final_carrier_rate_usd=final_rate,
                    equipment=first_active.equipment,
                )
            )
        return tuple(cases)

    def _baseline_cases(
        self, session: Session, cases: Sequence[HistoricalRateCase]
    ) -> dict[str, tuple[BaselineCaseResult, ...]]:
        """Run every baseline against tenant-local observations known at each cutoff."""
        outcomes: dict[str, list[BaselineCaseResult]] = {name: [] for name in BASELINE_MODEL_NAMES}
        for case in cases:
            target, observations, nearest_lane = self._baseline_inputs(session, case)
            predictions = (
                tenant_wide_median(observations),
                equipment_distance_band_median(target, observations),
                unshrunk_nearest_lane_weighted_median(nearest_lane),
                robust_huber_regression(target, observations),
                quantile_regression(target, observations),
            )
            for prediction in predictions:
                if prediction is not None:
                    outcomes[prediction.model_name].append(BaselineCaseResult(case, prediction))
        return {name: tuple(values) for name, values in outcomes.items()}

    def _baseline_inputs(
        self, session: Session, case: HistoricalRateCase
    ) -> tuple[BaselineTarget, tuple[BaselineObservation, ...], tuple[BaselineObservation, ...]]:
        """Read only immutable, completed same-tenant observations available at a cutoff."""
        set_tenant_context(session, case.tenant_id)
        target = session.scalar(
            select(LoadVersion)
            .where(
                LoadVersion.tenant_id == case.tenant_id,
                LoadVersion.load_id == case.load_id,
                LoadVersion.observed_at <= case.first_active_at,
            )
            .order_by(LoadVersion.observed_at.desc(), LoadVersion.id.desc())
        )
        if target is None:
            raise LookupError("backtest target is unavailable at its cutoff")
        rows = session.execute(
            select(LoadVersion, Load.source_system)
            .join(Load, Load.id == LoadVersion.load_id)
            .where(
                LoadVersion.tenant_id == case.tenant_id,
                LoadVersion.load_id != case.load_id,
                LoadVersion.observed_at <= case.first_active_at,
            )
        ).all()
        latest: dict[UUID, tuple[LoadVersion, SourceSystem]] = {}
        for version, source_system in rows:
            prior = latest.get(version.load_id)
            if prior is None or (version.observed_at, version.id) > (
                prior[0].observed_at,
                prior[0].id,
            ):
                latest[version.load_id] = (version, source_system)
        completed = tuple(
            (version, source_system)
            for version, source_system in latest.values()
            if version.status is LoadStatus.COMPLETED
        )
        rates = _rates_at_cutoff(session, case.tenant_id, completed, case.first_active_at)
        observations_by_version = {
            version.id: BaselineObservation(rate, version.equipment, version.distance_miles)
            for version, _source_system in completed
            if (rate := rates.get(version.id)) is not None
        }
        nearest_lane = tuple(
            BaselineObservation(
                observations_by_version[evidence.version_id].carrier_rate_usd,
                observations_by_version[evidence.version_id].equipment,
                observations_by_version[evidence.version_id].distance_miles,
                comparable_weight(evidence),
            )
            for evidence in self._comparable_repository.retrieve(
                session,
                case.tenant_id,
                case.load_id,
                target.id,
                case.first_active_at,
            )
            if evidence.version_id in observations_by_version
        )
        return (
            BaselineTarget(target.equipment, target.distance_miles),
            tuple(observations_by_version.values()),
            nearest_lane,
        )

    @staticmethod
    def evaluate_cases(
        cases: Iterable[HistoricalRateCase], estimate_at_cutoff: EstimatorCall
    ) -> BacktestReport:
        """Score explicit cases; labels never enter the estimator callback."""
        results: list[BacktestCaseResult] = []
        for case in cases:
            estimate = estimate_at_cutoff(case.tenant_id, case.load_id, case.first_active_at)
            if estimate.point_estimate_usd is None:
                results.append(BacktestCaseResult(case, estimate, None, None))
                continue
            error = abs(estimate.point_estimate_usd - case.final_carrier_rate_usd)
            in_range = (
                estimate.historical_comparison_lower_usd is not None
                and estimate.historical_comparison_upper_usd is not None
                and estimate.historical_comparison_lower_usd
                <= case.final_carrier_rate_usd
                <= estimate.historical_comparison_upper_usd
            )
            results.append(BacktestCaseResult(case, estimate, error, in_range))
        return _report(tuple(results))


def write_backtest_artifacts(report: BacktestReport, artifacts_dir: Path) -> tuple[Path, Path]:
    """Write deterministic JSON metrics and CSV case artifacts."""
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    metrics_path = artifacts_dir / "backtest_metrics.json"
    cases_path = artifacts_dir / "backtest_cases.csv"
    metrics_path.write_text(json.dumps(_report_json(report), indent=2, sort_keys=True) + "\n")
    with cases_path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=_case_fieldnames(), lineterminator="\n")
        writer.writeheader()
        for case in report.cases:
            writer.writerow(_case_row(case))
    return metrics_path, cases_path


def _final_carrier_rate(
    session: Session, tenant_id: UUID, load: Load, versions: Sequence[LoadVersion]
) -> Decimal | None:
    if load.source_system is not SourceSystem.HAULDESK:
        for version in reversed(versions):
            if version.currency == "USD" and version.carrier_rate_amount is not None:
                return version.carrier_rate_amount if version.carrier_rate_amount >= 0 else None
        return None
    entries = session.scalars(
        select(SourceRateEntry).where(
            SourceRateEntry.tenant_id == tenant_id,
            SourceRateEntry.load_id == load.id,
            SourceRateEntry.source_system == SourceSystem.HAULDESK,
            SourceRateEntry.side == FinancialSide.PAY,
            SourceRateEntry.currency == "USD",
        )
    ).all()
    if not entries:
        return None
    total = sum((entry.amount for entry in entries), Decimal(0))
    return total if total >= 0 else None


def _rates_at_cutoff(
    session: Session,
    tenant_id: UUID,
    completed: Sequence[tuple[LoadVersion, SourceSystem]],
    as_of: datetime,
) -> dict[UUID, Decimal]:
    """Return source-aware carrier totals using only facts known at ``as_of``."""
    result: dict[UUID, Decimal] = {}
    hauldesk_versions: dict[UUID, UUID] = {}
    for version, source_system in completed:
        if source_system is SourceSystem.HAULDESK:
            hauldesk_versions[version.load_id] = version.id
        elif version.currency == "USD" and version.carrier_rate_amount is not None:
            if version.carrier_rate_amount >= 0:
                result[version.id] = version.carrier_rate_amount
    if not hauldesk_versions:
        return result
    entries = session.execute(
        select(SourceRateEntry.load_id, SourceRateEntry.amount).where(
            SourceRateEntry.tenant_id == tenant_id,
            SourceRateEntry.source_system == SourceSystem.HAULDESK,
            SourceRateEntry.side == FinancialSide.PAY,
            SourceRateEntry.currency == "USD",
            SourceRateEntry.load_id.in_(hauldesk_versions),
            SourceRateEntry.observed_at <= as_of,
        )
    ).all()
    totals: dict[UUID, Decimal] = {}
    for load_id, amount in entries:
        totals[load_id] = totals.get(load_id, Decimal(0)) + amount
    for load_id, total in totals.items():
        if total >= 0:
            result[hauldesk_versions[load_id]] = total
    return result


def _report(results: tuple[BacktestCaseResult, ...]) -> BacktestReport:
    scored = tuple(result for result in results if result.absolute_error_usd is not None)
    return BacktestReport(
        case_count=len(results),
        scored_case_count=len(scored),
        metrics=_metrics(scored),
        cases=results,
        by_tier=_breakdown(
            scored,
            lambda result: (
                "NO_EVIDENCE"
                if result.estimate.local_tier is None
                else result.estimate.local_tier.value
            ),
        ),
        by_equipment=_breakdown(
            scored, lambda result: (result.case.equipment or EquipmentType.UNKNOWN).value
        ),
        by_history_depth=_breakdown(
            scored,
            lambda result: "RICH" if result.estimate.raw_evidence_count >= 4 else "SPARSE",
        ),
        baseline_models={
            name: BaselineModelReport(0, ErrorMetrics(None, None, None, None))
            for name in BASELINE_MODEL_NAMES
        },
    )


def _metrics(results: Sequence[BacktestCaseResult]) -> ErrorMetrics:
    if not results:
        return ErrorMetrics(None, None, None, None)
    errors = tuple(
        result.absolute_error_usd for result in results if result.absolute_error_usd is not None
    )
    actuals = tuple(result.case.final_carrier_rate_usd for result in results)
    range_flags = tuple(
        result.range_contains_actual
        for result in results
        if result.range_contains_actual is not None
    )
    actual_total = sum(actuals, Decimal(0))
    return ErrorMetrics(
        mae_usd=sum(errors, Decimal(0)) / Decimal(len(errors)),
        median_absolute_error_usd=_median(errors),
        wape=(sum(errors, Decimal(0)) / actual_total) if actual_total else None,
        range_coverage=(Decimal(sum(range_flags)) / Decimal(len(range_flags)))
        if range_flags
        else None,
    )


def _baseline_metrics(outcomes: Sequence[BaselineCaseResult]) -> ErrorMetrics:
    if not outcomes:
        return ErrorMetrics(None, None, None, None)
    errors = tuple(
        abs(outcome.prediction.point_estimate_usd - outcome.case.final_carrier_rate_usd)
        for outcome in outcomes
    )
    actual_total = sum((outcome.case.final_carrier_rate_usd for outcome in outcomes), Decimal(0))
    coverage = tuple(
        outcome.prediction.lower_usd
        <= outcome.case.final_carrier_rate_usd
        <= outcome.prediction.upper_usd
        for outcome in outcomes
        if outcome.prediction.lower_usd is not None and outcome.prediction.upper_usd is not None
    )
    return ErrorMetrics(
        mae_usd=sum(errors, Decimal(0)) / Decimal(len(errors)),
        median_absolute_error_usd=_median(errors),
        wape=(sum(errors, Decimal(0)) / actual_total) if actual_total else None,
        range_coverage=(Decimal(sum(coverage)) / Decimal(len(coverage))) if coverage else None,
    )


def _baseline_reports(
    outcomes: dict[str, tuple[BaselineCaseResult, ...]],
) -> dict[str, BaselineModelReport]:
    return {
        name: BaselineModelReport(len(values), _baseline_metrics(values))
        for name, values in outcomes.items()
    }


def _same_population_comparisons(
    production_cases: Sequence[BacktestCaseResult],
    baseline_cases: dict[str, tuple[BaselineCaseResult, ...]],
) -> dict[str, SamePopulationComparison]:
    """Compare every baseline only where it and production both made a prediction."""
    production_by_load = {result.case.load_id: result for result in production_cases}
    comparisons: dict[str, SamePopulationComparison] = {}
    for name, outcomes in baseline_cases.items():
        pairs = tuple(
            (production, baseline)
            for baseline in outcomes
            if (production := production_by_load.get(baseline.case.load_id)) is not None
            and production.absolute_error_usd is not None
        )
        comparisons[name] = SamePopulationComparison(
            len(pairs),
            _metrics(tuple(production for production, _baseline in pairs)),
            _baseline_metrics(tuple(baseline for _production, baseline in pairs)),
            _comparison_breakdown(
                pairs,
                lambda production: (
                    "NO_TIER"
                    if production.estimate.local_tier is None
                    else production.estimate.local_tier.value
                ),
            ),
            _comparison_breakdown(
                pairs,
                lambda production: (
                    "RICH" if production.estimate.raw_evidence_count >= 4 else "SPARSE"
                ),
            ),
        )
    return comparisons


def _comparison_breakdown(
    pairs: Sequence[tuple[BacktestCaseResult, BaselineCaseResult]],
    key: Callable[[BacktestCaseResult], str],
) -> dict[str, ComparisonBreakdown]:
    groups: dict[str, list[tuple[BacktestCaseResult, BaselineCaseResult]]] = defaultdict(list)
    for production, baseline in pairs:
        groups[key(production)].append((production, baseline))
    return {
        name: ComparisonBreakdown(
            len(group),
            _metrics(tuple(production for production, _baseline in group)),
            _baseline_metrics(tuple(baseline for _production, baseline in group)),
        )
        for name, group in sorted(groups.items())
    }


def _median(values: Sequence[Decimal]) -> Decimal:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / Decimal(2)


def _breakdown(
    results: Sequence[BacktestCaseResult], key: Callable[[BacktestCaseResult], str]
) -> dict[str, Breakdown]:
    groups: dict[str, list[BacktestCaseResult]] = defaultdict(list)
    for result in results:
        groups[key(result)].append(result)
    return {
        name: Breakdown(case_count=len(group), metrics=_metrics(group))
        for name, group in sorted(groups.items())
    }


def _report_json(report: BacktestReport) -> dict[str, object]:
    return {
        "case_count": report.case_count,
        "scored_case_count": report.scored_case_count,
        "metrics": _metrics_json(report.metrics),
        "by_tier": _breakdown_json(report.by_tier),
        "by_equipment": _breakdown_json(report.by_equipment),
        "by_history_depth": _breakdown_json(report.by_history_depth),
        "calibration": {
            "is_prediction_interval": False,
            "minimum_cases_per_group": 20,
            "confidence_levels": _breakdown_json(
                _breakdown(
                    tuple(
                        result for result in report.cases if result.absolute_error_usd is not None
                    ),
                    lambda result: result.estimate.confidence.level.value,
                )
            ),
            "range_coverage_by_tier": _breakdown_json(report.by_tier),
            "warning": (
                "Confidence is evidence quality and this range is historical comparison "
                "only; neither is calibrated with this deterministic demo dataset."
            ),
        },
        "model_selection_policy": {
            "promotion_eligible": False,
            "minimum_same_population_case_count": MIN_SAME_POPULATION_CASES_FOR_PROMOTION,
            "minimum_relative_mae_improvement": _decimal_text(
                MIN_RELATIVE_MAE_IMPROVEMENT_FOR_PROMOTION
            ),
            "requires": [
                "lower MAE and median absolute error on the same cases",
                "no worse sparse-history WAPE",
                "no worse historical-range coverage",
                "independent operational outcome data",
            ],
            "blocker": "Deterministic demo outcomes are not independent operational data.",
        },
        "models": {
            MODEL_VERSION: {
                "case_count": report.scored_case_count,
                "metrics": _metrics_json(report.metrics),
            },
            **{
                name: {
                    "case_count": value.case_count,
                    "metrics": _metrics_json(value.metrics),
                }
                for name, value in report.baseline_models.items()
            },
        },
        "same_population_comparisons": {
            name: {
                "case_count": value.case_count,
                "production_metrics": _metrics_json(value.production_metrics),
                "baseline_metrics": _metrics_json(value.baseline_metrics),
                "by_tier": _comparison_breakdown_json(value.by_tier),
                "by_history_depth": _comparison_breakdown_json(value.by_history_depth),
            }
            for name, value in report.same_population_comparisons.items()
        },
    }


def _breakdown_json(breakdown: dict[str, Breakdown]) -> dict[str, object]:
    return {
        name: {"case_count": value.case_count, "metrics": _metrics_json(value.metrics)}
        for name, value in breakdown.items()
    }


def _comparison_breakdown_json(
    breakdown: dict[str, ComparisonBreakdown],
) -> dict[str, object]:
    return {
        name: {
            "case_count": value.case_count,
            "production_metrics": _metrics_json(value.production_metrics),
            "baseline_metrics": _metrics_json(value.baseline_metrics),
        }
        for name, value in breakdown.items()
    }


def _metrics_json(metrics: ErrorMetrics) -> dict[str, str | None]:
    return {
        "mae_usd": _decimal_text(metrics.mae_usd),
        "median_absolute_error_usd": _decimal_text(metrics.median_absolute_error_usd),
        "wape": _decimal_text(metrics.wape),
        "range_coverage": _decimal_text(metrics.range_coverage),
    }


def _case_fieldnames() -> tuple[str, ...]:
    return (
        "tenant_id",
        "load_id",
        "first_active_at",
        "equipment",
        "actual_carrier_rate_usd",
        "point_estimate_usd",
        "absolute_error_usd",
        "range_lower_usd",
        "range_upper_usd",
        "range_contains_actual",
        "tier",
        "raw_evidence_count",
        "effective_evidence_count",
        "warnings",
    )


def _case_row(result: BacktestCaseResult) -> dict[str, str]:
    estimate = result.estimate
    return {
        "tenant_id": str(result.case.tenant_id),
        "load_id": str(result.case.load_id),
        "first_active_at": result.case.first_active_at.isoformat(),
        "equipment": (result.case.equipment or EquipmentType.UNKNOWN).value,
        "actual_carrier_rate_usd": str(result.case.final_carrier_rate_usd),
        "point_estimate_usd": _decimal_text(estimate.point_estimate_usd) or "",
        "absolute_error_usd": _decimal_text(result.absolute_error_usd) or "",
        "range_lower_usd": _decimal_text(estimate.historical_comparison_lower_usd) or "",
        "range_upper_usd": _decimal_text(estimate.historical_comparison_upper_usd) or "",
        "range_contains_actual": ""
        if result.range_contains_actual is None
        else str(result.range_contains_actual).lower(),
        "tier": "" if estimate.local_tier is None else estimate.local_tier.value,
        "raw_evidence_count": str(estimate.raw_evidence_count),
        "effective_evidence_count": str(estimate.effective_evidence_count),
        "warnings": ";".join(estimate.warnings),
    }


def _decimal_text(value: Decimal | None) -> str | None:
    return None if value is None else str(value)
