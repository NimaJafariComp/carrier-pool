# Implementation Status

Status is measured against `docs/IMPLEMENTATION_PLAN.md`. “Implemented” means
code and focused tests exist; a phase gate is marked separately when its full
acceptance evidence has not yet been recorded.

## Post-phase evaluation work — Complete (external validation unavailable)

- `docs/gaps.md` evaluation implementation complete: rate artifacts now contain
  per-baseline same-population comparisons with tier/rich-sparse breakdowns and
  explicit zero-case regression rows; confidence/range calibration diagnostics
  state that historical ranges are not prediction intervals. Ranking artifacts now
  use supported-only primary recall/MRR, all-candidate secondary diagnostics,
  no-rank reason counts, and lane/equipment/deadhead/recency ablations on the same
  27 labeled cases. The evaluator derives coverage from immutable cutoff facts and
  no longer imports generator catalog metadata. `make demo` plus demo-db
  `rate-backtest` pass; full unit suite is `191 passed, 19 skipped`, database
  integration is `7 passed`, generated-data smoke is `1 passed`, and Ruff/Pyright
  plus `git diff --check` pass. Tuning/promotion remains ineligible: deterministic
  demo outcomes are not independent operational evidence.
- First `docs/gaps.md` corrective slice complete: generated historical and holdout
  loads use 27 hand-authored booking/final carrier-pay outcomes rather than a
  lifecycle-position formula. FreightFlow and BrokerOS restate final corrections;
  HaulDesk appends positive/negative `ADJUSTMENT` ledger rows. Generator tests
  (`17 passed`), `make generate && make validate` (`123` files), resettable
  `make demo`, and demo-db `rate-backtest` pass.
- Current rate evaluation: `24/27` scored cases; production MAE `$130.14`, WAPE
  `9.24%`, historical-range coverage `12.5%`; same-population tenant median MAE
  `$159.58`, unshrunk nearest-lane MAE `$132.92`. Equipment/distance-band median
  MAE `$77.75` applies to only `20` cases, so it cannot select a production model.
  No weight tuning is justified. Remaining `docs/gaps.md` work: equal-population
  baseline reporting, richer independent ranking labels/evaluation, and confidence
  calibration.

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

- Generated source mileage is now hand-authored per ordered catalog route, rather
  than a shared placeholder. FreightFlow/BrokerOS emit curated miles; HaulDesk
  converts the same canonical value to km. UI rounds displayed miles to one decimal.

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
  under model version `carrier-ranking-v4`. It uses endpoint/route/recency lane
  weights with Kish ESS, recency-weighted equipment history, cutoff-relative
  recency, and only renormalizes present components, so missing location evidence
  is warned about but never silently becomes a zero-point penalty. Focused tests
  cover exact-lane priority, deadhead rank changes, stale-evidence decay, sparse
  one-load shrinkage, missing-evidence renormalization, and cutoff recency.
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

## Phase 11 — Persist decisions and finish API — Complete

- `DecisionRunService` resolves an exact tenant-local ACTIVE `LoadVersion` at
  explicit `as_of`, runs pricing and historical-fit ranking, and persists immutable
  `DecisionRun` plus rank-ordered `CarrierRecommendation` rows.
- Persisted snapshots include model versions/parameters, pricing confidence and
  warnings, structured reason codes, tenant-local evidence IDs, and a stored
  identity hash. Reuse is limited to identical tenant, load, input version,
  `as_of`, model versions, and parameters; output equality alone never reuses.
- Smoke coverage proves reproducibility/reuse, ordered immutable recommendations,
  inactive-load rejection, and a historical decision remaining unchanged despite
  later immutable load observations.
- FastAPI now exposes safe demo tenant selection, tenant-scoped active-load list
  and load detail, and persisted-decision retrieval. Decision responses include
  input/load summary, timestamps/model versions, pricing/range/confidence,
  fallback/evidence counts, ranked carriers with component scores and fixed
  explanation bullets, comparable summaries, and warnings. Cross-tenant or absent
  loads share generic `404`; inactive, not-computed, and insufficient-evidence
  states are stable `409`/`422` responses.
