"""Historical-fit carrier scoring contracts."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from carrier_pool.decisioning.carrier_features import CarrierFeatureSet
from carrier_pool.decisioning.carrier_scoring import CarrierHistoricalFitScorer, ScoringWeights
from carrier_pool.geography.comparables import ComparableLoadEvidence, LaneTier

NOW = datetime(2026, 7, 11, tzinfo=UTC)


def feature(
    carrier: str,
    *,
    tier: LaneTier = LaneTier.NEAR_EXACT,
    delivery_miles: float | None = 20,
    delivery_days: float = 1,
    relevant_days: int = 1,
    history: int = 4,
) -> CarrierFeatureSet:
    version_id = uuid4()
    evidence = ComparableLoadEvidence(
        load_id=uuid4(),
        load_external_id="history",
        version_id=version_id,
        equipment=None,
        tier=tier,
        origin_distance_miles=5,
        destination_distance_miles=5,
        route_mile_difference=None,
        recency_days=float(relevant_days),
        evidence_ids=(str(version_id),),
    )
    return CarrierFeatureSet(
        uuid4(),
        carrier,
        (evidence,),
        history,
        history,
        NOW - timedelta(days=relevant_days),
        NOW - timedelta(days=int(delivery_days)),
        version_id,
        delivery_miles,
        delivery_days,
        (str(version_id),),
        False,
        1,
        False,
        float(relevant_days),
        (float(relevant_days),) * history,
        (float(relevant_days),) * history,
    )


def test_exact_lane_evidence_outranks_broader_lane() -> None:
    ranked = CarrierHistoricalFitScorer().score(
        (feature("exact"), feature("broad", tier=LaneTier.TENANT_EQUIPMENT))
    )
    assert [item.carrier_external_id for item in ranked] == ["exact", "broad"]


def test_deadhead_and_recency_change_rank_without_availability_claims() -> None:
    ranked = CarrierHistoricalFitScorer().score(
        (feature("far", delivery_miles=200), feature("near", delivery_miles=5))
    )
    assert ranked[0].carrier_external_id == "near"
    assert all("available" not in warning.lower() for item in ranked for warning in item.warnings)


def test_old_delivery_decays_and_one_load_score_is_shrunk() -> None:
    stale = feature("stale", delivery_days=30, relevant_days=30)
    recent = feature("recent", delivery_days=1, relevant_days=1)
    one_load = feature("one", history=1)
    ranked = CarrierHistoricalFitScorer().score((stale, recent, one_load))
    scores = {item.carrier_external_id: item for item in ranked}
    assert scores["recent"].adjusted_score > scores["stale"].adjusted_score
    assert scores["one"].adjusted_score < 75


def test_missing_delivery_evidence_is_renormalized_not_scored_as_zero() -> None:
    complete = feature("complete", delivery_miles=5)
    missing = feature("missing", delivery_miles=None)
    missing = CarrierFeatureSet(
        missing.carrier_id,
        missing.carrier_external_id,
        missing.lane_history,
        missing.equipment_history_count,
        missing.completed_history_count,
        missing.relevant_completed_observed_at,
        missing.last_delivery_observed_at,
        missing.last_delivery_load_version_id,
        None,
        None,
        missing.raw_evidence_ids,
        missing.target_equipment_unknown,
        missing.relevant_completed_count,
        missing.has_broad_recency_evidence,
        missing.relevant_completed_age_days,
        missing.equipment_history_age_days,
        missing.completed_history_age_days,
    )
    rankings = CarrierHistoricalFitScorer().score((complete, missing))
    by_id = {item.carrier_external_id: item for item in rankings}
    assert by_id["missing"].raw_score > 0
    assert "DEADHEAD_LOCATION_UNAVAILABLE" in by_id["missing"].warnings


def test_recency_uses_age_at_cutoff_not_last_delivery_timestamp() -> None:
    recent = feature("recent", relevant_days=1, delivery_days=30)
    stale = feature("stale", relevant_days=30, delivery_days=1)
    rankings = CarrierHistoricalFitScorer().score((recent, stale))
    scores = {item.carrier_external_id: item for item in rankings}
    assert (
        scores["recent"].component_scores["recency"] > scores["stale"].component_scores["recency"]
    )


def test_unsupported_carrier_is_limited_and_cannot_outrank_exact_lane_evidence() -> None:
    exact = feature("exact", history=1)
    unsupported = replace(
        feature("unsupported", history=1),
        lane_history=(),
        equipment_history_count=0,
        relevant_completed_count=0,
        equipment_history_age_days=(),
    )

    ranked = CarrierHistoricalFitScorer().score((unsupported, exact))

    assert [item.carrier_external_id for item in ranked] == ["exact", "unsupported"]
    assert ranked[0].evidence_status == "SUPPORTED"
    assert ranked[0].tie_group == 1
    assert ranked[1].evidence_status == "LIMITED"
    assert ranked[1].tie_group is None
    assert ranked[1].component_scores["lane"] is None


def test_confidence_uses_equipment_coverage_not_equipment_fit() -> None:
    narrow = feature("narrow", history=4)
    broad = replace(
        feature("broad", history=4),
        equipment_history_count=1,
        equipment_history_age_days=(1.0,),
        completed_history_age_days=(1.0,) * 4,
    )

    scored = CarrierHistoricalFitScorer().score((narrow, broad))
    rankings = {item.carrier_external_id: item for item in scored}

    assert rankings["narrow"].component_scores["equipment"] == 1
    assert rankings["broad"].component_scores["equipment"] < 1
    assert rankings["narrow"].confidence_score > rankings["broad"].confidence_score


def test_component_ablation_uses_zero_weight_without_changing_evidence_status() -> None:
    near = feature("near", delivery_miles=5, history=1)
    far = feature("far", delivery_miles=300, history=1)

    with_deadhead = CarrierHistoricalFitScorer().score((near, far))
    without_deadhead = CarrierHistoricalFitScorer(ScoringWeights().without("deadhead")).score(
        (near, far)
    )

    by_id = {item.carrier_external_id: item for item in without_deadhead}
    assert all(item.evidence_status == "SUPPORTED" for item in without_deadhead)
    assert by_id["near"].component_scores["deadhead"] is not None
    assert with_deadhead != without_deadhead
