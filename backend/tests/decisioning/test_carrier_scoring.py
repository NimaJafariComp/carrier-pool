"""Historical-fit carrier scoring contracts."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from carrier_pool.decisioning.carrier_features import CarrierFeatureSet
from carrier_pool.decisioning.carrier_scoring import CarrierHistoricalFitScorer
from carrier_pool.geography.comparables import ComparableLoadEvidence, LaneTier

NOW = datetime(2026, 7, 11, tzinfo=UTC)


def feature(
    carrier: str,
    *,
    tier: LaneTier = LaneTier.NEAR_EXACT,
    delivery_miles: float = 20,
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
