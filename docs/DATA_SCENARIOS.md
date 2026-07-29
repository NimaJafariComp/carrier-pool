# Deterministic Data Scenarios

## Scope and invariants

The generator will create plain JSON sync files for 2026-07-01 through
2026-07-10, plus one Day 11 active-load sync per tenant at 06:00 on
2026-07-11. Every historical source directory receives files at 00:00, 06:00,
12:00, and 18:00. Each file contains one to three created or changed loads.
Schema examples remain JSONC documentation and are never overwritten.

All source files below are relative to `data/`. `D<n>` means
`2026-07-<nn>`. Important events are hand-authored. The canonical catalog and
generated `data/scenarios.json` are authoritative for exact event-file
membership; the anchor schedule below supersedes illustrative per-row date grids
if they differ. Deterministic background loads only fill otherwise-empty files
and use the same catalog, lifecycle, and one-to-three-load rules; they never
change a named scenario's expected result.

## Tenants and source bindings

| Tenant ID | Fictional broker | Source | Directory |
|---|---|---|---|
| `ff-broker` | North Star Freight | FreightFlow | `tms_a_freightflow` |
| `hd-broker` | Alamo Brokerage | HaulDesk | `tms_b_hauldesk` |
| `bo-broker` | Gulf Bridge Logistics | BrokerOS | `tms_c_brokeros` |

## Curated location catalog

| ID | City, state | ZIP | Metro |
|---|---|---:|---|
| `DFW-GP` | Grand Prairie, TX | 75050 | DFW |
| `DFW-IRV` | Irving, TX | 75039 | DFW |
| `DFW-DAL` | Dallas, TX | 75201 | DFW |
| `DFW-FTW` | Fort Worth, TX | 76102 | DFW |
| `DFW-ARL` | Arlington, TX | 76010 | DFW |
| `DFW-PLN` | Plano, TX | 75024 | DFW |
| `HOU-KAT` | Katy, TX | 77449 | Houston |
| `HOU-SUG` | Sugar Land, TX | 77478 | Houston |
| `HOU-HOU` | Houston, TX | 77002 | Houston |
| `HOU-PAS` | Pasadena, TX | 77502 | Houston |
| `HOU-BAY` | Baytown, TX | 77520 | Houston |
| `HOU-GLV` | Galveston, TX | 77550 | Houston |
| `SAT-NBR` | New Braunfels, TX | 78130 | San Antonio |
| `SAT-SCH` | Schertz, TX | 78154 | San Antonio |
| `SAT-SAT` | San Antonio, TX | 78205 | San Antonio |
| `SAT-SEG` | Seguin, TX | 78155 | San Antonio |

Directional lane labels are explanatory only: `DFW→HOU`, `HOU→SAT`, and
`suburb→suburb` are not replacements for later endpoint-distance matching.

## Stable customers and carriers

Customer identities are source-local and never join tenants. Carrier MC/DOT is
also source-local for mandatory logic. `FF-C-206` and `HD-C-206` deliberately
share authority numbers only to test that rule.

| Tenant | Customers | Carrier IDs and profile |
|---|---|---|
| `ff-broker` | `FF-CUST-101` Lone Star Beverages; `FF-CUST-102` Metro Retail; `FF-CUST-103` Prairie Foods | `FF-C-201` Lone Star Van (many DFW→HOU dry-van loads); `FF-C-202` Cedar Express (one highly similar load); `FF-C-203` Texas General (broad equipment, poor lane); `FF-C-204` Metro Haul (recent DFW delivery); `FF-C-205` Heritage Trucking (old DFW delivery); `FF-C-206` Crossroads Carrier (MC 1350101, DOT 3901001); `FF-C-207` Gulf Dry; `FF-C-208` Trinity Logistics |
| `hd-broker` | `HD-CUST-301` Alamo Building Supply; `HD-CUST-302` Hill Country Produce; `HD-CUST-303` Mission Foods | `HD-C-401` Delta Prime; `HD-C-402` Central Haul; `HD-C-403` Alamo Refrigerated; `HD-C-404` Guadalupe Transport; `HD-C-405` Mission Van; `HD-C-206` Crossroads Carrier (MC 1350101, DOT 3901001); `HD-C-407` Bexar Logistics; `HD-C-408` River City Freight |
| `bo-broker` | `BO-CUST-501` Gulf Coast Foods; `BO-CUST-502` Bayou Retail; `BO-CUST-503` South Texas Cold Chain | `BO-C-601` Gulf Reefers; `BO-C-602` Port City Transport; `BO-C-603` Lone Oak Logistics; `BO-C-604` Texas Triangle Freight; `BO-C-605` Cypress Carrier; `BO-C-606` Coastal Van; `BO-C-607` Alamo Route; `BO-C-608` Brazos Trucking |

