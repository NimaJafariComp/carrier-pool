"""FreightFlow source DTOs and plain-JSON parser; no normalization or persistence."""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from carrier_pool.domain.models import (
    CanonicalCarrierSnapshot,
    CanonicalCustomerSnapshot,
    CanonicalLoadSnapshot,
    CanonicalSourceIdentity,
    CanonicalStop,
    NormalizationWarning,
    NormalizedSync,
    SyncMetadata,
)
from carrier_pool.domain.types import ExternalId, Money, SourceSystem
from carrier_pool.ingestion.base import (
    InvalidSourceFileError,
    ParsedSync,
    SourceAdapter,
    SourceFile,
    TenantContext,
)
from carrier_pool.ingestion.mappings import map_freightflow_equipment, map_freightflow_status


class FreightFlowDto(BaseModel):
    """Preserve FreightFlow field names and values as supplied by its JSON payload."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")


def _offset_datetime(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include a UTC offset.")
    return value


class FreightFlowCustomerDto(FreightFlowDto):
    customer_id: int | str = Field(alias="customerId")
    name: str


class FreightFlowCarrierDto(FreightFlowDto):
    carrier_master_id: int | str = Field(alias="carrierMasterId")
    name: str
    mc_number: str = Field(alias="mcNumber")
    dot_number: str = Field(alias="dotNumber")
    phone_number: str = Field(alias="phoneNumber")


class FreightFlowStopDto(FreightFlowDto):
    stop_type: str = Field(alias="stopType")
    city: str
    state: str
    zip_code: str = Field(alias="zipCode")
    estimated_ready_datetime: datetime = Field(alias="estimatedReadyDateTime")
    estimated_close_datetime: datetime = Field(alias="estimatedCloseDateTime")
    actual_departure_datetime: datetime | None = Field(alias="actualDepartureDateTime")

    @field_validator(
        "estimated_ready_datetime", "estimated_close_datetime", "actual_departure_datetime"
    )
    @classmethod
    def require_offset(cls, value: datetime | None, info: Any) -> datetime | None:
        return None if value is None else _offset_datetime(value, info.field_name)


class FreightFlowLoadDto(FreightFlowDto):
    shipment_id: int | str = Field(alias="shipmentId")
    status: str
    mileage: float | int
    total_sell: float | int = Field(alias="totalSell")
    total_buy: float | int | None = Field(alias="totalBuy")
    customer: FreightFlowCustomerDto
    carrier: FreightFlowCarrierDto | None
    equipment: str
    weight_total: float | int = Field(alias="weightTotal")
    stops: list[FreightFlowStopDto]
    created_date: datetime = Field(alias="createdDate")
    last_modified_date: datetime = Field(alias="lastModifiedDate")

    @field_validator("created_date", "last_modified_date")
    @classmethod
    def require_offset(cls, value: datetime, info: Any) -> datetime:
        return _offset_datetime(value, info.field_name)


class FreightFlowSyncDto(FreightFlowDto):
    synced_at: datetime = Field(alias="syncedAt")
    loads: list[FreightFlowLoadDto]

    @field_validator("synced_at")
    @classmethod
    def require_offset(cls, value: datetime, info: Any) -> datetime:
        return _offset_datetime(value, info.field_name)


def parse_freightflow_file(source_file: SourceFile) -> FreightFlowSyncDto:
    """Parse one generated FreightFlow JSON file and retain its source DTO values."""
    try:
        payload = json.loads(source_file.content)
    except json.JSONDecodeError as error:
        raise InvalidSourceFileError(source_file.path, f"invalid JSON: {error.msg}") from error
    try:
        return FreightFlowSyncDto.model_validate(payload)
    except ValidationError as error:
        details = "; ".join(
            f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}"
            for item in error.errors()
        )
        raise InvalidSourceFileError(source_file.path, details) from error


def parse_freightflow_path(path: Path) -> FreightFlowSyncDto:
    """Read and parse a generated plain JSON FreightFlow file."""
    return parse_freightflow_file(SourceFile(path=path, content=path.read_bytes()))


@dataclass(frozen=True, slots=True)
class FreightFlowParsedPayload:
    sync: FreightFlowSyncDto
    raw_loads: tuple[dict[str, object], ...]


class FreightFlowAdapter(SourceAdapter):
    """FreightFlow parser and canonical normalizer; never persists data."""

    source_system = SourceSystem.FREIGHTFLOW

    def parse_file(self, source_file: SourceFile, tenant: TenantContext) -> ParsedSync:
        del tenant
        sync = parse_freightflow_file(source_file)
        raw_payload = json.loads(source_file.content)
        raw_loads = tuple(raw_payload["loads"])
        return ParsedSync(
            self.source_system, source_file, FreightFlowParsedPayload(sync, raw_loads)
        )

    def normalize(self, parsed_sync: ParsedSync, tenant: TenantContext) -> NormalizedSync:
        if not isinstance(parsed_sync.payload, FreightFlowParsedPayload):
            raise TypeError("FreightFlowAdapter requires FreightFlowParsedPayload.")
        payload = parsed_sync.payload
        observed_at = payload.sync.synced_at.astimezone(UTC)
        metadata = SyncMetadata(
            tenant.tenant_id,
            self.source_system,
            parsed_sync.source_file.path.name,
            observed_at,
            observed_at,
        )
        loads = tuple(self._normalize_load(load, tenant.tenant_id) for load in payload.sync.loads)
        customers = tuple(load.customer for load in loads)
        carriers = tuple(load.carrier for load in loads if load.carrier is not None)
        warnings = tuple(
            NormalizationWarning(
                "FREIGHTFLOW_STOP_ROLE_UNRECOGNIZED",
                f"Unrecognized stopType {stop.stop_type!r}.",
                field_path=f"loads.{load_index}.stops.{stop_index}.stopType",
            )
            for load_index, load in enumerate(payload.sync.loads)
            for stop_index, stop in enumerate(load.stops)
            if stop.stop_type not in {"First Pickup", "Last Drop"}
        )
        return NormalizedSync(
            metadata, loads, customers, carriers, warnings=warnings, raw_loads=payload.raw_loads
        )

    def _normalize_load(self, load: FreightFlowLoadDto, tenant_id: str) -> CanonicalLoadSnapshot:
        identity = CanonicalSourceIdentity(
            tenant_id, self.source_system, ExternalId(str(load.shipment_id))
        )
        customer = CanonicalCustomerSnapshot(
            CanonicalSourceIdentity(
                tenant_id, self.source_system, ExternalId(str(load.customer.customer_id))
            ),
            load.customer.name,
        )
        carrier = (
            None
            if load.carrier is None
            else CanonicalCarrierSnapshot(
                CanonicalSourceIdentity(
                    tenant_id, self.source_system, ExternalId(str(load.carrier.carrier_master_id))
                ),
                load.carrier.name,
                load.carrier.mc_number,
                load.carrier.dot_number,
                load.carrier.phone_number,
            )
        )
        stops = tuple(
            CanonicalStop(
                index,
                stop.stop_type == "First Pickup",
                stop.stop_type == "Last Drop",
                stop.city,
                stop.state,
                stop.zip_code,
                scheduled_start_at=stop.estimated_ready_datetime.astimezone(UTC),
                scheduled_end_at=stop.estimated_close_datetime.astimezone(UTC),
                actual_departure_at=None
                if stop.actual_departure_datetime is None
                else stop.actual_departure_datetime.astimezone(UTC),
            )
            for index, stop in enumerate(load.stops, 1)
        )
        return CanonicalLoadSnapshot(
            identity,
            map_freightflow_status(load.status),
            customer,
            stops,
            load.created_date.astimezone(UTC),
            load.last_modified_date.astimezone(UTC),
            carrier,
            map_freightflow_equipment(load.equipment),
            Money.from_value(str(load.total_sell)),
            None if load.total_buy is None else Money.from_value(str(load.total_buy)),
            weight_lbs=Decimal(str(load.weight_total)),
            distance_miles=Decimal(str(load.mileage)),
        )
