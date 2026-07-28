"""Structured carrier-ranking explanation contracts."""

from uuid import uuid4

from carrier_pool.decisioning.carrier_explanations import explain_rankings
from carrier_pool.decisioning.carrier_features import CarrierFeatureSet
from carrier_pool.decisioning.carrier_scoring import CarrierHistoricalFitScorer
from carrier_pool.geography.comparables import ComparableLoadEvidence, LaneTier


def test_explanations_use_evidence_templates_without_unsupported_claims() -> None:
    feature = CarrierFeatureSet(
        uuid4(),
        "carrier-1",
        (),
        0,
        1,
        None,
        None,
        None,
        None,
        None,
        ("load-version-1",),
        True,
    )
    ranking = CarrierHistoricalFitScorer().score((feature,))

    explanation = explain_rankings(ranking, (feature,))[0]

    assert explanation.rank == 1
    assert explanation.adjusted_score == ranking[0].adjusted_score
    assert explanation.confidence == ranking[0].confidence
    assert explanation.supporting_load_ids == ("load-version-1",)
    assert "UNKNOWN_TARGET_EQUIPMENT" in explanation.warnings
    text = " ".join(explanation.evidence_bullets).lower()
    assert all(word not in text for word in ("available", "reliable", "likely to accept"))
    assert explanation.model_version == "carrier-ranking-v4"


def test_distance_equipment_evidence_is_not_described_as_directional() -> None:
    version_id = uuid4()
    feature = CarrierFeatureSet(
        uuid4(),
        "carrier-1",
        (
            ComparableLoadEvidence(
                uuid4(), "history-1", version_id, None, LaneTier.DISTANCE_EQUIPMENT,
                None, None, None, 1, (str(version_id),),
            ),
        ),
        1, 1, None, None, None, None, None, (str(version_id),), False,
    )

    explanation = explain_rankings(CarrierHistoricalFitScorer().score((feature,)), (feature,))[0]

    assert "distance-and-equipment" in " ".join(explanation.evidence_bullets)
    assert "directional" not in " ".join(explanation.evidence_bullets)
