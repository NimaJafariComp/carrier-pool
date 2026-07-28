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

## Required scenario matrix

Dates in route columns are planned pickup → delivery. `FF`, `HD`, and `BO`
abbreviate the source directory. A listed file is an exact event location;
intervening appearances repeat only the source's normal changed-load snapshot.

| ID | Purpose and entities | Tenant / route / equipment / dates | Representative lifecycle updates and source files | Correction or financial event | Expected final state and Day 11 effect | Verification test |
|---|---|---|---|---|---|---|
| `SC-01` | FreightFlow full lifecycle; `FF-1001`, `FF-CUST-101`, `FF-C-201` | `ff-broker`; `DFW-GP→HOU-KAT`; dry van; D1→D2 | `D1 00` QUOTING; `D1 06` BOOKING; `D1 12` DISPATCHED; `D2 00` EN_ROUTE; `D2 12` DELIVERED; `D2 18` COMPLETED in `tms_a_freightflow/{timestamp}_sync.json` | Carrier rate appears at `D1 12`; final pay changes at `D2 18` | Completed, final carrier pay $1,200. Historic exact-lane evidence for `SC-24`. | `test_sc01_freightflow_full_lifecycle` |
| `SC-02` | HaulDesk full lifecycle; `HD-2001`, `HD-CUST-301`, `HD-C-401` | `hd-broker`; `SAT-NBR→HOU-PAS`; dry van; D1→D2 | `D1 00` 10; `D1 06` 20; `D1 12` 30; `D2 00` 40; `D2 12` 50; `D2 18` 90 | Bill/pay ledger rows first appear at 30. | Completed ledger-backed load; validates source status mapping. | `test_sc02_hauldesk_full_lifecycle` |
| `SC-03` | BrokerOS full lifecycle; `BO-3001`, `BO-CUST-501`, `BO-C-601` | `bo-broker`; `HOU-SUG→SAT-SCH`; reefer; D1→D3 | `D1 00` Quotes Requested; `D1 12` Ready to Book; `D2 00` Booked; `D2 12` In Transit; `D3 00` Delivered; `D3 18` Paid | Customer/carrier totals appear at Booked; final restatement permitted at Paid. | Completed directional reefer evidence for `SC-13`. | `test_sc03_brokeros_full_lifecycle` |
| `SC-04` | Booking-time pay; `FF-1002`, `FF-CUST-102`, `FF-C-207` | `ff-broker`; `DFW-IRV→HOU-HOU`; dry van; D2→D3 | `D2 06` BOOKING with null buy; `D2 12` DISPATCHED with assigned carrier and buy; `D3 12` DELIVERED | No later correction. | Carrier rate is absent before booking and fixed at booking. | `test_sc04_carrier_rate_appears_at_booking` |
| `SC-05` | Final amount after delivery; `FF-1001` | Same route as `SC-01` | `D2 12` DELIVERED pay $1,180; `D2 18` COMPLETED pay $1,200 | $20 final detention correction. | Immutable delivered and completed versions differ; only later `as_of` sees $1,200. | `test_sc05_final_amount_changes_after_delivery` |
| `SC-06` | Customer-rate correction; `FF-1003`, `FF-CUST-103`, `FF-C-208` | `ff-broker`; `DFW-ARL→HOU-BAY`; flatbed; D3→D4 | `D3 00` BOOKING sell $1,980; `D3 18` DISPATCHED; `D4 12` same source load with sell $2,060 | Replacement snapshot corrects sell total. | Current customer rate $2,060; pre-correction history remains. | `test_sc06_customer_rate_correction_preserves_history` |
| `SC-07` | Pickup ZIP correction; `FF-1004`, `FF-CUST-101`, `FF-C-201` | `ff-broker`; initially `DFW-DAL`, corrected to `DFW-GP`, then `HOU-KAT`; dry van; D3→D4 | `D3 06` BOOKING ZIP 75201; `D4 06` same load ZIP 75050; `D4 18` COMPLETED | Pickup postal code correction only. | Later comparable-lane retrieval uses Grand Prairie; earlier `as_of` uses Dallas. | `test_sc07_pickup_zip_correction_changes_lane_evidence` |
| `SC-08` | Equipment correction; `BO-3002`, `BO-CUST-502`, `BO-C-606` | `bo-broker`; `HOU-HOU→SAT-SAT`; UNKNOWN corrected to dry van; D4→D5 | `D4 00` Ready to Book equipment null; `D4 18` Booked dry van; `D5 18` Paid | Equipment null is corrected, never coerced. | Earlier version UNKNOWN; current completed version DRY_VAN. | `test_sc08_equipment_correction_preserves_unknown_history` |
| `SC-09` | HaulDesk fuel surcharge; `HD-2002`, `HD-CUST-302`, `HD-C-402` | `hd-broker`; `SAT-SEG→HOU-PAS`; reefer; D4→D5 | Initial booked load/rates `D4 06`; new PAY FUEL row `D4 18` | +$75 PAY ledger entry. | Pay total rises once; prior version remains lower. | `test_sc09_hauldesk_fuel_surcharge_applies_once` |
| `SC-10` | HaulDesk negative adjustment; `HD-2002` | Same as `SC-09` | New PAY ADJUSTMENT row at `D5 12` | -$30 PAY ledger entry. | Pay total falls once; no ledger overwrite/double count. | `test_sc10_hauldesk_negative_adjustment_applies_once` |
| `SC-11` | BrokerOS silent carrier-rate restatement; `BO-3003`, `BO-CUST-503`, `BO-C-602` | `bo-broker`; `HOU-KAT→SAT-NBR`; reefer; D4→D6 | `D4 12` Booked pay $1,420; `D5 18` Booked pay $1,475; `D6 12` Paid | No source correction marker; complete snapshot restates pay. | Current pay $1,475; two immutable rate versions, no ledger rows. | `test_sc11_brokeros_rate_restatement` |
| `SC-12` | Rich DFW→Houston dry-van history; `FF-1101…FF-1106`, `FF-C-201` | `ff-broker`; alternating `DFW-GP/IRV/ARL→HOU-KAT/SUG/HOU`; dry van; D1–D10 | Six completed replacement lifecycles, completion files: `D2 18`, `D4 18`, `D5 18`, `D7 18`, `D9 18`, `D10 12` | `FF-1104` incorporates `SC-07` ZIP correction. | At least six comparable completed loads. Main private rate/rank evidence for `SC-24`. | `test_sc12_rich_dfw_hou_history` |
| `SC-13` | Rich Houston→San Antonio reefer history; `BO-3101…BO-3105`, `BO-C-601` | `bo-broker`; alternating `HOU-SUG/KAT/PAS→SAT-SCH/NBR/SAT`; reefer; D1–D10 | Five completed BrokerOS lifecycles, completion files: `D3 18`, `D5 18`, `D7 12`, `D9 12`, `D10 18` | `BO-3102` is the `SC-11` restatement path. | At least five directional reefer comparables; supports geographic-neighbor target. | `test_sc13_rich_hou_sat_reefer_history` |
| `SC-14` | Thin suburb lane; `HD-2101`, `HD-C-404` | `hd-broker`; `DFW-PLN→HOU-BAY`; dry van; D6→D7 | BOOKED `D6 12`; COMPLETED `D7 18` | No correction. | Exactly one direct comparable; sparse fallback must be visible for `SC-26`. | `test_sc14_thin_suburb_lane` |
| `SC-15` | Many similar-load carrier; `FF-C-201` | `ff-broker`; `DFW→HOU`; dry van; D1–D10 | Assigned to `SC-01`, `SC-07`, and `FF-1101…FF-1106` on their booked files | Standard source corrections retained. | High effective lane history; strong historical-fit candidate for `SC-24`. | `test_sc15_many_similar_loads` |
| `SC-16` | One highly similar carrier; `FF-1201`, `FF-C-202` | `ff-broker`; `DFW-GP→HOU-KAT`; dry van | BOOKED then COMPLETED | No correction. | One excellent comparable remains sparse evidence beside Lone Star Van's richer history. | `test_sc16_one_similar_load_is_shrunk` |
| `SC-17` | Broad equipment, poor lane fit; `FF-1301…FF-1304`, `FF-C-203` | `ff-broker`; `HOU→SAT`/`SAT→DFW`; dry van, reefer, flatbed; D2–D10 | Four completed lifecycles at `D3 18`, `D5 12`, `D7 12`, `D10 18` | No correction. | Equipment breadth cannot outweigh poor DFW→Houston lane fit. | `test_sc17_broad_equipment_poor_lane_fit` |
| `SC-18` | Recent delivery near Day 11 pickup; `FF-1401`, `FF-C-204` | `ff-broker`; `HOU-PAS→DFW-GP`; dry van; completes late in history | BOOKED then COMPLETED at Grand Prairie | No correction. | Recent known delivery near `SC-24` pickup increases historical deadhead component; never availability claim. | `test_delivery_proximity_examples_have_distinct_historical_recency` |
| `SC-19` | Old delivery evidence decays; `FF-1402`, `FF-C-205` | `ff-broker`; `HOU-HOU→DFW-GP`; dry van; completes early in history | BOOKED then COMPLETED at Grand Prairie | No correction. | Same location signal as `SC-18`, but age suppresses its component for `SC-24`. | `test_delivery_proximity_examples_have_distinct_historical_recency` |
| `SC-20` | Ordered multi-stop BrokerOS load; `BO-3004`, `BO-CUST-501`, `BO-C-603` | `bo-broker`; `HOU-SUG→HOU-HOU→SAT-SCH`; reefer; D7→D8 | Ready to Book `D7 06`; Booked `D7 18`; Paid `D8 18` | Three child stops with order 1, 2, 3. | Ordered independent stop flags survive; no flattened-route assumption. | `test_sc20_brokeros_multistop_order` |
| `SC-21` | Unknown-equipment load; `BO-3005`, `BO-CUST-502`, no carrier initially | `bo-broker`; `HOU-PAS→SAT-NBR`; UNKNOWN; D8→D9 | Ready to Book `D8 06` equipment null; Booked `D8 18`; Paid `D9 18` | None. | UNKNOWN stays a first-class equipment value, not dry van. | `test_sc21_unknown_equipment_remains_unknown` |
| `SC-22` | Duplicate-file ingestion; `FF-1002` file checksum | `ff-broker`; uses `DFW-IRV→HOU-HOU`; dry van | Re-ingest `tms_a_freightflow/2026-07-02T12-00_sync.json` after chronological ingest | No new source observation. | No new ingestion facts, versions, or projections. This is an operation, not a duplicate generated file. | `test_sc22_duplicate_file_is_noop` |
| `SC-23` | Same authority under two tenants; `FF-C-206`, `HD-C-206` | `ff-broker` and `hd-broker`; both MC 1350101 / DOT 3901001; different local lanes | First appear in `D2 06` FreightFlow and `D2 12` HaulDesk respectively | None. | Two tenant-owned carrier records remain separate; changing one cannot affect another tenant's decision. | `test_sc23_same_mc_dot_does_not_cross_tenant_boundary` |
| `SC-24` | Day 11 exact-lane target; `FF-9001`, `FF-CUST-101` | `ff-broker`; `DFW-GP→HOU-KAT`; dry van; pickup D11→D12 | ACTIVE/BOOKING in `tms_a_freightflow/2026-07-11T06-00_sync.json` | No future booking/rate file generated. | Exact private history (`SC-12`); `FF-C-201` ranks above low-history and stale-evidence candidates; narrowest supported rate tier. | `test_sc24_day11_exact_lane_decision` |
| `SC-25` | Day 11 geographic-neighbor target; `BO-9001`, `BO-CUST-501` | `bo-broker`; `HOU-KAT→SAT-SAT`; reefer; pickup D11→D12 | ACTIVE/Ready to Book in `tms_c_brokeros/2026-07-11T06-00_sync.json` | No future booking/rate file generated. | Uses nearby `SC-13` endpoints, not an exact city pair; explanation gives endpoint distances and fallback tier. | `test_sc25_day11_geographic_neighbor_decision` |
| `SC-26` | Day 11 sparse fallback target; `HD-9001`, `HD-CUST-302` | `hd-broker`; `DFW-PLN→HOU-BAY`; dry van; pickup D11→D12 | ACTIVE/Open in `tms_b_hauldesk/2026-07-11T06-00_sync.json` | No future booking/rate file generated. | Thin `SC-14` evidence causes broader fallback, lower confidence, and shrinkage. | `test_sc26_day11_sparse_fallback_decision` |

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
3. Historical files use the literal name `{YYYY-MM-DD}T{HH-MM}_sync.json`.
   Event timestamps fall at or before the file timestamp; all source-specific
   modified timestamps advance with that event.
4. A single source file may carry up to three compatible scenario events. Its
   scenario membership is derived from scheduled events, not manually listed.
5. Source semantics win when scenarios overlap: FreightFlow/BrokerOS emit the
   entire changed load; HaulDesk emits changed load rows plus only new rate rows.
6. `SC-05` is the final update of `SC-01`; `SC-09` and `SC-10` are sequential
   financial updates of `SC-02`'s separate load `HD-2002`; `SC-15` describes
   the aggregate history created by the named FreightFlow loads.
7. Day 11 targets never receive a booking, completed rate, correction, or
   delivery event. They are decision inputs, not evaluation labels.
8. Background loads cannot use `FF-1001…FF-1402`, `HD-2001…HD-2101`,
   `BO-3001…BO-3105`, or Day 11 target IDs. They must be listed in the derived
   manifest with `background: true`.

## Acceptance evidence for later phases

The generated `data/scenarios.json` must derive every row above from one source
of truth and include: scenario ID, description, tenant, source files, logical and
source entity IDs, expected effect, verification test, and declared warnings.
The validator must reject a missing scenario, source file, catalog identity, ZIP,
or required Day 11 target.
