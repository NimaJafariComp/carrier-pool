# Geography and Comparable-Lane Design

## Decision and non-goals

Geography is a deterministic, tenant-local retrieval aid. It identifies historical
loads that are operationally similar to a target route; it does not claim live
truck location, availability, or acceptance probability. Direction, endpoint
distance, equipment, and `as_of` remain explicit evidence.

No runtime geocoder, map tile provider, or network lookup is permitted. City text
is never converted to coordinates at runtime.

## Bundled centroid data and attribution

Phase 8.2 will bundle a small, reviewed Texas Triangle subset at
`backend/src/carrier_pool/geography/data/tx_triangle_zip_centroids.csv`, plus
`ATTRIBUTION.md` beside it. The selected source is the [GeoNames Postal Code
dataset](https://www.geonames.org/export/zip/), specifically the US extract. Its
postal-code data is CC BY 3.0 and requires GeoNames credit and a link; its own
documentation also warns that coordinates can be estimated or derived from nearby
codes. The bundled attribution must include:

- Source name, download URL, dataset file name, download date, source last-modified
  value, license URL, and SHA-256 of the downloaded file.
- The exact attribution text: `Contains GeoNames postal-code data
  (www.geonames.org), CC BY 3.0.`
- The extraction rule, curator, review date, and a record of any manually corrected
  scenario ZIP. Manual values require their own cited authoritative source and reason.
- One row per supported five-digit ZIP, with `zip`, `city`, `state`, `latitude`,
  `longitude`, `metro_group`, `source_accuracy`, and `data_quality_flag`.

The bundle is a finite reference for this synthetic Texas Triangle dataset, not a
complete USPS ZIP directory. A centroid is an approximation, never a facility or
street address.

## Normalization and enrichment

Source stop facts remain immutable. Enrichment stores normalized lookup fields and
quality flags without rewriting the raw source city/state/ZIP.

| Field | Normalization | Failure result |
|---|---|---|
| ZIP | Trim; accept `NNNNN` or `NNNNN-NNNN`; retain first five digits. | `None`; `INVALID_ZIP_FORMAT` or `MISSING_ZIP`. |
| State | Trim and uppercase; require two ASCII letters. | `None`; `INVALID_STATE`. |
| City | Trim, collapse internal whitespace, uppercase for comparison only. | `None`; `MISSING_CITY`. |
| Lookup | Require normalized ZIP present in bundled CSV; state must be `TX` for a supported row. | No coordinates/H3/metro; `ZIP_NOT_IN_REFERENCE` or `STATE_ZIP_MISMATCH`. |

ZIP is coordinate authority when lookup succeeds. City is explanatory and used for
diagnostics only; it never overrides a known ZIP or triggers fuzzy geocoding.

## Curated metro groups

Metro membership is an explicit bundle field, not an inferred radius. Initial
scenario coverage is:

| Metro | ZIPs |
|---|---|
| `DFW` | 75024, 75039, 75050, 75201, 76010, 76102 |
| `HOUSTON` | 77002, 77449, 77478, 77502, 77520 |
| `SAN_ANTONIO` | 78130, 78154, 78155, 78205 |

Rows outside these groups may use `OTHER_TEXAS_TRIANGLE`; they never silently join
one of the three metros.

## Distance, H3, and directional identity

The displayed endpoint metric is great-circle Haversine distance in statute miles,
using WGS84 latitude/longitude and Earth radius `3958.7613 mi`.

```text
origin_distance      = haversine(target.pickup, historical.pickup)
destination_distance = haversine(target.dropoff, historical.dropoff)
route_mile_delta     = abs(target.distance_miles - historical.distance_miles)
```

Explanations show these three values and the selected tier. H3 is optional: if the
dependency is installed, resolution 8 is the fine candidate bucket and resolution
6 is the coarse candidate bucket. H3 may narrow database candidates only. It never
sets a tier, replaces Haversine, or appears as the reason that two lanes match.

The directional route identity is ordered:

```text
origin ZIP/metro → destination ZIP/metro
```

`DFW → HOUSTON` and `HOUSTON → DFW` are different lanes at every tier. Multi-stop
loads use first pickup as origin and last drop-off as destination; intermediate
stops remain preserved but do not reverse or flatten the route identity.

## Comparable-load tiers

Only completed, same-tenant historical versions observed at or before `as_of` are
eligible. The target load is excluded. Tier selection returns the narrowest tier
with evidence, with broader tiers available for later shrinkage rather than being
merged invisibly.

| Tier | Eligibility | Starting parameters |
|---|---|---|
| `NEAR_EXACT` | Same direction; exact equipment; both endpoints valid. | Origin ≤25 mi, destination ≤25 mi. |
| `REGIONAL` | Same direction; exact equipment; both endpoints valid. | Origin ≤50 mi, destination ≤50 mi. |
| `METRO_CORRIDOR` | Same ordered origin/destination metro pair; exact equipment. | No endpoint-radius requirement. |
| `DISTANCE_EQUIPMENT` | Exact equipment; any valid direction; comparable route length. | Route-mile delta ≤max(25 mi, 15% of target miles), capped at 75 mi. |
| `TENANT_EQUIPMENT` | Same tenant; exact equipment. | No geography requirement. |
| `TENANT_ALL_EQUIPMENT` | Same tenant; equipment unknown, or no exact-equipment evidence exists. | No geography/equipment requirement. |

Invalid or missing endpoint geography cannot qualify for the first three tiers.
It may qualify for `DISTANCE_EQUIPMENT` if route miles are valid, then tenant
baselines. If no completed tenant history exists, retrieval returns no evidence;
it must not borrow another tenant's data or invent a geographic match.

These are configuration defaults, versioned with the decision model. Tune only by
leakage-free backtests and scenario assertions: change one parameter set at a time,
record old/new values and metrics, retain directionality, and update this document
plus test expectations. Never tune against Day 11 labels.

## Weights, effective sample size, and diagnostics

Within a tier, each comparable receives a geography-aware weight:

```text
w_geo = exp(-origin_distance / 25) * exp(-destination_distance / 25)
w_route = exp(-route_mile_delta / 50)
w_recency = exp(-age_days / 30)
w = w_geo * w_route * w_recency
```

For tiers without valid endpoints, omit unavailable geography terms rather than
treating them as zero distance; diagnostics identify the relaxation and quality
flag. Tier membership remains the primary guardrail, so a broad baseline cannot
masquerade as a near-exact lane.

Report raw count, total weight, and Kish effective sample size:

```text
ESS = (sum(w) ^ 2) / sum(w ^ 2)
```

ESS, not raw count alone, controls confidence and shrinkage in Phase 9. A handful
of nearby recent loads can retain meaningful weight; many weak, distant, or stale
loads must not produce artificial certainty. Evidence payloads include tier,
endpoint distances, route-mile delta, age, individual weight, quality flags, and
the model/versioned threshold set.

## Approval criteria

Before implementation, approval confirms: GeoNames attribution is acceptable;
the 25/50-mile endpoint thresholds, H3 8/6 candidate-only role, distance band,
metro memberships, and ESS/weight defaults are accepted. Phase 8.2 may then add
the local CSV and enrichment; no code or reference data is created by this task.
