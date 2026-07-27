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
- Hand-authored catalog factory provides three source-bound tenants, 16 Texas
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
- Each source starts with a hand-authored anchor full lifecycle (`FF-1001`,
  `HD-2001`, `BO-3001`) and completes it before any later source load first
  becomes ACTIVE. Six historical loads per tenant now provide same-tenant
  near-exact/rich, regional, metro-corridor, thin/sparse, and
  distance/equipment fallback evidence while Day 11 target loads remain
  unchanged.
- HaulDesk lifecycle snapshots no longer manufacture ledger adjustments: a
  generated load emits its single linehaul row at booking unless an explicit
  financial scenario adds another row. Generated ingestion verifies `HD-2101`
  has a final PAY ledger total of `$1,150` and that Day 11 displays that same
  comparable rate for `HD-9001`.
- `data/scenarios.json` is deterministically derived from canonical typed scenario
  definitions and schedule paths. It contains all 26 required scenario IDs,
  tenant/source bindings, valid entity IDs, source files, descriptions, expected
  effects, verification tests, and expected warnings.
- `carrier-pool validate-data` and `make validate` validate the complete generated
  schedule, strict plain-JSON/source schemas, references, identities, timestamps,
  units, ZIP catalog coverage, lifecycle/money timing, Day 11 protection, required
  scenario coverage, and declared normalization warnings. Focused negative tests
  reject missing schedule files, broken BrokerOS references, and undeclared warnings.
- Generated-data validation also requires six completed historical loads per source
  and verifies the source anchor completes before any later load first becomes
  ACTIVE. The generated ingestion smoke requires at least five scored rolling
  cases per tenant/source (15 total), at least one rich and one sparse case, and
  confirms every comparable immutable version is observed at or before its target
  case's first-ACTIVE cutoff.

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

## Phase 8 — Geography and comparable-lane retrieval — Complete

- Approved local geography design lives in `docs/architecture/geography.md`.
  Packaged GeoNames-attributed Texas Triangle ZIP centroid data normalizes stop
  ZIP/city/state locally, returns explicit quality flags, and performs no network
  calls. Current ingestion and rebuild enrich stops with coordinates, metro group,
  and persisted quality flags. Focused tests cover DFW, Houston, San Antonio,
  suburbs, missing ZIPs, and invalid ZIPs.
- Typed Haversine, endpoint-pair, ordered metro/route identity, and optional H3
  candidate-cell utilities preserve exact distance as the business explanation.
  Unit properties cover symmetry, bounds, and approximate Texas baselines.
- Comparable retrieval is tenant-scoped and `as_of`-bounded over immutable load
  versions. It selects the narrowest eligible documented lane tier and returns
  endpoint distances, route-mile delta, recency, tier, and immutable evidence IDs.
  Integration tests cover suburb history, reverse-lane separation, metro fallback,
  exact-lane precedence, future-version exclusion, and tenant isolation.

## Phase 9 — Rate estimation and backtesting — Complete

- **9.1:** `docs/architecture/rate-estimation.md` specifies eligible completed
  history, replacement/ledger rate targets, as-of correction rules, weights,
  quantiles, ESS, shrinkage, ranges, confidence, evidence, and rolling backtests.
- **9.2:** pure typed `Decimal` weighted-statistics primitives implement median,
  quantiles, weight normalization, Kish ESS, and hierarchical blending. Focused
  tests cover empty/zero/single/duplicate values, float rejection, scale
  invariance, and exact normalized sums.
- **9.3:** `HierarchicalRateEstimator` consumes immutable comparable tiers and
  source-aware carrier totals at `as_of`. It uses replacement snapshots for
  FreightFlow/BrokerOS and append-only HaulDesk PAY ledger entries, includes
  sparse fallback, confidence diagnostics, structured comparable evidence, and
  explicit no-data/unknown-equipment/geography warnings. Unit tests cover exact,
  regional, metro sparse fallback, rich-tier threshold, unknown equipment,
  no-data, and timezone validation.
- **9.4:** `RateBacktestHarness` selects each load's first immutable `ACTIVE`
  observation as cutoff, calls the estimator only at that cutoff, and compares
  it with eventual corrected replacement/ledger carrier totals. `rate-backtest`
  and `make backtest` write `artifacts/backtest_metrics.json` and
  `artifacts/backtest_cases.csv`, including MAE, median error, WAPE, historical
  range coverage, and tier/equipment/rich-versus-sparse breakdowns. Focused tests
  prove a future correction is label-only and cannot alter the earlier estimator
  input; artifact and aggregate-metric tests pass.
