# Phase 3 database design

## Scope and invariants

PostgreSQL 18. UUID primary keys. `timestamptz` always UTC. Money `numeric(14,2)`
plus `char(3)` currency; never float. Source IDs remain `text`. Canonical enum
values stored as PostgreSQL enums: `source_system`, `load_status`,
`equipment_type`, `financial_side`, `ingestion_status`.

Every tenant-owned table has `tenant_id uuid not null references tenants(id)`.
All unique source identities include `(tenant_id, source_system, external_id)`.
No status-transition constraint: source corrections may regress status.

Immutable facts never update or delete after successful ingestion. Current tables
are rebuildable projections, not history. `as_of` means latest immutable version
with `observed_at <= :as_of`; current projections must not answer it.

## Tables

### `tenants`

Purpose: broker boundary; source system is configuration, not tenant identity.

| Column | Type | Notes |
|---|---|---|
| `id` | `uuid` | PK, generated UUID |
| `slug` | `text` | stable human identifier |
| `name` | `text` | broker name |
| `source_system` | `source_system` | configured primary source; not unique |
| `pool_opt_in` | `boolean` | default `false`; no pool tables in Phase 3 |
| `created_at`, `updated_at` | `timestamptz` | metadata |

Constraints/indexes: `unique (slug)`; index `source_system` only if tenant
selection needs it. Mutable: `name`, `pool_opt_in`, `updated_at`. Immutable:
`id`, `slug`, `created_at`. This table is admin-owned; no tenant RLS policy is
required because app tenancy is resolved before querying tenant data.

### `ingestion_files`

Purpose: one observed source file, idempotency boundary, audit and processing
result.

| Column | Type | Notes |
|---|---|---|
| `id` | `uuid` | PK |
| `tenant_id` | `uuid` | FK `tenants(id)` |
| `source_system` | `source_system` | source that produced file |
| `relative_path`, `file_name` | `text` | provenance |
| `sha256` | `char(64)` | content checksum |
| `raw_payload` | `jsonb` | immutable parsed source file for file-level audit |
| `sync_at`, `source_at`, `observed_at` | `timestamptz` | filename/payload/platform times; `source_at` nullable |
| `status` | `ingestion_status` | `PROCESSING`, `COMPLETED`, `FAILED` |
| `started_at`, `completed_at` | `timestamptz` | completion nullable |
| `loads_seen`, `versions_created`, `projections_updated`, `warnings_count`, `errors_count` | `integer` | nonnegative, default `0` |
| `error_details` | `jsonb` | nullable failure diagnostics |
| `created_at`, `updated_at` | `timestamptz` | metadata |

PK `id`; FK tenant. Unique `unique (tenant_id, sha256)`. Indexes:
`(tenant_id, source_system, sync_at)`, `(tenant_id, status, observed_at)`, and
`(tenant_id, file_name)`. Immutable after completion: checksum, raw payload,
source identity, paths, timestamps, counters, diagnostics. Mutable only while processing:
`status`, completion fields, counters, diagnostics, `updated_at`; failed records
may be retried by new checksum or explicit controlled reset.

### `customers`

Purpose: current tenant-local customer identity/projection.

| Column | Type | Notes |
|---|---|---|
| `id` | `uuid` | PK |
| `tenant_id` | `uuid` | FK `tenants(id)` |
| `source_system` | `source_system` | source namespace |
| `external_id` | `text` | canonical string ID |
| `name` | `text` | current observed name |
| `first_observed_at`, `last_observed_at`, `created_at`, `updated_at` | `timestamptz` | provenance/projection metadata |

Unique `unique (tenant_id, source_system, external_id)`. Index current lookup
`(tenant_id, id)` and name filter `(tenant_id, name)`. Mutable projection fields:
name, `last_observed_at`, `updated_at`; identity and first observation immutable.
Customer history is retained through immutable load snapshots; a separate customer
version table is not needed for Phase 3 because no customer-only history query is
planned.

### `carriers`

Purpose: current tenant-local carrier identity/projection. Same MC/DOT across
tenants never merges records.

