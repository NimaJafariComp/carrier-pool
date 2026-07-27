"""Leakage-safe rate-backtest contracts."""

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from carrier_pool.decisioning.backtest import (
    HistoricalRateCase,
    RateBacktestHarness,
    write_backtest_artifacts,
)
from carrier_pool.decisioning.pricing import (
    ConfidenceLevel,
    PricingConfidence,
    RateEstimate,
)
from carrier_pool.domain.types import EquipmentType
from carrier_pool.geography.comparables import LaneTier


def result(point: str | None, tier: LaneTier | None = LaneTier.NEAR_EXACT) -> RateEstimate:
    value = None if point is None else Decimal(point)
    return RateEstimate(
        model_version="pricing-hierarchical-v1",
        as_of=datetime(2026, 7, 1, tzinfo=UTC),
        point_estimate_usd=value,
        historical_comparison_lower_usd=value,
        historical_comparison_upper_usd=value,
        confidence=PricingConfidence(ConfidenceLevel.LOW, Decimal("0"), {}),
        local_tier=tier,
        broader_tier=None,
        blend_local_weight=None,
        raw_evidence_count=2,
        effective_evidence_count=Decimal("2"),
        comparables=(),
        warnings=(),
    )


def case(actual: str, *, equipment: EquipmentType = EquipmentType.DRY_VAN) -> HistoricalRateCase:
    return HistoricalRateCase(
        tenant_id=uuid4(),
        load_id=uuid4(),
        first_active_at=datetime(2026, 7, 1, tzinfo=UTC),
        final_carrier_rate_usd=Decimal(actual),
        equipment=equipment,
    )


def test_future_correction_is_label_only_not_earlier_prediction_input() -> None:
    historical_case = case("1200")
    calls: list[datetime] = []

    def estimate_at_cutoff(tenant_id, load_id, as_of):
        calls.append(as_of)
        return result("1000")

    report = RateBacktestHarness.evaluate_cases((historical_case,), estimate_at_cutoff)

    assert calls == [historical_case.first_active_at]
    assert report.cases[0].actual_carrier_rate_usd == Decimal("1200")
    assert report.cases[0].absolute_error_usd == Decimal("200")


def test_metrics_and_breakdowns_exclude_no_estimate_cases() -> None:
    first, second, no_estimate = case("1000"), case("1200"), case("900")
    predictions = iter((result("1100"), result("1000", LaneTier.REGIONAL), result(None, None)))

    report = RateBacktestHarness.evaluate_cases(
        (first, second, no_estimate), lambda tenant_id, load_id, as_of: next(predictions)
    )

    assert report.case_count == 3
    assert report.scored_case_count == 2
    assert report.metrics.mae_usd == Decimal("150")
    assert report.metrics.median_absolute_error_usd == Decimal("150")
    assert report.metrics.wape == Decimal("0.1363636363636363636363636364")
    assert report.metrics.range_coverage == Decimal("0")
    assert report.by_tier["NEAR_EXACT"].case_count == 1
    assert report.by_equipment["DRY_VAN"].case_count == 2
    assert report.by_history_depth["SPARSE"].case_count == 2


def test_artifact_writer_emits_json_and_csv(tmp_path: Path) -> None:
    report = RateBacktestHarness.evaluate_cases((case("1000"),), lambda *args: result("1000"))

    metrics_path, cases_path = write_backtest_artifacts(report, tmp_path)

    assert metrics_path.name == "backtest_metrics.json"
    assert cases_path.name == "backtest_cases.csv"
    assert '"mae_usd": "0"' in metrics_path.read_text()
    assert "actual_carrier_rate_usd" in cases_path.read_text()
    models = json.loads(metrics_path.read_text())["models"]
    assert models["pricing-hierarchical-v1"]["case_count"] == 1
    assert set(models) == {
        "pricing-hierarchical-v1",
        "tenant_wide_median",
        "equipment_distance_band_median",
        "unshrunk_nearest_lane_weighted_median",
        "robust_huber_regression",
        "quantile_regression",
    }
    assert all("metrics" in model for model in models.values())
