"""Constraint shape tests for Phase 3.4 persistence."""

from carrier_pool.db.base import Base


def test_phase_3_4_tables_and_constraints_exist() -> None:
    assert {"source_rate_entries", "decision_runs", "carrier_recommendations"} <= set(
        Base.metadata.tables
    )
    rates = Base.metadata.tables["source_rate_entries"]
    recommendations = Base.metadata.tables["carrier_recommendations"]
    rate_unique = [
        tuple(item.columns.keys())
        for item in rates.constraints
        if item.__class__.__name__ == "UniqueConstraint"
    ]
    recommendation_unique = [
        tuple(item.columns.keys())
        for item in recommendations.constraints
        if item.__class__.__name__ == "UniqueConstraint"
    ]
    assert ("tenant_id", "source_system", "external_id") in rate_unique
    assert ("tenant_id", "decision_run_id", "rank") in recommendation_unique
    assert recommendations.c.rank.nullable is False