Generator serializers assign each logical ID a stable source external ID: numeric
FreightFlow shipment/customer/carrier IDs; `HD-2026-xxxx`/numeric HaulDesk IDs;
and 18-character BrokerOS CRM IDs. The logical IDs are the scenario contract;
the generated manifest exposes both logical and source IDs.

### Day 11 ranking-evidence contrast

The two non-FreightFlow demo rosters deliberately avoid interchangeable carrier
histories. For the BrokerOS Katy→San Antonio reefer target, Gulf Reefers has rich
near-exact work; Port City has only regional Galveston→San Antonio work; Lone Oak
has a single older regional completion; Coastal's latest recorded delivery returns
to Katy; and Alamo Route includes a materially different Houston→DFW reefer lane.
For the HaulDesk Plano→Baytown dry-van target, Delta Prime has rich corridor work,
Guadalupe has the sparse local suburb lane, Mission has a materially different
Houston→San Antonio lane, and River City has a recent historical Baytown→Plano
delivery. These are historical-fit distinctions only, never availability claims.

## Required scenario matrix

The previous hand-maintained date grid became stale when the deterministic
schedule was expanded. This index now mirrors the current canonical scenario
definitions. The generated [scenario manifest](../data/scenarios.json) is the
source for the exact source-file list, tenant/source binding, entity IDs, declared
warnings, and verification name; `make validate` proves that every reference is
present and valid.

The three full-lifecycle scenarios each emit all six canonical states across six
successive files: `PLANNED`, `ACTIVE`, `COVERED`, `IN_TRANSIT`, `DELIVERED`, and
`COMPLETED`.

| ID | Current entities | Purpose and expected effect | Verification test |
|---|---|---|---|
| `SC-01` | FreightFlow `FF-1101`, `FF-C-201` | Full lifecycle; completed exact-lane history. | `test_sc01_freightflow_full_lifecycle` |
| `SC-02` | HaulDesk `HD-2101`, `HD-C-401` | Full lifecycle; ledger-backed completed load. | `test_sc02_hauldesk_full_lifecycle` |
| `SC-03` | BrokerOS `BO-3101`, `BO-C-601` | Full lifecycle; directional reefer evidence. | `test_sc03_brokeros_full_lifecycle` |
| `SC-04` | FreightFlow `FF-1201`, `FF-C-207` | Carrier pay appears only after booking. | `test_sc04_carrier_rate_appears_at_booking` |
| `SC-05` | FreightFlow `FF-1101` | Final amount correction is visible only at a later `as_of`. | `test_sc05_final_amount_changes_after_delivery` |
| `SC-06` | FreightFlow `FF-1301`, `FF-C-208` | Corrected customer total preserves earlier history. | `test_sc06_customer_rate_correction_preserves_history` |
| `SC-07` | FreightFlow `FF-1101`, `FF-C-201` | Corrected pickup ZIP changes later lane evidence only. | `test_sc07_pickup_zip_correction_changes_lane_evidence` |
| `SC-08` | BrokerOS `BO-3101`, `BO-C-606` | Unknown equipment remains a historical fact. | `test_sc08_equipment_correction_preserves_unknown_history` |
| `SC-09` | HaulDesk `HD-2101`, `HD-C-402` | Fuel surcharge increases ledger pay exactly once. | `test_sc09_hauldesk_fuel_surcharge_applies_once` |
| `SC-10` | HaulDesk `HD-2101`, `HD-C-402` | Negative adjustment reduces ledger pay exactly once. | `test_sc10_hauldesk_negative_adjustment_applies_once` |
| `SC-11` | BrokerOS `BO-3101`, `BO-C-602` | Latest replacement carrier rate becomes current. | `test_sc11_brokeros_rate_restatement` |
| `SC-12` | FreightFlow `FF-1101`, `FF-C-201` | Private DFW to Houston exact-lane history. | `test_sc12_rich_dfw_hou_history` |
| `SC-13` | BrokerOS `BO-3101`, `BO-C-601` | Houston to San Antonio reefer neighbor-lane evidence. | `test_sc13_rich_hou_sat_reefer_history` |
| `SC-14` | HaulDesk `HD-2101`, `HD-C-404` | Thin suburb-lane evidence for fallback behavior. | `test_sc14_thin_suburb_lane` |
| `SC-15` | FreightFlow `FF-1101`, `FF-C-201` | Many similar loads create a strong fit candidate. | `test_sc15_many_similar_loads` |
| `SC-16` | FreightFlow `FF-1201`, `FF-C-202` | One excellent match remains low-sample evidence. | `test_sc16_one_similar_load_is_shrunk` |
| `SC-17` | FreightFlow `FF-1301`, `FF-C-203` | Lane fit outweighs broad equipment history. | `test_sc17_broad_equipment_poor_lane_fit` |
| `SC-18` | FreightFlow `FF-1401`, `FF-C-204` | Recent historical delivery evidence helps the Day 11 fit. | `test_sc18_recent_delivery_location_evidence` |
| `SC-19` | FreightFlow `FF-1402`, `FF-C-205` | Old historical delivery evidence decays. | `test_sc19_old_delivery_evidence_decays` |
| `SC-20` | BrokerOS `BO-3004`, `BO-C-603` | Ordered multi-stop route survives serialization and ingestion. | `test_sc20_brokeros_multistop_order` |
| `SC-21` | BrokerOS `BO-3101` | Unknown equipment stays first-class. | `test_sc21_unknown_equipment_remains_unknown` |
| `SC-22` | FreightFlow `FF-1201` | Reingesting the same file is a no-op. | `test_sc22_duplicate_file_is_noop` |
| `SC-23` | `FF-C-206`, `HD-C-206` | Same MC/DOT remains tenant-local. | `test_sc23_same_mc_dot_does_not_cross_tenant_boundary` |
| `SC-24` | FreightFlow Day 11 `FF-9001` | Exact history supports the narrowest rate tier. | `test_sc24_day11_exact_lane_decision` |
| `SC-25` | BrokerOS Day 11 `BO-9001` | Near-exact history supports a high-evidence decision. | `test_sc25_day11_geographic_neighbor_decision` |
| `SC-26` | HaulDesk Day 11 `HD-9001` | Sparse local evidence blends with regional history at medium confidence. | `test_sc26_day11_sparse_fallback_decision` |

