"""Tenant-local, as-of carrier historical-fit feature retrieval."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from carrier_pool.db.models import Carrier, LoadVersion
from carrier_pool.db.tenant import set_tenant_context
from carrier_pool.domain.types import EquipmentType, LoadStatus
from carrier_pool.geography.comparables import ComparableLoadEvidence, ComparableLoadRepository
from carrier_pool.geography.lookup import GeographyLookup
from carrier_pool.geography.utilities import Coordinate, haversine_miles


@dataclass(frozen=True, slots=True)
class CarrierFeatureSet:
    """Immutable historical facts for one ranking candidate; never availability."""

    carrier_id: UUID
    carrier_external_id: str
    lane_history: tuple[ComparableLoadEvidence, ...]
    equipment_history_count: int
    completed_history_count: int
    relevant_completed_observed_at: datetime | None
    last_delivery_observed_at: datetime | None
    last_delivery_load_version_id: UUID | None
    delivery_to_pickup_miles: float | None
    delivery_to_pickup_gap_days: float | None
    raw_evidence_ids: tuple[str, ...]
    target_equipment_unknown: bool
    relevant_completed_count: int = 0
    has_broad_recency_evidence: bool = False
    relevant_completed_age_days: float | None = None
    equipment_history_age_days: tuple[float, ...] = ()
    completed_history_age_days: tuple[float, ...] = ()
    equipment_history_version_ids: tuple[UUID, ...] = ()
    relevant_completed_version_ids: tuple[UUID, ...] = ()
    completed_history_version_ids: tuple[UUID, ...] = ()


class CarrierFeatureService:
    """Build candidate features from immutable same-tenant observations at ``as_of``."""

    def __init__(self, comparables: ComparableLoadRepository | None = None) -> None:
        self._comparables = comparables or ComparableLoadRepository()

    def retrieve(
        self,
        session: Session,
        tenant_id: UUID,
        target_load_id: UUID,
        target_version_id: UUID | None,
        as_of: datetime,
    ) -> tuple[CarrierFeatureSet, ...]:
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("as_of must be timezone-aware")
        if target_version_id is None:
            raise LookupError("target load version not found")
        set_tenant_context(session, tenant_id)
        target = session.scalar(
            select(LoadVersion).where(
                LoadVersion.tenant_id == tenant_id,
                LoadVersion.load_id == target_load_id,
                LoadVersion.id == target_version_id,
                LoadVersion.observed_at <= as_of,
            )
        )
        if target is None:
            raise LookupError("target load version not found")
        if target.status is not LoadStatus.ACTIVE:
            raise ValueError("target load version must be ACTIVE")
        rows = session.scalars(
            select(LoadVersion).where(
                LoadVersion.tenant_id == tenant_id,
                LoadVersion.load_id != target_load_id,
                LoadVersion.observed_at <= as_of,
            )
        ).all()
        latest: dict[UUID, LoadVersion] = {}
        for version in rows:
            prior = latest.get(version.load_id)
            if prior is None or (version.observed_at, version.id) > (prior.observed_at, prior.id):
                latest[version.load_id] = version
        completed = tuple(
            version
            for version in latest.values()
            if version.status is LoadStatus.COMPLETED and version.carrier_id is not None
        )
        by_carrier: dict[UUID, list[LoadVersion]] = {}
        for version in completed:
            assert version.carrier_id is not None
            by_carrier.setdefault(version.carrier_id, []).append(version)
        evidence = self._comparables.retrieve_by_tier(
            session, tenant_id, target_load_id, target_version_id, as_of
        )
        lane_by_carrier: dict[UUID, list[ComparableLoadEvidence]] = {}
        version_by_id = {version.id: version for version in completed}
        for values in evidence.values():
            for item in values:
                version = version_by_id.get(item.version_id)
                if version is not None and version.carrier_id is not None:
                    lane_by_carrier.setdefault(version.carrier_id, []).append(item)
        carriers = session.scalars(
            select(Carrier).where(
                Carrier.tenant_id == tenant_id,
                Carrier.first_observed_at <= as_of,
            )
        ).all()
        result: list[CarrierFeatureSet] = []
        unknown = target.equipment in (None, EquipmentType.UNKNOWN)
        target_pickup = _endpoint(target, pickup=True)
        for carrier in carriers:
            history = tuple(
                sorted(
                    by_carrier.get(carrier.id, ()),
                    key=_version_precedence,
                )
            )
            if not history:
                continue
            equipment_history = (
                ()
                if unknown
                else tuple(value for value in history if value.equipment == target.equipment)
            )
            last_delivery = history[-1]
            delivery = _endpoint(last_delivery, pickup=False)
            lane = tuple(
                sorted(lane_by_carrier.get(carrier.id, ()), key=lambda item: item.version_id)
            )
            lane_version_ids = {item.version_id for item in lane}
            lane_versions = tuple(
                version_by_id[version_id]
                for version_id in lane_version_ids
                if version_id in version_by_id
            )
            relevant_versions = {value.id: value for value in (*equipment_history, *lane_versions)}
            has_broad_recency_evidence = not relevant_versions
            relevant = max(
                relevant_versions.values() if relevant_versions else history,
                key=_version_precedence,
            )
            evidence_ids = tuple(
                str(value)
                for value in (
                    *(item.version_id for item in lane),
                    *(value.id for value in history),
                )
            )
            result.append(
                CarrierFeatureSet(
                    carrier.id,
                    carrier.external_id,
                    lane,
                    len(equipment_history),
                    len(history),
                    relevant.observed_at,
                    last_delivery.observed_at,
                    last_delivery.id,
                    None
                    if delivery is None or target_pickup is None
                    else haversine_miles(delivery, target_pickup),
                    max(0.0, (as_of - last_delivery.observed_at).total_seconds() / 86_400),
                    tuple(dict.fromkeys(evidence_ids)),
                    unknown,
                    len(relevant_versions),
                    has_broad_recency_evidence,
                    max(0.0, (as_of - relevant.observed_at).total_seconds() / 86_400),
                    tuple(
                        max(0.0, (as_of - value.observed_at).total_seconds() / 86_400)
                        for value in equipment_history
                    ),
                    tuple(
                        max(0.0, (as_of - value.observed_at).total_seconds() / 86_400)
                        for value in history
                    ),
                    tuple(value.id for value in equipment_history),
                    tuple(sorted(relevant_versions, key=str)),
                    tuple(value.id for value in history),
                )
            )
        return tuple(sorted(result, key=lambda item: item.carrier_external_id))


def _endpoint(version: LoadVersion, *, pickup: bool) -> Coordinate | None:
    raw_stops: object = version.canonical_snapshot.get("stops")
    if not isinstance(raw_stops, list):
        return None
    stops = cast(list[object], raw_stops)
    ordered: Iterable[object] = stops if pickup else reversed(stops)
    flag = "is_pickup" if pickup else "is_dropoff"
    for raw_stop in ordered:
        if not isinstance(raw_stop, dict):
            continue
        stop = cast(dict[str, object], raw_stop)
        if stop.get(flag) is not True:
            continue
        postal_code = stop.get("postal_code")
        city = stop.get("city")
        state = stop.get("state")
        if not isinstance(postal_code, str) or not isinstance(state, str):
            continue
        # ZIP and state establish coordinates; city is explanatory only.
        geography = GeographyLookup.default().lookup(
            postal_code, city if isinstance(city, str) else None, state
        )
        if geography.latitude is not None and geography.longitude is not None:
            return Coordinate(float(geography.latitude), float(geography.longitude))
    return None


def _version_precedence(version: LoadVersion) -> tuple[datetime, UUID]:
    """Stable ordering for source observations with equal timestamps."""
    return version.observed_at, version.id
