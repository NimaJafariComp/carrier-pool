import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import {
  ApiError,
  getActiveLoads,
  getDecision,
  getTenants,
  type Decision,
  type Load,
} from "./api/client";
import { RouteGeographyPanel } from "./components/route-geography/RouteGeographyPanel";

const TENANT_STORAGE_KEY = "carrier-pool.demo-tenant-id";

function storedTenantId(): string | null {
  try {
    return globalThis.localStorage?.getItem(TENANT_STORAGE_KEY) ?? null;
  } catch {
    return null;
  }
}

function setStoredTenantId(tenantId: string): void {
  try {
    globalThis.localStorage?.setItem(TENANT_STORAGE_KEY, tenantId);
  } catch {
    // Demo selection remains in memory when browser storage is unavailable.
  }
}

function route(load: Load): string {
  const origin = load.stops.find((stop) => stop.is_pickup);
  const destination = [...load.stops].reverse().find((stop) => stop.is_dropoff);
  if (!origin || !destination) return "Route details unavailable";
  return `${origin.city}, ${origin.state} to ${destination.city}, ${destination.state}`;
}

function loadLabel(load: Pick<Load, "external_id" | "load_number">): string {
  return load.load_number ?? load.external_id;
}

function pickupDate(load: Load): string {
  const pickup = load.stops.find((stop) => stop.is_pickup);
  if (pickup?.scheduled_start_at) return formatDate(pickup.scheduled_start_at);
  return pickup?.planned_date ? formatDate(pickup.planned_date) : "Date pending";
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat("en-US", { dateStyle: "medium", timeZone: "UTC" }).format(
    new Date(value),
  );
}

function formatDateTime(value: string): string {
  return `${new Intl.DateTimeFormat("en-US", { dateStyle: "medium", timeStyle: "short", timeZone: "UTC" }).format(new Date(value))} UTC`;
}

function stopTiming(label: "Pickup" | "Delivery", stop: Load["stops"][number] | undefined): string {
  if (stop?.scheduled_start_at) return `${label} ${formatDateTime(stop.scheduled_start_at)}`;
  if (stop?.planned_date) return `${label} ${formatDate(stop.planned_date)}`;
  return `${label} timing pending`;
}

function scheduleSourceNote(load: Load): string {
  if (load.stops.some((stop) => stop.scheduled_start_at !== null)) {
    return "Appointment times supplied by source";
  }
  if (load.stops.some((stop) => stop.planned_date !== null)) {
    return "Planned dates only, no appointment times supplied";
  }
  return "No schedule supplied";
}

function usd(value: string | null, label = "expected rate"): string {
  return value === null
    ? "Rate unavailable"
    : `$${Number(value).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ${label}`;
}

function miles(value: string | null): string {
  if (value === null) return "N/A";
  return Number(value).toLocaleString("en-US", { maximumFractionDigits: 1 });
}

function titleCase(value: string): string {
  return value
    .split("_")
    .map((part) => `${part[0]}${part.slice(1).toLowerCase()}`)
    .join(" ");
}

function evidenceStrength(level: string | null | undefined): string {
  return (
    {
      HIGH: "Strong evidence",
      MEDIUM: "Moderate evidence",
      LOW: "Limited evidence",
    }[level ?? ""] ?? "Evidence pending"
  );
}

function tierDescription(decision: Decision): string {
  const local = decision.pricing.local_tier;
  if (!local) return "Tenant historical evidence";
  const localText = tierLabel(local, "summary");
  const broaderText = decision.pricing.broader_tier
    ? tierLabel(decision.pricing.broader_tier, "summary").toLowerCase()
    : null;
  return broaderText ? `${localText}, blended with ${broaderText}` : localText;
}

