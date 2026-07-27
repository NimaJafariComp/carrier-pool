# Implementation Status

Status is measured against `docs/IMPLEMENTATION_PLAN.md`. “Implemented” means
code and focused tests exist; a phase gate is marked separately when its full
acceptance evidence has not yet been recorded.

## Phase 0 — Freeze product thesis — Complete

- Repository rules in `AGENTS.md` define temporal correctness, tenant isolation,
  Decimal money, UTC time, explainability, and shared-pool deferral.
- `DECISIONS.md` records historical-fit claims, non-goals, immutable history,
  transparent pricing, and shared-pool gate.

## Phase 1 — Bootstrap repository — Complete

- Python/FastAPI backend with health endpoint, CLI, uv lockfile, Ruff, Pyright,
  pytest, Alembic, and Dockerfile.
- React/Vite frontend with TypeScript, Vitest, ESLint, Prettier, Playwright, and
  Dockerfile.
- Docker Compose PostgreSQL/backend/frontend stack, `.env.example`, Makefile,
  and GitHub Actions CI skeleton.
- `make test-integration` starts local PostgreSQL and runs database integration
  tests.

## Phase 2 — Define canonical contracts — Complete

- Canonical enums, tenant/source identities, immutable domain snapshots,
  structured warnings, and adapter contracts.
- Decimal money, UTC-only canonical timestamps, `UNKNOWN` equipment, independent
  pickup/drop-off flags, and ordered stops.
- Source status/equipment mappings plus FreightFlow, BrokerOS, and DST-safe
  HaulDesk conversion utilities.

## Phase 3 — Database design and migrations — Implemented; RLS evidence complete

- Database design document, SQLAlchemy models, Alembic migrations, temporal load
  versions/current projections/stops, source ledger entries, and decision tables.
- PostgreSQL RLS migration, non-owner application role, and transaction-local
  tenant context helper.
- PostgreSQL integration tests cover temporal persistence, Decimal round trips,
  immutable versions, and current-version links.
- Direct-SQL RLS integration test connects as `carrier_pool_app`; it verifies
  missing tenant context exposes no customer rows, tenant A sees only own rows,
  and tenant A cannot delete tenant B rows.

## Phase 4 — FreightFlow vertical slice — Complete

- FreightFlow DTO parser, canonical normalizer, database-free adapter, and
  transactional idempotent ingestion coordinator.
- Tenant-scoped current-load API and minimal React load-detail UI with loading,
  error, and not-found states.
- Parser/normalizer/API/UI tests exist. Contract tests normalize the supplied
  comment-stripped schema example through the actual adapter.
- PostgreSQL integration test ingests both supplied FreightFlow snapshots and
  verifies two immutable versions, later current projection, and duplicate-file
  no-op.

## Phase 5 — HaulDesk and BrokerOS adapters — Complete

- **5.1–5.3 HaulDesk:** DTO parsing, typed carrier/rate assembly, structured
  missing-carrier warnings, DST-safe normalization, append-only ledger entries,
  bill/pay totals, idempotent rate insertion, immutable versions, and current
  projections.
- **5.4–5.5 BrokerOS:** 18-character CRM IDs, typed Account/Location resolution,
  ordered child stops, cargo parsing, unit-aware weight aggregation, `UNKNOWN`
  equipment, restated rate versions, and current projections without ledger
  deltas.
- **5.6:** shared FreightFlow/HaulDesk/BrokerOS database-free canonical adapter
  contract tests.
- PostgreSQL integration tests cover HaulDesk ledger adjustments and BrokerOS
  restatements.
- BrokerOS canonical snapshots retain planned stop date plus per-item commodity,
  canonical and declared weight, declared weight unit, and pallet count.
- Contract tests normalize every supplied comment-stripped schema example as a
  file through its source adapter.

## Phase 6 — Deterministic data generator — complete

- `docs/DATA_SCENARIOS.md` defines the three-tenant catalog, 26 required
  scenarios, exact lifecycle/correction events, Day 11 target cases, and later
  verification names.
- `docs/architecture/data-generator.md` defines the generator boundary,
  lifecycle reducer, scheduler, serializers, derived manifest, and validator.
- Typed generator catalog models and deterministic lifecycle reducer support
  replacement totals, append-only financial entries, multi-stop routes,
  equipment/ZIP corrections, and Day 11 active targets.
- Focused tests cover lifecycle sequencing including status regression, correction
  application, append-only ledger entries, Day 11 target protection, and seeded
  determinism.
