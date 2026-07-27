# Hierarchical Carrier-Rate Estimation

## Scope and claim

This model estimates the total carrier pay a tenant may expect for a load from its
own historical evidence. It is not a quote, acceptance probability, availability
claim, or statistically calibrated prediction interval. All values are USD
`Decimal` totals; rate-per-mile is diagnostic only.

Model version starts as `pricing-hierarchical-v1`. Parameters are versioned with
the output and can change only after leakage-free backtest review.

## Eligible evidence and target definition

For target tenant `t` and cutoff `a`, an evidence load is eligible when:

1. It belongs to `t`; no cross-tenant fact may enter any tier or baseline.
2. Its latest immutable `LoadVersion` observed at or before `a` is `COMPLETED`.
3. That version has a known non-negative total carrier rate in USD.
4. It is not the target load.
5. It satisfies the selected Phase 8 lane/equipment tier.

FreightFlow and BrokerOS use the `carrier_rate_amount` from the latest replacement
snapshot known at `a`. A later restatement is invisible before its observation.
HaulDesk uses the sum of immutable `PAY` `SourceRateEntry` rows observed at or
before `a`, including positive surcharges and negative adjustments. It never uses a
current ledger total or overwrites a source row.

Example: a HaulDesk load has `$1,000` linehaul, `$75` fuel, and `-$30` adjustment.
At the second observation its target is `$1,075`; after the adjustment it is
`$1,045`. A prediction made before either later observation cannot see it.

## Time and correction rules

Every estimator request requires `(tenant_id, load_id, as_of)`. Target route,
equipment, and candidate values are reconstructed from immutable versions observed
at or before `as_of`; mutable current projections are not historical inputs.

For a live Day 11 decision, `as_of` is the target observation time, so all Day 10
corrections already observed are valid. In a backtest, `as_of` is when that load
first became `ACTIVE`; later booking, delivery, ledger rows, and corrections are
excluded from the input and retained only for the eventual outcome label.

## Similarity and recency weights

Phase 8 determines the narrowest available tier: `NEAR_EXACT`, `REGIONAL`,
`METRO_CORRIDOR`, `DISTANCE_EQUIPMENT`, `TENANT_EQUIPMENT`, or
`TENANT_ALL_EQUIPMENT`. H3 only retrieves candidates; it never determines a
weight or explanation.

For comparable `i`, with origin distance `o_i`, destination distance `d_i`, route
mile delta `m_i`, and age `r_i` in days:

```text
w_geo,i     = exp(-o_i / 25) * exp(-d_i / 25)
w_route,i   = exp(-m_i / 50)
w_recency,i = exp(-r_i / 30)
w_i         = w_geo,i * w_route,i * w_recency,i
```

Unavailable geography removes that factor; it is never treated as zero distance.
The evidence records the relaxed factor and quality flag. Normalize positive
weights only when an algorithm needs probabilities:

```text
p_i = w_i / sum(w_j)
```

If all weights are zero or no evidence exists, that tier has no estimate.

## Weighted statistics

Sort `(x_i, w_i)` by `x_i`, preserving duplicate values. Let `W = sum(w_i)`.
The weighted quantile at `q ∈ [0,1]` is the first sorted value whose cumulative
weight is at least `qW`; the weighted median is quantile `q=0.5`. No interpolation
is used, keeping displayed evidence values source-observable.

Example: rates `(1000, 1200, 1400)` with weights `(0.6, 0.3, 0.1)` have weighted
median `$1,000`, q25 `$1,000`, and q75 `$1,200`. With one positive sample, all
three are that sample. Empty/zero-weight input has no median or quantile.

Use Kish effective sample size:

```text
ESS = (sum(w_i)^2) / sum(w_i^2)
```

Ten equal weights give ESS `10`; one dominant weight among ten rows yields ESS
near `1`. Raw count and ESS are both exposed.

## Hierarchy, blending, and shrinkage

