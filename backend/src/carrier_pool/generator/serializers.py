"""Source-specific plain-JSON serializers for scheduled scenario state."""

from datetime import date, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from zoneinfo import ZoneInfo

from carrier_pool.domain.types import EquipmentType, LoadStatus, SourceSystem
from carrier_pool.generator.lifecycle import LifecycleResult, LoadLifecycleState
from carrier_pool.generator.models import (
    FinancialEvent,
    GeneratorLoad,
    ScenarioCatalog,
    ScheduledSync,
)

_CENTRAL = ZoneInfo("America/Chicago")

_FREIGHTFLOW_STATUS = {
    LoadStatus.PLANNED: "Quoting",
    LoadStatus.ACTIVE: "Booking",
    LoadStatus.COVERED: "Dispatched",
    LoadStatus.IN_TRANSIT: "En Route",
    LoadStatus.DELIVERED: "Delivered",
    LoadStatus.COMPLETED: "Completed",
}
_HAULDESK_STATUS = {
    LoadStatus.PLANNED: 10,
    LoadStatus.ACTIVE: 20,
    LoadStatus.COVERED: 30,
    LoadStatus.IN_TRANSIT: 40,
    LoadStatus.DELIVERED: 50,
    LoadStatus.COMPLETED: 90,
}
_BROKEROS_STATUS = {
    LoadStatus.PLANNED: "Quotes Requested",
    LoadStatus.ACTIVE: "Ready to Book",
    LoadStatus.COVERED: "Booked",
    LoadStatus.IN_TRANSIT: "In Transit",
    LoadStatus.DELIVERED: "Delivered",
    LoadStatus.COMPLETED: "Paid",
}


def serialize_sync(
    catalog: ScenarioCatalog, sync: ScheduledSync, state: LifecycleResult
) -> dict[str, object]:
    """Serialize one post-reduction sync without writing a file."""
    changed_load_ids = tuple(dict.fromkeys(event.load_id for event in sync.events))
    load_states = tuple(
        (catalog.load(load_id), state.loads[load_id]) for load_id in changed_load_ids
    )
    if sync.source_system is SourceSystem.FREIGHTFLOW:
        return _serialize_freightflow(catalog, sync, load_states)
    if sync.source_system is SourceSystem.HAULDESK:
        return _serialize_hauldesk(catalog, sync, load_states)
    if sync.source_system is SourceSystem.BROKEROS:
        return _serialize_brokeros(catalog, sync, load_states)
    raise ValueError(f"Unsupported source system: {sync.source_system}")


def _serialize_freightflow(
    catalog: ScenarioCatalog,
    sync: ScheduledSync,
    load_states: tuple[tuple[GeneratorLoad, LoadLifecycleState], ...],
) -> dict[str, object]:
    return {
        "syncedAt": _offset_timestamp(sync.sync_at),
        "loads": [_freightflow_load(catalog, sync, load, state) for load, state in load_states],
    }


def _freightflow_load(
    catalog: ScenarioCatalog,
    sync: ScheduledSync,
    load: GeneratorLoad,
    state: LoadLifecycleState,
) -> dict[str, object]:
    customer = _customer(catalog, load.customer_id)
    carrier = None if state.carrier_id is None else _carrier(catalog, state.carrier_id)
    customer_rate = state.customer_rate.amount if state.customer_rate else Decimal("1450")
    return {
        "shipmentId": _numeric_id("ff-load", load.logical_id),
        "status": _FREIGHTFLOW_STATUS[_required_status(state)],
        "mileage": _number(_distance_miles(load)),
        "totalSell": _number(customer_rate),
        "totalBuy": None if state.carrier_rate is None else _number(state.carrier_rate.amount),
        "customer": {
            "customerId": _numeric_id("ff-customer", customer.customer_id),
            "name": customer.name,
        },
        "carrier": None
        if carrier is None
        else {
            "carrierMasterId": _numeric_id("ff-carrier", carrier.carrier_id),
            "name": carrier.name,
            "mcNumber": carrier.mc_number,
            "dotNumber": carrier.dot_number,
            "phoneNumber": "+12145550101",
        },
        "equipment": _freightflow_equipment(state.equipment),
        "weightTotal": _number(_weight_lbs(load)),
        "stops": [
            {
                "stopType": "First Pickup" if stop.is_pickup else "Last Drop",
                "city": catalog.location(stop.location_id).city.upper(),
                "state": catalog.location(stop.location_id).state,
                "zipCode": stop.postal_code,
                "estimatedReadyDateTime": _offset_timestamp(_stop_start(stop.planned_date)),
                "estimatedCloseDateTime": _offset_timestamp(
                    _stop_start(stop.planned_date) + timedelta(hours=8)
                ),
                "actualDepartureDateTime": None,
            }
            for stop in state.stops
        ],
        "createdDate": _offset_timestamp(sync.sync_at),
        "lastModifiedDate": _offset_timestamp(sync.sync_at),
    }


