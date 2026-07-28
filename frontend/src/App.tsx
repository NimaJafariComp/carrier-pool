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

function pickupDate(load: Load): string {
  const pickup = load.stops.find((stop) => stop.is_pickup)?.scheduled_start_at;
  return pickup ? formatDate(pickup) : "Date pending";
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat("en-US", { dateStyle: "medium", timeZone: "UTC" }).format(
    new Date(value),
  );
}

function formatDateTime(value: string): string {
  return `${new Intl.DateTimeFormat("en-US", { dateStyle: "medium", timeStyle: "short", timeZone: "UTC" }).format(new Date(value))} UTC`;
}

function usd(value: string | null, label = "expected rate"): string {
  return value === null
    ? "Rate unavailable"
    : `$${Number(value).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ${label}`;
}

function titleCase(value: string): string {
  return value
    .split("_")
    .map((part) => `${part[0]}${part.slice(1).toLowerCase()}`)
    .join(" ");
}

function tierDescription(decision: Decision): string {
  const local = decision.pricing.local_tier;
  if (!local) return "Tenant historical evidence";
  const labels: Record<string, string> = {
    NEAR_EXACT: "Near exact lane",
    REGIONAL: "Regional lane",
    METRO_CORRIDOR: "Metro corridor",
    DISTANCE_EQUIPMENT: "Distance and equipment",
    TENANT_EQUIPMENT: "Tenant equipment",
    TENANT_ALL_EQUIPMENT: "Tenant all-equipment",
  };
  const localText = labels[local] ?? titleCase(local);
  const broaderText = decision.pricing.broader_tier
    ? (
        labels[decision.pricing.broader_tier] ?? titleCase(decision.pricing.broader_tier)
      ).toLowerCase()
    : null;
  return broaderText ? `${localText} blended with ${broaderText}` : `${localText} evidence`;
}

function comparableSummary(item: Record<string, unknown>): string {
  const externalId = item.load_external_id ?? item.external_id ?? item.load_id ?? "Historical load";
  const rate =
    typeof item.carrier_rate_usd === "string"
      ? usd(item.carrier_rate_usd, "")
      : "Rate not recorded";
  return `${externalId} · ${rate.trim()}`;
}

function rankingConfidence(score: string): string {
  const value = Number(score);
  if (value >= 0.75) return "High";
  if (value >= 0.45) return "Medium";
  return "Low";
}

function rankingWarning(reasonCode: string): string | null {
  return (
    {
      SPARSE_HISTORY_SHRINKAGE: "Limited history pulls this score toward the neutral prior.",
      UNKNOWN_TARGET_EQUIPMENT: "Target equipment is unknown; equipment-fit confidence is limited.",
      DEADHEAD_LOCATION_UNAVAILABLE:
        "No valid historical delivery-to-pickup distance is available.",
    }[reasonCode] ?? null
  );
}

function CarrierRankings({ carriers }: { carriers: Decision["ranked_carriers"] }) {
  const orderedCarriers = [...carriers].sort((left, right) => left.rank - right.rank);
  if (orderedCarriers.length === 0) return null;

  return (
    <section aria-labelledby="carrier-rankings-title" className="carrier-rankings">
      <div className="carrier-rankings__heading">
        <div>
          <p className="section-heading__label">Historical fit</p>
          <h3 id="carrier-rankings-title">Carriers to review first</h3>
        </div>
        <p>Historical-fit evidence only</p>
      </div>
      <ol className="carrier-rankings__list">
        {orderedCarriers.map((carrier) => {
          const warnings = carrier.reason_codes
            .map(rankingWarning)
            .filter((warning): warning is string => warning !== null);
          return (
            <li key={carrier.carrier_id} className="carrier-card">
              <div className="carrier-card__rank">Rank {carrier.rank}</div>
              <div className="carrier-card__summary">
                <h4>{carrier.carrier_name}</h4>
                <p>
                  <strong>{Number(carrier.adjusted_score).toFixed(1)} / 100</strong> ·{" "}
                  {rankingConfidence(carrier.confidence_score)} confidence
                </p>
              </div>
              <dl
                className="component-scores"
                aria-label={`${carrier.carrier_name} component scores`}
              >
                {Object.entries(carrier.component_scores).map(([name, value]) => (
                  <div key={name}>
                    <dt>{titleCase(name)}</dt>
                    <dd>{Math.round(Number(value) * 100)}</dd>
                  </div>
                ))}
              </dl>
              <ul className="carrier-card__bullets">
                {carrier.explanation_bullets.map((bullet) => (
                  <li key={bullet}>{bullet}</li>
                ))}
                {warnings.map((warning) => (
                  <li key={warning}>{warning}</li>
                ))}
              </ul>
              <details className="carrier-card__evidence">
                <summary>Evidence loads ({carrier.evidence_ids.length})</summary>
                {carrier.evidence_ids.length > 0 ? (
                  <ul>
                    {carrier.evidence_ids.map((id) => (
                      <li key={id}>{id}</li>
                    ))}
                  </ul>
                ) : (
                  <p>No supporting load IDs were recorded.</p>
                )}
              </details>
            </li>
          );
        })}
      </ol>
    </section>
  );
}

