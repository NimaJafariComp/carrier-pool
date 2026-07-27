"""Phase 7.3 deterministic current-projection precedence tests."""

from datetime import UTC, datetime

from carrier_pool.domain.types import LoadStatus
from carrier_pool.ingestion.precedence import VersionTiming, choose_current_version


def _at(hour: int) -> datetime:
    return datetime(2026, 7, 1, hour, tzinfo=UTC)


def test_later_source_sync_wins_even_when_status_regresses_as_correction() -> None:
    current = VersionTiming(_at(6), _at(6), _at(6), LoadStatus.COVERED)
    candidate = VersionTiming(_at(12), _at(5), _at(12), LoadStatus.ACTIVE)

    decision = choose_current_version(current, candidate)

    assert decision.becomes_current is True
    assert decision.anomaly_code == "STATUS_REGRESSION_CORRECTION"


def test_out_of_order_source_sync_never_replaces_current_projection() -> None:
    current = VersionTiming(_at(12), _at(12), _at(12), LoadStatus.COVERED)
    candidate = VersionTiming(_at(6), _at(18), _at(18), LoadStatus.COMPLETED)

    decision = choose_current_version(current, candidate)

    assert decision.becomes_current is False
    assert decision.anomaly_code == "OUT_OF_ORDER_SNAPSHOT"
