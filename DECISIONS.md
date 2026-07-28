# Reviewer's guide to Carrier Pool decisions

## What this product is for

Carrier Pool is a small decision-support tool for freight brokers. For a load that
is still looking for a carrier, it shows two things:

1. A **call order** for that broker's known carriers, based on completed work in
   the past.
2. An **expected carrier payment**, with a range of comparable historical payments
   and an indication of how much evidence supports it.

It is deliberately a decision aid, not an automated dispatcher. A broker remains
responsible for checking live capacity, service requirements, and the final price.

## What the ranking means

The carrier score is a **historical-fit score**. It asks: based on this broker's
own completed loads, how closely does this carrier's past work resemble this load?

The score considers four equally documented kinds of historical evidence:

- Similarity of the completed route, while preserving direction.
- Match between the load's equipment and completed work.
- How recently relevant completed work was observed.
- How close the carrier's *last recorded historical delivery* was to this load's
  pickup, including how old that record is.

The last item is not GPS, live truck location, availability, reliability, or a
prediction that the carrier will accept. The interface says so wherever it is
shown.

The product never claims that a carrier is available, likely to accept, reliable,
or the objectively best dispatch choice. The source files do not contain outreach,
declines, capacity, service outcomes, or complete candidate sets, so those claims
would not be defensible.

## How sparse evidence is treated fairly

A carrier with only one relevant completed load should not look as certain as a
carrier with many independent, similar completed loads. The score therefore starts
from a neutral value of 50 and moves toward the observed fit only as independent
history accumulates. Confidence is shown separately from the score.

The serving ranking model is `carrier-ranking-v5`. It counts each completed source
version once, even when that one load supplies route, equipment, and recency facts.
This avoids making one load look like several independent observations. There is no
hard ceiling on the score: enough independent, recent, same-equipment route history
can produce a genuinely strong score. Confidence still saturates rather than growing
without bound.

Low-confidence or unsupported carriers are not given a call order in the UI. They
appear under **More history needed**, rather than being presented as a negative
judgment about the carrier.

## Why v5 is the current ranking model

The earlier v4 calculation both counted overlapping evidence more than once and
capped the amount of evidence used for score shrinkage. That could overstate a
small history while making a very well-supported score impossible.

V5 removes both problems while retaining the protection against overreacting to
small samples. On the current deterministic evaluation set, v4, v5, and a more
aggressive v6 candidate produced the same ordering results: 24 scored temporal
cases, 83.33% top-1 recall, 91.67% top-3 recall, 0.889 mean reciprocal rank, and
11.6% close ties. V6 merely made numbers farther apart; it did not improve an
ordering outcome, so it is not used.

The eventual booked carrier is only a weak evaluation proxy. It does not prove that
the model predicts acceptance or that a higher score causes a better business
outcome. The synthetic data can catch regressions; it cannot prove production
accuracy.

## How the payment estimate works

The payment estimate uses only the selected broker's completed carrier payments
that were known at the stated decision time. It first looks for the closest
same-direction, same-equipment routes. If those are scarce, it expands in documented
steps to nearby routes, metro corridors, similar-distance/equipment work, and then
broader same-broker history.

Nearby evidence receives more weight than distant or old evidence. The estimate is
a weighted historical middle value, not a black-box prediction. The displayed range
is a range of comparable historical payments. It is **not** called a prediction
interval, because the demo has not established calibrated future coverage.

FreightFlow and BrokerOS can replace a previously reported total. HaulDesk instead
adds ledger entries, including negative adjustments. The system applies each source's
own correction rules before using a final historical carrier payment.

## Time and corrections

Every source file is processed one at a time in timestamp order. The raw file and
each normalized version are retained. Current load screens are rebuilt from those
versions, rather than overwriting the past.

Each stored decision records its input version, decision time, model versions,
parameters, warnings, and evidence IDs. A later correction can change a later
decision, but it cannot change a decision that was already stored. Backtests also
use only facts observed at or before each historical cutoff.

## Broker privacy and separation

Each broker is isolated. A decision, its comparable loads, and its carrier evidence
come only from that broker's records. This is enforced in application queries and
PostgreSQL row-level security. An ID belonging to another broker returns the same
generic not-found response as an unknown ID.

The same MC or DOT number may appear for two brokers, but it remains two separate
broker-owned carrier records. No shared carrier pool is enabled.

## What the demo data proves

The generated demo has ten historical days, four time-stamped syncs per source per
day, and three Day 11 loads awaiting a carrier. It includes complete load
lifecycles, corrected totals and details, replacement snapshots, append-only ledger
adjustments, rich and sparse histories, recent and stale delivery evidence, and
tenant-local repeated carrier authorities.

The dataset is designed as deterministic test data. It proves that ingestion,
corrections, time cutoffs, privacy boundaries, explanations, and repeatable demo
decisions behave as intended. It does not prove that the displayed estimates or
rankings are accurate enough for real broker operations.

## Deliberate exclusions

- No login screen or production identity system. The demo uses a trusted broker
  context; database and API isolation are still enforced.
- No live TMS feeds, live routing, traffic, external geocoding, or truck tracking.
- No external map tiles. Geography is bundled and deterministic.
- No shared carrier pool.
- No neural network, boosted-tree model, or LLM deciding rankings or prices.

These omissions keep the review focused on auditable source handling, time,
isolation, and evidence rather than unsupported operational claims.

## Known limits and the threshold for change

The demo uses authored synthetic scenarios and a small number of outcome labels.
Before changing ranking weights or replacing the transparent payment estimator, the
project requires leakage-safe, same-population evaluation with independent real
outcomes and no degradation for sparse cases. A more complex model is not justified
merely because it produces more dramatic scores.

Useful next work is to broaden the authored carrier-history mix in the demo so each
broker visibly demonstrates strong, moderate, sparse, stale, and unsupported
historical-fit cases. That improves review coverage; it does not relax scoring,
privacy, or time rules.