- `backend/scripts/export_openapi.py` exports the FastAPI contract to
  `frontend/openapi.json`; `openapi-typescript` generates
  `frontend/src/api/generated.ts`. The frontend now consumes generated
  `LoadResponse` types instead of a handwritten API interface. `make api-types`
  regenerates artifacts; `make api-types-check` rejects stale artifacts and runs
  in CI before frontend lint/type/test/build.
- Black-box API contracts ingest deterministic data and call real HTTP routes.
  They prove ACTIVE filtering, complete OpenAPI-backed decision payloads,
  same-tenant access, identical absent/cross-tenant `404`s, USD Decimal-string
  serialization, tenant-local evidence IDs, low-confidence rendering, and stable
  inactive/insufficient-data responses.
- **Gate evidence:** a persisted decision retains its exact input version, `as_of`,
  model versions, parameters, evidence, and ordered recommendations; the same
  identity reuses that immutable run, while later source observations do not mutate
  it. Generated OpenAPI schema/types are fresh and API requests remain tenant-safe.

## Phase 12 — Finish frontend — Tasks 12.1–12.4 complete

- Dark, accessible fictional-broker selector fetches only safe tenant fields,
  persists only the approved tenant ID in browser storage, and sends that ID only
  through the existing `X-Tenant-ID` trusted context header.
- TanStack Query scopes active-load data by tenant and invalidates all load queries
  before switching tenants, preventing stale tenant data from being displayed.
- The active-load desk shows route, equipment, pickup date, distance, persisted
  expected rate when available, confidence, and status, with loading, empty, and
  error states. The list endpoint now supplies stop timing and persisted current
  decision summaries without implicitly recomputing decisions.
- Component tests cover rendered summaries, tenant switching/storage, cache
  invalidation, loading, empty, and error states.
- **Task 12.2 complete:** selecting a load retrieves its tenant-scoped persisted
  decision and displays canonical route/timing, expected carrier rate, historical
  comparison range, confidence, retrieval/fallback tier, raw/effective evidence,
  comparable completed-load summaries, and explicit warnings. `as_of` and model
  versions remain in a secondary native disclosure. The UI never calls the range a
  prediction interval or recomputes a decision. Component tests cover high,
  medium, low, and insufficient-data states.
- **Task 12.3 complete:** ranked carrier cards render persisted rank order,
  adjusted historical-fit score, derived confidence, component breakdown,
  fixed evidence bullets, and expandable tenant-local evidence IDs. Historical
  delivery distance/time wording appears only when the persisted bullet supplies
  it; missing deadhead and sparse-history conditions render explicit warnings.
  The surrounding language states historical-fit evidence only and never claims
  availability, acceptance, or reliability.
- **Task 12.4 complete:** Playwright starts an isolated Vite review server and
  uses the deterministic `SC-24` exact/rich and `SC-26` sparse Day 11 scenario
  identities. Semantic browser flows verify rate/range/confidence/top evidence,
  sparse fallback/low-confidence messaging, tenant-switch cache isolation, and
  identical generic `404` responses for cross-tenant and unknown load IDs.

## Phase 13 — Tenancy, corrections, and historical hardening — In progress

- **Task 13.1 complete:** completed the tenant-scope audit across the tenant-owned
  schema, RLS policy, ORM/repository queries, API routes, feature/decision paths,
  frontend cache keys, and evidence payloads. Forced RLS remains mandatory defense
  in depth. The demo header now admits only server-authored broker bindings.
  Decision JSONB evidence is re-authorized against tenant-local immutable load
  versions before it can be serialized; foreign or malformed embedded evidence
  fails closed. Pricing's internal target-version lookup is explicitly tenant-scoped.
  Direct SQL and API regression coverage verifies the boundary.
