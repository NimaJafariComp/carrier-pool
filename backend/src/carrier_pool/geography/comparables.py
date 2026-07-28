"""Tenant-scoped, as-of comparable-load retrieval from immutable versions."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from carrier_pool.db.models import LoadVersion
from carrier_pool.db.tenant import set_tenant_context
from carrier_pool.domain.types import EquipmentType, LoadStatus
from carrier_pool.geography.lookup import GeographyLookup
from carrier_pool.geography.utilities import Coordinate, endpoint_distances


class LaneTier(StrEnum):
    """Ordered fallback tiers documented by the geography design."""

    NEAR_EXACT = "NEAR_EXACT"
    REGIONAL = "REGIONAL"
    METRO_CORRIDOR = "METRO_CORRIDOR"
    DISTANCE_EQUIPMENT = "DISTANCE_EQUIPMENT"
    TENANT_EQUIPMENT = "TENANT_EQUIPMENT"
    TENANT_ALL_EQUIPMENT = "TENANT_ALL_EQUIPMENT"


@dataclass(frozen=True, slots=True)
class ComparableLoadEvidence:
    """One immutable, explainable comparable observation."""

    load_id: UUID
    load_external_id: str
    version_id: UUID
    equipment: EquipmentType | None
    tier: LaneTier
    origin_distance_miles: float | None
    destination_distance_miles: float | None
    route_mile_difference: Decimal | None
    recency_days: float
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _Route:
    origin: Coordinate | None
    destination: Coordinate | None
    origin_metro: str | None
    destination_metro: str | None


class ComparableLoadRepository:
    """Select narrowest available same-tenant historical tier as of a cutoff."""

    def retrieve(
        self,
        session: Session,
        tenant_id: UUID,
        target_load_id: UUID,
        target_version_id: UUID,
        as_of: datetime,
    ) -> tuple[ComparableLoadEvidence, ...]:
        """Return one selected fallback tier without reading mutable projections."""
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("as_of must be timezone-aware.")
        if session.in_transaction():
            set_tenant_context(session, tenant_id)
            return self._retrieve(session, tenant_id, target_load_id, target_version_id, as_of)
        with session.begin():
            set_tenant_context(session, tenant_id)
            return self._retrieve(session, tenant_id, target_load_id, target_version_id, as_of)

    def retrieve_by_tier(
        self,
        session: Session,
        tenant_id: UUID,
        target_load_id: UUID,
        target_version_id: UUID,
        as_of: datetime,
    ) -> dict[LaneTier, tuple[ComparableLoadEvidence, ...]]:
        """Return tenant-local as-of evidence grouped by its narrowest matching tier."""
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("as_of must be timezone-aware.")
        if session.in_transaction():
            set_tenant_context(session, tenant_id)
            return self._retrieve_by_tier(
                session, tenant_id, target_load_id, target_version_id, as_of
            )
        with session.begin():
            set_tenant_context(session, tenant_id)
            return self._retrieve_by_tier(
                session, tenant_id, target_load_id, target_version_id, as_of
            )

    def _retrieve(
        self,
        session: Session,
        tenant_id: UUID,
        target_load_id: UUID,
        target_version_id: UUID,
        as_of: datetime,
    ) -> tuple[ComparableLoadEvidence, ...]:
        buckets = self._retrieve_by_tier(
            session, tenant_id, target_load_id, target_version_id, as_of
        )
        for tier in LaneTier:
            if buckets[tier]:
                return buckets[tier]
        return ()

    def _retrieve_by_tier(
        self,
        session: Session,
        tenant_id: UUID,
        target_load_id: UUID,
        target_version_id: UUID,
        as_of: datetime,
    ) -> dict[LaneTier, tuple[ComparableLoadEvidence, ...]]:
        target = session.scalar(
            select(LoadVersion).where(
                LoadVersion.tenant_id == tenant_id,
                LoadVersion.load_id == target_load_id,
                LoadVersion.id == target_version_id,
                LoadVersion.observed_at <= as_of,
            )
        )
        if target is None:
            raise LookupError("target load version not found.")
        versions = session.scalars(
            select(LoadVersion).where(
                LoadVersion.tenant_id == tenant_id,
                LoadVersion.observed_at <= as_of,
                LoadVersion.load_id != target_load_id,
            )
        ).all()

        latest_by_load: dict[UUID, LoadVersion] = {}
        for version in versions:
            prior = latest_by_load.get(version.load_id)
            if prior is None or (version.observed_at, version.id) > (prior.observed_at, prior.id):
                latest_by_load[version.load_id] = version

        target_route = _route(target)
        buckets: dict[LaneTier, list[ComparableLoadEvidence]] = {tier: [] for tier in LaneTier}
        completed = tuple(
            version for version in latest_by_load.values() if version.status is LoadStatus.COMPLETED
        )
        has_exact_equipment = any(version.equipment == target.equipment for version in completed)
        for version in completed:
            tier = _tier(target, target_route, version, has_exact_equipment)
            if tier is not None:
                buckets[tier].append(_evidence(target, target_route, version, tier, as_of))
        return {tier: tuple(sorted(buckets[tier], key=_evidence_sort_key)) for tier in LaneTier}


def _tier(
    target: LoadVersion,
    target_route: _Route,
    candidate: LoadVersion,
    has_exact_equipment: bool,
) -> LaneTier | None:
    same_equipment = candidate.equipment == target.equipment
    candidate_route = _route(candidate)
    distances = _distances(target_route, candidate_route)
    if same_equipment and distances is not None:
        if distances.origin_miles <= 25 and distances.destination_miles <= 25:
            return LaneTier.NEAR_EXACT
        if distances.origin_miles <= 50 and distances.destination_miles <= 50:
            return LaneTier.REGIONAL
    if (
        same_equipment
        and target_route.origin_metro is not None
        and target_route.destination_metro is not None
        and (target_route.origin_metro, target_route.destination_metro)
        == (candidate_route.origin_metro, candidate_route.destination_metro)
    ):
        return LaneTier.METRO_CORRIDOR
    difference = _route_mile_difference(target, candidate)
    distance_limit = (
        None
        if target.distance_miles is None
        else min(Decimal("75"), max(Decimal("25"), target.distance_miles * Decimal("0.15")))
    )
    if (
        same_equipment
        and difference is not None
        and distance_limit is not None
        and difference <= distance_limit
    ):
        return LaneTier.DISTANCE_EQUIPMENT
    if same_equipment:
        return LaneTier.TENANT_EQUIPMENT
    if target.equipment in (None, EquipmentType.UNKNOWN) or not has_exact_equipment:
        return LaneTier.TENANT_ALL_EQUIPMENT
    return None


def _evidence(
    target: LoadVersion,
    target_route: _Route,
    candidate: LoadVersion,
    tier: LaneTier,
    as_of: datetime,
) -> ComparableLoadEvidence:
    candidate_route = _route(candidate)
    distances = _distances(target_route, candidate_route)
    return ComparableLoadEvidence(
        load_id=candidate.load_id,
        load_external_id=_external_id(candidate),
        version_id=candidate.id,
        equipment=candidate.equipment,
        tier=tier,
        origin_distance_miles=None if distances is None else distances.origin_miles,
        destination_distance_miles=None if distances is None else distances.destination_miles,
        route_mile_difference=_route_mile_difference(target, candidate),
        recency_days=max(0.0, (as_of - candidate.observed_at).total_seconds() / 86_400),
        evidence_ids=(str(candidate.id), str(candidate.load_id), str(candidate.ingestion_file_id)),
    )


def _evidence_sort_key(value: ComparableLoadEvidence) -> tuple[float, float, str]:
    return (
        value.origin_distance_miles if value.origin_distance_miles is not None else float("inf"),
        value.destination_distance_miles
        if value.destination_distance_miles is not None
        else float("inf"),
        str(value.version_id),
    )


def _route(version: LoadVersion) -> _Route:
    stops = _array(version.canonical_snapshot.get("stops"))
    if stops is None:
        return _Route(None, None, None, None)
    parsed = tuple(_stop(value) for value in stops)
    valid = tuple(stop for stop in parsed if stop is not None)
    origin = next((stop for stop in valid if stop[0]), valid[0] if valid else None)
    destination = next((stop for stop in reversed(valid) if stop[1]), valid[-1] if valid else None)
    if origin is None or destination is None:
        return _Route(None, None, None, None)
    return _Route(origin[2], destination[2], origin[3], destination[3])


def _stop(value: object) -> tuple[bool, bool, Coordinate | None, str | None] | None:
    snapshot = _object(value)
    if snapshot is None:
        return None
    postal_code = snapshot.get("postal_code")
    city = snapshot.get("city")
    state = snapshot.get("state")
    if not isinstance(postal_code, str) or not isinstance(city, str) or not isinstance(state, str):
        return None
    geography = GeographyLookup.default().lookup(postal_code, city, state)
    coordinate = (
        None
        if geography.latitude is None or geography.longitude is None
        else Coordinate(float(geography.latitude), float(geography.longitude))
    )
    return (
        bool(snapshot.get("is_pickup")),
        bool(snapshot.get("is_dropoff")),
        coordinate,
        geography.metro_group,
    )


def _distances(target: _Route, candidate: _Route):
    if (
        target.origin is None
        or target.destination is None
        or candidate.origin is None
        or candidate.destination is None
    ):
        return None
    return endpoint_distances(
        target.origin, target.destination, candidate.origin, candidate.destination
    )


def _route_mile_difference(first: LoadVersion, second: LoadVersion) -> Decimal | None:
    if first.distance_miles is None or second.distance_miles is None:
        return None
    return abs(first.distance_miles - second.distance_miles)


def _external_id(version: LoadVersion) -> str:
    external_id = version.canonical_snapshot.get("external_id")
    if not isinstance(external_id, str) or not external_id:
        raise ValueError(f"Load version {version.id} has no immutable external ID.")
    return external_id


def _array(value: object) -> tuple[object, ...] | None:
    if not isinstance(value, list):
        return None
    return tuple(cast(list[object], value))


def _object(value: object) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    dictionary = cast(dict[object, object], value)
    return {str(key): item for key, item in dictionary.items()}
