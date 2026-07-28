# Carrier Pool data flow

These diagrams describe the implemented deterministic demo. They intentionally omit
external TMS APIs, queues, map providers, shared pools, and other services that the
project does not run.

## Source ingestion and rebuildable projections

```mermaid
flowchart LR
    FF[FreightFlow plain JSON syncs]
    HD[HaulDesk plain JSON syncs]
    BO[BrokerOS plain JSON syncs]

    DISC[Strict filename discovery<br/>one file at a time, chronological]
    FFA[FreightFlow adapter]
    HDA[HaulDesk adapter]
    BOA[BrokerOS adapter]
    CAN[Canonical normalized observations]

    FILES[Immutable ingestion files<br/>raw payload + checksum]
    VERSIONS[Immutable customer, carrier,<br/>load, and stop versions]
    LEDGER[Immutable HaulDesk<br/>source rate entries]
    REBUILD[Projection rebuild]
    CURRENT[Current tenant-scoped projections<br/>loads, stops, customers, carriers]

    FF --> DISC
    HD --> DISC
    BO --> DISC
    DISC --> FILES
    DISC --> FFA
    DISC --> HDA
    DISC --> BOA
    FFA --> CAN
    HDA --> CAN
    BOA --> CAN
    CAN --> VERSIONS
    CAN --> LEDGER
    VERSIONS --> REBUILD
    LEDGER --> REBUILD
    REBUILD --> CURRENT
```

FreightFlow and BrokerOS changed-load snapshots create versions. HaulDesk changed
loads create versions and its newly reported financial rows create ledger entries.
Current projections are derived state, not the historical source of truth.

## As-of decision flow

```mermaid
flowchart LR
    ACTIVE[ACTIVE load + explicit as_of]
    INPUT[Immutable target load version<br/>observed at or before as_of]
    HISTORY[Same-tenant completed versions<br/>and ledger entries at or before as_of]
    COMP[Comparable-load retrieval<br/>directional tiers + endpoint distances]
    PRICE[Hierarchical rate estimator<br/>pricing-hierarchical-v1]
    FEATURES[Carrier feature service<br/>lane, equipment, recency,<br/>historical delivery proximity]
    RANK[Historical-fit scorer<br/>carrier-ranking-v5]
    DECISION[Immutable decision run<br/>ordered carrier recommendations<br/>model versions, warnings, evidence IDs]
    API[Tenant-safe decision API and UI]

    ACTIVE --> INPUT
    INPUT --> COMP
    HISTORY --> COMP
    COMP --> PRICE
    INPUT --> FEATURES
    HISTORY --> FEATURES
    FEATURES --> RANK
    PRICE --> DECISION
    RANK --> DECISION
    DECISION --> API
```

The rate estimator and ranker do not read current projections as historical
shortcuts. A correction observed after `as_of` can affect a later decision, but not
an already stored decision or an earlier backtest input.

## Broker boundary and row-level security

```mermaid
flowchart LR
    UI[Demo UI broker selector]
    HEADER[X-Tenant-ID]
    ALLOW[Fixed demo broker allowlist]
    REQUEST[Tenant-scoped API request]
    CONTEXT[Transaction-local app.tenant_id]
    APP[Repository and service queries<br/>explicit tenant_id filters]
    RLS[PostgreSQL FORCE RLS<br/>non-owner application role]
    DATA[Tenant-owned rows and<br/>validated evidence references]
    RESPONSE[Only same-broker response<br/>cross-broker ID: generic not found]

    UI --> HEADER
    HEADER --> ALLOW
    ALLOW --> REQUEST
    REQUEST --> CONTEXT
    REQUEST --> APP
    CONTEXT --> RLS
    APP --> RLS
    RLS --> DATA
    DATA --> RESPONSE
```

The demo header is accepted only for the configured demo brokers. Application
filters and RLS both enforce tenancy. Embedded JSON evidence is additionally
validated against tenant-local immutable versions before serialization.

## Deterministic scenario generation

```mermaid
flowchart LR
    CATALOG[Hand-authored scenario catalog<br/>tenants, carriers, locations, loads, events]
    SCHEDULE[Deterministic scheduler<br/>10 historical days + Day 11 targets]
    ENGINE[Lifecycle engine<br/>explicit events and corrections]
    FF[FreightFlow serializer]
    HD[HaulDesk serializer]
    BO[BrokerOS serializer]
    SYNC[Plain JSON sync files<br/>three TMS directories]
    MANIFEST[data/scenarios.json<br/>derived scenario manifest]
    VALIDATE[Generated-data validator<br/>filenames, schemas, chronology,<br/>identities, warnings, ZIPs]

    CATALOG --> SCHEDULE
    CATALOG --> MANIFEST
    SCHEDULE --> ENGINE
    ENGINE --> FF
    ENGINE --> HD
    ENGINE --> BO
    FF --> SYNC
    HD --> SYNC
    BO --> SYNC
    SCHEDULE --> MANIFEST
    SYNC --> VALIDATE
    MANIFEST --> VALIDATE
    CATALOG --> VALIDATE
```

The catalog, schedule, serializers, and manifest share canonical definitions.
Generation writes only known plain JSON paths and leaves the JSONC schema examples
untouched.