- **Task 13.2 complete:** the production estimator and ranker serialize Tenant B's
  Day 11 answer before and after a valid, pre-cutoff Tenant A completed load is
  inserted; the bytes are identical. Direct SQL confirms the non-owner app role
  cannot select, update, or delete Tenant B rows under Tenant A context. API tests
  keep other-tenant load IDs generic-not-found and exclude foreign carrier/evidence
  IDs. Matching MC/DOT values create distinct tenant-local carrier rows.
- **Task 13.3 complete:** generated-scenario integration coverage proves
  FreightFlow and BrokerOS replacement corrections create immutable new versions and
  update current projections; HaulDesk's ledger adjustment is applied exactly once
  across re-ingestion; a late correction changes Day 11 rate evidence without
  changing an earlier persisted decision; historical backtests retain the correction
  only as a later label; and rebuild reproduces corrected projection state.
  `make correction-demo` runs this concise proof.
- **Task 13.4 complete:** temporal-data audit traced comparable retrieval, pricing,
  carrier features/ranking, decision persistence, and both backtest harnesses.
  Historical model paths use immutable `LoadVersion`/`SourceRateEntry` facts at or
  before `as_of`; current projections are used only to enumerate eventual evaluation
  labels or serve current API state. Pricing and decision-evidence rehydration now
  repeat the cutoff predicate rather than relying solely on upstream evidence
  invariants. Regression coverage inserts future carrier assignment, replacement
  rate, and correction versions before an earlier cutoff and proves they cannot enter
  historical comparables, estimates, rankings, persisted evidence, or backtests.

  - **Phase 13 gate — Complete** Automated API/service, direct-SQL RLS, prediction-invariance, generated-correction,
  and temporal-leakage integration coverage passes together. It proves tenant-local
  present state and historical reconstruction remain isolated and correction-safe.

## Phases 14–16 — In progress

- **Phase 14, Task 14.1 complete:** backend now uses a dependency-builder/runtime
  Dockerfile and both final application images run non-root. Compose has explicit
  database, migration, backend, frontend, and optional `initialize` services;
  backend readiness verifies a live app-role database connection; frontend/backend
  wait on healthy dependencies; and `initialize` mounts generated source data
  read-only, validates it, ingests it, and persists Day 11 decisions.
- **Phase 14, Task 14.2 complete:** the root Makefile now exposes the complete
  documented repository command surface, including explicit `test`, `e2e`,
  `rebuild`, and guarded `reset` targets. `make demo` deterministically recreates
  the dedicated demo database, validates/ingests source syncs chronologically,
  persists Day 11 decisions, builds and starts the API/UI, and prints both URLs and
  the three demo brokers. README contains the command reference and reset boundary.
- **Phase 14, Task 14.3 complete:** GitHub Actions CI now has explicit backend,
  frontend/OpenAPI, PostgreSQL/data-smoke, and Playwright jobs. Every job installs
  from the uv or pnpm lockfile; PostgreSQL is health-checked before migrations;
  CI runs lint/type/unit/integration/RLS checks, deterministic generation and
  validation, chronological ingestion, Day 11 decisions, generated-type freshness,
  and browser review coverage. Failure-only database and Playwright artifacts are
  uploaded where available.
- **Phase 14, Task 14.4 complete:** a clean, disposable committed snapshot with
  an empty Compose volume was evaluated using README commands only. The first run
  exposed a database-readiness race in `make demo` and missing tool prerequisites;
  `db-up` now waits for the Compose database health check, and README names Docker
  Compose, Python/uv, and Node/pnpm requirements. The repaired snapshot completed
  `make setup`, `make demo`, `make check`, and `make backtest`, and live API
  inspection confirmed both a rich and a sparse Day 11 decision.
- **Phase 14 gate — Complete.** A clean environment reaches the UI, ingests data,
  computes decisions, runs checks, and writes backtest artifacts with documented
  commands only.