function tierLabel(tier: string, context: "summary" | "table" = "table"): string {
  const labels: Record<string, { summary: string; table: string }> = {
    NEAR_EXACT: { summary: "Near-exact lane", table: "Near-exact geography" },
    REGIONAL: { summary: "Regional lane", table: "Regional route" },
    METRO_CORRIDOR: { summary: "Metro corridor", table: "Metro-corridor route" },
    DISTANCE_EQUIPMENT: {
      summary: "Nearby route, same equipment",
      table: "Nearby route, same equipment",
    },
    TENANT_EQUIPMENT: {
      summary: "Same equipment, broader broker history",
      table: "Same equipment, broader history",
    },
    TENANT_ALL_EQUIPMENT: {
      summary: "Broader broker history",
      table: "Broader broker history",
    },
  };
  return labels[tier]?.[context] ?? titleCase(tier);
}

function comparableMatch(item: Decision["comparable_loads"][number]): {
  tier: string;
  endpoints: string;
} {
  const origin = item.origin_distance_miles;
  const destination = item.destination_distance_miles;
  const sameOrigin = origin !== null && origin !== undefined && origin < 0.5;
  const sameDestination = destination !== null && destination !== undefined && destination < 0.5;
  const endpoint = (distance: number | null | undefined, place: string): string => {
    if (distance === null || distance === undefined) return `${place} unavailable`;
    return distance < 0.5
      ? `Same ${place.toLowerCase()}`
      : `${Math.round(distance)} mi ${place.toLowerCase()}`;
  };

  return {
    tier: item.tier ? tierLabel(item.tier) : "Match details unavailable",
    endpoints:
      sameOrigin && sameDestination
        ? "Same pickup & delivery area"
        : `${endpoint(origin, "Pickup")} · ${endpoint(destination, "Delivery")}`,
  };
}

function rankingConfidence(score: string): string {
  const value = Number(score);
  if (value >= 0.75) return "High";
  if (value >= 0.45) return "Medium";
  return "Low";
}

function pricingWarning(warning: string): string {
  if (!/^[A-Z_]+$/.test(warning)) return warning;
  return (
    {
      BROADER_FALLBACK:
        "Few close route matches were available, so this estimate also uses this broker’s completed loads on other routes.",
      UNKNOWN_EQUIPMENT: "Equipment is unknown, which limits confidence in this estimate.",
      MISSING_GEOGRAPHY: "Some historical locations could not be compared precisely.",
      NO_HISTORICAL_EVIDENCE: "No eligible completed history was available for this estimate.",
    }[warning] ?? "Additional historical evidence note."
  );
}

function pricingWarnings(warnings: string[]): string[] {
  const hasSparseEvidence = warnings.includes("SPARSE_EVIDENCE");
  const visibleWarnings = hasSparseEvidence
    ? warnings.filter((warning) => warning !== "SPARSE_EVIDENCE" && warning !== "BROADER_FALLBACK")
    : warnings;
  return [
    ...(hasSparseEvidence
      ? [
          "Limited history, this estimate is based on a small set of this broker’s completed loads, so certainty is lower.",
        ]
      : []),
    ...visibleWarnings.map(pricingWarning),
  ].filter((warning, index, values) => values.indexOf(warning) === index);
}

function componentLabel(name: string): string {
  return (
    {
      lane: "Lane similarity",
      equipment: "Equipment match",
      deadhead: "Last delivery proximity (historical)",
      recency: "Recent completed work",
    }[name] ?? titleCase(name)
  );
}

function roundedEffectiveEvidence(value: string): string {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed.toFixed(1) : value;
}

function comparisonRange(decision: Decision): { label: string; value: string } {
  const { historical_comparison_lower_usd: lower, historical_comparison_upper_usd: upper } =
    decision.pricing;
  if (lower === null || upper === null) {
    return { label: "Historical comparison range", value: "Range unavailable" };
  }
  if (lower !== upper) {
    return {
      label: "Historical comparison range",
      value: `${usd(lower, "").trim()}–${usd(upper, "").trim()}`,
    };
  }
  const observedRates = decision.comparable_loads
    .map((item) => item.carrier_rate_usd)
    .filter((rate): rate is string => typeof rate === "string")
    .map(Number)
    .filter(Number.isFinite);
  const observedLower = Math.min(...observedRates);
  const observedUpper = Math.max(...observedRates);
  if (observedRates.length > 1 && observedLower < observedUpper) {
    return {
      label: "Comparable-rate spread",
      value: `$${observedLower.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}–$${observedUpper.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`,
    };
  }
  return { label: "Historical comparison range", value: usd(lower, "").trim() };
}

