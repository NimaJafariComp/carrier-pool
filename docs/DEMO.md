# 10-minute review call

This runbook uses only the deterministic demo dataset. It is a decision-support
demo, not a claim that a carrier is available, will accept a load, or will be
the best operational choice.

## Before the call

From the repository root, run:

```bash
make demo
curl -fsS http://localhost:8000/health
curl -fsS http://localhost:8000/ready
```

Expected result: `make demo` generates and validates 123 sync files, ingests
them in chronological order, creates three Day 11 decisions, starts the UI,
and prints these URLs:

- UI: <http://localhost:5173>
- API documentation: <http://localhost:8000/docs>

The health and readiness commands should each return `{"status":"ok"}`.

Use these fixed demo brokers:

| Broker | Source | Tenant ID | Day 11 load | Scenario |
| --- | --- | --- | --- | --- |
| North Star Freight | FreightFlow | `11111111-1111-4111-8111-111111111111` | `FF-9001` | `SC-24` |
| Alamo Brokerage | HaulDesk | `22222222-2222-4222-8222-222222222222` | `HD-9001` | `SC-26` |
| Gulf Bridge Logistics | BrokerOS | `33333333-3333-4333-8333-333333333333` | `BO-9001` | `SC-25` |

## Call sequence

### 0:00, product thesis

Say: “Carrier Pool is a small broker-scoped decision-support tool. For an active
load it shows a historical carrier-rate estimate and a historical-fit call order,
with the tenant-local completed loads behind both. It does not predict acceptance,
availability, reliability, live truck location, or live traffic.”

The application deliberately keeps the three brokers separate, even where a
carrier's MC/DOT is repeated in the source data.

### 0:30, demo startup and health

Show the successful `make demo` output and the two health responses from the
pre-call commands. Open <http://localhost:5173> and point out the broker selector.
It is a demo context selector, not a login system. Changing it replaces the
tenant-scoped query cache.

If a service is not ready, wait a few seconds and rerun the two `curl` commands.
If it still fails, inspect only the relevant logs:

```bash
docker compose --env-file .env.example ps
docker compose --env-file .env.example logs --tail=100 db backend frontend
make demo
```

`make demo` safely recreates only the guarded `carrier_pool_demo` database.

### 1:15, one-file-at-a-time ingestion across three TMSs

Show that the same deterministic schedule is ingested separately for each broker:

```bash
cd backend
DATABASE_URL="postgresql+psycopg://carrier_pool:carrier_pool@localhost:5432/carrier_pool_demo" \
  uv run carrier-pool ingestion-summary --tenant-id 11111111-1111-4111-8111-111111111111
DATABASE_URL="postgresql+psycopg://carrier_pool:carrier_pool@localhost:5432/carrier_pool_demo" \
  uv run carrier-pool ingestion-summary --tenant-id 22222222-2222-4222-8222-222222222222
DATABASE_URL="postgresql+psycopg://carrier_pool:carrier_pool@localhost:5432/carrier_pool_demo" \
  uv run carrier-pool ingestion-summary --tenant-id 33333333-3333-4333-8333-333333333333
cd ..
```

Expected observation: each summary is tenant-scoped and reports its own source
files. The generated schedule contains four timestamped sync files per source per
historical day, and `make demo` passed each file to the coordinator in global
chronological order.

### 2:00, exact, rich Day 11 price and carrier evidence

In the UI select **North Star Freight**, then open **FF-9001**, Grand Prairie,
TX to Katy, TX, dry van. This is `SC-24`; the FreightFlow source shipment ID is
`888509` and its Day 11 source file is
`data/tms_a_freightflow/2026-07-11T06-00_sync.json`.

Show:

- The expected carrier rate, historical comparison range, confidence, and the
  “as of” time.
- The comparable completed-load table. Its route, carrier pay, geographic match,
  completed date, and safe tenant-local carrier label explain why each observation
  was included.
- The historical-fit carrier cards. `FF-C-201`, Lone Star Van, has the rich
  same-direction, same-equipment history from `SC-12` and `SC-15`.

Say: “The displayed evidence was observed by the decision's `as_of` timestamp.
The score is historical fit, not a promise of a truck or acceptance.”

### 3:15, sparse local fallback and uncertainty

Select **Alamo Brokerage**, then open **HD-9001**, Plano, TX to Baytown, TX,
dry van. This is `SC-26`, from
`data/tms_b_hauldesk/2026-07-11T06-00_sync.json`.

