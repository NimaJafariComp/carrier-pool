# Reviewer's guide to Carrier Pool decisions

## What this product is for

Carrier Pool is a small decision-support tool for freight brokers. For a load that
is still looking for a carrier, it shows an expected carrier payment and a
historical-fit call order for that broker's known carriers.

It is deliberately a decision aid, not an automated dispatcher. A broker remains
responsible for checking live capacity, service requirements, and the final price.

## Immutable history, current state, and time

**Decision:** retain every source payload, normalized version, and HaulDesk ledger
entry immutably; derive current projections from those facts. Every historical
question requires an explicit `as_of` cutoff.

This makes a correction auditable, lets projections be rebuilt without reading
their previous values, and prevents a future assignment, rate, or correction from
entering an earlier decision. Stored decisions retain their exact input version,
time, model versions, parameters, warnings, and evidence IDs. The rebuild and
temporal-leakage integration tests cover this behavior.

**Rejected:** overwrite-only current records and querying current projections for
history. Both make corrections unauditable and leak future facts into backtests.

## Source corrections and status regressions

**Decision:** respect each source's correction contract. FreightFlow and BrokerOS
are replacement snapshots: a later snapshot creates a new immutable version and
can restate a prior total or detail. HaulDesk financial rows are append-only: its
carrier total is the `PAY` ledger sum observed at the requested cutoff, including
positive surcharges and negative adjustments.

A later source observation with a lower lifecycle status may become current when
its source timestamps make it newer. The system records
`STATUS_REGRESSION_CORRECTION` rather than treating that correction as impossible.
Out-of-order snapshots remain non-current and unchanged snapshots do not create
redundant versions. Generated correction and precedence integration tests verify
these cases.

**Rejected:** one generic correction rule, status-monotonicity constraints, and
silently discarding regressions. They would either double-count HaulDesk money or
hide a valid source correction.

## Geography and lane definition

**Decision:** use the bundled Texas Triangle ZIP-centroid reference and Haversine
endpoint distance as the business-facing metric. A lane is directional: origin to
destination is not the reverse route. ZIP is coordinate authority; city is
diagnostic text only.

The initial tiers are, in order: near-exact at origin and destination within 25
miles, regional within 50 miles, same ordered metro corridor, comparable
distance with matching equipment, tenant matching-equipment history, then a
tenant-wide fallback. Comparable route length is within `max(25 mi, 15%)`, capped
at 75 miles. H3 resolutions 8 and 6 are optional candidate-retrieval buckets only;
they never choose a tier or replace the displayed Haversine distance.

**Rejected:** city/state equality, undirected routes, runtime geocoding, and H3 as
the explanation of similarity. Those approaches respectively miss nearby suburbs,
merge reverse lanes, add a network dependency, or make the evidence opaque.

## Payment estimate and baselines

**Decision:** serve `pricing-hierarchical-v1`, a transparent, tenant-local,
recency-weighted hierarchical estimate of total carrier pay. It uses completed
carrier payments known at `as_of`, weighted medians/quantiles, effective sample
size, and disclosed fallback blending. Its displayed range is a historical
comparison range, never a calibrated prediction interval.

`make backtest` currently reports 57 labeled cases and 48 production scored cases:
MAE `$91.76`, median absolute error `$40.00`, WAPE `6.69%`, and historical-range
coverage `20.83%`. It compares tenant-wide median, equipment-plus-distance-band
median, unshrunk nearest-lane weighted median, robust Huber regression, and
quantile regression when enough rows exist. The artifacts show no promotion is
eligible because deterministic demo outcomes are not independent operational data.

**Rejected:** a black-box serving model, rate-per-mile as the business target, and
promoting the numerically lower synthetic-data baseline. The current artifacts are
useful regression checks, but are insufficient to establish production accuracy or
justify model complexity.

## Historical-fit ranking, shrinkage, and confidence

**Decision:** serve `carrier-ranking-v5`. It is a transparent historical-fit
score, not a probability that a carrier will accept. It combines four bounded
signals: directional lane similarity, matching-equipment history, recency of
relevant completed work, and the distance/time gap from the carrier's last
recorded delivery to the target pickup.