- Hand-authored catalog factory provides three source-bound tenants, 15 Texas
  Triangle locations, nine customers, and eight carriers per tenant. Every
  carrier has stable MC/DOT values; one authority is intentionally shared across
  two tenant-local carrier records. Profiles cover rich/thin lanes, low/high
  history, broad equipment, and recent/stale delivery evidence.
- Validation tests cover catalog determinism, uniqueness, tenant/reference
  integrity, repeated authority isolation, and all three Day 11 targets.
- Pure FreightFlow, HaulDesk, and BrokerOS serializers emit plain JSON matching
  source vocabulary, IDs, units, timestamp formats, replacement/ledger semantics,
  and BrokerOS required references. Serializer contracts round-trip through the
  existing adapters and structurally match supplied JSONC examples.
- Deterministic scheduler and safe writer create 120 historical files (four slots
  per day for ten days across all sources) plus three Day 11 active-load files.
  `carrier-pool generate` and `make generate` write only known plain-JSON paths,
  preserve JSONC schema examples, and rerun byte-for-byte identically.
- `data/scenarios.json` is deterministically derived from canonical typed scenario
  definitions and schedule paths. It contains all 26 required scenario IDs,
  tenant/source bindings, valid entity IDs, source files, descriptions, expected
  effects, verification tests, and expected warnings.
- `carrier-pool validate-data` and `make validate` validate the complete generated
  schedule, strict plain-JSON/source schemas, references, identities, timestamps,
  units, ZIP catalog coverage, lifecycle/money timing, Day 11 protection, required
  scenario coverage, and declared normalization warnings. Focused negative tests
  reject missing schedule files, broken BrokerOS references, and undeclared warnings.

## Phase 7 — Complete ingestion and rebuild logic — Complete

- `FileIngestionOrchestrator` binds each generated source directory to an explicit
  tenant/source pair, ignores JSONC examples, rejects non-generated JSON names,
  parses filename timestamps, and submits exactly one file at a time in global
  chronological order.
- `carrier-pool ingest-file` requires explicit tenant/source binding.
  `carrier-pool ingest-all` and `make ingest` require one explicit tenant UUID per
  TMS directory; `make ingest` fails safely when those variables are absent.
- Focused tests cover ignored JSONC/non-JSON files, invalid names, global ordering,
  trusted directory binding, and one-at-a-time callback dispatch.
- Every coordinator now wraps parse, normalization, and persistence in a file-level
  transaction. Fatal errors roll back all domain facts, then persist a separate
  tenant-scoped `FAILED` ingestion record with sanitized structured error code/type.
  A corrected checksum can ingest successfully after a failed attempt.
- PostgreSQL integration tests force failure after first persisted load, prove all
  partial facts roll back, verify sanitized failure metadata, and verify parse-failure
  records carry no raw payload.
- Current projections use deterministic `(source sync, source modified, observed)`
  precedence while every distinct immutable version remains stored. Older source
  snapshots cannot replace current state; later source corrections can regress status
  and remain current with structured anomaly warnings on the ingestion file.
- Canonically unchanged snapshots from a different file create no redundant version.
  Pure and PostgreSQL tests cover out-of-order attempts, late regression corrections,
  anomaly persistence, and unchanged snapshots.

- `rebuild-projections` reconstructs one tenant's loads, ordered stops, linked customer
  and carrier projections, and HaulDesk ledger totals exclusively from immutable load
  versions and source rate entries in one transaction. `make rebuild-projections`
  requires `TENANT_ID`. The integration test corrupts the mutable state, rebuilds it,
  and verifies the normalized state hash matches incremental ingestion.

- Per-file JSON CLI/log reports include tenant, source, filename, checksum, record,
  version, projection, warning, error, and no-op counts. `ingestion-summary` returns
  tenant-scoped review-demo totals. Smoke coverage generates and validates all 123
  files, ingests them, verifies no-op replay, and proves rebuild parity.

## Phases 8–16 — Not started

- Phase 8: geography and comparable-lane retrieval.
- Phase 9: rate estimation and leakage-free backtesting.
- Phase 10: carrier historical-fit ranking.
- Phase 11: persisted decisions and complete API.
- Phase 12: final frontend.
- Phase 13: tenancy/correction/historical hardening.
- Phase 14: reproducibility, operations, and CI completion.
- Phase 15: documentation and review preparation.
- Phase 16: optional shared carrier pool.

## Latest verification — 2026-07-27

- Backend suite with PostgreSQL: `141 passed`.
- PostgreSQL integration target: `7 passed` (temporal persistence, direct-SQL
  RLS, FreightFlow replacement/idempotency, HaulDesk ledger, BrokerOS restatement).
- Ruff: pass. Pyright: `0 errors, 0 warnings, 0 informations`.