def _serialize_hauldesk(
    catalog: ScenarioCatalog,
    sync: ScheduledSync,
    load_states: tuple[tuple[GeneratorLoad, LoadLifecycleState], ...],
) -> dict[str, object]:
    financial_events = tuple(event for event in sync.events if isinstance(event, FinancialEvent))
    return {
        "synced_at": _hauldesk_timestamp(sync.sync_at),
        "loads": [_hauldesk_load(catalog, sync, load, state) for load, state in load_states],
        "carriers": _hauldesk_carriers(catalog, load_states),
        "rates": [_hauldesk_rate(event) for event in financial_events],
    }


def _hauldesk_load(
    catalog: ScenarioCatalog,
    sync: ScheduledSync,
    load: GeneratorLoad,
    state: LoadLifecycleState,
) -> dict[str, object]:
    customer = _customer(catalog, load.customer_id)
    pickup, delivery = state.stops[0], state.stops[-1]
    pickup_location = catalog.location(pickup.location_id)
    delivery_location = catalog.location(delivery.location_id)
    return {
        "load_num": load.logical_id,
        "status_code": _HAULDESK_STATUS[_required_status(state)],
        "customer_code": customer.customer_id,
        "customer_name": customer.name,
        "carrier_ref": None
        if state.carrier_id is None
        else _numeric_id("hd-carrier", state.carrier_id),
        "equip": _hauldesk_equipment(state.equipment),
        "weight_kg": _number(_weight_lbs(load) / Decimal("2.2046226218")),
        "dist_km": _number(_distance_miles(load) / Decimal("0.6213711922")),
        "pu_city": pickup_location.city,
        "pu_state": pickup_location.state,
        "pu_zip": pickup.postal_code,
        "pu_date": pickup.planned_date.isoformat(),
        "pu_departed_at": None,
        "del_city": delivery_location.city,
        "del_state": delivery_location.state,
        "del_zip": delivery.postal_code,
        "del_date": delivery.planned_date.isoformat(),
        "del_arrived_at": None,
        "entered_at": _hauldesk_timestamp(sync.sync_at),
        "updated_at": _hauldesk_timestamp(sync.sync_at),
    }


def _hauldesk_carriers(
    catalog: ScenarioCatalog, load_states: tuple[tuple[GeneratorLoad, LoadLifecycleState], ...]
) -> list[dict[str, object]]:
    carrier_ids = tuple(
        dict.fromkeys(state.carrier_id for _, state in load_states if state.carrier_id is not None)
    )
    return [
        {
            "carrier_id": _numeric_id("hd-carrier", carrier_id),
            "carrier_name": carrier.name,
            "mc_no": carrier.mc_number,
            "dot_no": carrier.dot_number,
            "home_city": "Dallas",
            "home_state": "TX",
            "phone": "(214) 555-0101",
        }
        for carrier_id in carrier_ids
        for carrier in (catalog.carrier(carrier_id),)
    ]


def _hauldesk_rate(event: FinancialEvent) -> dict[str, object]:
    return {
        "rate_id": _numeric_id("hd-rate", event.entry_id),
        "load_num": event.load_id,
        "side": event.side.value.lower(),
        "code": event.code,
        "amount_usd": _number(event.amount.amount),
        "created_at": _hauldesk_timestamp(event.occurred_at),
    }


def _serialize_brokeros(
    catalog: ScenarioCatalog,
    sync: ScheduledSync,
    load_states: tuple[tuple[GeneratorLoad, LoadLifecycleState], ...],
) -> dict[str, object]:
    records = [_brokeros_load(catalog, sync, load, state) for load, state in load_states]
    return {
        "synced_at": _brokeros_timestamp(sync.sync_at),
        "records": records,
        "referenced_records": _brokeros_references(catalog, load_states),
    }


