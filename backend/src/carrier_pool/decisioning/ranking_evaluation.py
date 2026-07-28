"""Leakage-safe evaluation for historical-fit carrier rankings.

The eventually booked carrier is an outcome label only.  It is a weak behavioral
proxy, not evidence of availability, acceptance probability, or carrier quality.
"""

import json
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from carrier_pool.db.models import Carrier, Load, LoadVersion, Tenant
from carrier_pool.db.tenant import set_tenant_context
from carrier_pool.decisioning.carrier_features import CarrierFeatureService, CarrierFeatureSet
from carrier_pool.decisioning.carrier_scoring import (
    CarrierHistoricalFit,
    CarrierHistoricalFitScorer,
    ScoringWeights,
)
from carrier_pool.domain.types import LoadStatus
from carrier_pool.geography.comparables import LaneTier

WEAK_PROXY_CAVEAT = (
    "The eventually booked carrier is only a weak behavioral proxy; it does not prove "
    "availability, acceptance probability, reliability, or dispatch quality."
)
WEIGHT_TUNING_BLOCKERS = (
    "Booking labels are a weak proxy, not acceptance or dispatch-quality outcomes.",
    "The deterministic demo dataset is not an independent operational holdout.",
)
REQUIRED_COVERAGE_TAGS = frozenset(
    {
        "RICH",
        "SPARSE",
        "NEAR_EXACT",
        "BROADER_LANE",
        "DISTANCE_EQUIPMENT",
        "LIMITED_CANDIDATE",
        "CLOSE_SCORE_TIE",
    }
)
MIN_CASE_COUNT = 24
MIN_SCORED_CASE_COUNT = 14
MIN_SCORED_CASES_PER_SOURCE = 3
RICH_EFFECTIVE_HISTORY = Decimal(3)
_COMPONENT_ABLATIONS = ("lane", "equipment", "recency")


@dataclass(frozen=True, slots=True)
class RankingEvaluationCase:
    """One cutoff-safe ranking and the later carrier booking label."""

    tenant_id: str
    eventually_booked_carrier_id: str
    ranked_carrier_ids: tuple[str, ...]
    history_depth: str
    top_score_margin: Decimal | None = None
    top_fit_is_tied: bool = False
    limited_candidate_count: int = 0
    supported_candidate_count: int = 0
    coverage_tags: tuple[str, ...] = ()
    source_system: str = ""
    supported_ranked_carrier_ids: tuple[str, ...] | None = None
    all_candidate_no_rank_reason: str | None = None
    supported_no_rank_reason: str | None = None
    top_supported_confidence: str | None = None


@dataclass(frozen=True, slots=True)
class RankingMetrics:
    """Recall and reciprocal-rank measures for a single evaluation population."""

    case_count: int
    scored_case_count: int
    top_1_recall: str | None
    top_3_recall: str | None
    mean_reciprocal_rank: str | None
    no_rank_count: int
    by_history_depth: dict[str, "RankingMetrics"] = field(
        default_factory=lambda: dict[str, RankingMetrics]()
    )
    mean_top_score_margin: str | None = None
    top_fit_tie_rate: str | None = None
    limited_candidate_count: int = 0
    supported_candidate_count: int = 0
    top_fit_tie_count: int = 0
    clear_top_count: int = 0
    coverage_counts: dict[str, int] = field(default_factory=lambda: dict[str, int]())
    scored_case_count_by_source: dict[str, int] = field(default_factory=lambda: dict[str, int]())
    no_rank_reason_counts: dict[str, int] = field(default_factory=lambda: dict[str, int]())
    top_supported_confidence_counts: dict[str, int] = field(
        default_factory=lambda: dict[str, int]()
    )


@dataclass(frozen=True, slots=True)
class RankingEvaluationReport:
    """Supported-only primary metrics plus same-case ablations and audit context."""

    with_deadhead: RankingMetrics
    without_deadhead: RankingMetrics
    all_candidates_with_deadhead: RankingMetrics
    all_candidates_without_deadhead: RankingMetrics
    component_ablations: dict[str, RankingMetrics] = field(
        default_factory=lambda: dict[str, RankingMetrics]()
    )
    all_candidate_component_ablations: dict[str, RankingMetrics] = field(
        default_factory=lambda: dict[str, RankingMetrics]()
    )
    caveat: str = WEAK_PROXY_CAVEAT
    weight_tuning_eligible: bool = False
    weight_tuning_blockers: tuple[str, ...] = WEIGHT_TUNING_BLOCKERS


