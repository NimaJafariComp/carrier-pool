"""Phase 6.3 deterministic catalog and integrity tests."""

from dataclasses import replace

import pytest

from carrier_pool.domain.types import EquipmentType, SourceSystem
from carrier_pool.generator.catalog import CarrierHistoryProfile, build_catalog
from carrier_pool.generator.models import ScenarioCatalog


def test_catalog_is_deterministic_and_has_three_source_bound_tenants() -> None:
    first = build_catalog()
    second = build_catalog()

    assert first == second
    assert {(tenant.tenant_id, tenant.source_system) for tenant in first.tenants} == {
        ("ff-broker", SourceSystem.FREIGHTFLOW),
        ("hd-broker", SourceSystem.HAULDESK),
        ("bo-broker", SourceSystem.BROKEROS),
    }
    assert len(first.locations) >= 15
    assert len({location.postal_code for location in first.locations}) == len(first.locations)


def test_catalog_has_eight_carriers_per_tenant_and_required_history_profiles() -> None:
    catalog = build_catalog()
    per_tenant = {
        tenant.tenant_id: [
            carrier for carrier in catalog.carriers if carrier.tenant_id == tenant.tenant_id
        ]
        for tenant in catalog.tenants
    }

    assert {tenant_id: len(carriers) for tenant_id, carriers in per_tenant.items()} == {
        "ff-broker": 8,
        "hd-broker": 8,
        "bo-broker": 8,
    }
    ff_profiles = {carrier.history_profile for carrier in per_tenant["ff-broker"]}
    assert {
        CarrierHistoryProfile.RICH_LANE,
        CarrierHistoryProfile.LOW_HISTORY,
        CarrierHistoryProfile.BROAD_EQUIPMENT_POOR_LANE,
        CarrierHistoryProfile.RECENT_DELIVERY,
        CarrierHistoryProfile.STALE_DELIVERY,
    } <= ff_profiles
    assert all(carrier.equipment_history for carrier in catalog.carriers)
    assert all(carrier.mc_number and carrier.dot_number for carrier in catalog.carriers)


def test_shared_authority_is_intentionally_tenant_local() -> None:
    catalog = build_catalog()
    shared = [carrier for carrier in catalog.carriers if carrier.mc_number == "1350101"]

    assert {(carrier.carrier_id, carrier.tenant_id) for carrier in shared} == {
        ("FF-C-206", "ff-broker"),
        ("HD-C-206", "hd-broker"),
    }
    assert {carrier.dot_number for carrier in shared} == {"3901001"}


def test_catalog_day11_targets_cover_exact_neighbor_and_sparse_cases() -> None:
    catalog = build_catalog()
    targets = {load.logical_id: load for load in catalog.loads if load.day11_target}

    assert set(targets) == {"FF-9001", "BO-9001", "HD-9001"}
    assert targets["FF-9001"].equipment is EquipmentType.DRY_VAN
    assert [stop.location_id for stop in targets["BO-9001"].stops] == ["HOU-KAT", "SAT-SAT"]
    assert [stop.location_id for stop in targets["HD-9001"].stops] == ["DFW-PLN", "HOU-BAY"]


def test_catalog_rejects_duplicate_locations_and_cross_tenant_customer_reference() -> None:
    catalog = build_catalog()

    with pytest.raises(ValueError, match="duplicate location ID"):
        ScenarioCatalog(
            tenants=catalog.tenants,
            locations=(*catalog.locations, catalog.locations[0]),
            customers=catalog.customers,
            carriers=catalog.carriers,
            loads=catalog.loads,
        )

    invalid_load = replace(catalog.loads[0], customer_id="BO-CUST-501")
    with pytest.raises(ValueError, match="customer must belong"):
        ScenarioCatalog(
            tenants=catalog.tenants,
            locations=catalog.locations,
            customers=catalog.customers,
            carriers=catalog.carriers,
            loads=(invalid_load, *catalog.loads[1:]),
        )