function carrierWarning(reasonCode: string): string | null {
  return (
    {
      DEADHEAD_LOCATION_UNAVAILABLE:
        "No historical delivery-to-pickup distance is available for this carrier.",
      UNKNOWN_TARGET_EQUIPMENT: "Target equipment is unknown, so equipment fit is limited.",
    }[reasonCode] ?? null
  );
}

function componentEvidenceLabel(component: string): string {
  return (
    {
      lane: "Lane matches",
      equipment: "Equipment history",
      recency: "Recent relevant work",
      deadhead: "Last recorded delivery",
    }[component] ?? titleCase(component)
  );
}

type EvidenceLoad = Decision["ranked_carriers"][number]["evidence_by_component"][string][number];

function historicalGap(days: number): string {
  const hours = Math.max(0, Math.round(days * 24));
  return hours < 48 ? `${Math.max(1, hours)} hours earlier` : `${Math.round(days)} days earlier`;
}

function componentEvidenceSummary(evidence: EvidenceLoad): string {
  if (
    evidence.delivery_to_pickup_miles !== null &&
    evidence.delivery_to_pickup_miles !== undefined &&
    evidence.delivery_to_pickup_gap_days !== null &&
    evidence.delivery_to_pickup_gap_days !== undefined
  ) {
    return [
      evidence.load_number ?? evidence.load_external_id,
      "Last recorded delivery",
      `${Math.round(evidence.delivery_to_pickup_miles)} mi from this pickup`,
      `recorded ${historicalGap(evidence.delivery_to_pickup_gap_days)}`,
      "Historical record only, not live truck location or availability.",
    ].join(" · ");
  }
  const geographyMatch =
    evidence.tier &&
    evidence.origin_distance_miles !== null &&
    evidence.origin_distance_miles !== undefined &&
    evidence.destination_distance_miles !== null &&
    evidence.destination_distance_miles !== undefined
      ? `${tierLabel(evidence.tier)} · ${Math.round(evidence.origin_distance_miles)} mi from pickup · ${Math.round(evidence.destination_distance_miles)} mi from delivery`
      : evidence.tier
        ? tierLabel(evidence.tier)
        : null;
  const bits = [
    evidence.load_number ?? evidence.load_external_id,
    evidence.route,
    evidence.equipment ? titleCase(evidence.equipment) : null,
    evidence.completed_observed_at
      ? `Completed ${formatDate(evidence.completed_observed_at)}`
      : null,
    evidence.distance_miles == null ? null : `${miles(evidence.distance_miles)} mi`,
    geographyMatch,
  ].filter((value): value is string => Boolean(value));
  return bits.join(" · ");
}

