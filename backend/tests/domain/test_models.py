from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest

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
from carrier_pool.domain.types import EquipmentType, LoadStatus, Money, SourceSystem


def _utc_datetime(hour: int) -> datetime:
    return datetime(2026, 7, 6, hour, tzinfo=UTC)


def _identity(external_id: str | int) -> CanonicalSourceIdentity:
    return CanonicalSourceIdentity(
        tenant_id="freightflow-demo",
        source_system=SourceSystem.FREIGHTFLOW,
        external_id=external_id,
    )


def _customer() -> CanonicalCustomerSnapshot:
    return CanonicalCustomerSnapshot(identity=_identity(889264), name="Lone Star Beverages")


def _stop(sequence: int, *, is_pickup: bool, is_dropoff: bool) -> CanonicalStop:
    return CanonicalStop(
        sequence=sequence,
        is_pickup=is_pickup,
        is_dropoff=is_dropoff,
        city="Grand Prairie" if is_pickup else "Katy",
        state="TX",
        postal_code="75050" if is_pickup else "77449",
        scheduled_start_at=_utc_datetime(8),
        scheduled_end_at=_utc_datetime(16),
    )


def test_normal_load_snapshot_preserves_canonical_fields() -> None:
    carrier = CanonicalCarrierSnapshot(
        identity=_identity(835692),
        name="Ibrahim Transport Inc",
        mc_number="1346382",
    )
    load = CanonicalLoadSnapshot(
        identity=_identity(127472397),
        status=LoadStatus.COVERED,
        customer=_customer(),
        carrier=carrier,
        equipment=EquipmentType.DRY_VAN,
        customer_rate=Money.from_value("1450.00"),
        carrier_rate=Money.from_value("1180.00"),
        weight_lbs=Decimal("24000"),
        distance_miles=Decimal("242.1"),
        stops=(
            _stop(1, is_pickup=True, is_dropoff=False),
            _stop(2, is_pickup=False, is_dropoff=True),
        ),
        source_created_at=_utc_datetime(4),
        source_modified_at=_utc_datetime(10),
    )

    assert load.identity.external_id == "127472397"
    assert load.carrier_rate == Money.from_value("1180.00")
    assert load.stops[0].is_pickup is True
    assert load.stops[1].is_dropoff is True


def test_normalized_sync_preserves_ordered_multi_stop_load_and_independent_flags() -> None:
    stops = (
        _stop(1, is_pickup=True, is_dropoff=False),
        _stop(2, is_pickup=True, is_dropoff=True),
        _stop(3, is_pickup=False, is_dropoff=True),
    )
    load = CanonicalLoadSnapshot(
        identity=_identity("a0jO900000YgsYJIAZ"),
        status=LoadStatus.ACTIVE,
        customer=_customer(),
        stops=stops,
        source_created_at=_utc_datetime(4),
        source_modified_at=_utc_datetime(4),
    )
    metadata = SyncMetadata(
        tenant_id="freightflow-demo",
        source_system=SourceSystem.FREIGHTFLOW,
        source_file_name="2026-07-06T06-00_sync.json",
        sync_at=_utc_datetime(6),
        observed_at=_utc_datetime(6),
    )
    normalized_sync = NormalizedSync(
        metadata=metadata,
        loads=(load,),
        customers=(_customer(),),
        warnings=(
            NormalizationWarning(code="MISSING_PHONE", message="Carrier phone unavailable."),
        ),
    )

    assert [stop.sequence for stop in normalized_sync.loads[0].stops] == [1, 2, 3]
    assert normalized_sync.loads[0].stops[1].is_pickup is True
    assert normalized_sync.loads[0].stops[1].is_dropoff is True


def test_unknown_equipment_remains_distinct_from_dry_van() -> None:
    load = CanonicalLoadSnapshot(
        identity=_identity("SHP6743062"),
        status=LoadStatus.ACTIVE,
        customer=_customer(),
        equipment=EquipmentType.UNKNOWN,
        stops=(_stop(1, is_pickup=True, is_dropoff=False),),
        source_created_at=_utc_datetime(4),
        source_modified_at=_utc_datetime(4),
    )

    assert load.equipment is EquipmentType.UNKNOWN
    assert load.equipment is not EquipmentType.DRY_VAN


@pytest.mark.parametrize(
    "invalid_time",
    [
        datetime(2026, 7, 6, 8),
        datetime(2026, 7, 6, 8, tzinfo=timezone(timedelta(hours=-5))),
    ],
)
def test_canonical_models_reject_non_utc_timestamps(invalid_time: datetime) -> None:
    with pytest.raises(ValueError, match="timezone-aware UTC|UTC timezone"):
        CanonicalStop(
            sequence=1,
            is_pickup=True,
            is_dropoff=False,
            city="Grand Prairie",
            state="TX",
            postal_code="75050",
            scheduled_start_at=invalid_time,
        )
