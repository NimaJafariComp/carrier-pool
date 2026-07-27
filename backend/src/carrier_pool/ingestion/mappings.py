"""Pure source-to-canonical mappings for one observation at a time.

These functions intentionally do not enforce monotonic status transitions. Later
source corrections may validly report an earlier lifecycle status; persistence and
anomaly-warning behavior belong to later ingestion work.
"""

from collections.abc import Mapping

from carrier_pool.domain.types import (
    EquipmentType,
    LoadStatus,
    SourceSystem,
    UnsupportedSourceStatusError,
)
from carrier_pool.ingestion.base import UnsupportedSourceValueError

FREIGHTFLOW_STATUS_MAP: Mapping[str, LoadStatus] = {
    "Quoting": LoadStatus.PLANNED,
    "Booking": LoadStatus.ACTIVE,
    "Dispatched": LoadStatus.COVERED,
    "At Shipper": LoadStatus.COVERED,
    "En Route": LoadStatus.IN_TRANSIT,
    "At Receiver": LoadStatus.IN_TRANSIT,
    "Delivered": LoadStatus.DELIVERED,
    "Completed": LoadStatus.COMPLETED,
}
FREIGHTFLOW_EQUIPMENT_MAP: Mapping[str, EquipmentType] = {
    "53 ft Van | Dry": EquipmentType.DRY_VAN,
    "53 ft Van | Reefer": EquipmentType.REEFER,
    "48 ft Flatbed": EquipmentType.FLATBED,
}

HAULDESK_STATUS_MAP: Mapping[int, LoadStatus] = {
    10: LoadStatus.PLANNED,
    20: LoadStatus.ACTIVE,
    30: LoadStatus.COVERED,
    40: LoadStatus.IN_TRANSIT,
    50: LoadStatus.DELIVERED,
    90: LoadStatus.COMPLETED,
}
HAULDESK_EQUIPMENT_MAP: Mapping[str, EquipmentType] = {
    "V": EquipmentType.DRY_VAN,
    "R": EquipmentType.REEFER,
    "F": EquipmentType.FLATBED,
}

BROKEROS_STATUS_MAP: Mapping[str, LoadStatus] = {
    "Quotes Requested": LoadStatus.PLANNED,
    "Ready to Book": LoadStatus.ACTIVE,
    "Booked": LoadStatus.COVERED,
    "In Transit": LoadStatus.IN_TRANSIT,
    "Delivered": LoadStatus.DELIVERED,
    "Invoiced": LoadStatus.DELIVERED,
    "Paid": LoadStatus.COMPLETED,
}
BROKEROS_EQUIPMENT_MAP: Mapping[str, EquipmentType] = {
    "Dry Van": EquipmentType.DRY_VAN,
    "Reefer": EquipmentType.REEFER,
    "Flatbed": EquipmentType.FLATBED,
}


def _map_status[StatusSourceValue: (str, int)](
    source_system: SourceSystem,
    source_value: StatusSourceValue,
    mapping: Mapping[StatusSourceValue, LoadStatus],
) -> LoadStatus:
    try:
        return mapping[source_value]
    except KeyError as error:
        raise UnsupportedSourceStatusError(source_system, source_value) from error


def _map_equipment(
    source_system: SourceSystem,
    field_path: str,
    source_value: str,
    mapping: Mapping[str, EquipmentType],
) -> EquipmentType:
    try:
        return mapping[source_value]
    except KeyError as error:
        raise UnsupportedSourceValueError(source_system, field_path, source_value) from error


def map_freightflow_status(source_value: str) -> LoadStatus:
    """Map FreightFlow's documented textual load statuses."""
    return _map_status(SourceSystem.FREIGHTFLOW, source_value, FREIGHTFLOW_STATUS_MAP)


def map_freightflow_equipment(source_value: str) -> EquipmentType:
    """Map FreightFlow's documented free-text equipment values."""
    return _map_equipment(
        SourceSystem.FREIGHTFLOW, "equipment", source_value, FREIGHTFLOW_EQUIPMENT_MAP
    )


def map_hauldesk_status(source_value: int) -> LoadStatus:
    """Map HaulDesk's documented numeric status codes."""
    return _map_status(SourceSystem.HAULDESK, source_value, HAULDESK_STATUS_MAP)


def map_hauldesk_equipment(source_value: str) -> EquipmentType:
    """Map HaulDesk's documented equipment codes."""
    return _map_equipment(SourceSystem.HAULDESK, "equip", source_value, HAULDESK_EQUIPMENT_MAP)


def map_brokeros_status(source_value: str) -> LoadStatus:
    """Map BrokerOS's documented load-status picklist values."""
    return _map_status(SourceSystem.BROKEROS, source_value, BROKEROS_STATUS_MAP)


def map_brokeros_equipment(source_value: str | None) -> EquipmentType:
    """Map BrokerOS equipment, treating its documented null value as unknown."""
    if source_value is None:
        return EquipmentType.UNKNOWN
    return _map_equipment(
        SourceSystem.BROKEROS,
        "bos__Equipment_Type__c",
        source_value,
        BROKEROS_EQUIPMENT_MAP,
    )