- Phase 15: documentation and review preparation.
- Phase 16: optional shared carrier pool.

## Historical verification archive

- Phase 14.2 repository commands: Make dry-runs confirm required dependency order
  for `reset`, `demo`, `rebuild`, `test`, `e2e`, `backtest`, `api-types`, and
  `check`. Backend/unit and frontend tests: `196 passed, 26 skipped`; database
  integration target: `7 passed`; Playwright: `3 passed`; focused API tests:
  `6 passed`; Make/OpenAPI generation and diff whitespace checks pass.
- Phase 14.3 CI: `actionlint` and Prettier validate the workflow. `make check`
  passes after applying the repository formatters; Playwright passes (`3`); and the
  local equivalent of the PostgreSQL CI job passes migrations plus all `222` backend
  tests (including RLS), deterministic generation/validation of `123` files,
  chronological ingestion, and `3` persisted Day 11 decisions.
- Phase 14 skipped-test audit: all `26` tests that skip only when `DATABASE_URL`
  is absent were run against PostgreSQL with the separate non-owner app role and
  passed. This covers persistence, direct-SQL RLS, temporal leakage, comparable
  retrieval, source persistence/failure/correction/rebuild behavior, generated-data
  ingestion smoke, precedence, and tenant-safe API contracts.
- Phase 14.4 clean-clone rehearsal: an initial empty-volume `make demo` failed
  because `dropdb` ran before PostgreSQL accepted connections. After changing
  `db-up` to Compose `--wait` and documenting required local tools, a fresh clean
  snapshot passed `make setup`, `make demo`, `make check`, and `make backtest`.
  It produced all `123` source files, three persisted Day 11 decisions, and the
  backtest artifacts. API inspection verified rich FreightFlow load `888509`
  (`NEAR_EXACT`, 5 raw / 4.46 effective observations, High) and sparse HaulDesk
  load `HD-9001` (broader fallback, 6 raw / 3.26 effective observations, Medium).
- Phase 14.1 container hardening: Compose configuration and clean image builds pass.
  A separate `carrier-pool-phase14` project with an empty named volume completed
  migrations, reached healthy DB/backend/frontend services, returned `/ready` and the
  UI HTML, then ran the optional read-only initializer: `123` sync files validated and
  ingested, with `3` Day 11 decisions created. The isolated project and its volume
  were removed afterward. Final images run as `app` UID `999` and `nginx` UID `101`.

- Phase 13.1 tenant-boundary audit: focused Ruff and Pyright pass. Tenant-header,
  PostgreSQL API-contract, and direct-SQL RLS coverage: `7 passed`. The new
  regression persists a deliberately malformed decision payload containing another
  tenant's immutable evidence ID and verifies the API omits its ID, route, and rate
  rather than serializing the embedded JSONB fields.
- Phase 13.2 prediction-invariance coverage: API-contract, direct-SQL RLS, and
  catalog tests: `15 passed`. This includes
  `test_broker_a_history_never_changes_broker_b_prediction`, byte-for-byte
  serialization before/after added Tenant A history, direct cross-tenant update and
  delete denial, generic cross-tenant load/carrier not-found behavior, foreign
  carrier/evidence exclusion, and distinct records for intentionally matching
  MC/DOT values.
- Phase 13.3 generated-correction proof: `make correction-demo` migrates the
  local database and passes `2` focused integration tests. It verifies FreightFlow
  and BrokerOS replacement totals, one-time HaulDesk ledger adjustment totals, a
  late correction changing Day 11 rate evidence, immutable earlier decision output,
  cutoff-safe backtest evidence, and corrected projection rebuild parity.
- **Task 13.3 complete:** generated-scenario integration coverage now proves
  FreightFlow and BrokerOS replacement corrections create immutable new versions and
  update their current projections; HaulDesk's positive ledger adjustment is applied
  exactly once across re-ingestion; a late FreightFlow correction changes Day 11
  historical-rate evidence while an earlier persisted decision remains byte-stable;
  the historical backtest keeps the correction as a later label rather than an input;
  and rebuild reproduces the corrected current projection hash. `make correction-demo`
  runs this concise proof.
