"""Tenant-local, as-of hierarchical carrier-rate estimation."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from carrier_pool.db.models import Load, LoadVersion, SourceRateEntry
from carrier_pool.db.tenant import set_tenant_context
from carrier_pool.decisioning.statistics import (
    WeightedObservation,
    blend_with_broader,
    effective_sample_size,
    weighted_median,
    weighted_quantile,
)
from carrier_pool.domain.types import EquipmentType, FinancialSide, SourceSystem
from carrier_pool.geography.comparables import (
    ComparableLoadEvidence,
    ComparableLoadRepository,
    LaneTier,
)

MODEL_VERSION = "pricing-hierarchical-v1"
SHRINKAGE_STRENGTH = Decimal(6)
SPARSE_ESS_THRESHOLD = Decimal(4)
ESS_COMPARISON_EPSILON = Decimal("0.0000001")


class ConfidenceLevel(StrEnum):
    """Human-readable confidence bands for historical comparison evidence."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


@dataclass(frozen=True, slots=True)
class PricingTarget:
    """Immutable target version inputs needed by the estimator."""

    version_id: UUID
    equipment: EquipmentType | None


@dataclass(frozen=True, slots=True)
class ComparableRateEvidence:
    """One displayed comparable with its source-aware carrier-pay total."""

    load_id: UUID
    load_external_id: str
    load_version_id: UUID
    carrier_rate_usd: Decimal
    tier: LaneTier
    origin_distance_miles: float | None
    destination_distance_miles: float | None
    route_mile_difference: Decimal | None
    recency_days: float
    weight: Decimal
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PricingConfidence:
    """Confidence label and all auditable component values."""

    level: ConfidenceLevel
    score: Decimal
    components: dict[str, Decimal]


