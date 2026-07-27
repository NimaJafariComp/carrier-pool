"""Phase 6.2 scenario lifecycle engine tests."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from carrier_pool.domain.types import EquipmentType, FinancialSide, LoadStatus, Money, SourceSystem
from carrier_pool.generator.lifecycle import LifecycleEngine
from carrier_pool.generator.models import (
    CarrierDefinition,
    CustomerDefinition,
    FinancialEvent,
    GeneratorConfig,
    GeneratorLoad,
    GeneratorTenant,
    LifecycleEvent,
    LocationDefinition,
    ScenarioCatalog,
    ScenarioStop,
    ScheduledSync,
)


def _at(day: int, hour: int) -> datetime:
    return datetime(2026, 7, day, hour, tzinfo=UTC)


def _catalog(*, day11_target: bool = False) -> ScenarioCatalog:
    tenant = GeneratorTenant("ff-broker", "North Star Freight", SourceSystem.FREIGHTFLOW)
    pickup = LocationDefinition("DFW-GP", "Grand Prairie", "TX", "75050")
    delivery = LocationDefinition("HOU-KAT", "Katy", "TX", "77449")
    customer = CustomerDefinition("FF-CUST-101", "ff-broker", "Lone Star Beverages")
    carrier = CarrierDefinition(
        "FF-C-201",
        "ff-broker",
        "Lone Star Van",
        mc_number="1350101",
        dot_number="3901001",
    )
    load = GeneratorLoad(
        logical_id="FF-1001",
        tenant_id="ff-broker",
        source_system=SourceSystem.FREIGHTFLOW,
        customer_id="FF-CUST-101",
        stops=(
            ScenarioStop(1, True, False, "DFW-GP", date(2026, 7, 7)),
            ScenarioStop(2, False, True, "HOU-KAT", date(2026, 7, 8)),
        ),
        equipment=EquipmentType.DRY_VAN,
        day11_target=day11_target,
    )
    return ScenarioCatalog(
        tenants=(tenant,),
        locations=(pickup, delivery),
        customers=(customer,),
        carriers=(carrier,),
        loads=(load,),
    )


def test_lifecycle_accepts_status_regression_and_replacement_totals() -> None:
    catalog = _catalog()
    syncs = (
        ScheduledSync(
            "D1-00",
            "ff-broker",
            SourceSystem.FREIGHTFLOW,
            _at(1, 0),
            (LifecycleEvent("FF-1001", _at(1, 0), status=LoadStatus.ACTIVE),),
        ),
        ScheduledSync(
            "D1-06",
            "ff-broker",
            SourceSystem.FREIGHTFLOW,
            _at(1, 6),
            (
                LifecycleEvent(
                    "FF-1001",
                    _at(1, 6),
                    status=LoadStatus.COVERED,
                    carrier_id="FF-C-201",
                    customer_rate=Money(Decimal("1450")),
                    carrier_rate=Money(Decimal("1180")),
                ),
            ),
        ),
        ScheduledSync(
            "D1-12",
            "ff-broker",
            SourceSystem.FREIGHTFLOW,
            _at(1, 12),
            (
                LifecycleEvent(
                    "FF-1001",
                    _at(1, 12),
                    status=LoadStatus.ACTIVE,
                    correction_reason="source status correction",
                    carrier_rate=Money(Decimal("1200")),
                ),
            ),
        ),
    )

    state = LifecycleEngine(catalog).apply(syncs).loads["FF-1001"]

    assert state.status is LoadStatus.ACTIVE
    assert state.carrier_id == "FF-C-201"
    assert state.customer_rate == Money(Decimal("1450"))
    assert state.carrier_rate == Money(Decimal("1200"))
    assert state.applied_event_ids == ("D1-00:0", "D1-06:0", "D1-12:0")


def test_correction_updates_zip_and_equipment_while_ledger_stays_append_only() -> None:
    catalog = _catalog()
    corrected_stops = (
        ScenarioStop(1, True, False, "DFW-GP", date(2026, 7, 7), postal_code="75050"),
        ScenarioStop(2, False, True, "HOU-KAT", date(2026, 7, 8)),
    )
    syncs = (
        ScheduledSync(
            "D1-00",
            "ff-broker",
            SourceSystem.FREIGHTFLOW,
            _at(1, 0),
            (LifecycleEvent("FF-1001", _at(1, 0), status=LoadStatus.ACTIVE),),
        ),
        ScheduledSync(
            "D1-06",
            "ff-broker",
            SourceSystem.FREIGHTFLOW,
            _at(1, 6),
            (
                LifecycleEvent(
                    "FF-1001",
                    _at(1, 6),
                    equipment=EquipmentType.REEFER,
                    stops=corrected_stops,
                    correction_reason="equipment and pickup ZIP correction",
                ),
            ),
        ),
        ScheduledSync(
            "D1-12",
            "ff-broker",
            SourceSystem.FREIGHTFLOW,
            _at(1, 12),
            (
                FinancialEvent(
                    "FF-1001",
                    _at(1, 12),
                    entry_id="rate-1",
                    side=FinancialSide.PAY,
                    code="FUEL",
                    amount=Money(Decimal("75")),
                ),
            ),
        ),
    )

    state = LifecycleEngine(catalog).apply(syncs).loads["FF-1001"]

    assert state.equipment is EquipmentType.REEFER
    assert state.stops[0].postal_code == "75050"
    assert state.financial_entries[0].entry_id == "rate-1"
    assert state.financial_entries[0].amount == Money(Decimal("75"))


def test_engine_is_deterministic_and_rejects_day11_target_progression() -> None:
    catalog = _catalog(day11_target=True)
    active_sync = ScheduledSync(
        "D11-06",
        "ff-broker",
        SourceSystem.FREIGHTFLOW,
        _at(11, 6),
        (LifecycleEvent("FF-1001", _at(11, 6), status=LoadStatus.ACTIVE),),
    )

    first = LifecycleEngine(catalog, GeneratorConfig(seed=42)).apply((active_sync,))
    second = LifecycleEngine(catalog, GeneratorConfig(seed=42)).apply((active_sync,))

    assert first == second
    assert GeneratorConfig(seed=42).minor_variation("background-1", 1, 9) == GeneratorConfig(
        seed=42
    ).minor_variation("background-1", 1, 9)
    with pytest.raises(ValueError, match="Day 11 target"):
        LifecycleEngine(catalog).apply(
            (
                active_sync,
                ScheduledSync(
                    "D12-00",
                    "ff-broker",
                    SourceSystem.FREIGHTFLOW,
                    _at(12, 0),
                    (LifecycleEvent("FF-1001", _at(12, 0), status=LoadStatus.COVERED),),
                ),
            )
        )
