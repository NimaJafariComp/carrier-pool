# Decisions

## Product thesis

Carrier Pool is a multi-tenant freight-broker decision-support system. For each
broker's `ACTIVE` load, it answers:

1. Which of this broker's known carriers is the strongest **historical fit** to
   call first, and what evidence supports that order?
2. What total carrier rate should this broker expect to pay, what historical
   comparison range supports it, and how confident is that estimate?

Answers must use only that broker's data available at the decision's `as_of` time.
They must expose comparable loads, fallback level, evidence strength, and limits.

## Carrier ranking: historical fit, not acceptance prediction

The ranking describes historical lane, equipment, recency, and last-known-delivery
fit. It does **not** predict acceptance, live capacity, carrier reliability, or the
optimal dispatch decision.

Available source data records the carrier ultimately booked, but not all carriers
contacted, declined offers, quotes, capacity, service quality, or operational
constraints. Those missing labels make acceptance-probability and optimal-dispatch
claims unsupported. Deadhead evidence is phrased as a historical delivery location,
never as a claim that a truck is available.

## History, corrections, and time

Choose immutable observed source history plus rebuildable current projections.

- Keep raw source observations and normalized immutable versions.
- Rebuild current loads, stops, carrier/customer state, and derived totals from
  history rather than treating overwrite-only records as truth.
- Process files one at a time in chronological order and make repeat ingestion a
  no-op.
- Resolve historical features and backtests with explicit `as_of` cutoffs.

Reason: FreightFlow sends replacement snapshots; BrokerOS may silently restate
totals; HaulDesk appends rate-ledger entries, including negative corrections. This
design preserves auditability, supports source-specific correction semantics, and
prevents later facts from changing a past decision.

Rejected: mutable current-state-only storage. Revisit only for a disposable,
non-audited prototype with no correction analysis or historical evaluation.

## Rate estimation

Choose a transparent hierarchical, recency-weighted estimator as production default.
It retrieves same-tenant comparable loads by directional lane and equipment, uses
weighted medians/quantiles, and falls back from near-exact lanes to broader tenant
baselines. Effective sample size controls blending and confidence. Returned ranges
are historical comparison ranges, not calibrated prediction intervals unless tested
coverage supports that claim.

Reason: synthetic history is limited; this is explainable, deterministic, resilient
to sparse lanes, and easy to audit.

Rejected: opaque ML as default (including neural/boosted models). Revisit a more
complex model only if leakage-free backtesting materially improves results, remains
explainable, and sufficient real-world training labels exist.

### Baseline comparison policy

Backtesting includes analysis-only tenant-wide median, equipment-plus-distance-band
median, unshrunk nearest-lane weighted median, robust Huber regression, and quantile
regression baselines. pandas and scikit-learn remain in the `analysis` dependency
group, not serving runtime dependencies. Huber requires at least eight observations;
quantile regression requires at least twenty.

The production choice remains `pricing-hierarchical-v1`. Do not promote a regression
baseline from synthetic data unless rolling as-of results materially improve both MAE
and median absolute error without worsening sparse-history WAPE or range coverage.
Current local generated-fixture backtests contain no scored estimates, so they do not
provide evidence to promote any baseline.

## Shared carrier pool

Shared-pool functionality is optional and deferred. It may begin only after all
mandatory work is green:

- 120 historical files validate and ingest.
- Every Day 11 active load has a complete decision.
- Tenant-isolation and temporal-leakage tests pass.
- Backtest and baseline-comparison artifacts exist.
- `make demo` and `make test` succeed from a clean checkout.
- Mandatory UI is complete.

If implemented, it must be explicit opt-in and a separate, privacy-controlled
projection. It must not expose customers, exact facilities, individual loads/rates,
customer rates, margins, or broker-specific carrier IDs; it must not alter private
rate baselines invisibly.

Rejected: shipping a partial shared pool with relaxed tenant filters. Revisit only
after approved privacy contract, isolated projection, and adversarial privacy tests.

## Explicit non-goals

- Authentication UI and production identity: demo tenant context is sufficient;
  core isolation is higher value.
- Live TMS APIs: assignment supplies already-downloaded files.
- External geocoding or map tiles: local geography keeps demo deterministic.
- Kubernetes, Terraform, Kafka, Celery, Redis, or service-mesh infrastructure:
  workload is sequential and small; these add unrewarded operational complexity.
- LLM/vector-database recommendation logic: explanations must be deterministic and
  evidence-backed.
- Elaborate visual design, heavy component libraries, Redux, or maps: clarity beats
  polish.
- Predicting acceptance, live availability, or reliability: required outcome data is
  absent.

Revisit each only when validated scale, integration, security, or product needs
justify it without weakening temporal correctness or tenant isolation.

## Known limitations

- Synthetic data is intentionally small and cannot establish production model
  performance.
- Booked carrier is only a weak proxy for a good recommendation.
- Historical delivery locations do not reveal real-time truck position or capacity.
- ZIP centroids and Haversine endpoint distance approximate operational geography;
  they do not capture routing, facility constraints, or traffic.
- Historical rates omit market signals, carrier quotes, capacity, and many accessorial
  drivers; sparse lanes require broad fallbacks and lower confidence.
- Demo tenant selection is not production authentication, even though application
  and database tenant isolation are required.