- **9.5:** analysis-only tenant-wide, equipment/distance-band, unshrunk lane,
  Huber, and quantile-regression baselines run at every case's same immutable
  `as_of` cutoff against tenant-local completed observations only. Artifacts expose
  each model's explicit case count, MAE, median error, WAPE, and range coverage;
  models without enough observations show zero eligible cases rather than sharing
  another model's population. pandas/scikit-learn live only in backend's `analysis`
  dependency group. The production estimator remains `pricing-hierarchical-v1`;
  `DECISIONS.md` records no promotion without material leakage-safe improvement.
- **Gate evidence:** each generated Day 11 target produces a Decimal point estimate,
  historical range, confidence, fallback/warnings, and immutable comparable evidence
  in a database smoke check. After the Phase 6 anchor schedule repair, fresh
  generated smoke tenants yield 18 eligible labels and 15 scored predictions across
  NEAR_EXACT, REGIONAL, METRO_CORRIDOR, DISTANCE_EQUIPMENT, and
  TENANT_ALL_EQUIPMENT. `make backtest` writes aligned, model-specific metrics and
  displayed case counts (current local populated DB: 314 eligible labels and 195
  production-scored cases); the smoke test verifies baseline populations never
  exceed available rolling cases and all comparable evidence is at or before each
  target cutoff. Phase 9 gate passed after `make generate`, `make validate`,
  generated-data ingestion, backtest artifact review, and the isolated gate smoke.
- The generated-data gate test asserts every Day 11 target has a Decimal point
  estimate, ordered historical range, confidence, and immutable comparable
  evidence; rolling scoring is nonzero; production and every eligible baseline
  expose metric rows; and the separate correction contract keeps future corrected
  totals label-only while estimator inputs remain bounded by the first-ACTIVE
  cutoff.

## Phase 10 — Carrier historical-fit ranking — Complete

- `docs/architecture/carrier-ranking.md` defines tenant-local historical-fit
  eligibility, components, weights, ESS/shrinkage, confidence, tie-breaking,
  structured explanations, strict deadhead wording, temporal/tenant boundaries,
  and leakage-safe proxy evaluation.
- `CarrierFeatureService` now reconstructs tenant-local candidate features from
  immutable completed versions at `as_of`: directional-lane evidence, equipment
  and completed-history counts, relevant-work recency, last known delivery time,
  delivery-to-pickup distance/time gap, and immutable evidence IDs. Generated-data
  ingestion coverage proves recent and stale delivery evidence remain distinct and
  no live-availability claim is produced.
- `CarrierHistoricalFitScorer` applies documented lane/equipment/deadhead/recency
  components, neutral-prior shrinkage, separate confidence, and stable tie-breaking
  under model version `carrier-ranking-v1`. Focused tests cover exact-lane priority,
  deadhead rank changes, stale-evidence decay, and sparse one-load shrinkage.
- Structured ranking explanations use fixed reason templates and include rank,
  adjusted score, confidence, component values, evidence IDs, warnings, and model
  version. Explanation tests prohibit unsupported availability, reliability, and
  acceptance wording.
- `rate-backtest` now writes `artifacts/ranking_metrics.json` beside the rate
  artifacts. It evaluates first-ACTIVE, tenant-local rankings using the eventually
  booked carrier only as a stated weak behavioral proxy; top-1/top-3 recall, MRR,
  no-rank counts, rich/sparse effective-history breakdowns, and paired
  no-deadhead ablation results use the same cases. Generated-ingestion coverage
  proves reading another tenant's history cannot alter a fixed tenant ranking.
- **Gate evidence:** generated-ingestion smoke scores every Day 11 target twice
  from identical immutable inputs, requires non-empty evidence-backed rankings and
  explanations, and confirms sparse scores carry neutral-prior shrinkage while
  explanation templates avoid availability/acceptance claims.

## Phases 11–16 — Not started

- Phase 11: persisted decisions and complete API.
- Phase 12: final frontend.
- Phase 13: tenancy/correction/historical hardening.
- Phase 14: reproducibility, operations, and CI completion.
- Phase 15: documentation and review preparation.
- Phase 16: optional shared carrier pool.

## Latest verification — 2026-07-27

- `make backtest`: pass; wrote rate artifacts and `artifacts/ranking_metrics.json`
  for `440` historical cases (`300` rate-scored).
- Phase 10 ranking evaluation/scoring tests: `4 passed`. Focused Ruff passes.
- Full backend Pyright currently reports `51` pre-existing/adjacent unknown-type
  errors in baseline analysis, generator scheduling, and carrier snapshot parsing;
  this does not change completed behavioral gate smoke, but type-cleanliness is
  not yet a repository-wide pass.
- Phase 10 Day 11 ranking gate smoke: `1 passed` against local Compose PostgreSQL.
- Prior baseline verification: `make test-integration`: `7 passed`; full backend
  suite with `DATABASE_URL`: `182 passed` (before Phase 10 additions).
