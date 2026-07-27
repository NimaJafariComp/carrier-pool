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

## Phases 6–16 — Not started

- Phase 6: deterministic scenario data generator.
- Phase 7: complete chronological ingestion/rebuild logic.
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

- Backend suite with PostgreSQL: `110 passed`.
- PostgreSQL integration target: `7 passed` (temporal persistence, direct-SQL
  RLS, FreightFlow replacement/idempotency, HaulDesk ledger, BrokerOS restatement).
- Ruff: pass. Pyright: `0 errors, 0 warnings, 0 informations`.
