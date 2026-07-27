"""Deterministic ordering for rebuildable current projections."""

from dataclasses import dataclass
from datetime import datetime

from carrier_pool.domain.types import LoadStatus

_STATUS_ORDER = {
    LoadStatus.PLANNED: 0,
    LoadStatus.ACTIVE: 1,
    LoadStatus.COVERED: 2,
    LoadStatus.IN_TRANSIT: 3,
    LoadStatus.DELIVERED: 4,
    LoadStatus.COMPLETED: 5,
}


@dataclass(frozen=True, slots=True)
class VersionTiming:
    """Source precedence inputs for one immutable canonical load version."""

    source_sync_at: datetime
    source_modified_at: datetime
    observed_at: datetime
    status: LoadStatus


@dataclass(frozen=True, slots=True)
class PrecedenceDecision:
    """Whether a candidate updates current projection and any source anomaly."""

    becomes_current: bool
    anomaly_code: str | None = None


def choose_current_version(
    current: VersionTiming | None, candidate: VersionTiming
) -> PrecedenceDecision:
    """Prefer source sync, then source modification, then observation timestamp.

    A later source snapshot may legitimately regress a lifecycle status as a correction.
    It remains current while recording the anomaly for explanation and review.
    """
    if current is None:
        return PrecedenceDecision(True)
    current_key = (current.source_sync_at, current.source_modified_at, current.observed_at)
    candidate_key = (
        candidate.source_sync_at,
        candidate.source_modified_at,
        candidate.observed_at,
    )
    if candidate_key <= current_key:
        return PrecedenceDecision(False, "OUT_OF_ORDER_SNAPSHOT")
    if _STATUS_ORDER[candidate.status] < _STATUS_ORDER[current.status]:
        return PrecedenceDecision(True, "STATUS_REGRESSION_CORRECTION")
    return PrecedenceDecision(True)