| Column | Type | Notes |
|---|---|---|
| `id` | `uuid` | PK |
| `tenant_id` | `uuid` | FK `tenants(id)` |
| `source_system` | `source_system` | source namespace |
| `external_id` | `text` | canonical string ID |
| `name`, `normalized_name` | `text` | current name/retrieval aid |
| `mc_number`, `dot_number`, `phone_number` | `text` | nullable, tenant-local facts |
| `home_city`, `home_state` | `text` | nullable |
| `first_observed_at`, `last_observed_at`, `created_at`, `updated_at` | `timestamptz` | provenance/projection metadata |

Unique `unique (tenant_id, source_system, external_id)`. Indexes:
`(tenant_id, id)`, `(tenant_id, mc_number)` where non-null,
`(tenant_id, dot_number)` where non-null, and `(tenant_id, normalized_name)`.
Mutable: descriptive/current fields and last observation. Immutable: identity,
first observation. No unique MC/DOT constraint: TMS quality and future pool policy
must not block valid tenant-local records.

### `carrier_versions`

Purpose: immutable carrier observations when a source reports changed details.

| Column | Type | Notes |
|---|---|---|
| `id` | `uuid` | PK |
| `tenant_id`, `carrier_id`, `ingestion_file_id` | `uuid` | FKs to tenant/carrier/file |
| `source_modified_at`, `observed_at` | `timestamptz` | canonical time model |
| `snapshot_hash` | `char(64)` | canonical carrier snapshot hash |
| `canonical_snapshot`, `raw_snapshot` | `jsonb` | normalized/raw evidence |
| `supersedes_id` | `uuid` | nullable self-FK |
| `created_at` | `timestamptz` | insert time |

Unique `unique (tenant_id, carrier_id, snapshot_hash)` prevents duplicate version
facts. Index for as-of carrier reconstruction:
`(tenant_id, carrier_id, observed_at desc, id desc)`. All fields immutable.

### `loads`

Purpose: mutable current projection for UI/live selection only.

| Column | Type | Notes |
|---|---|---|
| `id` | `uuid` | PK |
| `tenant_id` | `uuid` | FK `tenants(id)` |
| `source_system` | `source_system` | source namespace |
| `external_id`, `load_number` | `text` | external ID required; number nullable |
| `customer_id`, `carrier_id` | `uuid` | nullable FKs; customer normally set, carrier lifecycle nullable |
| `status`, `equipment` | enums | equipment nullable only when source omitted it; `UNKNOWN` stored explicitly |
| `customer_rate_amount`, `carrier_rate_amount` | `numeric(14,2)` | nullable lifecycle values |
| `currency` | `char(3)` | default/check `USD` |
| `weight_lbs` | `numeric(14,3)` | nullable, nonnegative |
| `distance_miles` | `numeric(12,3)` | nullable, nonnegative |
| `source_created_at`, `source_modified_at`, `observed_at` | `timestamptz` | latest source/current observation |
| `current_version_id` | `uuid` | non-null FK `load_versions(id)` after first projection |
| `created_at`, `updated_at` | `timestamptz` | projection metadata |

Unique `unique (tenant_id, source_system, external_id)`. Indexes:
`(tenant_id, status, observed_at desc)` for active-current list;
`(tenant_id, current_version_id)`; `(tenant_id, carrier_id, status)`; and
`(tenant_id, customer_id)`. Projection fields are mutable only through rebuild;
identity and `created_at` immutable. A composite FK from `(tenant_id,
current_version_id)` to the version's tenant-compatible identity should be added
if migration ordering permits; service rebuild must enforce it regardless.

### `load_versions`

Purpose: immutable normalized load observation. Source truth for restatements and
all historical reconstruction.

| Column | Type | Notes |
|---|---|---|
| `id` | `uuid` | PK |
| `tenant_id`, `load_id`, `ingestion_file_id` | `uuid` | FKs |
| `source_modified_at`, `observed_at` | `timestamptz` | as-of ordering uses observed time |
| `status`, `equipment` | enums | denormalized retrieval fields; `UNKNOWN` distinct |
| `customer_id`, `carrier_id` | `uuid` | nullable carrier only |
| `customer_rate_amount`, `carrier_rate_amount` | `numeric(14,2)` | nullable restated totals |
| `currency` | `char(3)` | default/check `USD` |
| `weight_lbs`, `distance_miles` | `numeric(14,3)`, `numeric(12,3)` | nullable, nonnegative |
| `canonical_snapshot`, `raw_snapshot` | `jsonb` | full normalized and raw source objects |
| `snapshot_hash` | `char(64)` | canonical plus source-content hash |
| `supersedes_id` | `uuid` | nullable self-FK to prior accepted version |
| `created_at` | `timestamptz` | insert time |