## Schedule and conflict rules

1. Each source begins with three early anchors. Their six lifecycle observations
   occupy the first six historical slots. Every ordinary historical load first
   becomes `ACTIVE` only after those three anchors are completed. Later spare
   capacity carries seven more FreightFlow anchors and seven varied anchors for
   each of HaulDesk and BrokerOS. FreightFlow retains nine or more independent
   same-carrier exact/return observations for its strong Day 11 fit; the other
   anchors deliberately distribute completed work across lane, equipment, and
   history-depth profiles so the demo does not present dormant carrier records as
   a candidate roster. These are supporting history, not separate required
   scenarios.
2. Each source has six ordinary historical lifecycle loads. The first 36 source slots run
   their ordered six-stage lifecycle blocks; the final four slots carry three
   authored rolling-holdout loads through `PLANNED`, `ACTIVE`, `COVERED`, and
   `COMPLETED` together. Later booked-carrier labels and coverage tags are
   canonical catalog data, never derived from ranking output. They cover rich and
   sparse history, near-exact, broader-lane, distance/equipment,
   limited-candidate, and close-score-tie cases. Day 11 targets are unchanged.
   `SC-26` intentionally has a small near-exact price group plus a regional
   dry-van baseline; this is the visible sparse-local/blended-price demo case.
3. Historical files use the literal name `{YYYY-MM-DD}T{HH-MM}_sync.json`.
   Event timestamps fall at or before the file timestamp; all source-specific
   modified timestamps advance with that event.
4. A single source file may carry up to three compatible scenario events. Its
   scenario membership is derived from scheduled events, not manually listed.
5. Source semantics win when scenarios overlap: FreightFlow/BrokerOS emit the
   entire changed load; HaulDesk emits changed load rows plus only new rate rows.
6. `SC-05` and `SC-07` are later updates of `SC-01` load `FF-1101`; `SC-09`
   and `SC-10` are sequential financial updates of `SC-02` load `HD-2101`;
   `SC-15` describes the aggregate history created by the named FreightFlow
   load and its supporting anchors.
7. Day 11 targets never receive a booking, completed rate, correction, or
   delivery event. They are decision inputs, not evaluation labels.
8. Background loads cannot reuse a required-scenario or Day 11 target ID. They
   must be listed in the derived manifest with `background: true`.

## Acceptance evidence for later phases

The generated `data/scenarios.json` must derive every row above from one source
of truth and include: scenario ID, description, tenant, source files, logical and
source entity IDs, expected effect, verification test, and declared warnings.
The validator must reject a missing scenario, source file, catalog identity, ZIP,
or required Day 11 target.