function DecisionDetail({ decision }: { decision: Decision }) {
  const { pricing } = decision;
  const warnings = [...new Set([...pricing.warnings, ...decision.warnings])];
  const pickup = decision.load.stops.find((stop) => stop.is_pickup)?.scheduled_start_at;
  const dropoff = [...decision.load.stops]
    .reverse()
    .find((stop) => stop.is_dropoff)?.scheduled_start_at;

  return (
    <section aria-labelledby="decision-title" className="decision-panel">
      <div className="decision-panel__heading">
        <div>
          <p className="section-heading__label">Decision record</p>
          <h2 id="decision-title">{route(decision.load)}</h2>
          <p>
            {pickup ? `Pickup ${formatDateTime(pickup)}` : "Pickup timing pending"}
            {dropoff ? ` · Delivery ${formatDateTime(dropoff)}` : ""}
          </p>
        </div>
      </div>

      <div className="decision-metrics">
        <div>
          <span>Expected carrier rate</span>
          <strong>{usd(pricing.point_estimate_usd, "expected carrier rate")}</strong>
        </div>
        <div>
          <span>Historical comparison range</span>
          <strong>
            {pricing.historical_comparison_lower_usd && pricing.historical_comparison_upper_usd
              ? `${usd(pricing.historical_comparison_lower_usd, "").trim()}–${usd(pricing.historical_comparison_upper_usd, "").trim()} historical comparison range`
              : "Range unavailable"}
          </strong>
        </div>
        <div>
          <span>Confidence</span>
          <strong>{titleCase(decision.confidence.level)} confidence</strong>
        </div>
      </div>

      <div className="decision-evidence">
        <div>
          <span>Retrieval</span>
          <strong>{tierDescription(decision)}</strong>
        </div>
        <div>
          <span>Evidence</span>
          <strong>
            {pricing.raw_evidence_count} historical loads · {pricing.effective_evidence_count}{" "}
            effective
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
          <ul>
            {decision.comparable_loads.map((item, index) => (
              <li key={`${String(item.load_version_id ?? item.load_id ?? index)}`}>
                {comparableSummary(item)}
              </li>
            ))}
          </ul>
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
            <dt>Pricing model</dt>
            <dd>{decision.pricing_model_version}</dd>
          </div>
          <div>
            <dt>Ranking model</dt>
            <dd>{decision.ranking_model_version}</dd>
          </div>
        </dl>
      </details>
    </section>
  );
}

export function App() {
  const queryClient = useQueryClient();
  const [tenantId, setTenantId] = useState<string | null>(storedTenantId);
  const [selectedLoadId, setSelectedLoadId] = useState<string | null>(null);
  const tenants = useQuery({ queryKey: ["tenants"], queryFn: getTenants });

  useEffect(() => {
    if (tenantId || !tenants.data?.[0]) return;
    const firstTenant = tenants.data[0].id;
    setStoredTenantId(firstTenant);
    setTenantId(firstTenant);
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
        <header className="masthead">
          <p className="app-shell__eyebrow">Fictional broker workspace</p>
          <h1 id="app-title">Carrier Pool</h1>
          <p className="masthead__summary">Active freight, with evidence before action.</p>
        </header>

        <label className="tenant-picker" htmlFor="tenant-picker">
          <span>Fictional broker</span>
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

        <section aria-labelledby="active-loads-title" className="load-section">
          <div className="section-heading">
            <div>
              <p className="section-heading__label">Dispatch desk</p>
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
                  <article className="load-card">
                    <div className="route-rail" aria-hidden="true" />
                    <div className="load-card__main">
                      <p className="load-card__reference">{load.external_id}</p>
                      <h3>{route(load)}</h3>
                      <p>
                        {pickupDate(load)} · {load.equipment ?? "Equipment unknown"} ·{" "}
                        {load.distance_miles ?? "—"} mi
                      </p>
                    </div>
                    <div className="load-card__decision">
                      <strong>{usd(load.expected_rate_usd)}</strong>
                      <span>
                        {load.confidence
                          ? `${load.confidence[0]}${load.confidence.slice(1).toLowerCase()} confidence`
                          : "Decision pending"}
                      </span>
                      <span className="status-badge">{load.status}</span>
                      <button
                        type="button"
                        onClick={() => setSelectedLoadId(load.id)}
                        aria-label={`View decision for ${load.external_id}`}
                      >
                        View decision
                      </button>
                    </div>
                  </article>
                </li>
              ))}
            </ol>
          )}
        </section>

        {decision.isLoading && <p role="status">Loading decision evidence…</p>}
        {decision.isError &&
          (decision.error instanceof ApiError && decision.error.status === 422 ? (
            <p className="empty-state">No decision evidence is available yet.</p>
          ) : (
            <p role="alert">Unable to load this decision evidence.</p>
          ))}
        {decision.data && <DecisionDetail decision={decision.data} />}
      </section>
    </main>
  );
}
