# Carrier Historical-Fit Ranking

## Scope and claim

`carrier-ranking-v4` orders a tenant's own known carriers for an ACTIVE target
load using historical fit evidence available at an explicit `as_of` timestamp.
It answers “which carriers have the strongest supported historical fit to call
first?” It is not an acceptance-probability, live-capacity, availability,
reliability, service-quality, or optimal-dispatch model. The source data has no
record of all calls, declines, quotes, truck positions, or operational
constraints, so those claims would be unsupported.

All calculations are tenant-local, deterministic, and auditable. A ranking is a
historical decision-support view, not an instruction to dispatch a carrier.

## Candidate eligibility

For target tenant `t`, target immutable load version `v`, and cutoff `a`, a
candidate carrier is eligible when all conditions hold:

1. The carrier belongs to `t`; repeated MC/DOT authority values do not join
   carriers across tenants.
2. The carrier is a tenant-owned canonical carrier known at or before `a`.
3. The target version is ACTIVE and observed at or before `a`.
4. The carrier has at least one tenant-local historical load or carrier snapshot
   observed at or before `a`. Candidates with no usable completed-load evidence
   may be omitted by default; if shown for a review mode, they receive neutral
   score, `LOW` confidence, and `NO_COMPLETED_HISTORY`.
5. Historical work uses the latest immutable version of each non-target load
   known at `a`, and only when that version is COMPLETED. Future booking,
   assignment, delivery, carrier, or correction facts are excluded.

The candidate list must not use mutable current projections as historical input,
must not infer a carrier from a shared authority number, and must never query a
different tenant.

## Evidence and component scores

Each component is normalized to `[0, 1]` before combination. Missing evidence is
reported, not converted into a favorable score. Intermediate evidence contains
immutable load/version IDs and observed timestamps.

### Lane fit — 40 points

Use completed, same-carrier historical loads whose direction matches the target.
Classify each with the approved Phase 8 tier and the existing endpoint/route
weight:

```text
w_i = exp(-origin_miles_i / 25)
    * exp(-destination_miles_i / 25)
    * exp(-route_mile_delta_i / 50)
    * exp(-age_days_i / 30)
```

Unavailable distance terms are omitted, never treated as zero. Give tier quality
`1.00, .80, .60, .45, .30, .15` for `NEAR_EXACT`, `REGIONAL`,
`METRO_CORRIDOR`, `DISTANCE_EQUIPMENT`, `TENANT_EQUIPMENT`, and
`TENANT_ALL_EQUIPMENT`, respectively. Starting lane score is the weighted mean
tier quality, multiplied by `min(1, ESS_lane / 4)`.

Route direction remains ordered: `DFW → HOUSTON` is not `HOUSTON → DFW`.

### Equipment fit — 20 points

Among the carrier's completed loads, calculate the recency-weighted share with
the target equipment:

```text
equipment_fit = sum(exp(-age_days / 45) for exact-equipment loads)
                / sum(exp(-age_days / 45) for all completed loads)
```

If the target equipment is `UNKNOWN`, set the component to neutral `0.50`, emit
`UNKNOWN_TARGET_EQUIPMENT`, and cap confidence at LOW. If the carrier has no
completed history, equipment fit is unavailable rather than zero.

### Historical delivery proximity (“deadhead evidence”) — 20 points

Find the carrier's latest known completed delivery at or before `a`; use its
final drop-off ZIP centroid and completion observation time. If the target pickup
ZIP is valid, calculate:

```text
proximity = exp(-delivery_to_pickup_miles / 75)
time_gap  = exp(-gap_days / 14)
deadhead_evidence = proximity * time_gap
```

The displayed evidence is “last known delivery was X miles from the target pickup
Y days earlier.” It does not say or imply the carrier, truck, driver, or equipment
is currently there, available, en route, reliable, or likely to accept. Missing
or invalid ZIPs emit `DEADHEAD_LOCATION_UNAVAILABLE`; no location term is
invented.

### Recency of relevant work — 20 points

Use the most recent relevant completed lane/equipment evidence at or before `a`:

```text
recency_fit = exp(-relevant_age_days / 30)
```

If no relevant evidence exists, use the carrier's most recent completed load with
an explicit `BROAD_RECENCY_EVIDENCE` warning. If no completed work exists,
recency is unavailable.

## Score, effective history, and shrinkage

The available component score uses only present terms and renormalizes their
starting weights so missing geography cannot silently depress a carrier:

```text
raw_0_100 = 100 * sum(component_weight * component_score)
                 / sum(component_weight for available components)
```

Starting weights are lane `.40`, equipment `.20`, deadhead evidence `.20`, and
recency `.20`. They are model-versioned defaults, tunable only through
leakage-safe evaluation and documented parameter changes.

Use a component-neutral prior of `50` and a combined effective history:

```text
ESS_total = min(8, ESS_lane + 0.5 * exact_equipment_count + 0.5 * relevant_count)
alpha = ESS_total / (ESS_total + 6)
adjusted_score = alpha * raw_0_100 + (1 - alpha) * 50
```

`ESS_lane` is Kish effective sample size over lane weights:

```text
ESS_lane = (sum(w_i)^2) / sum(w_i^2)
```

Thus a single highly similar historical load can inform ordering but cannot
produce an extreme score. A carrier with no usable evidence has no default rank;
if explicitly included, it is score `50`, confidence LOW, and marked as such.

## Confidence is separate

Score measures adjusted historical fit; confidence measures how much evidence
supports it. Starting confidence score:

```text
confidence = .45 * min(1, ESS_total / 6)
           + .20 * lane_quality
           + .15 * recency_fit
           + .10 * equipment_coverage
           + .10 * geography_completeness
```