Unique `unique (tenant_id, load_id, snapshot_hash)`; duplicate unchanged source
facts create no version. Indexes:
`(tenant_id, load_id, observed_at desc, id desc)` for `as_of` latest version;
`(tenant_id, observed_at desc)` for rebuild cutoff;
`(tenant_id, status, observed_at desc)` for historical active-load retrieval;
`(tenant_id, carrier_id, observed_at desc)` for evidence; and a GIN index on
`canonical_snapshot` only after JSON-path query need is proven.

All fields immutable. FreightFlow and BrokerOS later full-load snapshots create a
new row whenever hash changes, point `supersedes_id` at prior version, and then
advance `loads.current_version_id`. Corrected money, equipment, stops, and status
are facts in later versions; earlier values remain queryable.

### `stops`

Purpose: mutable current ordered route projection for current UI and retrieval.

| Column | Type | Notes |
|---|---|---|
| `id` | `uuid` | PK |
| `tenant_id`, `load_id` | `uuid` | FKs |
| `sequence` | `integer` | starts at 1 |
| `is_pickup`, `is_dropoff` | `boolean` | independent; both allowed |
| `facility_name`, `city`, `state`, `postal_code` | `text` | facility nullable; location required |
| `latitude`, `longitude` | `numeric(9,6)` | nullable curated-geography output |
| `h3_fine`, `h3_coarse` | `text` | nullable retrieval aids |
| `scheduled_start_at`, `scheduled_end_at`, `actual_arrival_at`, `actual_departure_at` | `timestamptz` | nullable lifecycle values |
| `created_at`, `updated_at` | `timestamptz` | projection metadata |

Unique `unique (tenant_id, load_id, sequence)`. Indexes:
`(tenant_id, load_id, sequence)` and partial retrieval indexes
`(tenant_id, h3_fine) where is_pickup`, `(tenant_id, h3_fine) where is_dropoff`.
Rows are deleted/reinserted or upserted only during projection rebuild; not
historical source of truth. Sequence, pickup/drop-off flags, schedule and actual
times are versioned inside the parent load snapshot.

### `source_rate_entries`

Purpose: immutable source financial facts, primarily HaulDesk ledger rows.

| Column | Type | Notes |
|---|---|---|
| `id` | `uuid` | PK |
| `tenant_id`, `load_id`, `ingestion_file_id` | `uuid` | FKs |
| `source_system` | `source_system` | source namespace |
| `external_id` | `text` | source rate ID as canonical string |
| `side` | `financial_side` | `BILL` or `PAY` |
| `code` | `text` | e.g. `LINEHAUL`, `FUEL`, `ADJUSTMENT` |
| `amount` | `numeric(14,2)` | negative allowed |
| `currency` | `char(3)` | default/check `USD` |
| `source_created_at`, `observed_at`, `created_at` | `timestamptz` | temporal provenance |
| `raw_snapshot` | `jsonb` | source evidence |

Unique `unique (tenant_id, source_system, external_id)`. Indexes:
`(tenant_id, load_id, observed_at, side)` for as-of sums;
`(tenant_id, source_system, external_id)`; and
`(tenant_id, load_id, source_created_at)`. All fields immutable.

HaulDesk each appended `rate_id` inserts exactly once; negative adjustment is a
new row, never update/delete. As-of bill/pay total is `sum(amount)` filtered by
tenant/load/side and `observed_at <= :as_of`. FreightFlow and BrokerOS totals are
not synthesized ledger entries: their restated totals remain in `load_versions`.

### `decision_runs`

Purpose: immutable persisted output of one requested historical-fit decision.

| Column | Type | Notes |
|---|---|---|
| `id` | `uuid` | PK |
| `tenant_id`, `load_id`, `input_version_id` | `uuid` | FKs; input version is exact target state |
| `as_of` | `timestamptz` | required explicit cutoff |
| `ranking_model_version`, `pricing_model_version` | `text` | deterministic model identity |
| `model_parameters` | `jsonb` | immutable parameters/thresholds |
| `price_estimate`, `confidence`, `evidence_summary` | `jsonb` | displayed structured data |
| `created_at` | `timestamptz` | run time |