- Phase 13.4 temporal-leakage audit: focused Ruff/Pyright pass; decisioning,
  comparable-retrieval, generated-correction, and new temporal-leakage integration
  coverage: `47 passed`. The regression retains a future-assigned carrier and a
  future replacement rate/correction in the database, but every Day 5 model input
  and persisted decision uses only the Day 4 completed version.
- Phase 13 gate: API contract/prediction invariance, direct-SQL RLS, generated
  corrections, and temporal-leakage suite: `12 passed`. Ruff passes for all gate
  files. The combined Pyright command reports pre-existing test-only unknown-type
  diagnostics in `test_api_contract_integration.py`; source and new temporal test
  Pyright checks pass, and this does not affect the passing gate behavior.

- Ranking honesty correction: `carrier-ranking-v4` preserves unavailable
  components as `null`, uses tier-accurate rather than blanket directional language,
  separates limited-relevant-history carriers from supported call-order candidates,
  and groups supported scores within two points as no meaningful historical
  separation. Persisted decisions now retain human-readable component evidence.
  Confidence now uses exact-equipment evidence coverage rather than equipment-fit
  quality. Fresh demo validation rebuilt 123 source syncs, ingested them, persisted
  3 Day 11 decisions, and wrote a 24-case ranking report: 14 scored cases across
  FreightFlow (4), HaulDesk (5), and BrokerOS (5), with 14 close-score ties and
  10 clear supported tops. The no-deadhead ablation had equal top-1 recall and only
  a small MRR increase (`0.6786` vs `0.6607`); it is not evidence to tune weights.
  This remains temporal synthetic-holdout coverage, not production performance evidence.
  Generated-ingestion integration coverage now also proves a later immutable load
  correction exists yet cannot enter feature evidence or change a ranking rebuilt
  at that load's earlier `ACTIVE` cutoff.
- Curated route-mile correction: generator catalog now assigns each ordered route
  a deterministic highway-mile value instead of emitting the former `242.1` for
  every load. Day 11 output verifies FreightFlow `239.4 mi`, HaulDesk `430.5 km`
  (`267.5 mi` canonical), and BrokerOS `193.6 mi`. Generator catalog/lifecycle/
  serializer tests (`15 passed`), Ruff, `make generate`, `make validate`, frontend
  component tests, lint, typecheck, production build, and `git diff --check` pass.
- Schedule-source clarity: active-load cards and decision detail now distinguish
  source-provided appointment times from date-only plans, using “Planned dates
  only — no appointment times supplied” rather than making date-only HaulDesk or
  BrokerOS records look equivalent to FreightFlow appointment windows. Frontend
  component tests, Playwright review path, lint, typecheck, and build pass.
- Ranking evaluation coverage correction: rolling scheduler now reuses known
  tenant-local carriers for later loads, while retaining first-observation labels
  as explicit no-rank diagnostics. Ranking artifacts now separate `case_count`,
  `scored_case_count`, and `no_rank_count`; recall/MRR are conditional on scored
  cases and never hide no-rank cases. Reset demo backtest: 15 labels, 11 scored,
  4 no-rank; 4 rich and 7 sparse scored cases. The synthetic scored top-1 result
  is `1.0`, so it is coverage evidence only—not proof of production accuracy or a
  reason to tune weights. The deadhead ablation changes MRR from `1.0` to `0.9545`.
- Schedule-display correction: FreightFlow stop windows are now ordered by stop
  sequence; HaulDesk normalization preserves strict source pickup/delivery dates;
  date-only schedules are exposed as `planned_date` through the generated API
  contract and displayed without invented times. A reset `make demo` confirms Day
  11 Grand Prairie→Katy at `Jul 11 13:00` then `Jul 12 20:00` UTC, Plano→Baytown
  at pickup `Jul 11`/delivery `Jul 12`, and Katy→San Antonio at pickup `Jul 11`/
  delivery `Jul 12` rather than “pending” or same-day schedules.
