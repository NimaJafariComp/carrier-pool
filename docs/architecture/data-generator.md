# Deterministic Data Generator Design

## Purpose

The generator is an executable scenario specification for Phase 6. It creates
repeatable synthetic source files that exercise correction semantics, geography,
sparse evidence, ranking evidence, and tenant isolation. It is not a fake live
TMS integration and must not call external services or use Faker for core facts.

The authoritative scenario contract is [DATA_SCENARIOS.md](../DATA_SCENARIOS.md).
Code will encode that contract once and derive both files and `data/scenarios.json`
from it.

## Inputs and ownership

| Input | Owner | Constraint |
|---|---|---|
| Tenant/source binding | catalog | Exactly one source per fictional tenant in Phase 6. |
| Location catalog | catalog | Texas Triangle city/state/ZIP entries only; later Phase 8 adds local centroid data. |
| Customer/carrier catalog | catalog | Stable source-local IDs; eight carriers per tenant; repeated MC/DOT never implies a join. |
| Scenario event definitions | scenario module | Hand-authored lifecycle, correction, financial, and Day 11 events. |
| Schedule | scheduler | D1–D10 four files/day/source; Day 11 06:00 targets. |
| Minor variation seed | generator config | May vary harmless background descriptions only; never IDs, money, dates, routes, or expected outcomes. |

## Canonical generator model

The future generator module has typed, immutable definitions. Source serializers
receive scheduled canonical observations; they never decide lifecycle behavior.

```text
ScenarioCatalog
  ├─ TenantDefinition / LocationDefinition / CustomerDefinition / CarrierDefinition
  ├─ LoadDefinition
  │    ├─ stable logical ID, source-local ID, route, equipment, money basis
  │    └─ source-specific serializer identity map
  └─ ScheduledEvent
       ├─ file timestamp, source event timestamp, load ID
       ├─ lifecycle state or field correction
       ├─ optional replacement totals
       └─ optional append-only financial entry

ScheduledEvent[]
  → lifecycle reducer (per load, chronological)
  → canonical observations / ledger deltas
  → source serializer
  → plain JSON file + derived scenario manifest
```

`LoadDefinition` stores an ordered stop list. A stop has independent pickup and
drop-off flags. Date-only source plans remain a `date`, while source timestamps
are normalized by existing adapter rules. Money is `Decimal`; physical units are
canonical pounds/miles before serialization.

Each generated ordered route also has a hand-authored, deterministic highway-mile
value in the catalog. Serializers use that source fact (converting only for
HaulDesk km output); it is not a centroid/Haversine calculation. Haversine remains
the separate explainable geography metric for endpoint similarity.

## Lifecycle and correction rules

The reducer accepts source corrections rather than imposing monotonic state:

- FreightFlow and BrokerOS events produce a complete replacement snapshot of the
  current canonical state at that file time.
- HaulDesk load rows represent changed current fields; rate events are individual
  immutable ledger rows and serializers emit each `rate_id` once.
- A correction is a normal event changing a field, even if it regresses status
  or alters an earlier amount/detail. The generated manifest labels the intended
  correction so validation can distinguish it from accidental inconsistency.
- An event's source modified time cannot be later than its file sync time;
  timestamps are monotonic per source entity except explicitly documented source
  corrections, which still get a later observation time.
- Day 11 targets finish as ACTIVE/Open/Ready to Book. No generated future fact
  may assign their carrier or carrier rate.

## File scheduler

Historical slots are the Cartesian product:

```text
days       = 2026-07-01 … 2026-07-10
slot hours = 00, 06, 12, 18
sources    = FreightFlow, HaulDesk, BrokerOS
```

For every slot/source, scheduler first places required events from the scenario
catalog. It then selects deterministic background lifecycle events needed to keep
the file between one and three changed loads. It may never move a required event,
duplicate a source rate ID, or add a change to a Day 11 target. The file name is
derived from its UTC-style schedule label, while the contained timestamp uses the
source's documented convention:

| Source | File payload behavior |
|---|---|
| FreightFlow | Offset-aware timestamps, US pounds/miles, full changed snapshots. |
| HaulDesk | Naive `America/Chicago` timestamps, kg/km, numeric status/equipment, new ledger rows only. |
| BrokerOS | UTC CRM timestamps, 18-character IDs, complete referenced records, ordered stops/cargo. |

The scheduler emits three Day 11 files at `2026-07-11T06-00_sync.json`, one in
each source directory. They hold `SC-24`, `SC-25`, and `SC-26` respectively.

## Serialization contract

Each source serializer owns only wire shape and source semantics.

| Serializer | Required guarantees |
|---|---|
| FreightFlow | Numeric external IDs remain stable; nullable carrier/pay prior to booking; ordered nested stops; comments never emitted. |
| HaulDesk | Stable load/carrier/rate IDs; coded status/equipment; date-only plans; kg/km conversion; no repeated ledger row. |
| BrokerOS | Every referenced Account/Location included; child stops ordered by number; cargo preserves commodity, declared unit/weight, and pallets; null equipment remains null. |

Serializers will have snapshot tests against comment-stripped schema examples.
Generated files themselves are parsed as strict plain JSON.

## Derived scenario manifest

`data/scenarios.json` is a build output, not an authored second catalog. For each
scenario it records:

```json
{
  "scenario_id": "SC-24",
  "tenant": "ff-broker",
  "source_system": "FREIGHTFLOW",
  "source_files": ["tms_a_freightflow/2026-07-11T06-00_sync.json"],
  "entities": ["logical_load:FF-9001", "source_load:127472901"],
  "expected_effect": "exact private lane evidence and narrowest supported tier",
  "verification_test": "test_sc24_day11_exact_lane_decision",
  "expected_warnings": []
}
```

The manifest also records background loads separately, preserving the proof that
every required source file has a deterministic reason for its contents.

## Validation design

`carrier-pool validate-data` will validate generated data without database writes:

1. Required file names, 120 historical file coverage, chronology, and 1–3 changed
   loads per file.
2. Strict JSON; source DTO parse; source references; stable IDs; legal source
   status/equipment; unit and timestamp sanity.
3. Lifecycle timing: pay appears no earlier than booking for replacement sources;
   HaulDesk rate IDs are unique and negative adjustments are allowed.
4. Catalog coverage: referenced ZIPs exist, required scenarios/Day 11 targets
   exist, and all manifest file/entity links resolve.
5. Semantic intent: rich/thin history counts, multi-stop/unknown-equipment cases,
   repeated MC/DOT isolation case, declared corrections, and expected warnings.

Validation fails on undeclared warnings, source parse errors, missing scenario
links, accidental file duplication, or a generated fact after a Day 11 target's
decision timestamp.

## Determinism and safe reruns

Generation sorts catalog and event IDs before scheduling, uses only explicit
timestamps, and serializes with stable JSON ordering/formatting. Running it twice
must produce byte-identical generated JSON and manifest output. It may replace
only known generated `.json` paths after validating they are inside the three data
directories; it must never change `example_sync*.jsonc`.

## Non-goals for this phase

Tasks 6.1–6.5 define documentation, typed lifecycle mechanics, a hand-authored
catalog, pure TMS serializers, and deterministic sync-file generation through
`carrier-pool generate` / `make generate`. No scenario manifest, generated-data
validator, location centroid dataset, ingestion orchestration, estimator, ranker,
or shared pool exists yet. Those belong to Tasks 6.6–6.7 and later phases.