No tenant-scoped natural uniqueness: repeated deterministic runs are valid audit
events. Indexes `(tenant_id, load_id, as_of desc, created_at desc)` and
`(tenant_id, input_version_id)`. All fields immutable.

### `carrier_recommendations`

Purpose: immutable ranked carrier rows belonging to a decision run.

| Column | Type | Notes |
|---|---|---|
| `id` | `uuid` | PK |
| `tenant_id`, `decision_run_id`, `carrier_id` | `uuid` | FKs |
| `rank` | `integer` | positive deterministic ordinal |
| `raw_score`, `adjusted_score`, `confidence` | `numeric(8,4)` | score values; not money |
| `component_values`, `explanation_reason_codes`, `evidence_ids` | `jsonb` | structured, tenant-local evidence |
| `created_at` | `timestamptz` | run copy for audit |

Unique `unique (tenant_id, decision_run_id, rank)` and
`unique (tenant_id, decision_run_id, carrier_id)`. Indexes
`(tenant_id, decision_run_id, rank)` and `(tenant_id, carrier_id, created_at desc)`.
All fields immutable. A trigger or application invariant verifies recommendation
tenant matches both decision run and carrier.

## JSONB child-version decision

Keep versioned stops, BrokerOS line items/commodities, source-only detail, and
future source fields in `load_versions.canonical_snapshot`; retain original object
in `raw_snapshot`. Do not make versioned stop child tables in Phase 3. Reason:
every load version is atomically observed as one source snapshot; versioned child
rows multiply history tables and consistency rules without a current query need.
`stops` is normalized current projection for ordered route and geography queries.
If historical stop-level spatial filtering becomes a measured need, add immutable
`load_version_stops` derived solely from existing snapshots; never replace them.

## Ingestion, restatement, and rebuild

Ingest one chronologically ordered file in one transaction. Hash first; completed
`(tenant_id, sha256)` returns no-op. Store raw payload on file or immutable entity
facts, normalize, insert changed versions/facts, then rebuild affected current
projections. Fatal parse failure rolls back facts and records failed file in a
separate transaction.

`rebuild-projections [--as-of timestamp]` truncates/recreates only tenant-scoped
mutable projections (`customers`, `carriers`, `loads`, `stops`) from immutable
versions and source-rate entries. Current rebuild uses latest `observed_at`; an
as-of rebuild uses cutoff. For HaulDesk, calculate rates by ledger sum at cutoff;
for FreightFlow/BrokerOS use selected restated load version. Never derive
historical answers from `loads`/`stops`.

## RLS and roles

`migration_owner`: owns schema/tables, runs Alembic, `BYPASSRLS` only if needed
for controlled rebuild/migration. `app_user`: non-owner, non-superuser, no
`BYPASSRLS`; receives only DML/SELECT grants. Optional `reporting_admin`: trusted
maintenance role, never request-facing.

Enable and `FORCE ROW LEVEL SECURITY` on every tenant-owned table:
`ingestion_files`, `customers`, `carriers`, `carrier_versions`, `loads`,
`load_versions`, `stops`, `source_rate_entries`, `decision_runs`, and
`carrier_recommendations`. At each app transaction start execute
`set local app.tenant_id = '<trusted UUID>'`. Policy shape for each table:

```sql
USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid);
```

Missing context returns no rows; mutation fails policy. Application repository
queries still filter `tenant_id`: RLS is defense in depth, not query design.
Tenant ID comes from trusted server context, never request body. Admin operations
use separate role/connection and explicit audited tenant scope. Direct-SQL tests
must prove same-tenant read/write, cross-tenant select/update denial, and no
context no access.

## Migration order

1. Enums, roles, `tenants`, `ingestion_files`.
2. Customer/carrier projections, `carrier_versions`, load projections,
   `load_versions`, stops.
3. Source-rate ledger, decision evidence tables.
4. Grants, FORCE RLS, policies, transaction-local tenant helper, direct-SQL tests.

No ORM code or migration is part of this design task.