@dataclass(frozen=True, slots=True)
class RateEstimate:
    """Explainable price result; absent totals represent no eligible history."""

    model_version: str
    as_of: datetime
    point_estimate_usd: Decimal | None
    historical_comparison_lower_usd: Decimal | None
    historical_comparison_upper_usd: Decimal | None
    confidence: PricingConfidence
    local_tier: LaneTier | None
    broader_tier: LaneTier | None
    blend_local_weight: Decimal | None
    raw_evidence_count: int
    effective_evidence_count: Decimal
    comparables: tuple[ComparableRateEvidence, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _TierSummary:
    tier: LaneTier
    median: Decimal
    lower: Decimal
    upper: Decimal
    effective_sample_size: Decimal
    comparables: tuple[ComparableRateEvidence, ...]


@dataclass(frozen=True, slots=True)
class _ResolvedTier:
    tier: LaneTier
    point: Decimal
    lower: Decimal
    upper: Decimal
    local_weight: Decimal
    broader_tier: LaneTier | None
    comparables: tuple[ComparableRateEvidence, ...]


class HierarchicalRateEstimator:
    """Produce only same-tenant, immutable-history rate estimates."""

    def __init__(self, comparable_repository: ComparableLoadRepository | None = None) -> None:
        self._comparable_repository = comparable_repository or ComparableLoadRepository()

    def estimate(
        self, session: Session, tenant_id: UUID, load_id: UUID, as_of: datetime
    ) -> RateEstimate:
        """Estimate carrier pay from facts observed no later than ``as_of``."""
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("as_of must be timezone-aware.")
        target = self._target_at_as_of(session, tenant_id, load_id, as_of)
        tiered_evidence = self._comparables_by_tier(session, tenant_id, target, as_of)
        all_evidence = tuple(item for tier in LaneTier for item in tiered_evidence.get(tier, ()))
        rate_values = self._carrier_rates_at_as_of(session, tenant_id, all_evidence, as_of)
        summaries = _summaries_by_tier(tiered_evidence, rate_values)
        resolved = _resolve_hierarchy(summaries)
        if resolved is None:
            return _no_evidence(as_of)

        local = summaries[resolved.tier]
        warnings = _warnings(target, local, resolved)
        return RateEstimate(
            model_version=MODEL_VERSION,
            as_of=as_of,
            point_estimate_usd=resolved.point,
            historical_comparison_lower_usd=resolved.lower,
            historical_comparison_upper_usd=resolved.upper,
            confidence=_confidence(target, local, resolved),
            local_tier=resolved.tier,
            broader_tier=resolved.broader_tier,
            blend_local_weight=resolved.local_weight,
            # The API displays every resolved (local plus broader) comparison. Its
            # counts must therefore describe that same displayed evidence set.
            raw_evidence_count=len(resolved.comparables),
            effective_evidence_count=effective_sample_size(
                tuple(item.weight for item in resolved.comparables)
            ),
            comparables=resolved.comparables,
            warnings=warnings,
        )

    def _target_at_as_of(
        self, session: Session, tenant_id: UUID, load_id: UUID, as_of: datetime
    ) -> PricingTarget:
        set_tenant_context(session, tenant_id)
        versions = session.scalars(
            select(LoadVersion).where(
                LoadVersion.tenant_id == tenant_id,
                LoadVersion.load_id == load_id,
                LoadVersion.observed_at <= as_of,
            )
        ).all()
        if not versions:
            raise LookupError("target load not found at as_of.")
        target = max(versions, key=lambda value: (value.observed_at, value.id))
        return PricingTarget(target.id, target.equipment)

    def _comparables_by_tier(
        self, session: Session, tenant_id: UUID, target: PricingTarget, as_of: datetime
    ) -> dict[LaneTier, tuple[ComparableLoadEvidence, ...]]:
        row = session.scalar(
            select(LoadVersion.load_id).where(
                LoadVersion.tenant_id == tenant_id,
                LoadVersion.id == target.version_id,
                LoadVersion.observed_at <= as_of,
            )
        )
        if row is None:
            raise LookupError("target load version not found.")
        return self._comparable_repository.retrieve_by_tier(
            session, tenant_id, row, target.version_id, as_of
        )

    def _carrier_rates_at_as_of(
        self,
        session: Session,
        tenant_id: UUID,
        evidence: Sequence[ComparableLoadEvidence],
        as_of: datetime,
    ) -> dict[UUID, tuple[Decimal, tuple[str, ...]]]:
        if not evidence:
            return {}
        set_tenant_context(session, tenant_id)
        version_ids = tuple(item.version_id for item in evidence)
        rows = session.execute(
            select(LoadVersion, Load.source_system)
            .join(Load, Load.id == LoadVersion.load_id)
            .where(
                LoadVersion.tenant_id == tenant_id,
                LoadVersion.id.in_(version_ids),
                LoadVersion.observed_at <= as_of,
            )
        ).all()
        result: dict[UUID, tuple[Decimal, tuple[str, ...]]] = {}
        hauldesk_load_ids: set[UUID] = set()
        hauldesk_versions: dict[UUID, UUID] = {}
        for version, source_system in rows:
            if source_system is SourceSystem.HAULDESK:
                hauldesk_load_ids.add(version.load_id)
                hauldesk_versions[version.load_id] = version.id
            elif version.currency == "USD" and version.carrier_rate_amount is not None:
                if version.carrier_rate_amount >= 0:
                    result[version.id] = (version.carrier_rate_amount, (str(version.id),))

        if not hauldesk_load_ids:
            return result
        ledger_rows = session.execute(
            select(SourceRateEntry.load_id, SourceRateEntry.id, SourceRateEntry.amount).where(
                SourceRateEntry.tenant_id == tenant_id,
                SourceRateEntry.source_system == SourceSystem.HAULDESK,
                SourceRateEntry.side == FinancialSide.PAY,
                SourceRateEntry.load_id.in_(hauldesk_load_ids),
                SourceRateEntry.observed_at <= as_of,
                SourceRateEntry.currency == "USD",
            )
        ).all()
        totals: dict[UUID, Decimal] = {}
        identifiers: dict[UUID, list[str]] = {}
        for load_id, rate_entry_id, amount in ledger_rows:
            totals[load_id] = totals.get(load_id, Decimal(0)) + amount
            identifiers.setdefault(load_id, []).append(str(rate_entry_id))
        for load_id, total in totals.items():
            if total >= 0:
                result[hauldesk_versions[load_id]] = (total, tuple(sorted(identifiers[load_id])))
        return result


def _summaries_by_tier(
    tiers: dict[LaneTier, tuple[ComparableLoadEvidence, ...]],
    rates: dict[UUID, tuple[Decimal, tuple[str, ...]]],
) -> dict[LaneTier, _TierSummary]:
    summaries: dict[LaneTier, _TierSummary] = {}
    for tier in LaneTier:
        comparable_rates = tuple(
            _with_rate(item, rates[item.version_id])
            for item in tiers.get(tier, ())
            if item.version_id in rates
        )
        observations = tuple(
            WeightedObservation(value=item.carrier_rate_usd, weight=item.weight)
            for item in comparable_rates
        )
        median = weighted_median(observations)
        lower = weighted_quantile(observations, Decimal("0.25"))
        upper = weighted_quantile(observations, Decimal("0.75"))
        if median is None or lower is None or upper is None:
            continue
        summaries[tier] = _TierSummary(
            tier=tier,
            median=median,
            lower=lower,
            upper=upper,
            effective_sample_size=effective_sample_size(
                tuple(item.weight for item in comparable_rates)
            ),
            comparables=comparable_rates,
        )
    return summaries


def _with_rate(
    evidence: ComparableLoadEvidence, rate: tuple[Decimal, tuple[str, ...]]
) -> ComparableRateEvidence:
    amount, rate_identifiers = rate
    weight = comparable_weight(evidence)
    return ComparableRateEvidence(
        load_id=evidence.load_id,
        load_external_id=evidence.load_external_id,
        load_version_id=evidence.version_id,
        carrier_rate_usd=amount,
        tier=evidence.tier,
        origin_distance_miles=evidence.origin_distance_miles,
        destination_distance_miles=evidence.destination_distance_miles,
        route_mile_difference=evidence.route_mile_difference,
        recency_days=evidence.recency_days,
        weight=weight,
        evidence_ids=(*evidence.evidence_ids, *rate_identifiers),
    )


def comparable_weight(evidence: ComparableLoadEvidence) -> Decimal:
    geography = Decimal(1)
    if evidence.origin_distance_miles is not None:
        geography *= _decay(evidence.origin_distance_miles, Decimal(25))
    if evidence.destination_distance_miles is not None:
        geography *= _decay(evidence.destination_distance_miles, Decimal(25))
    route = (
        Decimal(1)
        if evidence.route_mile_difference is None
        else _decay(evidence.route_mile_difference, Decimal(50))
    )
    return geography * route * _decay(evidence.recency_days, Decimal(30))


def _decay(value: Decimal | float, scale: Decimal) -> Decimal:
    decimal_value = value if isinstance(value, Decimal) else Decimal(str(value))
    return (-(decimal_value / scale)).exp()


def _resolve_hierarchy(summaries: dict[LaneTier, _TierSummary]) -> _ResolvedTier | None:
    resolved: _ResolvedTier | None = None
    for tier in reversed(tuple(LaneTier)):
        local = summaries.get(tier)
        if local is None:
            continue
        if resolved is None or not _is_sparse(local.effective_sample_size):
            resolved = _ResolvedTier(
                tier=tier,
                point=local.median,
                lower=local.lower,
                upper=local.upper,
                local_weight=Decimal(1),
                broader_tier=None,
                comparables=local.comparables,
            )
            continue
        point_blend = blend_with_broader(
            local.median, resolved.point, local.effective_sample_size, SHRINKAGE_STRENGTH
        )
        lower_blend = blend_with_broader(
            local.lower, resolved.lower, local.effective_sample_size, SHRINKAGE_STRENGTH
        )
        upper_blend = blend_with_broader(
            local.upper, resolved.upper, local.effective_sample_size, SHRINKAGE_STRENGTH
        )
        assert point_blend is not None and lower_blend is not None and upper_blend is not None
        lower = min(lower_blend.estimate, point_blend.estimate)
        upper = max(upper_blend.estimate, point_blend.estimate)
        resolved = _ResolvedTier(
            tier=tier,
            point=point_blend.estimate,
            lower=lower,
            upper=upper,
            local_weight=point_blend.local_weight,
            broader_tier=resolved.tier,
            comparables=(*local.comparables, *resolved.comparables),
        )
    return resolved


def _confidence(
    target: PricingTarget, local: _TierSummary, resolved: _ResolvedTier
) -> PricingConfidence:
    ess_component = min(Decimal(1), local.effective_sample_size / Decimal(8))
    tier_component = {
        LaneTier.NEAR_EXACT: Decimal("1.0"),
        LaneTier.REGIONAL: Decimal("0.8"),
        LaneTier.METRO_CORRIDOR: Decimal("0.6"),
        LaneTier.DISTANCE_EQUIPMENT: Decimal("0.45"),
        LaneTier.TENANT_EQUIPMENT: Decimal("0.3"),
        LaneTier.TENANT_ALL_EQUIPMENT: Decimal("0.15"),
    }[local.tier]
    similarity = sum((_similarity(item) for item in local.comparables), Decimal(0)) / Decimal(
        len(local.comparables)
    )
    total_weight = sum((item.weight for item in local.comparables), Decimal(0))
    age = (
        sum(
            (item.weight * Decimal(str(item.recency_days)) for item in local.comparables),
            Decimal(0),
        )
        / total_weight
    )
    recency = _decay(age, Decimal(30))
    dispersion = Decimal(1) - min(
        Decimal(1), (resolved.upper - resolved.lower) / max(resolved.point, Decimal(1))
    )
    score = (
        Decimal("0.45") * ess_component
        + Decimal("0.20") * tier_component
        + Decimal("0.15") * similarity
        + Decimal("0.10") * recency
        + Decimal("0.10") * dispersion
    )
    if target.equipment in (None, EquipmentType.UNKNOWN):
        level = ConfidenceLevel.LOW
    elif score >= Decimal("0.75"):
        level = ConfidenceLevel.HIGH
    elif score >= Decimal("0.45"):
        level = ConfidenceLevel.MEDIUM
    else:
        level = ConfidenceLevel.LOW
    return PricingConfidence(
        level=level,
        score=score,
        components={
            "effective_sample_size": ess_component,
            "tier_quality": tier_component,
            "mean_similarity": similarity,
            "recency_quality": recency,
            "dispersion_quality": dispersion,
        },
    )


def _similarity(evidence: ComparableRateEvidence) -> Decimal:
    geography_and_route = evidence.weight / _decay(evidence.recency_days, Decimal(30))
    return min(Decimal(1), geography_and_route)


def _warnings(
    target: PricingTarget, local: _TierSummary, resolved: _ResolvedTier
) -> tuple[str, ...]:
    warnings: list[str] = []
    if _is_sparse(local.effective_sample_size):
        warnings.append("SPARSE_EVIDENCE")
    if resolved.broader_tier is not None:
        warnings.append("BROADER_FALLBACK")
    if target.equipment in (None, EquipmentType.UNKNOWN):
        warnings.append("UNKNOWN_EQUIPMENT")
    if any(
        item.origin_distance_miles is None or item.destination_distance_miles is None
        for item in resolved.comparables
    ):
        warnings.append("MISSING_GEOGRAPHY")
    return tuple(warnings)


def _is_sparse(effective_sample_size_value: Decimal) -> bool:
    """Avoid classifying equal-weight boundary evidence as sparse from Decimal rounding."""
    return effective_sample_size_value + ESS_COMPARISON_EPSILON < SPARSE_ESS_THRESHOLD


def _no_evidence(as_of: datetime) -> RateEstimate:
    return RateEstimate(
        model_version=MODEL_VERSION,
        as_of=as_of,
        point_estimate_usd=None,
        historical_comparison_lower_usd=None,
        historical_comparison_upper_usd=None,
        confidence=PricingConfidence(ConfidenceLevel.LOW, Decimal(0), {}),
        local_tier=None,
        broader_tier=None,
        blend_local_weight=None,
        raw_evidence_count=0,
        effective_evidence_count=Decimal(0),
        comparables=(),
        warnings=("NO_HISTORICAL_EVIDENCE",),
    )