def ranking_acceptance_failures(report: RankingEvaluationReport) -> tuple[str, ...]:
    """Return coverage/separation gate failures; proxy recall is intentionally excluded."""
    metrics = report.with_deadhead
    failures: list[str] = []
    if metrics.case_count < MIN_CASE_COUNT:
        failures.append(f"case_count must be at least {MIN_CASE_COUNT}")
    if metrics.scored_case_count < MIN_SCORED_CASE_COUNT:
        failures.append(f"scored_case_count must be at least {MIN_SCORED_CASE_COUNT}")
    low_sources = sorted(
        source
        for source in ("FREIGHTFLOW", "HAULDESK", "BROKEROS")
        if metrics.scored_case_count_by_source.get(source, 0) < MIN_SCORED_CASES_PER_SOURCE
    )
    if low_sources:
        failures.append(
            "requires at least "
            f"{MIN_SCORED_CASES_PER_SOURCE} scored cases per source: {', '.join(low_sources)}"
        )
    missing = sorted(tag for tag in REQUIRED_COVERAGE_TAGS if not metrics.coverage_counts.get(tag))
    if missing:
        failures.append(f"missing authored coverage tags: {', '.join(missing)}")
    if metrics.top_fit_tie_count < 1:
        failures.append("requires at least one close-score tie case")
    if metrics.clear_top_count < 3:
        failures.append("requires at least three clearly separated supported tops")
    ablations = {"without_deadhead": report.without_deadhead, **report.component_ablations}
    for name, ablation in ablations.items():
        if metrics.case_count != ablation.case_count:
            failures.append(f"{name} ablation must use identical case counts")
    return tuple(failures)


def evaluate_rankings(
    with_deadhead_cases: Iterable[RankingEvaluationCase],
    without_deadhead_cases: Iterable[tuple[str, str, tuple[str, ...], str] | RankingEvaluationCase],
    component_ablations: Mapping[str, Iterable[RankingEvaluationCase]] | None = None,
) -> RankingEvaluationReport:
    """Evaluate identical cutoff cases with and without deadhead evidence.

    Callers must supply tenant-local rankings.  The evaluator intentionally has no
    cross-tenant data access, so another tenant cannot alter a fixed prediction.
    """
    with_cases = tuple(with_deadhead_cases)
    without_cases = tuple(
        item if isinstance(item, RankingEvaluationCase) else RankingEvaluationCase(*item)
        for item in without_deadhead_cases
    )
    ablations = {name: tuple(values) for name, values in (component_ablations or {}).items()}
    return RankingEvaluationReport(
        _metrics(with_cases, supported_only=True),
        _metrics(without_cases, supported_only=True),
        _metrics(with_cases, supported_only=False),
        _metrics(without_cases, supported_only=False),
        {name: _metrics(values, supported_only=True) for name, values in sorted(ablations.items())},
        {
            name: _metrics(values, supported_only=False)
            for name, values in sorted(ablations.items())
        },
    )


def write_ranking_artifacts(report: RankingEvaluationReport, artifacts_dir: Path) -> Path:
    """Write deterministic ranking metrics next to rate-backtest artifacts."""
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    path = artifacts_dir / "ranking_metrics.json"
    path.write_text(json.dumps(asdict(report), indent=2, sort_keys=True) + "\n")
    return path