def _brokeros_load(
    catalog: ScenarioCatalog,
    sync: ScheduledSync,
    load: GeneratorLoad,
    state: LoadLifecycleState,
) -> dict[str, object]:
    customer = _customer(catalog, load.customer_id)
    return {
        "Id": _brokeros_id("a0j", load.logical_id),
        "Name": load.logical_id,
        "bos__Load_Status__c": _BROKEROS_STATUS[_required_status(state)],
        "bos__Distance_Miles__c": _number(_distance_miles(load)),
        "bos__Customer__c": _brokeros_id("001", customer.customer_id),
        "bos__Carrier__c": None
        if state.carrier_id is None
        else _brokeros_id("001", state.carrier_id),
        "bos__Equipment_Type__c": _brokeros_equipment(state.equipment),
        "bos__Customer_Rate__c": None
        if state.customer_rate is None
        else _number(state.customer_rate.amount),
        "bos__Carrier_Rate__c": None
        if state.carrier_rate is None
        else _number(state.carrier_rate.amount),
        "bos__Stops__r": [
            {
                "bos__Number__c": stop.sequence,
                "bos__Is_Pickup__c": stop.is_pickup,
                "bos__Is_Dropoff__c": stop.is_dropoff,
                "bos__Location__c": _brokeros_id("001", stop.location_id),
                "bos__Scheduled_Date__c": stop.planned_date.isoformat(),
                "bos__Arrival_Time__c": None,
            }
            for stop in state.stops
        ],
        "bos__Line_Items__r": [
            {
                "bos__Commodity__c": "General freight",
                "bos__Weight__c": _number(_weight_lbs(load)),
                "bos__Weight_Units__c": "lbs",
                "bos__Pallet_Count__c": 18,
            }
        ],
        "CreatedDate": _brokeros_timestamp(sync.sync_at),
        "LastModifiedDate": _brokeros_timestamp(sync.sync_at),
    }


def _brokeros_references(
    catalog: ScenarioCatalog, load_states: tuple[tuple[GeneratorLoad, LoadLifecycleState], ...]
) -> dict[str, dict[str, object]]:
    references: dict[str, dict[str, object]] = {}
    for load, state in load_states:
        customer = _customer(catalog, load.customer_id)
        references[_brokeros_id("001", customer.customer_id)] = {
            "type": "Account",
            "record_type": "Customer",
            "Name": customer.name,
        }
        if state.carrier_id is not None:
            carrier = catalog.carrier(state.carrier_id)
            references[_brokeros_id("001", carrier.carrier_id)] = {
                "type": "Account",
                "record_type": "Carrier",
                "Name": carrier.name,
            }
        for stop in state.stops:
            location = catalog.location(stop.location_id)
            references[_brokeros_id("001", stop.location_id)] = {
                "type": "Location",
                "Name": f"{location.city} Facility",
                "bos__City__c": location.city,
                "bos__State__c": location.state,
                "bos__Postal_Code__c": stop.postal_code,
            }
    return references


def _customer(catalog: ScenarioCatalog, customer_id: str):
    return next(customer for customer in catalog.customers if customer.customer_id == customer_id)


def _carrier(catalog: ScenarioCatalog, carrier_id: str):
    return catalog.carrier(carrier_id)


def _required_status(state: LoadLifecycleState) -> LoadStatus:
    if state.status is None:
        raise ValueError(f"load {state.logical_id} has no lifecycle status.")
    return state.status


def _weight_lbs(load: GeneratorLoad) -> Decimal:
    del load
    return Decimal("24000")


def _distance_miles(load: GeneratorLoad) -> Decimal:
    del load
    return Decimal("242.1")


def _numeric_id(namespace: str, logical_id: str) -> int:
    return 100000 + int(sha256(f"{namespace}:{logical_id}".encode()).hexdigest()[:8], 16) % 899999


def _brokeros_id(prefix: str, logical_id: str) -> str:
    return prefix + sha256(f"{prefix}:{logical_id}".encode()).hexdigest()[:15].upper()


def _number(value: Decimal) -> int | float:
    return int(value) if value == value.to_integral_value() else float(value)


def _offset_timestamp(value: datetime) -> str:
    return value.astimezone(_CENTRAL).isoformat()


def _hauldesk_timestamp(value: datetime) -> str:
    return value.astimezone(_CENTRAL).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")


def _brokeros_timestamp(value: datetime) -> str:
    return value.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%S.000+0000")


def _stop_start(planned_date: date) -> datetime:
    return datetime.combine(planned_date, datetime.min.time(), tzinfo=_CENTRAL).replace(hour=8)


def _freightflow_equipment(equipment: EquipmentType) -> str:
    mapping = {
        EquipmentType.DRY_VAN: "53 ft Van | Dry",
        EquipmentType.REEFER: "53 ft Van | Reefer",
        EquipmentType.FLATBED: "48 ft Flatbed",
    }
    try:
        return mapping[equipment]
    except KeyError as error:
        raise ValueError("FreightFlow cannot serialize UNKNOWN equipment.") from error


def _hauldesk_equipment(equipment: EquipmentType) -> str:
    mapping = {
        EquipmentType.DRY_VAN: "V",
        EquipmentType.REEFER: "R",
        EquipmentType.FLATBED: "F",
    }
    try:
        return mapping[equipment]
    except KeyError as error:
        raise ValueError("HaulDesk cannot serialize UNKNOWN equipment.") from error


def _brokeros_equipment(equipment: EquipmentType) -> str | None:
    return {
        EquipmentType.DRY_VAN: "Dry Van",
        EquipmentType.REEFER: "Reefer",
        EquipmentType.FLATBED: "Flatbed",
        EquipmentType.UNKNOWN: None,
    }[equipment]
