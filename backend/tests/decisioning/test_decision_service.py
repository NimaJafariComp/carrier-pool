"""Decision-run identity contracts."""

from carrier_pool.decisioning.decision_runs import decision_identity


def test_identity_requires_exact_input_version_as_of_models_and_parameters() -> None:
    baseline = decision_identity(
        "tenant", "load", "version-a", "2026-07-11T06:00:00+00:00", "ranking-v1", "pricing-v1"
    )
    assert baseline == decision_identity(
        "tenant", "load", "version-a", "2026-07-11T06:00:00+00:00", "ranking-v1", "pricing-v1"
    )
    assert baseline != decision_identity(
        "tenant", "load", "version-b", "2026-07-11T06:00:00+00:00", "ranking-v1", "pricing-v1"
    )
