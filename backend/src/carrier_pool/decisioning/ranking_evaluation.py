"""Leakage-safe evaluation for historical-fit carrier rankings.

The eventually booked carrier is an outcome label only.  It is a weak behavioral
proxy, not evidence of availability, acceptance probability, or carrier quality.
"""

import json
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import asdict, dataclass, replace
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from carrier_pool.db.models import Carrier, Load, LoadVersion, Tenant
from carrier_pool.db.tenant import set_tenant_context
from carrier_pool.decisioning.carrier_features import CarrierFeatureService
from carrier_pool.decisioning.carrier_scoring import CarrierHistoricalFitScorer
from carrier_pool.domain.types import LoadStatus

WEAK_PROXY_CAVEAT = (
    "The eventually booked carrier is only a weak behavioral proxy; it does not prove "
    "availability, acceptance probability, reliability, or dispatch quality."
)


@dataclass(frozen=True, slots=True)
class RankingEvaluationCase:
    """One cutoff-safe ranking and the later carrier booking label."""

    tenant_id: str
    eventually_booked_carrier_id: str
    ranked_carrier_ids: tuple[str, ...]
    history_depth: str


@dataclass(frozen=True, slots=True)
class RankingMetrics:
    """Recall and reciprocal-rank measures for a single evaluation population."""

    case_count: int
    top_1_recall: str | None
    top_3_recall: str | None
    mean_reciprocal_rank: str | None
    no_rank_count: int
    by_history_depth: dict[str, "RankingMetrics"]


@dataclass(frozen=True, slots=True)
class RankingEvaluationReport:
    """Paired rankings make the deadhead-evidence ablation directly comparable."""

    with_deadhead: RankingMetrics
    without_deadhead: RankingMetrics
    caveat: str = WEAK_PROXY_CAVEAT


def evaluate_rankings(
    with_deadhead_cases: Iterable[RankingEvaluationCase],
    without_deadhead_cases: Iterable[tuple[str, str, tuple[str, ...], str] | RankingEvaluationCase],
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
    return RankingEvaluationReport(_metrics(with_cases), _metrics(without_cases))


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
        """Evaluate tenant-local, first-ACTIVE rankings and a deadhead ablation."""
        tenants = tenant_ids or tuple(session.scalars(select(Tenant.id).order_by(Tenant.id)).all())
        with_deadhead: list[RankingEvaluationCase] = []
        without_deadhead: list[RankingEvaluationCase] = []
        for tenant_id in tenants:
            for load in session.scalars(select(Load).where(Load.tenant_id == tenant_id)).all():
                case = self._case(session, tenant_id, load)
                if case is None:
                    continue
                paired = self._rank_pair(session, case)
                if paired is None:
                    continue
                with_case, without_case = paired
                with_deadhead.append(with_case)
                without_deadhead.append(without_case)
        return evaluate_rankings(with_deadhead, without_deadhead)

    def _case(
        self, session: Session, tenant_id: UUID, load: Load
    ) -> tuple[LoadVersion, str] | None:
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
        return None if carrier is None else (first_active, carrier.external_id)

    def _rank_pair(
        self, session: Session, case: tuple[LoadVersion, str]
    ) -> tuple[RankingEvaluationCase, RankingEvaluationCase] | None:
        target, booked_carrier_id = case
        features = self._features.retrieve(
            session, target.tenant_id, target.load_id, target.id, target.observed_at
        )
        if not features:
            return None
        scored = self._scorer.score(features)
        by_carrier = {item.carrier_external_id: item for item in scored}
        booked_score = by_carrier.get(booked_carrier_id)
        depth = (
            "RICH"
            if booked_score is not None and booked_score.effective_history >= Decimal(4)
            else "SPARSE"
        )
        common = (str(target.tenant_id), booked_carrier_id, depth)
        without_features = tuple(
            replace(item, delivery_to_pickup_miles=None, delivery_to_pickup_gap_days=None)
            for item in features
        )
        return (
            RankingEvaluationCase(
                common[0], common[1], tuple(item.carrier_external_id for item in scored), common[2]
            ),
            RankingEvaluationCase(
                common[0],
                common[1],
                tuple(item.carrier_external_id for item in self._scorer.score(without_features)),
                common[2],
            ),
        )


def _metrics(cases: tuple[RankingEvaluationCase, ...]) -> RankingMetrics:
    grouped: dict[str, list[RankingEvaluationCase]] = defaultdict(list)
    for case in cases:
        grouped[case.history_depth].append(case)
    return _summary(
        cases,
        {
            depth: _summary(tuple(grouped[depth]), {})
            for depth in sorted({"RICH", "SPARSE", *grouped})
        },
    )


def _summary(
    cases: tuple[RankingEvaluationCase, ...], by_history_depth: dict[str, RankingMetrics]
) -> RankingMetrics:
    if not cases:
        return RankingMetrics(0, None, None, None, 0, by_history_depth)
    ranks = tuple(_rank(case) for case in cases)
    count = Decimal(len(cases))
    return RankingMetrics(
        len(cases),
        _decimal(sum(rank == 1 for rank in ranks) / count),
        _decimal(sum(rank is not None and rank <= 3 for rank in ranks) / count),
        _decimal(
            sum((Decimal(1) / rank if rank is not None else Decimal(0)) for rank in ranks) / count
        ),
        sum(rank is None for rank in ranks),
        by_history_depth,
    )


def _rank(case: RankingEvaluationCase) -> Decimal | None:
    try:
        return Decimal(case.ranked_carrier_ids.index(case.eventually_booked_carrier_id) + 1)
    except ValueError:
        return None


def _decimal(value: Decimal | float) -> str:
    return format(Decimal(str(value)).normalize(), "f")
