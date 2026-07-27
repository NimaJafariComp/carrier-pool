"""Shared database-free canonical contract for every supported source adapter."""

import json
from datetime import UTC
from decimal import Decimal
from pathlib import Path
from typing import Protocol

import pytest

from carrier_pool.domain.models import NormalizedSync
from carrier_pool.domain.types import EquipmentType, LoadStatus, Money, SourceSystem
from carrier_pool.ingestion.base import ParsedSync, SourceFile, TenantContext
from carrier_pool.ingestion.brokeros import BrokerOSAdapter
from carrier_pool.ingestion.freightflow import FreightFlowAdapter
from carrier_pool.ingestion.hauldesk import HaulDeskAdapter


class AdapterFactory(Protocol):
    def __call__(self) -> FreightFlowAdapter | HaulDeskAdapter | BrokerOSAdapter: ...


def _freightflow_source() -> SourceFile:
    return SourceFile(
        Path("freightflow.json"),
        json.dumps(
            {
                "syncedAt": "2026-07-06T06:00:00-05:00",
                "loads": [
                    {
                        "shipmentId": "FF-1",
                        "status": "Booking",
                        "mileage": 100,
                        "totalSell": 1200,
                        "totalBuy": None,
                        "customer": {"customerId": "C-1", "name": "Customer"},
                        "carrier": None,
                        "equipment": "53 ft Van | Dry",
                        "weightTotal": 1000,
                        "stops": [
                            {
                                "stopType": "First Pickup",
                                "city": "Dallas",
                                "state": "TX",
                                "zipCode": "75201",
                                "estimatedReadyDateTime": "2026-07-07T08:00:00-05:00",
                                "estimatedCloseDateTime": "2026-07-07T16:00:00-05:00",
                                "actualDepartureDateTime": None,
                            }
                        ],
                        "createdDate": "2026-07-06T04:00:00-05:00",
                        "lastModifiedDate": "2026-07-06T04:00:00-05:00",
                    }
                ],
            }
        ).encode(),
    )


def _hauldesk_source() -> SourceFile:
    return SourceFile(
        Path("hauldesk.json"),
        json.dumps(
            {
                "synced_at": "2026-07-06 06:00:00",
                "loads": [
                    {
                        "load_num": "HD-1",
                        "status_code": 20,
                        "customer_code": "C-1",
                        "customer_name": "Customer",
                        "carrier_ref": 9,
                        "equip": "V",
                        "weight_kg": 1000,
                        "dist_km": 100,
                        "pu_city": "Dallas",
                        "pu_state": "TX",
                        "pu_zip": "75201",
                        "pu_date": "2026-07-07",
                        "pu_departed_at": None,
                        "del_city": "Austin",
                        "del_state": "TX",
                        "del_zip": "78701",
                        "del_date": "2026-07-08",
                        "del_arrived_at": None,
                        "entered_at": "2026-07-05 14:00:00",
                        "updated_at": "2026-07-06 03:00:00",
                    }
                ],
                "carriers": [],
                "rates": [
                    {
                        "rate_id": 1,
                        "load_num": "HD-1",
                        "side": "pay",
                        "code": "LINEHAUL",
                        "amount_usd": 1000,
                        "created_at": "2026-07-06 03:00:00",
                    }
                ],
            }
        ).encode(),
    )


def _brokeros_source() -> SourceFile:
    return SourceFile(
        Path("brokeros.json"),
        json.dumps(
            {
                "synced_at": "2026-07-06T11:00:00.000+0000",
                "records": [
                    {
                        "Id": "a0jO900000YgsYJIAZ",
                        "Name": "BOS-1",
                        "bos__Load_Status__c": "Ready to Book",
                        "bos__Distance_Miles__c": 100,
                        "bos__Customer__c": "0011I00000NMUrPQAX",
                        "bos__Carrier__c": None,
                        "bos__Equipment_Type__c": None,
                        "bos__Customer_Rate__c": 1200,
                        "bos__Carrier_Rate__c": None,
                        "bos__Stops__r": [
                            {
                                "bos__Number__c": 1,
                                "bos__Is_Pickup__c": True,
                                "bos__Is_Dropoff__c": False,
                                "bos__Location__c": "0011I00000HAeJnQAL",
                                "bos__Scheduled_Date__c": "2026-07-07",
                                "bos__Arrival_Time__c": None,
                            }
                        ],
                        "bos__Line_Items__r": [
                            {
                                "bos__Commodity__c": "Food",
                                "bos__Weight__c": 1000,
                                "bos__Weight_Units__c": "lbs",
                                "bos__Pallet_Count__c": 1,
                            }
                        ],
                        "CreatedDate": "2026-07-06T09:40:02.000+0000",
                        "LastModifiedDate": "2026-07-06T09:40:02.000+0000",
                    }
                ],
                "referenced_records": {
                    "0011I00000HAeJnQAL": {
                        "type": "Location",
                        "Name": "Facility",
                        "bos__City__c": "Dallas",
                        "bos__State__c": "TX",
                        "bos__Postal_Code__c": "75201",
                    },
                    "0011I00000NMUrPQAX": {
                        "type": "Account",
                        "record_type": "Customer",
                        "Name": "Customer",
                    },
                },
            }
        ).encode(),
    )


@pytest.mark.parametrize(
    ("adapter_factory", "source_factory", "source_system"),
    [
        (FreightFlowAdapter, _freightflow_source, SourceSystem.FREIGHTFLOW),
        (HaulDeskAdapter, _hauldesk_source, SourceSystem.HAULDESK),
        (BrokerOSAdapter, _brokeros_source, SourceSystem.BROKEROS),
    ],
)
def test_source_adapters_return_database_free_canonical_contract(
    adapter_factory: AdapterFactory,
    source_factory: callable,
    source_system: SourceSystem,
) -> None:
    tenant = TenantContext("tenant-contract")
    adapter = adapter_factory()
    parsed: ParsedSync = adapter.parse_file(source_factory(), tenant)
    normalized: NormalizedSync = adapter.normalize(parsed, tenant)
    load = normalized.loads[0]
    money = (
        load.customer_rate
        or load.carrier_rate
        or normalized.source_financial_entries[0].amount
    )

    assert normalized.metadata.tenant_id == tenant.tenant_id
    assert normalized.metadata.source_system is source_system
    assert load.identity.tenant_id == tenant.tenant_id
    assert load.identity.source_system is source_system
    assert normalized.metadata.sync_at.tzinfo is UTC
    assert load.source_created_at.tzinfo is UTC
    assert isinstance(money, Money)
    assert isinstance(money.amount, Decimal)
    assert isinstance(load.weight_lbs, Decimal)
    assert isinstance(load.distance_miles, Decimal)
    sequences = tuple(stop.sequence for stop in load.stops)
    assert sequences == tuple(sorted(sequences))
    assert isinstance(load.status, LoadStatus)
    assert isinstance(load.equipment, EquipmentType)
    assert isinstance(normalized.warnings, tuple)
    assert all(warning.code and warning.message for warning in normalized.warnings)


def test_contract_exercises_structured_source_warning_without_database_side_effects() -> None:
    adapter = HaulDeskAdapter()
    tenant = TenantContext("tenant-contract")
    normalized = adapter.normalize(adapter.parse_file(_hauldesk_source(), tenant), tenant)

    assert normalized.warnings[0].code == "HAULDESK_UNKNOWN_CARRIER_REF"
    assert normalized.warnings[0].field_path == "loads.0.carrier_ref"