- Ranking v2 correction: `make generate && make validate`, resettable `make demo`,
  and demo-database `make backtest` pass. Ranking contracts: `7 passed`; focused
  Ruff and Pyright: `0` errors. The clean 18-case demo backtest has 15 ranking
  cases (all sparse; 12 booked carriers were not candidates at cutoff), top-1
  `0.1333`, top-3 `0.2`, and paired deadhead MRR `0.1556` versus `0.1667` without
  deadhead. This is diagnostic evidence only, not a reason to tune weights.
- `make backtest`: pass; wrote rate artifacts and `artifacts/ranking_metrics.json`
  for `494` historical cases (`345` rate-scored).
- Phase 10 ranking evaluation/scoring tests: `4 passed`. Focused Ruff passes.
- Full backend Pyright: `0 errors, 0 warnings, 0 informations` after typed
  boundaries for baseline-regression outputs, generated schedule loads, and
  canonical JSON stop parsing.
- Phase 10 Day 11 ranking gate smoke: `1 passed` against local Compose PostgreSQL.
- Phase 11 decision-run persistence smoke: `1 passed`; decision identity/scoring
  tests: `4 passed`; full backend Pyright: `0 errors, 0 warnings, 0 informations`.
- Phase 11 API contracts: `6 passed`; generated-data decision smoke: `2 passed`.
- OpenAPI type generation/staleness check: pass; frontend typecheck, tests, lint,
  and production build: pass.
- Phase 11 real API contracts: `3 passed` (`8` API tests including unit contracts);
  full backend Pyright: `0 errors, 0 warnings, 0 informations`.
- Phase 11 gate suite: `10 passed`; OpenAPI freshness, frontend typecheck/tests/lint,
  production build, and `git diff --check`: pass.
- Phase 12.1 frontend components: `5 passed`; frontend typecheck, lint, and
  production build pass. Focused API contracts: `5 passed`; Ruff passes.
- Phase 12.2 frontend components: `9 passed`; frontend typecheck, lint, and
  production build pass; `git diff --check` passes.
- Phase 12.3 frontend components: `11 passed`; frontend typecheck, lint, and
  production build pass; `git diff --check` passes.
- Phase 12.4 browser review path: `2 passed` in Chromium; frontend component and
  contract suite (`11 passed`), typecheck, lint, production build, and
  `git diff --check` pass.
- Phase 12 accessibility sign-off fixes: carrier rank is visible text rather than
  an aria-label on a non-semantic container, and long monospace evidence IDs wrap
  safely on narrow cards. Focused component tests (`10 passed`), frontend
  typecheck, lint, production build, browser review path (`2 passed`), and
  `git diff --check` pass.
- Local review deployment: rebuilt backend/frontend images serve the current Phase
  12 bundle; Nginx proxies `/api/` to the backend, and the application role can
  read the safe tenant directory. Migration contract tests (`2 passed`), frontend
  component tests (`10 passed`), typecheck, lint, build, and live proxy check pass.
- Legacy persisted decisions without an explicit currency now default to documented
  USD during API serialization. Focused API integration test passes; Ruff, Pyright,
  `git diff --check`, and the live Day 11 decision endpoint pass.
- Frontend presentation refresh: the review UI is now a responsive dispatcher
  workbench with a compact utility header, active-load rail, persistent decision
  workspace, selected-load state, and an explicit pre-selection prompt. Focused
  component tests (`10 passed`), browser review path (`2 passed`), typecheck, lint,
  build, container rebuild, and visual desktop inspection pass.
- Frontend visual refinement: replaced the decorative teal/blue console treatment
  with a restrained graphite operations desk: warm neutral type, one amber signal
  color, square utility surfaces, tabular numeric treatment, and no decorative motion.
  Live browser inspection, component tests (`12 passed`), Playwright review path
  (`2 passed`), lint, typecheck, production build, and container rebuild pass.