class RankingBacktestHarness:
    """Reconstruct ranking inputs at first ACTIVE and compare later booking labels."""

    def __init__(
        self,
        features: CarrierFeatureService | None = None,
        scorer: CarrierHistoricalFitScorer | None = None,
    ) -> None:
        self._features = features or CarrierFeatureService()
        self._scorer = scorer or CarrierHistoricalFitScorer()

    def run(
        self, session: Session, tenant_ids: tuple[UUID, ...] | None = None
    ) -> RankingEvaluationReport:
        """Evaluate tenant-local rankings plus all documented component ablations."""
        tenants = tenant_ids or tuple(session.scalars(select(Tenant.id).order_by(Tenant.id)).all())
        scorers = {
            "with_deadhead": self._scorer,
            "without_deadhead": CarrierHistoricalFitScorer(ScoringWeights().without("deadhead")),
            **{
                f"without_{component}": CarrierHistoricalFitScorer(
                    ScoringWeights().without(component)
                )
                for component in _COMPONENT_ABLATIONS
            },
        }
        evaluated: dict[str, list[RankingEvaluationCase]] = {name: [] for name in scorers}
        for tenant_id in tenants:
            for load in session.scalars(select(Load).where(Load.tenant_id == tenant_id)).all():
                case = self._case(session, tenant_id, load)
                if case is None:
                    continue
                for name, scorer in scorers.items():
                    evaluated[name].append(self._rank_case(session, case, scorer))
        return evaluate_rankings(
            evaluated["with_deadhead"],
            evaluated["without_deadhead"],
            {
                name: values
                for name, values in evaluated.items()
                if name not in {"with_deadhead", "without_deadhead"}
            },
        )

    def _case(
        self, session: Session, tenant_id: UUID, load: Load
    ) -> tuple[LoadVersion, str, str] | None:
        set_tenant_context(session, tenant_id)
        versions = tuple(
            session.scalars(
                select(LoadVersion)
                .where(LoadVersion.tenant_id == tenant_id, LoadVersion.load_id == load.id)
                .order_by(LoadVersion.observed_at, LoadVersion.id)
            ).all()
        )
        first_active = next((item for item in versions if item.status is LoadStatus.ACTIVE), None)
        final_booked = next(
            (item for item in reversed(versions) if item.carrier_id is not None), None
        )
        if first_active is None or final_booked is None:
            return None
        carrier = session.scalar(
            select(Carrier).where(
                Carrier.tenant_id == tenant_id, Carrier.id == final_booked.carrier_id
            )
        )
        return (
            None
            if carrier is None
            else (first_active, carrier.external_id, load.source_system.value)
        )

    def _rank_case(
        self,
        session: Session,
        case: tuple[LoadVersion, str, str],
        scorer: CarrierHistoricalFitScorer,
    ) -> RankingEvaluationCase:
        target, booked_carrier_id, source_system = case
        features = self._features.retrieve(
            session, target.tenant_id, target.load_id, target.id, target.observed_at
        )
        if not features:
            return RankingEvaluationCase(
                str(target.tenant_id),
                booked_carrier_id,
                (),
                "SPARSE",
                source_system=source_system,
                all_candidate_no_rank_reason="NO_CANDIDATES_AT_CUTOFF",
                supported_no_rank_reason="NO_SUPPORTED_CANDIDATES_AT_CUTOFF",
            )
        scored = scorer.score(features)
        by_carrier = {item.carrier_external_id: item for item in scored}
        booked_score = by_carrier.get(booked_carrier_id)
        depth = (
            "RICH"
            if booked_score is not None and booked_score.effective_history >= RICH_EFFECTIVE_HISTORY
            else "SPARSE"
        )
        supported = tuple(item for item in scored if item.evidence_status == "SUPPORTED")
        top_margin = (
            None
            if len(supported) < 2
            else supported[0].adjusted_score - supported[1].adjusted_score
        )
        top_tied = len(supported) > 1 and supported[0].tie_group == supported[1].tie_group
        limited_count = len(scored) - len(supported)
        booked_features = next(
            (item for item in features if item.carrier_external_id == booked_carrier_id), None
        )
        return RankingEvaluationCase(
            str(target.tenant_id),
            booked_carrier_id,
            tuple(item.carrier_external_id for item in scored),
            depth,
            top_margin,
            top_tied,
            limited_count,
            len(supported),
            _coverage_tags(features, booked_features, booked_score, top_tied, depth),
            source_system,
            tuple(item.carrier_external_id for item in supported),
            None if booked_score is not None else "BOOKED_CARRIER_NOT_CANDIDATE_AT_CUTOFF",
            _supported_no_rank_reason(booked_score, supported),
            None if not supported else supported[0].confidence,
        )


def _metrics(cases: tuple[RankingEvaluationCase, ...], *, supported_only: bool) -> RankingMetrics:
    grouped: dict[str, list[RankingEvaluationCase]] = defaultdict(list)
    for case in cases:
        grouped[case.history_depth].append(case)
    return _summary(
        cases,
        {
            depth: _summary(tuple(grouped[depth]), {}, supported_only=supported_only)
            for depth in sorted({"RICH", "SPARSE", *grouped})
        },
        supported_only=supported_only,
    )