Show the small near-exact group and the broader same-equipment regional evidence.
The UI should explain that the estimate uses a smaller local sample and broader
same-broker history. This scenario is deliberately a sparse-local fallback. Its
documented current confidence is **medium**, not low, because the estimator blends
the local observations with relevant regional history; its sparse and broader-
fallback warnings are the important uncertainty signal.

### 4:15, low-history shrinkage

Return to **North Star Freight**, **FF-9001**, and compare the rich candidate
**Lone Star Van** (`FF-C-201`) with **Cedar Express** (`FF-C-202`). `SC-16` gives
Cedar Express one highly similar completed load.

Show the evidence counts, separate confidence label, and the “More history needed”
state where applicable. Explain: one strong observation can support a useful
comparison, but its score is pulled toward the neutral historical-fit prior so it
does not outrank a carrier with independent, repeated evidence merely because its
single observation looks excellent.

### 5:15, duplicate ingestion is a no-op

Re-ingest the `SC-22` source file after the chronological ingest:

```bash
cd backend
DATABASE_URL="postgresql+psycopg://carrier_pool:carrier_pool@localhost:5432/carrier_pool_demo" \
  uv run carrier-pool ingest-file ../data/tms_a_freightflow/2026-07-02T12-00_sync.json \
  --tenant-id 11111111-1111-4111-8111-111111111111 \
  --source-system FREIGHTFLOW
cd ..
```

Expected result: output includes `"no_op": true`; there are no new versions or
projection changes. `SC-22` intentionally uses an already-ingested checksum.

### 6:00, correction semantics and immutable old decisions

Run the focused correction proof:

```bash
make correction-demo
```

Expected result: the test passes. It proves that FreightFlow and BrokerOS restated
snapshots create a new immutable version and update the current projection, while
the HaulDesk `HD-2101` append-only pay rows total $1,225 exactly once. It also
proves a later correction can affect a later decision but cannot mutate a decision
stored before that correction, and that rebuilding projections reproduces the
corrected state.

This proof uses isolated test tenants in the normal local test database. It does
not change the three visible demo brokers. If it fails because PostgreSQL is down,
run `make db-up` and rerun the command.

### 7:00, broker isolation

Run the database integration suite:

```bash
make test-integration
cd backend
DATABASE_URL="postgresql+psycopg://carrier_pool:carrier_pool@localhost:5432/carrier_pool" \
  uv run pytest -q tests/test_api_contract_integration.py -k \
  'cross_tenant_matches_absent_and_evidence_never_crosses_tenants or broker_a_history_never_changes_broker_b_prediction or same_mc_dot_carriers_remain_separate_tenant_records'
cd ..
```

Expected result: `make test-integration` includes direct-SQL RLS select/update
denial. The focused API tests then prove generic cross-broker not-found responses,
same-MC/DOT non-merging, tenant-local evidence, and the invariant that new
FreightFlow history cannot change a fixed HaulDesk prediction.

For a visible API check, open the API documentation URL, authorize neither a
foreign tenant nor an arbitrary header, and use a configured `X-Tenant-ID` only.
A load ID from another broker receives the same generic not-found response as an
unknown load ID.

### 8:00, backtest and honest limitations

Run:

```bash
make backtest
jq '{case_count, scored_case_count, metrics}' artifacts/backtest_metrics.json
jq '{with_deadhead: (.with_deadhead | {case_count, scored_case_count, top_1_recall, top_3_recall, mean_reciprocal_rank, top_fit_tie_rate}), weight_tuning_eligible, weight_tuning_blockers}' artifacts/ranking_metrics.json
```

Expected result: `make backtest` rebuilds the deterministic demo database,
evaluates only evidence available at each historical cutoff, and rewrites the
artifacts. The JSON commands show the case population beside each metric so models
are not compared on hidden, different populations.

Close with the limitations: these are authored synthetic scenarios, not a claim of
production accuracy; booked-carrier ranking labels are a weak behavioral proxy;
the range is a historical comparison range rather than a calibrated prediction
interval; and no live availability, live routing, traffic, authentication, or
shared carrier pool is implemented.

If the backtest acceptance command fails after local edits, retain the generated
artifacts for inspection, run `make generate && make validate`, then rerun
`make backtest`. Do not tune model weights solely to improve a synthetic metric.