- Frontend explanation-language pass: primary decision labels now explain matching,
  weighted historical evidence, carrier-ranking reasons, and supporting completed
  loads in dispatch language. Model/version/audit metadata remains in the secondary
  disclosure. Focused component tests (`12 passed`), lint, typecheck, production
  build, and diff checks pass.
- Decision explanation consistency pass: displayed raw/effective evidence now covers
  the same resolved comparison set shown in the UI; effective sample size is rounded
  to one decimal; raw warning codes are translated and deduplicated; carrier notes are
  deduplicated; ranking factors use plain-language labels; and collapsed weighted
  quantiles show the observed comparable-rate spread instead of a misleading flat
  comparison range. Pricing/API tests (`12 passed`), frontend component tests (`13
  passed`), Playwright review path (`2 passed`), Ruff, Pyright, build, diff checks,
  and a regenerated `make demo` pass.
- Frontend copy cleanup: removed ornamental/marketing labels and replaced the empty
  decision-state language with the direct instruction “Select a load.” Frontend
  component tests (`13 passed`), Playwright (`2 passed`), lint, typecheck, build,
  and diff checks pass.
- Reproducible review-demo path: `make demo` now applies migrations, seeds the three
  fixed broker/source bindings, generates and validates deterministic data, ingests
  it chronologically, persists Day 11 decisions, and starts the UI. The public tenant
  directory is limited to those bindings, and stale browser selections are replaced
  before any broker data is requested. Live directory/load checks and focused backend
  and frontend tests pass.
- Demo database isolation: `make demo` resets only `carrier_pool_demo`, while normal
  local and integration-test work remains in `carrier_pool`. Two complete demo runs
  passed, each generated/validated `123` sync files and created `3` Day 11 decisions
  before starting the UI. The isolated demo database has `3` tenants versus `560` test
  and development tenants in the primary database; live API checks confirmed all three
  demo tenants have an active load with persisted pricing and carrier evidence. API
  integration coverage (`5 passed`) proves the public directory excludes deliberately
  created integration-fixture tenants; Ruff, Pyright, Compose configuration, and diff
  checks pass.
- Prior baseline verification: `make test-integration`: `7 passed`; full backend
  suite with `DATABASE_URL`: `182 passed` (before Phase 10 additions).
- Comparable-load evidence now has a tenant-safe typed API contract and compact
  desktop table: source load label, route/equipment, carrier pay, readable endpoint
  match, and completion observation. Database UUIDs remain stored audit facts but are
  not returned as comparable display fields. Focused API contracts (`4 passed`),
  frontend components (`14 passed`), frontend typecheck/lint/build, backend Ruff and
  Pyright, generated OpenAPI types, `git diff --check`, live API inspection, and a
  regenerated `make demo` pass.
- UI evidence-strength audit: Day 11 pricing has one High and two Medium outcomes;
  the High case has strong near-exact, equipment-known, recent evidence, so model
  thresholds were intentionally left unchanged. The UI now maps confidence to
  Strong/Moderate/Limited evidence, keeps numeric scores internal, shows plain
  comparable/effective/tier facts, and provides an expandable reason for any
  non-strong result. Frontend components (`14 passed`), lint, typecheck, production
  build, and Playwright review path (`2 passed`) pass.

## Latest verification — 2026-07-28

- **Clean evaluator path:** a disposable committed snapshot with an empty Docker
  volume passed `make setup`, `make demo`, `make check`, and `make backtest` using
  README instructions only. It reached healthy UI/API services, validated and
  ingested `123` sync files, persisted `3` Day 11 decisions, and wrote backtest
  artifacts.
- **Decision review:** rich FreightFlow `888509` returned near-exact, High-evidence
  pricing; sparse HaulDesk `HD-9001` returned its documented broader fallback and
  Medium evidence.
