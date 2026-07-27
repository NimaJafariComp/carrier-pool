"""BrokerOS DTO parsing and in-file Account/Location reference resolution."""

import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from carrier_pool.domain.models import (
    CanonicalCarrierSnapshot,
    CanonicalCustomerSnapshot,
    CanonicalLoadSnapshot,
    CanonicalSourceIdentity,
    CanonicalStop,
    NormalizedSync,
    SyncMetadata,
)
from carrier_pool.domain.types import ExternalId, Money, SourceSystem, decimal_from_value
from carrier_pool.ingestion.base import (
    InvalidSourceFileError,
    ParsedSync,
    SourceAdapter,
    SourceFile,
    TenantContext,
)
from carrier_pool.ingestion.conversions import kilograms_to_pounds, parse_brokeros_datetime
from carrier_pool.ingestion.mappings import map_brokeros_equipment, map_brokeros_status

BROKEROS_ID_LENGTH = 18


class BrokerOSDto(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _opaque_id(value: object) -> str:
    if not isinstance(value, str) or len(value) != BROKEROS_ID_LENGTH:
        raise ValueError("BrokerOS IDs must be 18-character strings.")
    return value


def _utc_timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("BrokerOS timestamps must be strings.")
    return parse_brokeros_datetime(value)


class BrokerOSStopDto(BrokerOSDto):
    bos__Number__c: int
    bos__Is_Pickup__c: bool
    bos__Is_Dropoff__c: bool
    bos__Location__c: str
    bos__Scheduled_Date__c: str
    bos__Arrival_Time__c: datetime | None

    @field_validator("bos__Number__c")
    @classmethod
    def valid_number(cls, value: int) -> int:
        if value < 1:
            raise ValueError("bos__Number__c must be positive.")
        return value

    @field_validator("bos__Location__c", mode="before")
    @classmethod
    def valid_location_id(cls, value: object) -> str:
        return _opaque_id(value)

    @field_validator("bos__Arrival_Time__c", mode="before")
    @classmethod
    def parse_arrival_time(cls, value: object) -> datetime | None:
        return None if value is None else _utc_timestamp(value)


class BrokerOSLineItemDto(BrokerOSDto):
    bos__Commodity__c: str
    bos__Weight__c: Decimal | int
    bos__Weight_Units__c: str
    bos__Pallet_Count__c: Decimal | int

    @field_validator("bos__Commodity__c", "bos__Weight_Units__c")
    @classmethod
    def nonempty_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("BrokerOS cargo text fields must not be empty.")
        return value


class BrokerOSLoadDto(BrokerOSDto):
    Id: str
    Name: str
    bos__Load_Status__c: str
    bos__Distance_Miles__c: Decimal | int
    bos__Customer__c: str
    bos__Carrier__c: str | None
    bos__Equipment_Type__c: str | None
    bos__Customer_Rate__c: Decimal | int | None
    bos__Carrier_Rate__c: Decimal | int | None
    bos__Stops__r: list[BrokerOSStopDto]
    bos__Line_Items__r: list[BrokerOSLineItemDto]
    CreatedDate: datetime
    LastModifiedDate: datetime

    @field_validator("Id", "bos__Customer__c", "bos__Carrier__c", mode="before")
    @classmethod
    def valid_record_id(cls, value: object) -> str | None:
        return None if value is None else _opaque_id(value)

    @field_validator("CreatedDate", "LastModifiedDate", mode="before")
    @classmethod
    def parse_timestamp(cls, value: object) -> datetime:
        return _utc_timestamp(value)


class BrokerOSReferenceDto(BrokerOSDto):
    type: str
    record_type: str | None = None
    Name: str
    bos__City__c: str | None = None
    bos__State__c: str | None = None
    bos__Postal_Code__c: str | None = None


class BrokerOSAccountDto(BrokerOSDto):
    type: str
    record_type: str
    Name: str

    @field_validator("type")
    @classmethod
    def account_type(cls, value: str) -> str:
        if value != "Account":
            raise ValueError("BrokerOS Account reference must have type Account.")
        return value


class BrokerOSLocationDto(BrokerOSDto):
    type: str
    Name: str
    bos__City__c: str
    bos__State__c: str
    bos__Postal_Code__c: str

    @field_validator("type")
    @classmethod
    def location_type(cls, value: str) -> str:
        if value != "Location":
            raise ValueError("BrokerOS Location reference must have type Location.")
        return value


class BrokerOSSyncDto(BrokerOSDto):
    synced_at: datetime
    records: list[BrokerOSLoadDto]
    referenced_records: dict[str, BrokerOSReferenceDto]

    @field_validator("synced_at", mode="before")
    @classmethod
    def parse_timestamp(cls, value: object) -> datetime:
        return _utc_timestamp(value)


@dataclass(frozen=True, slots=True)
class BrokerOSResolvedStop:
    stop: BrokerOSStopDto
    location_id: str
    location: BrokerOSLocationDto


@dataclass(frozen=True, slots=True)
class BrokerOSResolvedLoad:
    load: BrokerOSLoadDto
    customer_id: str
    customer: BrokerOSAccountDto
    carrier_id: str | None
    carrier: BrokerOSAccountDto | None
    stops: tuple[BrokerOSResolvedStop, ...]


@dataclass(frozen=True, slots=True)
class BrokerOSAssembly:
    sync: BrokerOSSyncDto
    accounts_by_id: dict[str, BrokerOSAccountDto]
    locations_by_id: dict[str, BrokerOSLocationDto]
    loads: tuple[BrokerOSResolvedLoad, ...]


class BrokerOSAdapter(SourceAdapter):
    """Database-free BrokerOS parser and normalizer."""

    source_system = SourceSystem.BROKEROS

    def parse_file(self, source_file: SourceFile, tenant: TenantContext) -> ParsedSync:
        del tenant
        return ParsedSync(self.source_system, source_file, parse_brokeros_file(source_file))

    def normalize(self, parsed_sync: ParsedSync, tenant: TenantContext) -> NormalizedSync:
        if not isinstance(parsed_sync.payload, BrokerOSAssembly):
            raise TypeError("BrokerOSAdapter requires BrokerOSAssembly.")
        return normalize_brokeros(
            parsed_sync.payload, tenant.tenant_id, parsed_sync.source_file.path.name
        )


def parse_brokeros_file(source_file: SourceFile) -> BrokerOSAssembly:
    """Parse one BrokerOS file and resolve its typed in-file references."""
    try:
        sync = BrokerOSSyncDto.model_validate(json.loads(source_file.content, parse_float=Decimal))
        accounts, locations = _typed_references(sync)
        loads = tuple(_resolve_load(load, accounts, locations) for load in sync.records)
    except (json.JSONDecodeError, ValidationError, ValueError) as error:
        raise InvalidSourceFileError(source_file.path, str(error)) from error
    return BrokerOSAssembly(sync, accounts, locations, loads)


def _typed_references(
    sync: BrokerOSSyncDto,
) -> tuple[dict[str, BrokerOSAccountDto], dict[str, BrokerOSLocationDto]]:
    accounts: dict[str, BrokerOSAccountDto] = {}
    locations: dict[str, BrokerOSLocationDto] = {}
    for external_id, reference in sync.referenced_records.items():
        _opaque_id(external_id)
        if reference.type == "Account":
            accounts[external_id] = BrokerOSAccountDto.model_validate(
                reference.model_dump(exclude_none=True)
            )
        elif reference.type == "Location":
            locations[external_id] = BrokerOSLocationDto.model_validate(
                reference.model_dump(exclude_none=True)
            )
        else:
            raise ValueError(f"Unsupported BrokerOS reference type: {reference.type!r}.")
    return accounts, locations


def _resolve_load(
    load: BrokerOSLoadDto,
    accounts: dict[str, BrokerOSAccountDto],
    locations: dict[str, BrokerOSLocationDto],
) -> BrokerOSResolvedLoad:
    customer = _required_account(
        load.bos__Customer__c, accounts, "bos__Customer__c", "Customer"
    )
    carrier = (
        None
        if load.bos__Carrier__c is None
        else _required_account(load.bos__Carrier__c, accounts, "bos__Carrier__c", "Carrier")
    )
    stops = tuple(
        BrokerOSResolvedStop(
            stop=stop,
            location_id=stop.bos__Location__c,
            location=_required_location(stop.bos__Location__c, locations),
        )
        for stop in sorted(load.bos__Stops__r, key=lambda stop: stop.bos__Number__c)
    )
    return BrokerOSResolvedLoad(
        load,
        load.bos__Customer__c,
        customer,
        load.bos__Carrier__c,
        carrier,
        stops,
    )


def _required_account(
    external_id: str,
    accounts: dict[str, BrokerOSAccountDto],
    field_name: str,
    expected_record_type: str,
) -> BrokerOSAccountDto:
    try:
        account = accounts[external_id]
    except KeyError as error:
        raise ValueError(f"Missing Account reference for {field_name}: {external_id!r}.") from error
    if account.record_type != expected_record_type:
        raise ValueError(
            f"Wrong Account record_type for {field_name}: {account.record_type!r}; "
            f"expected {expected_record_type!r}."
        )
    return account


def _required_location(
    external_id: str, locations: dict[str, BrokerOSLocationDto]
) -> BrokerOSLocationDto:
    try:
        return locations[external_id]
    except KeyError as error:
        raise ValueError(f"Missing Location reference: {external_id!r}.") from error


def normalize_brokeros(
    assembly: BrokerOSAssembly, tenant_id: str, source_file_name: str
) -> NormalizedSync:
    """Normalize resolved BrokerOS records without persistence side effects."""
    loads: list[CanonicalLoadSnapshot] = []
    customers: list[CanonicalCustomerSnapshot] = []
    carriers: list[CanonicalCarrierSnapshot] = []
    for resolved in assembly.loads:
        source_load = resolved.load
        customer = CanonicalCustomerSnapshot(
            CanonicalSourceIdentity(
                tenant_id, SourceSystem.BROKEROS, ExternalId(resolved.customer_id)
            ),
            resolved.customer.Name,
        )
        carrier = (
            None
            if resolved.carrier is None or resolved.carrier_id is None
            else CanonicalCarrierSnapshot(
                CanonicalSourceIdentity(
                    tenant_id, SourceSystem.BROKEROS, ExternalId(resolved.carrier_id)
                ),
                resolved.carrier.Name,
            )
        )
        stops = tuple(
            CanonicalStop(
                sequence=resolved_stop.stop.bos__Number__c,
                is_pickup=resolved_stop.stop.bos__Is_Pickup__c,
                is_dropoff=resolved_stop.stop.bos__Is_Dropoff__c,
                city=resolved_stop.location.bos__City__c,
                state=resolved_stop.location.bos__State__c,
                postal_code=resolved_stop.location.bos__Postal_Code__c,
                facility_name=resolved_stop.location.Name,
                actual_arrival_at=resolved_stop.stop.bos__Arrival_Time__c,
            )
            for resolved_stop in resolved.stops
        )
        loads.append(
            CanonicalLoadSnapshot(
                identity=CanonicalSourceIdentity(
                    tenant_id, SourceSystem.BROKEROS, ExternalId(source_load.Id)
                ),
                status=map_brokeros_status(source_load.bos__Load_Status__c),
                customer=customer,
                stops=stops,
                source_created_at=source_load.CreatedDate,
                source_modified_at=source_load.LastModifiedDate,
                carrier=carrier,
                equipment=map_brokeros_equipment(source_load.bos__Equipment_Type__c),
                customer_rate=None
                if source_load.bos__Customer_Rate__c is None
                else Money.from_value(source_load.bos__Customer_Rate__c),
                carrier_rate=None
                if source_load.bos__Carrier_Rate__c is None
                else Money.from_value(source_load.bos__Carrier_Rate__c),
                weight_lbs=sum(
                    (_line_item_weight_lbs(item) for item in source_load.bos__Line_Items__r),
                    Decimal("0"),
                ),
                distance_miles=decimal_from_value(source_load.bos__Distance_Miles__c),
                load_number=source_load.Name,
            )
        )
        customers.append(customer)
        if carrier is not None:
            carriers.append(carrier)
    sync_at = assembly.sync.synced_at
    return NormalizedSync(
        metadata=SyncMetadata(
            tenant_id, SourceSystem.BROKEROS, source_file_name, sync_at, sync_at
        ),
        loads=tuple(loads),
        customers=tuple(customers),
        carriers=tuple(carriers),
        raw_loads=tuple(resolved.load.model_dump(mode="json") for resolved in assembly.loads),
    )


def _line_item_weight_lbs(item: BrokerOSLineItemDto) -> Decimal:
    unit = item.bos__Weight_Units__c.lower()
    weight = decimal_from_value(item.bos__Weight__c)
    if weight < 0:
        raise ValueError("BrokerOS line-item weight must not be negative.")
    if unit in {"lb", "lbs", "pound", "pounds"}:
        return weight
    if unit in {"kg", "kgs", "kilogram", "kilograms"}:
        return kilograms_to_pounds(weight)
    raise ValueError(f"Unsupported BrokerOS weight unit: {item.bos__Weight_Units__c!r}.")
