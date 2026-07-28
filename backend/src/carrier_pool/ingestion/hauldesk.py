"""HaulDesk DTO parsing and in-file relational assembly; no persistence."""

import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from carrier_pool.domain.models import (
    CanonicalCarrierSnapshot,
    CanonicalCustomerSnapshot,
    CanonicalLoadSnapshot,
    CanonicalSourceIdentity,
    CanonicalStop,
    NormalizationWarning,
    NormalizedSync,
    SourceFinancialEntry,
    SyncMetadata,
)
from carrier_pool.domain.types import ExternalId, FinancialSide, Money, SourceSystem
from carrier_pool.ingestion.base import (
    InvalidSourceFileError,
    ParsedSync,
    SourceAdapter,
    SourceFile,
    TenantContext,
)
from carrier_pool.ingestion.conversions import (
    kilograms_to_pounds,
    kilometers_to_miles,
    parse_hauldesk_datetime,
)
from carrier_pool.ingestion.mappings import map_hauldesk_equipment, map_hauldesk_status

STATUS_CODES = {10, 20, 30, 40, 50, 90}
EQUIPMENT_CODES = {"V", "R", "F"}
RATE_SIDES = {"bill", "pay"}
RATE_CODES = {"LINEHAUL", "FUEL", "ACCESSORIAL", "ADJUSTMENT"}