For each tier `k`, compute local weighted median `L_k`, q25/q75, and `ESS_k`.
Broaden in documented order until evidence exists. A tier with `ESS_k >= 4` is
usable without mandatory broadening. Otherwise blend it with the next broader
estimate `B_k`; no-data at every tier returns `NO_HISTORICAL_EVIDENCE`.

Starting evidence strength uses `K = 6`:

```text
alpha_k = ESS_k / (ESS_k + K)
estimate_k = alpha_k * L_k + (1 - alpha_k) * B_k
```

Example: regional median `$1,100`, ESS `2`, metro baseline `$1,200` gives
`alpha=0.25` and estimate `$1,175`. A near-exact median with ESS `8` has
`alpha≈0.57`; it remains partly shrunk rather than claiming certainty. The broadest
available tenant baseline is its own `B_k`; if it has ESS below 4, return its
median with low confidence and no invented external prior.

Blended q25/q75 use the same alpha against broader q25/q75, then enforce
`lower <= estimate <= upper`. This is a transparent historical comparison range,
not a calibrated prediction interval.

## Range and confidence

Display `historical_comparison_range = [blended_q25, blended_q75]`, expanded to
include the point estimate. Call it a prediction interval only after a separately
documented calibration procedure demonstrates target coverage by tier; Phase 9
does not make that claim.

Confidence score begins as:

```text
score = 0.45 * min(1, ESS / 8)
      + 0.20 * tier_quality
      + 0.15 * mean_similarity
      + 0.10 * recency_quality
      + 0.10 * dispersion_quality
```

`tier_quality` is `1.0, .8, .6, .45, .3, .15` in tier order;
`mean_similarity` is normalized mean positive geometry/route weight;
`recency_quality = exp(-weighted_age_days / 30)`; and
`dispersion_quality = 1 - min(1, (q75-q25)/max(median,1))`. Unknown equipment
caps confidence at `LOW`. Starting labels: `HIGH >= .75`, `MEDIUM >= .45`, else
`LOW`. Inputs, score, caps, and thresholds are output diagnostics.

## Structured response

```json
{
  "model_version": "pricing-hierarchical-v1",
  "as_of": "2026-07-11T06:00:00Z",
  "point_estimate_usd": "1175.00",
  "historical_comparison_range_usd": ["1100.00", "1250.00"],
  "confidence": {"level": "LOW", "score": "0.39", "ess": "2.00", "raw_count": 2},
  "fallback": {"local_tier": "REGIONAL", "broader_tier": "METRO_CORRIDOR", "alpha": "0.25"},
  "comparables": [{"load_version_id": "…", "origin_miles": "9.2", "weight": "0.71"}],
  "warnings": ["SPARSE_EVIDENCE"]
}
```

Comparable entries include immutable version/load/rate-entry IDs, observed time,
total carrier rate, endpoint distances, route delta, age, weight, tier, and any
geography/equipment relaxation. Warnings include no data, sparse ESS, unknown
equipment, missing geography, and broad fallback; never hidden cross-tenant data.

## Rolling backtest and model selection

Create one chronological case for each load that first becomes `ACTIVE` and has an
eventual corrected final carrier total. For each case, predict at its first active
observation using only facts observed then; compare with eventual final total.
Cases never train on later rows, and corrections after the cutoff affect labels but
not inputs.

Compare production against tenant-wide median, equipment+distance-band median,
unshrunk nearest-lane weighted median, robust Huber regression, and quantile
regression only when sample size supports it. Report case count, MAE, median
absolute error, WAPE `sum(|e|)/sum(actual)`, q25–q75 range coverage, and breakdowns
by tier, equipment, and rich/sparse lane.

Select the simplest explainable model unless another method materially improves
rolling MAE and median error without worsening sparse-tier WAPE or range coverage.
Record parameters, data cutoff, model version, metrics, and rationale in
`DECISIONS.md`; do not select from Day 11 outcomes.