def _summary(
    cases: tuple[RankingEvaluationCase, ...],
    by_history_depth: dict[str, RankingMetrics],
    *,
    supported_only: bool,
) -> RankingMetrics:
    if not cases:
        return RankingMetrics(0, 0, None, None, None, 0, by_history_depth)
    ranks = tuple(_rank(case, supported_only=supported_only) for case in cases)
    scored_ranks = tuple(rank for rank in ranks if rank is not None)
    count = Decimal(len(scored_ranks))
    margins = tuple(case.top_score_margin for case in cases if case.top_score_margin is not None)
    coverage_counts: defaultdict[str, int] = defaultdict(int)
    for case in cases:
        for tag in case.coverage_tags:
            coverage_counts[tag] += 1
    tied_count = sum(case.top_fit_is_tied for case in cases)
    clear_top_count = sum(
        case.supported_candidate_count > 0 and not case.top_fit_is_tied for case in cases
    )
    scored_by_source: defaultdict[str, int] = defaultdict(int)
    no_rank_reasons: defaultdict[str, int] = defaultdict(int)
    confidence_counts: defaultdict[str, int] = defaultdict(int)
    for case, rank in zip(cases, ranks, strict=True):
        if rank is not None and case.source_system:
            scored_by_source[case.source_system] += 1
        if rank is None:
            reason = (
                case.supported_no_rank_reason
                if supported_only
                else case.all_candidate_no_rank_reason
            )
            no_rank_reasons[reason or "NO_RANK_REASON_UNSPECIFIED"] += 1
        if case.top_supported_confidence is not None:
            confidence_counts[case.top_supported_confidence] += 1
    return RankingMetrics(
        len(cases),
        len(scored_ranks),
        None if not scored_ranks else _decimal(sum(rank == 1 for rank in scored_ranks) / count),
        None if not scored_ranks else _decimal(sum(rank <= 3 for rank in scored_ranks) / count),
        _decimal(sum((Decimal(1) / rank for rank in scored_ranks), Decimal(0)) / count)
        if scored_ranks
        else None,
        sum(rank is None for rank in ranks),
        by_history_depth,
        None if not margins else _decimal(sum(margins, Decimal(0)) / Decimal(len(margins))),
        _decimal(sum(case.top_fit_is_tied for case in cases) / Decimal(len(cases))),
        sum(case.limited_candidate_count for case in cases),
        sum(case.supported_candidate_count for case in cases),
        tied_count,
        clear_top_count,
        dict(sorted(coverage_counts.items())),
        dict(sorted(scored_by_source.items())),
        dict(sorted(no_rank_reasons.items())),
        dict(sorted(confidence_counts.items())),
    )


def _rank(case: RankingEvaluationCase, *, supported_only: bool) -> Decimal | None:
    carrier_ids = (
        case.ranked_carrier_ids
        if not supported_only or case.supported_ranked_carrier_ids is None
        else case.supported_ranked_carrier_ids
    )
    try:
        return Decimal(carrier_ids.index(case.eventually_booked_carrier_id) + 1)
    except ValueError:
        return None


def _decimal(value: Decimal | float) -> str:
    return format(Decimal(str(value)).normalize(), "f")


def _supported_no_rank_reason(
    booked_score: CarrierHistoricalFit | None,
    supported: tuple[CarrierHistoricalFit, ...],
) -> str | None:
    if booked_score is not None and booked_score.evidence_status == "SUPPORTED":
        return None
    if booked_score is not None:
        return "BOOKED_CARRIER_LIMITED_RELEVANT_HISTORY"
    if not supported:
        return "NO_SUPPORTED_CANDIDATES_AT_CUTOFF"
    return "BOOKED_CARRIER_NOT_SUPPORTED_AT_CUTOFF"


def _coverage_tags(
    features: tuple[CarrierFeatureSet, ...],
    booked_features: CarrierFeatureSet | None,
    booked_score: CarrierHistoricalFit | None,
    top_tied: bool,
    history_depth: str,
) -> tuple[str, ...]:
    """Derive coverage from the same immutable cutoff facts being evaluated."""
    tags = {history_depth}
    if top_tied:
        tags.add("CLOSE_SCORE_TIE")
    if booked_score is None or booked_score.evidence_status != "SUPPORTED":
        tags.add("LIMITED_CANDIDATE")
    if booked_features is not None and booked_features.equipment_history_count > 0:
        tags.add("KNOWN_EQUIPMENT")
    tiers = tuple(item.tier for feature in features for item in feature.lane_history)
    if LaneTier.NEAR_EXACT in tiers:
        tags.add("NEAR_EXACT")
    if any(tier in {LaneTier.REGIONAL, LaneTier.METRO_CORRIDOR} for tier in tiers):
        tags.add("BROADER_LANE")
    if any(tier in {LaneTier.DISTANCE_EQUIPMENT, LaneTier.TENANT_EQUIPMENT} for tier in tiers):
        tags.add("DISTANCE_EQUIPMENT")
    return tuple(sorted(tags))
