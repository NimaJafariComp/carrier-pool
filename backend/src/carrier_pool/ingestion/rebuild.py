"""Rebuild mutable tenant projections from immutable ingestion facts."""

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, cast
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from carrier_pool.db.models import (
    Carrier,
    Customer,
    IngestionFile,
    Load,
    LoadVersion,
    SourceRateEntry,
    Stop,
)
from carrier_pool.db.tenant import set_tenant_context
from carrier_pool.domain.types import FinancialSide, SourceSystem
from carrier_pool.geography.enrichment import enrich_stop
from carrier_pool.ingestion.precedence import VersionTiming, choose_current_version


@dataclass(frozen=True, slots=True)
class RebuildResult:
    """Counts of mutable projections reconstructed for one tenant."""

    loads_rebuilt: int
    stops_rebuilt: int


def rebuild_current_projections(session: Session, tenant_id: UUID) -> RebuildResult:
    """Reconstruct one tenant's current rows without reading current values as inputs."""
    with session.begin():
        set_tenant_context(session, tenant_id)
        rows = session.execute(
            select(LoadVersion, Load, IngestionFile)
            .join(Load, Load.id == LoadVersion.load_id)
            .join(IngestionFile, IngestionFile.id == LoadVersion.ingestion_file_id)
            .where(LoadVersion.tenant_id == tenant_id)
        ).all()
        current_by_load: dict[UUID, tuple[LoadVersion, Load, IngestionFile]] = {}
        customer_versions: dict[UUID, list[LoadVersion]] = {}
        carrier_versions: dict[UUID, list[LoadVersion]] = {}
        for version, load, ingestion in rows:
            if version.customer_id is not None:
                customer_versions.setdefault(version.customer_id, []).append(version)
            if version.carrier_id is not None:
                carrier_versions.setdefault(version.carrier_id, []).append(version)
            current = current_by_load.get(load.id)
            if (
                current is None
                or choose_current_version(
                    _timing(current[0], current[2]), _timing(version, ingestion)
                ).becomes_current
            ):
                current_by_load[load.id] = (version, load, ingestion)

        session.execute(delete(Stop).where(Stop.tenant_id == tenant_id))
        _rebuild_customers(session, customer_versions)
        _rebuild_carriers(session, carrier_versions)

        stops_rebuilt = 0
        for version, load, _ingestion in current_by_load.values():
            _apply_load_projection(session, load, version)
            stops_rebuilt += _add_stops(session, load, version)
        return RebuildResult(len(current_by_load), stops_rebuilt)


def _timing(version: LoadVersion, ingestion: IngestionFile) -> VersionTiming:
    return VersionTiming(
        source_sync_at=ingestion.sync_at,
        source_modified_at=version.source_modified_at,
        observed_at=version.observed_at,
        status=version.status,
    )


def _rebuild_customers(
    session: Session, versions_by_customer: dict[UUID, list[LoadVersion]]
) -> None:
    for customer_id, versions in versions_by_customer.items():
        latest = max(versions, key=lambda version: version.observed_at)
        customer = session.get(Customer, customer_id)
        if customer is None:
            continue
        customer.name = _customer_name(latest)
        customer.first_observed_at = min(version.observed_at for version in versions)
        customer.last_observed_at = latest.observed_at


def _rebuild_carriers(session: Session, versions_by_carrier: dict[UUID, list[LoadVersion]]) -> None:
    for carrier_id, versions in versions_by_carrier.items():
        latest = max(versions, key=lambda version: version.observed_at)
        carrier = session.get(Carrier, carrier_id)
        if carrier is None:
            continue
        name, mc_number, dot_number, phone_number, home_city, home_state = _carrier_details(latest)
        carrier.name = name
        carrier.normalized_name = name.upper()
        carrier.mc_number = mc_number
        carrier.dot_number = dot_number
        carrier.phone_number = phone_number
        carrier.home_city = home_city
        carrier.home_state = home_state
        carrier.first_observed_at = min(version.observed_at for version in versions)
        carrier.last_observed_at = latest.observed_at


def _apply_load_projection(session: Session, load: Load, version: LoadVersion) -> None:
    snapshot = version.canonical_snapshot
    load.load_number = _optional_string(snapshot.get("load_number"))
    if version.customer is None:
        raise ValueError(f"Load version {version.id} has no customer projection.")
    load.customer = version.customer
    load.carrier = version.carrier
    load.status = version.status
    load.equipment = version.equipment
    if load.source_system is SourceSystem.HAULDESK:
        load.customer_rate_amount, load.carrier_rate_amount = _hauldesk_totals(
            session, load.id, version.observed_at
        )
    else:
        load.customer_rate_amount = version.customer_rate_amount
        load.carrier_rate_amount = version.carrier_rate_amount
    load.currency = version.currency
    load.weight_lbs = version.weight_lbs
    load.distance_miles = version.distance_miles
    load.source_created_at = _source_created_at(version)
    load.source_modified_at = version.source_modified_at
    load.observed_at = version.observed_at
    load.current_version = version