- **Automated checks:** `make check` and Playwright (`3 passed`) pass. All
  PostgreSQL-gated tests were also run explicitly: `26 passed`; the complete
  database-enabled backend suite: `222 passed`.
- **CI correction:** GitHub run `#18` exposed two independent quality-job defects:
  API generation was not formatted before freshness comparison, and global database
  URLs made the backend unit job run integration tests without a PostgreSQL service.
  Generated OpenAPI artifacts are now formatted deterministically and database URLs
  are scoped to the PostgreSQL job. `make api-types-check`, backend unit tests
  (`196 passed, 26 expected DB skips`), frontend lint/type/tests/build, and
  `actionlint` pass locally.
- **Historical-delivery evidence:** newly created decisions now persist and expose
  the tenant-local last-delivery-to-pickup distance and observation-time gap. The UI
  renders these only when both facts exist, labels them as historical records rather
  than live location, and the refreshed demo returned `485834` at `228 mi` and
  `0.5` days. API contracts (`9 passed`) and frontend tests (`14 passed`) pass.

## Historical verification archive (continued)

- Comparable-load table refinement: compact fixed columns now keep source IDs,
  routes, carrier pay, match evidence, and completion dates on one visual row;
  long audit IDs/routes use an ellipsis with the full value on hover. Match prose is
  reduced to a tier and endpoint summary. A fresh `make demo` confirmed current
  evidence details and visual rendering; frontend unit tests (`14 passed`), lint,
  typecheck, build, Playwright (`2 passed`), and diff checks pass.
- Mobile overflow correction: the scrollable comparable table had propagated its
  minimum width through nested grid items, causing the complete mobile workspace to
  render at desktop width. Grid boundaries now explicitly shrink. A Playwright
  narrow-viewport regression asserts page `scrollWidth == clientWidth`; all three
  browser review tests pass and live mobile inspection confirms the fix.
- Comparable-table layout refinement: the active-load rail is narrower and the
  decision workspace wider. The table now fits its desktop allocation without a
  scrollbar; on narrow screens every comparable becomes a compact, non-scrolling
  evidence row. Frontend tests (`14 passed`), lint, typecheck, build, Playwright
  (`3 passed`), desktop/mobile visual checks, and a fresh `make demo` pass.
- Comparable-table layout refinement: the active-load rail is narrower and the
  decision workspace wider. The table now fits its desktop allocation without a
  scrollbar; on narrow screens every comparable becomes a compact, non-scrolling
  evidence row. Frontend tests (`14 passed`), lint, typecheck, build, Playwright
  (`3 passed`), desktop/mobile visual checks, and a fresh `make demo` pass.
- Mobile overflow correction: the scrollable comparable table had propagated its
  minimum width through nested grid items, causing the complete mobile workspace to
  render at desktop width. Grid boundaries now explicitly shrink. A Playwright
  narrow-viewport regression asserts page `scrollWidth == clientWidth`; all three
  browser review tests pass and live mobile inspection confirms the fix.
- Comparable-load table refinement: compact fixed columns now keep source IDs,
  routes, carrier pay, match evidence, and completion dates on one visual row;
  long audit IDs/routes use an ellipsis with the full value on hover. Match prose is
  reduced to a tier and endpoint summary. A fresh `make demo` confirmed current
  evidence details and visual rendering; frontend unit tests (`14 passed`), lint,
  typecheck, build, Playwright (`2 passed`), and diff checks pass.
- UI evidence-strength audit: Day 11 pricing has one High and two Medium outcomes;
  the High case has strong near-exact, equipment-known, recent evidence, so model
  thresholds were intentionally left unchanged. The UI now maps confidence to
  Strong/Moderate/Limited evidence, keeps numeric scores internal, shows plain
  comparable/effective/tier facts, and provides an expandable reason for any
  non-strong result. Frontend components (`14 passed`), lint, typecheck, production
  build, and Playwright review path (`2 passed`) pass.