V5 counts each completed load once when measuring independent history, even when
that load contributes to several signals. Its raw component score is then shrunk
toward a neutral 50 using `ESS / (ESS + 6)`: a small or repetitive history cannot
produce an extreme recommendation, while several independent, strong matches can
move the score materially away from neutral. It does not impose a separate hard
cap on score effective sample size. Confidence is calculated independently from
evidence depth, lane quality, equipment coverage, recency, and geography; a high
score is not automatically high confidence.

On the current 57 temporal cases, V5 has 32 supported scored cases, 50.0% top-1
recall, 90.625% top-3 recall, MRR 0.6875, and a 5.26% top-fit tie rate. These are
diagnostics against an eventually booked-carrier proxy, not proof of operational
accuracy. The ranking artifact marks weight tuning ineligible because the demo is
not an independent operational holdout.

**Rejected:** v4, because overlapping component counts could overstate one load
and its score-ESS cap could limit genuinely deep history; the v6 analysis
candidate, because lowering the shrinkage constant raised numeric margins without
improving ordering or tie behavior; and score-only certainty, because sparse
evidence must remain visible.

## Booked-carrier labels and historical delivery proximity

**Decision:** use the eventually booked carrier only as a weak retrospective
ranking proxy. It is not an acceptance label, a causal result, or a measure of
dispatch quality. Report no-rank cases and case counts instead of hiding them.

Last delivery proximity means the distance and time gap between a carrier's latest
recorded completed delivery and the target pickup at `as_of`. It can inform
historical fit, but does not claim a truck, driver, or equipment is currently
there, available, reliable, or likely to accept.

**Rejected:** training an acceptance model, calling historical proximity live
deadhead, and reporting only top-1 recall. The source data has no complete call
set, declines, capacity, live location, or service outcomes.

## Tenant boundary and row-level security

**Decision:** make tenant identity part of every entity, query, feature, cache
key, unique source identity, decision, and evidence payload. PostgreSQL uses a
non-owner application role with `FORCE ROW LEVEL SECURITY` and a transaction-local
trusted tenant context; application queries still filter by tenant as defense in
depth. Same MC/DOT values in different brokers remain separate carrier records.

Cross-broker load or carrier IDs return the same generic not-found response as an
unknown ID. Direct-SQL RLS, prediction-invariance, API evidence-isolation, and
same-authority tests verify the boundary.

**Rejected:** application filters alone, global MC/DOT deduplication, and implicit
cross-broker joins. Any of these could expose private history or alter a broker's
private price/ranking baseline.

## Deliberate exclusions and shared pool

**Decision:** keep the demo without an authentication UI, production identity
system, live TMS feed, live routing/traffic, external geocoder, truck tracking,
distributed job system, LLM decisioning, or neural/boosted serving model. The demo
uses a trusted broker context while still enforcing API and database isolation.

The shared carrier pool is deferred. No broker's private load, rate, customer, or
facility facts are shared, and no external carrier can alter a private rate
baseline. Phase 16 remains an optional future phase behind an explicit opt-in,
coarsened-data, privacy-test gate.

**Rejected:** adding these systems for presentation value. They would expand the
security and operational surface before improving the assignment's core evidence,
time, correction, or isolation guarantees.

## What would change the decision

The highest-value next data is leakage-safe operational history: complete candidate
call sets, outreach/decline outcomes, accepted and rejected quotes, carrier
capacity, service outcomes, verified facility locations, and enough independent
examples across lane, equipment, geography, and history-depth bands.

At larger scale, retain immutable source facts, partition/index by tenant and
observation time, and materialize only rebuildable projections or approved
tenant-local retrieval aids. Any weight or model change needs pre-registered
candidate parameters, identical-case comparison, independent outcomes, and no
harm to sparse-history behavior. Complexity is not a substitute for better data.

**Rejected:** tuning weights against Day 11 answers or a small synthetic holdout,
and adopting a shared pool before its separate privacy contract is implemented and
tested.