`equipment_coverage = min(1, exact-equipment completed-load count / 3)`. It is
evidence coverage, not equipment-fit quality; equipment fit remains a score term.

`HIGH >= .75`, `MEDIUM >= .45`, otherwise `LOW`. Unknown target equipment,
missing pickup geography, or `ESS_total < 1` caps confidence at LOW. Return raw
counts, ESS, component availability, caps, and warning codes; never use a high
score as a proxy for high confidence.

## Deterministic ordering

Sort candidates by:

1. Descending adjusted score, rounded only for display (full Decimal value sorts).
2. Descending confidence score.
3. Descending `ESS_total`.
4. Most recent relevant completion timestamp.
5. Stable tenant-local carrier external ID ascending.

The carrier name, MC, DOT, or a cross-tenant identifier must not be a tie-breaker.

### Decision presentation guardrails

The deterministic sort is not automatically a meaningful call order. A carrier is
`SUPPORTED` only when it has lane evidence or at least one exact-equipment completed
load; a carrier supported only by generic recency or historical delivery proximity
is `LIMITED` and is shown separately without a call-order claim. Supported carriers
within two adjusted-score points share a fit group and the UI says there is no
meaningful historical separation. Missing component evidence stays `null` through
the API and renders as “No evidence”, never as a zero score.

## Evaluation acceptance

Generated temporal holdout needs at least 24 authored cases and 14 scored cases,
with at least three scored cases per source. Rich means `ESS_total >= 3`; lower
effective history is sparse.
It must include rich and sparse history; near-exact, broader-lane,
distance/equipment, limited-candidate, and close-score-tie cases; at least one
tie; and at least three clearly separated supported tops. These are coverage and
presentation checks, not top-1 targets. Booked-carrier top-1/3 and MRR remain
weak-proxy diagnostics only. Deadhead ablation always uses identical cases.

## Structured explanations

Outputs use reason codes plus structured values. Rendering selects fixed templates;
no free-form model generates claims.

| Code | Template intent |
|---|---|
| `STRONG_DIRECTIONAL_LANE_HISTORY` | “{count} completed {tier} directional loads support this historical fit.” |
| `EQUIPMENT_HISTORY_MATCH` | “Completed history includes {count} {equipment} loads.” |
| `RECENT_RELEVANT_COMPLETION` | “Relevant completed work was observed {days} days before this load.” |
| `LAST_KNOWN_DELIVERY_NEAR_PICKUP` | “Last known delivery was {miles} miles from pickup {days} days earlier.” |
| `SPARSE_HISTORY_SHRINKAGE` | “Limited history pulls the score toward the neutral prior.” |
| `UNKNOWN_TARGET_EQUIPMENT` | “Target equipment is unknown; equipment-fit confidence is limited.” |
| `DEADHEAD_LOCATION_UNAVAILABLE` | “No valid historical delivery-to-pickup distance is available.” |
| `NO_COMPLETED_HISTORY` | “No completed tenant-local history supports a historical-fit rank.” |

Every explanation includes rank or fit group, adjusted score, confidence, component scores,
model version, supporting load/version IDs, and warnings. Prohibited wording
includes “available,” “nearby now,” “will accept,” “reliable,” “best carrier,” or
any equivalent claim not supported by source facts.

## `as_of`, corrections, and tenant boundaries

Every request requires `(tenant_id, target_load_id, target_version_id, as_of)`.
The target, carrier identity, carrier history, completed status, equipment,
delivery location, and timestamps are resolved from immutable observations known
at or before `as_of`. A later status correction or ZIP/equipment correction is
visible only after its observation time. A later HaulDesk ledger entry has no
effect unless it changes a completed-load fact already known at the cutoff.

Repository methods, cache keys, candidate queries, reason payloads, and evidence
IDs are tenant-scoped. Cross-tenant carrier/load IDs return generic not-found
behavior; no ranking, count, or reason may disclose another tenant's data.

## Evaluation and caveats

For each historical target that first became ACTIVE, reconstruct candidates at its
first-ACTIVE `as_of`. Use the eventually booked carrier only as a weak behavioral
proxy, never as an acceptance label or ground truth for dispatch quality. Report:

- total labeled cases, **supported-only** scored cases (where the eventually
  booked carrier is in the supported call-order set at the cutoff), and no-rank
  cases with explicit reasons. Report top-1 recall, top-3 recall, and MRR only
  over supported scored cases; never hide no-rank cases. All-candidate metrics
  are secondary diagnostics and must not be presented as call-order quality;
- mean reciprocal rank (MRR);
- eligible-case count and no-rank count;
- results by rich/sparse effective-history band and equipment; and
- ablations with lane, equipment, deadhead evidence, and recency weights each set
  to zero, on identical cases; and
- top-score margin, top-fit tie rate, and limited-history candidate count.

Show counts for every subgroup and model/ablation. Do not compare metrics across
different case populations. Corrections after the cutoff change only the eventual
booked/outcome label where applicable, never candidate features. Synthetic data,
unknown contact sets, selection bias in bookings, and absent availability data mean
these metrics cannot demonstrate acceptance prediction, causal benefit, or
production dispatch performance.

Generated booking labels are hand-authored temporal holdouts: they appear only
after first ACTIVE and are never derived from the scorer. They test leakage and
ranking separation, not real-world acceptance prediction.

### Weight-change threshold

Do not tune production ranking weights from this demo. Any future weight change
requires at least 100 independent, supported-only outcome cases with complete
candidate-set capture, a pre-registered candidate weight set, identical-case
ablations, and at least a 5% relative improvement in the stated proxy metric
without worsening sparse-case no-rank rate or close-score tie behavior. Booked
carrier labels alone do not satisfy this threshold; they remain weak diagnostics.