def _hauldesk_totals(session: Session, load_id: UUID, as_of: datetime) -> tuple[Decimal, Decimal]:
    rows = session.execute(
        select(SourceRateEntry.side, func.coalesce(func.sum(SourceRateEntry.amount), 0))
        .where(
            SourceRateEntry.load_id == load_id,
            SourceRateEntry.source_system == SourceSystem.HAULDESK,
            SourceRateEntry.observed_at <= as_of,
        )
        .group_by(SourceRateEntry.side)
    )
    totals = {side: Decimal(amount) for side, amount in rows}
    return totals.get(FinancialSide.BILL, Decimal("0")), totals.get(FinancialSide.PAY, Decimal("0"))


def _add_stops(session: Session, load: Load, version: LoadVersion) -> int:
    stops = _array(version.canonical_snapshot.get("stops", []))
    if stops is None:
        raise ValueError(f"Load version {version.id} has invalid stops snapshot.")
    for value in stops:
        stop = _object(value)
        if stop is None:
            raise ValueError(f"Load version {version.id} has invalid stop value.")
        geography = enrich_stop(str(stop["city"]), str(stop["state"]), str(stop["postal_code"]))
        session.add(
            Stop(
                tenant_id=load.tenant_id,
                load=load,
                sequence=int(stop["sequence"]),
                is_pickup=bool(stop["is_pickup"]),
                is_dropoff=bool(stop["is_dropoff"]),
                facility_name=_optional_string(stop.get("facility_name")),
                city=str(stop["city"]),
                state=str(stop["state"]),
                postal_code=str(stop["postal_code"]),
                latitude=geography.latitude,
                longitude=geography.longitude,
                metro_group=geography.metro_group,
                geography_quality_flags=geography.quality_flags,
                scheduled_start_at=_timestamp(stop.get("scheduled_start_at")),
                scheduled_end_at=_timestamp(stop.get("scheduled_end_at")),
                actual_arrival_at=_timestamp(stop.get("actual_arrival_at")),
                actual_departure_at=_timestamp(stop.get("actual_departure_at")),
            )
        )
    return len(stops)


def _customer_name(version: LoadVersion) -> str:
    customer = _object(version.canonical_snapshot.get("customer"))
    if customer is not None and isinstance(customer.get("name"), str):
        return customer["name"]
    raw_customer = _object(version.raw_snapshot.get("customer"))
    if raw_customer is not None and isinstance(raw_customer.get("name"), str):
        return raw_customer["name"]
    raise ValueError(f"Load version {version.id} has no immutable customer name.")


def _carrier_details(
    version: LoadVersion,
) -> tuple[str, str | None, str | None, str | None, str | None, str | None]:
    carrier = _object(version.canonical_snapshot.get("carrier"))
    raw_carrier = _object(version.raw_snapshot.get("carrier"))
    if carrier is None or not isinstance(carrier.get("name"), str):
        carrier = raw_carrier
    if carrier is None or not isinstance(carrier.get("name"), str):
        raise ValueError(f"Load version {version.id} has no immutable carrier name.")
    return (
        str(carrier["name"]),
        _carrier_value(carrier, raw_carrier, "mc_number", "mcNumber"),
        _carrier_value(carrier, raw_carrier, "dot_number", "dotNumber"),
        _carrier_value(carrier, raw_carrier, "phone_number", "phoneNumber"),
        _carrier_value(carrier, raw_carrier, "home_city", "homeCity"),
        _carrier_value(carrier, raw_carrier, "home_state", "homeState"),
    )


def _source_created_at(version: LoadVersion) -> datetime:
    value = version.canonical_snapshot.get("source_created_at")
    if value is None:
        value = version.raw_snapshot.get("createdDate")
    timestamp = _timestamp(value)
    if timestamp is None:
        raise ValueError(f"Load version {version.id} has no immutable source created timestamp.")
    return timestamp


def _timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"Expected ISO timestamp string, got {value!r}.")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"Expected timezone-aware timestamp, got {value!r}.")
    return parsed.astimezone(UTC)


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _object(value: object) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    dictionary = cast(dict[object, object], value)
    return {str(key): item for key, item in dictionary.items()}


def _array(value: object) -> list[object] | None:
    if not isinstance(value, list):
        return None
    return list(cast(list[object], value))


def _carrier_value(
    canonical: dict[str, Any], raw: dict[str, Any] | None, key: str, raw_key: str
) -> str | None:
    value = _optional_string(canonical.get(key))
    if value is not None or raw is None:
        return value
    return _optional_string(raw.get(key)) or _optional_string(raw.get(raw_key))