class HaulDeskDto(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HaulDeskLoadDto(HaulDeskDto):
    load_num: str
    status_code: int
    customer_code: str
    customer_name: str
    carrier_ref: int | None
    equip: str
    weight_kg: Decimal | int
    dist_km: Decimal | int
    pu_city: str
    pu_state: str
    pu_zip: str
    pu_date: date
    pu_departed_at: datetime | None
    del_city: str
    del_state: str
    del_zip: str
    del_date: date
    del_arrived_at: datetime | None
    entered_at: datetime
    updated_at: datetime

    @field_validator("status_code")
    @classmethod
    def valid_status(cls, value: int) -> int:
        if value not in STATUS_CODES:
            raise ValueError("Unsupported HaulDesk status_code.")
        return value

    @field_validator("load_num")
    @classmethod
    def stable_load_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("load_num must not be empty.")
        return value

    @field_validator("equip")
    @classmethod
    def valid_equipment(cls, value: str) -> str:
        if value not in EQUIPMENT_CODES:
            raise ValueError("Unsupported HaulDesk equip.")
        return value

    @field_validator("pu_departed_at", "del_arrived_at", "entered_at", "updated_at", mode="before")
    @classmethod
    def parse_timestamp(cls, value: object) -> datetime | None:
        return _parse_hauldesk_timestamp(value)


class HaulDeskCarrierDto(HaulDeskDto):
    carrier_id: int
    carrier_name: str
    mc_no: str
    dot_no: str
    home_city: str
    home_state: str
    phone: str

    @field_validator("carrier_id")
    @classmethod
    def stable_carrier_id(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("carrier_id must be positive.")
        return value


class HaulDeskRateDto(HaulDeskDto):
    rate_id: int
    load_num: str
    side: str
    code: str
    amount_usd: Decimal | int
    created_at: datetime

    @field_validator("rate_id")
    @classmethod
    def stable_rate_id(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("rate_id must be positive.")
        return value

    @field_validator("load_num")
    @classmethod
    def stable_load_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("load_num must not be empty.")
        return value

    @field_validator("side")
    @classmethod
    def valid_side(cls, value: str) -> str:
        if value not in RATE_SIDES:
            raise ValueError("Unsupported HaulDesk rate side.")
        return value

    @field_validator("code")
    @classmethod
    def valid_code(cls, value: str) -> str:
        if value not in RATE_CODES:
            raise ValueError("Unsupported HaulDesk rate code.")
        return value

    @field_validator("created_at", mode="before")
    @classmethod
    def parse_timestamp(cls, value: object) -> datetime:
        parsed = _parse_hauldesk_timestamp(value)
        if parsed is None:
            raise ValueError("HaulDesk timestamp must not be null.")
        return parsed


class HaulDeskSyncDto(HaulDeskDto):
    synced_at: datetime
    loads: list[HaulDeskLoadDto]
    carriers: list[HaulDeskCarrierDto]
    rates: list[HaulDeskRateDto]

    @field_validator("synced_at", mode="before")
    @classmethod
    def parse_timestamp(cls, value: object) -> datetime:
        parsed = _parse_hauldesk_timestamp(value)
        if parsed is None:
            raise ValueError("HaulDesk timestamp must not be null.")
        return parsed


@dataclass(frozen=True, slots=True)
class HaulDeskAssembly:
    sync: HaulDeskSyncDto
    carriers_by_id: dict[int, HaulDeskCarrierDto]
    carriers_by_load_num: dict[str, HaulDeskCarrierDto | None]
    rates_by_load_num: dict[str, tuple[HaulDeskRateDto, ...]]
    warnings: tuple[NormalizationWarning, ...]


class HaulDeskAdapter(SourceAdapter):
    """Database-free HaulDesk parser and normalizer."""

    source_system = SourceSystem.HAULDESK

    def parse_file(self, source_file: SourceFile, tenant: TenantContext) -> ParsedSync:
        del tenant
        return ParsedSync(self.source_system, source_file, parse_hauldesk_file(source_file))

    def normalize(self, parsed_sync: ParsedSync, tenant: TenantContext) -> NormalizedSync:
        if not isinstance(parsed_sync.payload, HaulDeskAssembly):
            raise TypeError("HaulDeskAdapter requires HaulDeskAssembly.")
        return normalize_hauldesk(
            parsed_sync.payload, tenant.tenant_id, parsed_sync.source_file.path.name
        )


def parse_hauldesk_file(
    source_file: SourceFile, known_carrier_ids: set[int] | None = None
) -> HaulDeskAssembly:
    try:
        sync = HaulDeskSyncDto.model_validate(json.loads(source_file.content, parse_float=Decimal))
    except (json.JSONDecodeError, ValidationError) as error:
        raise InvalidSourceFileError(source_file.path, str(error)) from error
    _require_unique(source_file, "load_num", (load.load_num for load in sync.loads))
    _require_unique(source_file, "carrier_id", (carrier.carrier_id for carrier in sync.carriers))
    _require_unique(source_file, "rate_id", (rate.rate_id for rate in sync.rates))
    carriers = {carrier.carrier_id: carrier for carrier in sync.carriers}
    carriers_by_load_num = {
        load.load_num: None if load.carrier_ref is None else carriers.get(load.carrier_ref)
        for load in sync.loads
    }
    rates: dict[str, list[HaulDeskRateDto]] = {}
    for rate in sync.rates:
        rates.setdefault(rate.load_num, []).append(rate)
    known = known_carrier_ids or set()
    warnings = tuple(
        NormalizationWarning(
            "HAULDESK_UNKNOWN_CARRIER_REF",
            f"carrier_ref {load.carrier_ref} unavailable.",
            field_path=f"loads.{index}.carrier_ref",
        )
        for index, load in enumerate(sync.loads)
        if load.carrier_ref is not None
        and load.carrier_ref not in carriers
        and load.carrier_ref not in known
    )
    return HaulDeskAssembly(
        sync,
        carriers,
        carriers_by_load_num,
        {key: tuple(value) for key, value in rates.items()},
        warnings,
    )


def _require_unique(source_file: SourceFile, field_name: str, values: Iterable[object]) -> None:
    seen: set[object] = set()
    duplicate = next((value for value in values if value in seen or seen.add(value)), None)
    if duplicate is not None:
        message = f"Duplicate HaulDesk {field_name}: {duplicate!r}."
        raise InvalidSourceFileError(source_file.path, message)


def _parse_hauldesk_timestamp(value: object) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("HaulDesk timestamps must be strings.")
    return parse_hauldesk_datetime(value)


def normalize_hauldesk(
    assembly: HaulDeskAssembly, tenant_id: str, source_file_name: str
) -> NormalizedSync:
    """Normalize one assembled sync; ledger rows remain independent source facts."""

    sync_at = assembly.sync.synced_at
    loads: list[CanonicalLoadSnapshot] = []
    customers: list[CanonicalCustomerSnapshot] = []
    for load in assembly.sync.loads:
        identity = CanonicalSourceIdentity(
            tenant_id, SourceSystem.HAULDESK, ExternalId(load.load_num)
        )
        customer = CanonicalCustomerSnapshot(
            CanonicalSourceIdentity(
                tenant_id, SourceSystem.HAULDESK, ExternalId(load.customer_code)
            ),
            load.customer_name,
        )
        source_carrier = assembly.carriers_by_load_num[load.load_num]
        carrier = (
            None
            if source_carrier is None
            else CanonicalCarrierSnapshot(
                CanonicalSourceIdentity(
                    tenant_id,
                    SourceSystem.HAULDESK,
                    ExternalId(str(source_carrier.carrier_id)),
                ),
                source_carrier.carrier_name,
                source_carrier.mc_no,
                source_carrier.dot_no,
                source_carrier.phone,
                source_carrier.home_city,
                source_carrier.home_state,
            )
        )
        stops = (
            CanonicalStop(
                1,
                True,
                False,
                load.pu_city,
                load.pu_state,
                load.pu_zip,
                planned_date=load.pu_date,
                actual_departure_at=load.pu_departed_at,
            ),
            CanonicalStop(
                2,
                False,
                True,
                load.del_city,
                load.del_state,
                load.del_zip,
                planned_date=load.del_date,
                actual_arrival_at=load.del_arrived_at,
            ),
        )
        loads.append(
            CanonicalLoadSnapshot(
                identity,
                map_hauldesk_status(load.status_code),
                customer,
                stops,
                load.entered_at,
                load.updated_at,
                carrier,
                equipment=map_hauldesk_equipment(load.equip),
                weight_lbs=kilograms_to_pounds(load.weight_kg),
                distance_miles=kilometers_to_miles(load.dist_km),
                load_number=load.load_num,
            )
        )
        customers.append(customer)
    entries = tuple(
        SourceFinancialEntry(
            CanonicalSourceIdentity(
                tenant_id, SourceSystem.HAULDESK, ExternalId(str(rate.rate_id))
            ),
            CanonicalSourceIdentity(tenant_id, SourceSystem.HAULDESK, ExternalId(rate.load_num)),
            FinancialSide(rate.side.upper()),
            rate.code,
            Money.from_value(rate.amount_usd),
            rate.created_at,
        )
        for rate in assembly.sync.rates
    )
    return NormalizedSync(
        SyncMetadata(tenant_id, SourceSystem.HAULDESK, source_file_name, sync_at, sync_at),
        tuple(loads),
        tuple(customers),
        source_financial_entries=entries,
        warnings=assembly.warnings,
    )
