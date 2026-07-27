import pytest

from carrier_pool.domain.types import EquipmentType, LoadStatus, UnsupportedSourceStatusError
from carrier_pool.ingestion.base import UnsupportedSourceValueError
from carrier_pool.ingestion.mappings import (
    map_brokeros_equipment,
    map_brokeros_status,
    map_freightflow_equipment,
    map_freightflow_status,
    map_hauldesk_equipment,
    map_hauldesk_status,
)


@pytest.mark.parametrize(
    ("source_value", "expected"),
    [
        ("Quoting", LoadStatus.PLANNED),
        ("Booking", LoadStatus.ACTIVE),
        ("Dispatched", LoadStatus.COVERED),
        ("At Shipper", LoadStatus.COVERED),
        ("En Route", LoadStatus.IN_TRANSIT),
        ("At Receiver", LoadStatus.IN_TRANSIT),
        ("Delivered", LoadStatus.DELIVERED),
        ("Completed", LoadStatus.COMPLETED),
    ],
)
def test_freightflow_status_mapping(source_value: str, expected: LoadStatus) -> None:
    assert map_freightflow_status(source_value) is expected


@pytest.mark.parametrize(
    ("source_value", "expected"),
    [
        ("53 ft Van | Dry", EquipmentType.DRY_VAN),
        ("53 ft Van | Reefer", EquipmentType.REEFER),
        ("48 ft Flatbed", EquipmentType.FLATBED),
    ],
)
def test_freightflow_equipment_mapping(source_value: str, expected: EquipmentType) -> None:
    assert map_freightflow_equipment(source_value) is expected


@pytest.mark.parametrize(
    ("source_value", "expected"),
    [
        (10, LoadStatus.PLANNED),
        (20, LoadStatus.ACTIVE),
        (30, LoadStatus.COVERED),
        (40, LoadStatus.IN_TRANSIT),
        (50, LoadStatus.DELIVERED),
        (90, LoadStatus.COMPLETED),
    ],
)
def test_hauldesk_status_mapping(source_value: int, expected: LoadStatus) -> None:
    assert map_hauldesk_status(source_value) is expected


@pytest.mark.parametrize(
    ("source_value", "expected"),
    [("V", EquipmentType.DRY_VAN), ("R", EquipmentType.REEFER), ("F", EquipmentType.FLATBED)],
)
def test_hauldesk_equipment_mapping(source_value: str, expected: EquipmentType) -> None:
    assert map_hauldesk_equipment(source_value) is expected


@pytest.mark.parametrize(
    ("source_value", "expected"),
    [
        ("Quotes Requested", LoadStatus.PLANNED),
        ("Ready to Book", LoadStatus.ACTIVE),
        ("Booked", LoadStatus.COVERED),
        ("In Transit", LoadStatus.IN_TRANSIT),
        ("Delivered", LoadStatus.DELIVERED),
        ("Invoiced", LoadStatus.DELIVERED),
        ("Paid", LoadStatus.COMPLETED),
    ],
)
def test_brokeros_status_mapping(source_value: str, expected: LoadStatus) -> None:
    assert map_brokeros_status(source_value) is expected


@pytest.mark.parametrize(
    ("source_value", "expected"),
    [
        ("Dry Van", EquipmentType.DRY_VAN),
        ("Reefer", EquipmentType.REEFER),
        ("Flatbed", EquipmentType.FLATBED),
        (None, EquipmentType.UNKNOWN),
    ],
)
def test_brokeros_equipment_mapping(source_value: str | None, expected: EquipmentType) -> None:
    assert map_brokeros_equipment(source_value) is expected


@pytest.mark.parametrize(
    ("mapper", "source_value", "expected_source"),
    [
        (map_freightflow_status, "Cancelled", "FREIGHTFLOW"),
        (map_hauldesk_status, 999, "HAULDESK"),
        (map_brokeros_status, "Cancelled", "BROKEROS"),
    ],
)
def test_unknown_statuses_raise_source_specific_errors(
    mapper: object, source_value: object, expected_source: str
) -> None:
    with pytest.raises(UnsupportedSourceStatusError, match=expected_source):
        mapper(source_value)  # type: ignore[operator]


@pytest.mark.parametrize(
    ("mapper", "source_value", "expected_source"),
    [
        (map_freightflow_equipment, "Box Truck", "FREIGHTFLOW"),
        (map_hauldesk_equipment, "X", "HAULDESK"),
        (map_brokeros_equipment, "Tanker", "BROKEROS"),
    ],
)
def test_unknown_equipment_raises_source_specific_errors(
    mapper: object, source_value: str, expected_source: str
) -> None:
    with pytest.raises(UnsupportedSourceValueError, match=expected_source):
        mapper(source_value)  # type: ignore[operator]