function CarrierRankings({ carriers }: { carriers: Decision["ranked_carriers"] }) {
  const orderedCarriers = [...carriers].sort((left, right) => left.rank - right.rank);
  if (orderedCarriers.length === 0) return null;
  const callOrder = orderedCarriers.filter(
    (carrier) =>
      carrier.evidence_status === "SUPPORTED" && Number(carrier.confidence_score) >= 0.45,
  );
  const moreHistoryNeeded = orderedCarriers.filter((carrier) => !callOrder.includes(carrier));
  const tieGroupCounts = new Map<number, number>();
  for (const carrier of callOrder) {
    if (carrier.tie_group !== null) {
      tieGroupCounts.set(carrier.tie_group, (tieGroupCounts.get(carrier.tie_group) ?? 0) + 1);
    }
  }

  return (
    <section aria-labelledby="carrier-rankings-title" className="carrier-rankings">
      <div className="carrier-rankings__heading">
        <div>
          <h3 id="carrier-rankings-title">Carriers to review first</h3>
        </div>
        <p>Completed-work history, not availability</p>
      </div>
      {callOrder.length > 0 && (
        <ol className="carrier-rankings__list">
          {callOrder.map((carrier) => {
            const isTie =
              carrier.tie_group !== null && (tieGroupCounts.get(carrier.tie_group) ?? 0) > 1;
            const bullets = [
              ...new Set([
                ...carrier.explanation_bullets,
                ...carrier.reason_codes
                  .map(carrierWarning)
                  .filter((warning): warning is string => warning !== null),
              ]),
            ];
            return (
              <li key={carrier.carrier_id} className="carrier-card">
                <div className="carrier-card__rank">
                  {isTie ? `Fit group ${carrier.tie_group}` : `Rank ${carrier.rank}`}
                </div>
                <div className="carrier-card__summary">
                  <h4>{carrier.carrier_name}</h4>
                  <p>
                    <strong>{Number(carrier.adjusted_score).toFixed(1)} / 100</strong> ·{" "}
                    {rankingConfidence(carrier.confidence_score)} confidence
                  </p>
                </div>
                <div className="component-scores__group">
                  <p>Why this carrier ranks here</p>
                  <dl
                    className="component-scores"
                    aria-label={`${carrier.carrier_name} ranking factors`}
                  >
                    {Object.entries(carrier.component_scores).map(([name, value]) => (
                      <div key={name}>
                        <dt>{componentLabel(name)}</dt>
                        <dd>{value === null ? "No evidence" : Math.round(Number(value) * 100)}</dd>
                      </div>
                    ))}
                  </dl>
                </div>
                <ul className="carrier-card__bullets">
                  {bullets.map((bullet) => (
                    <li key={bullet}>{bullet}</li>
                  ))}
                </ul>
                <details className="carrier-card__evidence">
                  <summary>Evidence by factor</summary>
                  {Object.entries(carrier.evidence_by_component).map(([component, evidence]) => (
                    <section key={component}>
                      <h5>{componentEvidenceLabel(component)}</h5>
                      {evidence.length > 0 ? (
                        <ul>
                          {evidence.map((item, index) => (
                            <li key={`${component}-${item.load_external_id ?? index}`}>
                              {componentEvidenceSummary(item)}
                            </li>
                          ))}
                        </ul>
                      ) : (
                        <p>No supporting completed load.</p>
                      )}
                    </section>
                  ))}
                </details>
              </li>
            );
          })}
        </ol>
      )}
      {moreHistoryNeeded.length > 0 && (
        <section className="limited-carriers" aria-labelledby="limited-carriers-title">
          <h4 id="limited-carriers-title">More history needed</h4>
          <p>Not enough matching completed work to set a call order.</p>
          <ol className="carrier-rankings__list">
            {moreHistoryNeeded.map((carrier) => (
              <li key={carrier.carrier_id} className="carrier-card carrier-card--limited">
                <div className="carrier-card__rank">No call order</div>
                <div className="carrier-card__summary">
                  <h4>{carrier.carrier_name}</h4>
                  <p>Limited historical evidence</p>
                </div>
              </li>
            ))}
          </ol>
        </section>
      )}
    </section>
  );
}

function DecisionDetail({ decision }: { decision: Decision }) {
  const { pricing } = decision;
  const rateRange = comparisonRange(decision);
  const warnings = pricingWarnings([...pricing.warnings, ...decision.warnings]);
  const pickup = decision.load.stops.find((stop) => stop.is_pickup);
  const dropoff = [...decision.load.stops].reverse().find((stop) => stop.is_dropoff);

  return (
    <section aria-labelledby="decision-title" className="decision-panel">
      <div className="decision-panel__heading">
        <div>
          <h2 id="decision-title">{route(decision.load)}</h2>
          <p>
            {stopTiming("Pickup", pickup)} · {stopTiming("Delivery", dropoff)}
          </p>
          <p className="schedule-source-note">{scheduleSourceNote(decision.load)}</p>
        </div>
      </div>

      <div className="decision-metrics">
        <div>
          <span>Expected carrier rate</span>
          <strong>{usd(pricing.point_estimate_usd, "")}</strong>
        </div>
        <div>
          <span>{rateRange.label}</span>
          <strong>{rateRange.value}</strong>
        </div>
        <div>
          <span>Confidence</span>
          <strong>{evidenceStrength(decision.confidence.level)}</strong>
        </div>
      </div>

      <div className="decision-evidence">
        <div>
          <span>Based on</span>
          <strong>
            {tierDescription(decision).replace(" matches", "")} · {pricing.raw_evidence_count}{" "}
            completed loads
          </strong>
        </div>
      </div>

      {warnings.length > 0 && (
        <section aria-label="Decision warnings" className="decision-warnings">
          <h3>Evidence notes</h3>
          <ul>
            {warnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        </section>
      )}

      <section aria-labelledby="comparables-title" className="comparables">
        <h3 id="comparables-title">Comparable completed loads</h3>
        {decision.comparable_loads.length > 0 ? (
          <div className="comparables__table-wrap">
            <table>
              <thead>
                <tr>
                  <th scope="col">Load</th>
                  <th scope="col">Route</th>
                  <th scope="col">Carrier pay</th>
                  <th scope="col">Match</th>
                  <th scope="col">Completed</th>
                </tr>
              </thead>
              <tbody>
                {decision.comparable_loads.map((item, index) => {
                  const match = comparableMatch(item);
                  return (
                    <tr key={`${item.load_external_id}-${index}`}>
                      <td className="comparables__load-id">
                        <span title={item.load_external_id}>
                          {item.load_number ?? item.load_external_id}
                        </span>
                      </td>
                      <td className="comparables__route">
                        <div>
                          <strong title={item.route}>{item.route}</strong>
                          {item.equipment && <span>{titleCase(item.equipment)}</span>}
                        </div>
                      </td>
                      <td className="comparables__rate">
                        {usd(item.carrier_rate_usd ?? null, "").trim()}
                      </td>
                      <td className="comparables__match">
                        <strong>{match.tier}</strong>
                        <span>{match.endpoints}</span>
                      </td>
                      <td className="comparables__completed">
                        {item.completed_observed_at
                          ? formatDate(item.completed_observed_at)
                          : "Date unavailable"}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : (
          <p>No comparable completed loads are recorded for this decision.</p>
        )}
      </section>

      <CarrierRankings carriers={decision.ranked_carriers} />

      <details className="decision-details">
        <summary>Decision details</summary>
        <dl>
          <div>
            <dt>As of</dt>
            <dd>{formatDateTime(decision.as_of)}</dd>
          </div>
          <div>
            <dt>Weighted evidence</dt>
            <dd>
              {pricing.raw_evidence_count} completed loads,{" "}
              {roundedEffectiveEvidence(pricing.effective_evidence_count)} weighted matches
            </dd>
          </div>
          <div>
            <dt>Pricing model</dt>
            <dd>{decision.pricing_model_version}</dd>
          </div>
          <div>
            <dt>Ranking model</dt>
            <dd>{decision.ranking_model_version}</dd>
          </div>
        </dl>
      </details>

      <RouteGeographyPanel load={decision.load} />
    </section>
  );
}

export function App() {
  const queryClient = useQueryClient();
  const [tenantId, setTenantId] = useState<string | null>(null);
  const [selectedLoadId, setSelectedLoadId] = useState<string | null>(null);
  const tenants = useQuery({ queryKey: ["tenants"], queryFn: getTenants });

  useEffect(() => {
    if (!tenants.data?.[0]) return;
    const storedTenant = storedTenantId();
    const nextTenantId =
      storedTenant !== null && tenants.data.some((tenant) => tenant.id === storedTenant)
        ? storedTenant
        : tenants.data[0].id;
    if (tenantId === nextTenantId) return;
    if (storedTenant !== nextTenantId) setStoredTenantId(nextTenantId);
    setSelectedLoadId(null);
    setTenantId(nextTenantId);
  }, [tenantId, tenants.data]);

  const loads = useQuery({
    queryKey: ["loads", tenantId],
    queryFn: () => getActiveLoads(tenantId ?? ""),
    enabled: tenantId !== null,
  });
  const decision = useQuery({
    queryKey: ["decision", tenantId, selectedLoadId],
    queryFn: () => getDecision(tenantId ?? "", selectedLoadId ?? ""),
    enabled: tenantId !== null && selectedLoadId !== null,
  });

  function changeTenant(nextTenantId: string): void {
    setStoredTenantId(nextTenantId);
    queryClient.invalidateQueries({ queryKey: ["loads"] });
    setSelectedLoadId(null);
    setTenantId(nextTenantId);
  }

  return (
    <main className="app-shell">
      <section aria-labelledby="app-title" className="app-shell__content">
        <header className="workspace-header">
          <div className="workspace-brand">
            <h1 id="app-title">Carrier Pool</h1>
          </div>
          <label className="tenant-picker" htmlFor="tenant-picker">
            <span>Broker</span>
            <select
              id="tenant-picker"
              value={tenantId ?? ""}
              onChange={(event) => changeTenant(event.target.value)}
              disabled={tenants.isLoading || tenants.isError}
            >
              {tenants.data?.map((tenant) => (
                <option key={tenant.id} value={tenant.id}>
                  {tenant.name} · {tenant.source_system}
                </option>
              ))}
            </select>
          </label>
        </header>

        <div className="workbench">
          <section aria-labelledby="active-loads-title" className="load-section">
            <div className="section-heading">
              <div>
                <h2 id="active-loads-title">Active loads</h2>
              </div>
              {loads.data && <span className="count-pill">{loads.data.length}</span>}
            </div>
            {(tenants.isLoading || loads.isLoading) && <p role="status">Loading active loads…</p>}
            {(tenants.isError || loads.isError) && (
              <p role="alert">
                Unable to load active freight. Check the selected broker and try again.
              </p>
            )}
            {loads.isSuccess && loads.data.length === 0 && (
              <p className="empty-state">No active loads for this broker.</p>
            )}
            {loads.data && loads.data.length > 0 && (
              <ol className="load-list">
                {loads.data.map((load) => (
                  <li key={load.id}>
                    <article
                      className={`load-card${selectedLoadId === load.id ? " load-card--selected" : ""}`}
                      aria-label={`Active load ${loadLabel(load)}`}
                    >
                      <div className="route-rail" aria-hidden="true" />
                      <div className="load-card__main">
                        <p className="load-card__reference">{loadLabel(load)}</p>
                        <h3>{route(load)}</h3>
                        <p>
                          {pickupDate(load)} · {load.equipment ?? "Equipment unknown"} ·{" "}
                          {miles(load.distance_miles)} mi
                        </p>
                      </div>
                      <div className="load-card__decision">
                        <div className="load-card__outcome">
                          <span>Expected rate</span>
                          <strong>{usd(load.expected_rate_usd, "").trim()}</strong>
                          <span>
                            {load.confidence
                              ? evidenceStrength(load.confidence)
                              : "Decision pending"}
                          </span>
                        </div>
                        <div className="load-card__action">
                          <span className="status-badge">{load.status}</span>
                          <button
                            type="button"
                            onClick={() => setSelectedLoadId(load.id)}
                            aria-label={`View decision for ${loadLabel(load)}`}
                          >
                            View decision
                          </button>
                        </div>
                      </div>
                    </article>
                  </li>
                ))}
              </ol>
            )}
          </section>

          <aside className="decision-workspace" aria-live="polite">
            {decision.isLoading && <p role="status">Loading decision evidence…</p>}
            {decision.isError &&
              (decision.error instanceof ApiError && decision.error.status === 422 ? (
                <p className="empty-state">No decision evidence is available yet.</p>
              ) : (
                <p role="alert">Unable to load this decision evidence.</p>
              ))}
            {decision.data ? (
              <DecisionDetail decision={decision.data} />
            ) : (
              !decision.isLoading &&
              !decision.isError && (
                <section
                  className="decision-placeholder"
                  aria-labelledby="decision-placeholder-title"
                >
                  <h2 id="decision-placeholder-title">Select a load</h2>
                  <p>Choose an active load to view its rate and carrier evidence.</p>
                </section>
              )
            )}
          </aside>
        </div>
      </section>
    </main>
  );
}
