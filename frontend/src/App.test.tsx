import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { components } from "./api/generated";
import { App } from "./App";

const tenantA = {
  id: "tenant-a",
  slug: "northstar",
  name: "Northstar Freight",
  source_system: "FREIGHTFLOW",
};
const tenantB = { id: "tenant-b", slug: "gulf", name: "Gulf Broker", source_system: "HAULDESK" };
const activeLoad = {
  id: "load-a",
  external_id: "FF-9001",
  status: "ACTIVE",
  equipment: "DRY_VAN",
  distance_miles: "240",
  observed_at: "2026-07-11T06:00:00+00:00",
  expected_rate_usd: "1250.00",
  confidence: "MEDIUM",
  stops: [
    {
      sequence: 1,
      is_pickup: true,
      is_dropoff: false,
      city: "Dallas",
      state: "TX",
      postal_code: "75201",
      latitude: "32.7767",
      longitude: "-96.7970",
      geography_quality_flags: [],
      planned_date: "2026-07-11",
      scheduled_start_at: null,
    },
    {
      sequence: 2,
      is_pickup: false,
      is_dropoff: true,
      city: "Houston",
      state: "TX",
      postal_code: "77002",
      latitude: "29.7604",
      longitude: "-95.3698",
      geography_quality_flags: [],
      planned_date: "2026-07-11",
      scheduled_start_at: null,
    },
  ],
};

function decision(overrides: Partial<components["schemas"]["DecisionResponse"]> = {}) {
  return {
    as_of: "2026-07-11T06:00:00+00:00",
    ranking_model_version: "carrier-ranking-v2",
    pricing_model_version: "pricing-hierarchical-v1",
    model_parameters: {},
    load: activeLoad,
    pricing: {
      currency: "USD",
      point_estimate_usd: "1250.00",
      historical_comparison_lower_usd: "1100.00",
      historical_comparison_upper_usd: "1300.00",
      local_tier: "NEAR_EXACT",
      broader_tier: null,
      blend_local_weight: "1",
      raw_evidence_count: 6,
      effective_evidence_count: "5.3",
      warnings: [],
    },
    confidence: { level: "HIGH", score: "0.83", components: {} },
    ranked_carriers: [],
    comparable_loads: [
      {
        load_external_id: "FF-1001",
        route: "Dallas, TX → Houston, TX",
        equipment: "DRY_VAN",
        completed_observed_at: "2026-07-08T00:00:00+00:00",
        distance_miles: "240",
        carrier_rate_usd: "1180.00",
        origin_distance_miles: 0,
        destination_distance_miles: 0,
        route_mile_difference: "0",
        recency_days: 3,
        tier: "NEAR_EXACT",
      },
    ],
    warnings: [],
    ...overrides,
  } satisfies components["schemas"]["DecisionResponse"];
}

function renderApp(
  queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } }),
) {
  return {
    queryClient,
    ...render(
      <QueryClientProvider client={queryClient}>
        <App />
      </QueryClientProvider>,
    ),
  };
}

let storedValues: Record<string, string> = {};

beforeEach(() => {
  storedValues = {};
  vi.stubGlobal("localStorage", {
    getItem: (key: string) => storedValues[key] ?? null,
    setItem: (key: string, value: string) => {
      storedValues[key] = value;
    },
  });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("App", () => {
  it("shows fictional-broker selector and active-load summary", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string) =>
        Promise.resolve({
          ok: true,
          json: async () => (url.endsWith("/tenants") ? [tenantA, tenantB] : [activeLoad]),
        }),
      ),
    );
    renderApp();

    const selector = await screen.findByLabelText("Broker");
    await waitFor(() => {
      expect((selector as HTMLSelectElement).value).toBe("tenant-a");
    });
    expect(
      await screen.findByRole("heading", { name: "Dallas, TX to Houston, TX" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Expected rate")).toBeInTheDocument();
    expect(screen.getAllByText("$1,250.00")).toHaveLength(1);
    expect(screen.getByText("Moderate evidence")).toBeInTheDocument();
    expect(
      screen.getByText("Choose an active load to view its rate and carrier evidence."),
    ).toBeInTheDocument();
  });

  it("replaces a stale stored broker with an approved demo broker", async () => {
    storedValues["carrier-pool.demo-tenant-id"] = "old-test-tenant";
    const fetchMock = vi.fn((url: string, init?: RequestInit) =>
      Promise.resolve({
        ok: true,
        json: async () => (url.endsWith("/tenants") ? [tenantA, tenantB] : [activeLoad]),
        headers: init?.headers,
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    renderApp();

    const selector = await screen.findByLabelText("Broker");
    await waitFor(() => expect((selector as HTMLSelectElement).value).toBe(tenantA.id));
    expect(storedValues["carrier-pool.demo-tenant-id"]).toBe(tenantA.id);
    expect(
      fetchMock.mock.calls.some(
        ([url, init]) =>
          String(url).includes("/loads") &&
          new Headers((init as RequestInit | undefined)?.headers).get("X-Tenant-ID") ===
            "old-test-tenant",
      ),
    ).toBe(false);
  });

  it("switches tenant, stores only its ID, and invalidates load queries", async () => {
    const fetchMock = vi.fn((url: string, init?: RequestInit) =>
      Promise.resolve({
        ok: true,
        json: async () => {
          if (url.endsWith("/tenants")) return [tenantA, tenantB];
          return init?.headers && new Headers(init.headers).get("X-Tenant-ID") === tenantB.id
            ? []
            : [activeLoad];
        },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const { queryClient } = renderApp();
    const invalidate = vi.spyOn(queryClient, "invalidateQueries");
    const user = userEvent.setup();

    const selector = screen.getByLabelText("Broker") as HTMLSelectElement;
    await waitFor(() => expect(selector.options).toHaveLength(2));
    await user.selectOptions(selector, tenantB.id);
    await waitFor(() =>
      expect(screen.getByText("No active loads for this broker.")).toBeInTheDocument(),
    );
    expect(storedValues["carrier-pool.demo-tenant-id"]).toBe(tenantB.id);
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["loads"] });
  });

  it("shows a loading state while broker data is pending", () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => new Promise(() => undefined)),
    );

    renderApp();

    expect(screen.getByRole("status")).toHaveTextContent("Loading active loads");
  });

  it("shows an error state when broker data cannot be loaded", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.reject(new Error("network unavailable"))),
    );

    renderApp();

    expect(await screen.findByRole("alert")).toHaveTextContent("Unable to load active freight");
  });

  it("shows a high-confidence decision with canonical timing and comparable evidence", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string) =>
        Promise.resolve({
          ok: true,
          json: async () =>
            url.endsWith("/tenants")
              ? [tenantA]
              : url.includes("/decision")
                ? decision()
                : [activeLoad],
        }),
      ),
    );
    renderApp();

    await userEvent
      .setup()
      .click(await screen.findByRole("button", { name: /view decision for ff-9001/i }));

    expect(
      await screen.findByRole("heading", { name: "Dallas, TX to Houston, TX", level: 2 }),
    ).toBeInTheDocument();
    expect(screen.getByText(/Pickup Jul 11, 2026/)).toBeInTheDocument();
    expect(screen.getByText(/Delivery Jul 11, 2026/)).toBeInTheDocument();
    expect(screen.queryByText("Delivery timing pending")).not.toBeInTheDocument();
    expect(
      screen.getByText("Planned dates only, no appointment times supplied"),
    ).toBeInTheDocument();
    expect(screen.getAllByText("$1,250.00")).toHaveLength(2);
    expect(screen.getByText("$1,100.00–$1,300.00")).toBeInTheDocument();
    expect(screen.getByText("Strong evidence")).toBeInTheDocument();
    expect(screen.queryByText("Why this is not high evidence")).not.toBeInTheDocument();
    expect(screen.getByText("Near-exact lane · 6 completed loads")).toBeInTheDocument();
    expect(screen.getByText("6 completed loads, 5.3 weighted matches")).toBeInTheDocument();
    expect(screen.getByText("$1,180.00")).toBeInTheDocument();
    expect(screen.getByText("As of")).toBeInTheDocument();
    expect(screen.getByText("pricing-hierarchical-v1")).toBeInTheDocument();
    expect(screen.getByText("Route geography")).toBeInTheDocument();
    expect(screen.getByText("240.0 mi")).toBeInTheDocument();
  });

  it("shows medium and low confidence with explicit evidence warnings", async () => {
    const lowDecision = decision({
      pricing: {
        ...decision().pricing,
        local_tier: "REGIONAL",
        broader_tier: "METRO_CORRIDOR",
        raw_evidence_count: 2,
        effective_evidence_count: "1.4",
        warnings: ["BROADER_FALLBACK"],
      },
      confidence: { level: "LOW", score: "0.31", components: {} },
      warnings: ["UNKNOWN_EQUIPMENT"],
    });
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string) =>
        Promise.resolve({
          ok: true,
          json: async () =>
            url.endsWith("/tenants")
              ? [tenantA]
              : url.includes("/decision")
                ? lowDecision
                : [activeLoad],
        }),
      ),
    );
    renderApp();

    await userEvent.setup().click(await screen.findByRole("button", { name: /view decision/i }));

    expect(await screen.findByText("Limited evidence")).toBeInTheDocument();
    expect(
      screen.getByText("Regional lane, blended with metro corridor · 2 completed loads"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "Few close route matches were available, so this estimate also uses this broker’s completed loads on other routes.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Equipment is unknown, which limits confidence in this estimate."),
    ).toBeInTheDocument();
    expect(screen.queryByText("Why this is not high evidence")).not.toBeInTheDocument();
  });

  it("shows the observed comparable-rate spread when weighted quantiles collapse", async () => {
    const collapsedRange = decision({
      pricing: {
        ...decision().pricing,
        point_estimate_usd: "1233.33",
        historical_comparison_lower_usd: "1233.33",
        historical_comparison_upper_usd: "1233.33",
      },
      comparable_loads: [
        {
          ...decision().comparable_loads[0],
          load_external_id: "FF-1001",
          carrier_rate_usd: "1225.00",
        },
        {
          ...decision().comparable_loads[0],
          load_external_id: "FF-1002",
          carrier_rate_usd: "1250.00",
        },
      ],
    });
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string) =>
        Promise.resolve({
          ok: true,
          json: async () =>
            url.endsWith("/tenants")
              ? [tenantA]
              : url.includes("/decision")
                ? collapsedRange
                : [activeLoad],
        }),
      ),
    );
    renderApp();

    await userEvent.setup().click(await screen.findByRole("button", { name: /view decision/i }));

    expect(screen.getByText("Comparable-rate spread")).toBeInTheDocument();
    expect(screen.getByText("$1,225.00–$1,250.00")).toBeInTheDocument();
  });

  it("renders comparable history as a readable evidence table", async () => {
    const brokerOsDecision = decision({
      comparable_loads: [
        {
          ...decision().comparable_loads[0],
          load_external_id: "a0jFB388F4DF5D2288",
          load_number: "BO-3101",
          tier: "DISTANCE_EQUIPMENT",
          origin_distance_miles: 18,
          destination_distance_miles: 22,
        },
      ],
    });
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string) =>
        Promise.resolve({
          ok: true,
          json: async () =>
            url.endsWith("/tenants")
              ? [tenantA]
              : url.includes("/decision")
                ? brokerOsDecision
                : [activeLoad],
        }),
      ),
    );
    renderApp();

    await userEvent.setup().click(await screen.findByRole("button", { name: /view decision/i }));

    expect(screen.getByRole("columnheader", { name: "Carrier pay" })).toBeInTheDocument();
    expect(screen.getByText("Dallas, TX → Houston, TX")).toBeInTheDocument();
    expect(screen.getByText("Nearby route, same equipment")).toBeInTheDocument();
    expect(screen.getByText("18 mi from pickup · 22 mi from delivery")).toBeInTheDocument();
    expect(screen.getByText("$1,180.00")).toBeInTheDocument();
    expect(screen.getByText("BO-3101")).toBeInTheDocument();
    expect(screen.queryByText("a0jFB388F4DF5D2288")).not.toBeInTheDocument();
  });

  it("progressively reveals comparable loads without making the evidence table unbounded", async () => {
    const comparableLoads = Array.from({ length: 7 }, (_, index) => ({
      ...decision().comparable_loads[0],
      load_external_id: `FF-${1001 + index}`,
      load_number: `FF-${1001 + index}`,
    }));
    const manyComparables = decision({ comparable_loads: comparableLoads });
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string) =>
        Promise.resolve({
          ok: true,
          json: async () =>
            url.endsWith("/tenants")
              ? [tenantA]
              : url.includes("/decision")
                ? manyComparables
                : [activeLoad],
        }),
      ),
    );
    renderApp();

    await userEvent.setup().click(await screen.findByRole("button", { name: /view decision/i }));

    expect(screen.getByText("Showing 5 of 7")).toBeInTheDocument();
    expect(screen.getByText("FF-1005")).toBeInTheDocument();
    expect(screen.queryByText("FF-1006")).not.toBeInTheDocument();
    const reveal = screen.getByRole("button", { name: "Show 2 more loads" });
    expect(reveal).toHaveAttribute("aria-expanded", "false");

    await userEvent.setup().click(reveal);

    expect(screen.getByText("Showing all 7")).toBeInTheDocument();
    expect(screen.getByText("FF-1006")).toBeInTheDocument();
    expect(screen.getByText("FF-1007")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Show fewer loads" })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
  });

  it("shows a medium-confidence decision without overstating certainty", async () => {
    const mediumDecision = decision({
      confidence: { level: "MEDIUM", score: "0.57", components: {} },
    });
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string) =>
        Promise.resolve({
          ok: true,
          json: async () =>
            url.endsWith("/tenants")
              ? [tenantA]
              : url.includes("/decision")
                ? mediumDecision
                : [activeLoad],
        }),
      ),
    );
    renderApp();

    await userEvent.setup().click(await screen.findByRole("button", { name: /view decision/i }));

    await screen.findByRole("heading", { name: "Dallas, TX to Houston, TX", level: 2 });
    expect(screen.getAllByText("Moderate evidence")).toHaveLength(2);
    expect(screen.queryByText(/prediction interval/i)).not.toBeInTheDocument();
  });

  it("shows an insufficient-data state without inventing a rate", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string) =>
        Promise.resolve({
          ok: !url.includes("/decision"),
          status: url.includes("/decision") ? 422 : 200,
          json: async () => (url.endsWith("/tenants") ? [tenantA] : [activeLoad]),
        }),
      ),
    );
    renderApp();

    await userEvent.setup().click(await screen.findByRole("button", { name: /view decision/i }));

    expect(await screen.findByText("No decision evidence is available yet.")).toBeInTheDocument();
    expect(screen.queryByText(/expected carrier rate/)).not.toBeInTheDocument();
  });

  it("renders carrier rankings in rank order with structured evidence details", async () => {
    const rankedDecision = decision({
      ranked_carriers: [
        {
          rank: 2,
          carrier_id: "carrier-2",
          carrier_name: "Mesa Linehaul",
          adjusted_score: "66.4",
          confidence_score: "0.52",
          component_scores: { lane: "0.7", equipment: "0.8", deadhead: "0.2", recency: "0.6" },
          reason_codes: [],
          explanation_bullets: ["Completed equipment-matching loads are recorded."],
          evidence_ids: ["load-version-2"],
          evidence_status: "SUPPORTED",
          tie_group: 2,
          evidence_by_component: {},
        },
        {
          rank: 1,
          carrier_id: "carrier-1",
          carrier_name: "Triangle Transport",
          adjusted_score: "74.8",
          confidence_score: "0.78",
          component_scores: { lane: "0.9", equipment: "1", deadhead: "0.7", recency: "0.8" },
          reason_codes: [],
          explanation_bullets: ["Last known delivery was 18 miles from pickup 2 days earlier."],
          evidence_ids: ["load-version-1"],
          evidence_status: "SUPPORTED",
          tie_group: 1,
          evidence_by_component: {
            lane: [
              {
                load_external_id: "FF-1101",
                route: "Grand Prairie, TX → Katy, TX",
                equipment: "DRY_VAN",
                completed_observed_at: "2026-07-08T23:00:00+00:00",
                distance_miles: "239.4",
                tier: "NEAR_EXACT",
                origin_distance_miles: 12,
                destination_distance_miles: 23,
              },
            ],
            deadhead: [
              {
                load_external_id: "FF-1401",
                route: "Houston, TX → Grand Prairie, TX",
                equipment: "DRY_VAN",
                completed_observed_at: "2026-07-10T18:00:00+00:00",
                distance_miles: "240",
                tier: null,
                origin_distance_miles: null,
                destination_distance_miles: null,
                delivery_to_pickup_miles: 12.4,
                delivery_to_pickup_gap_days: 0.75,
              },
            ],
          },
        },
      ],
    });
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string) =>
        Promise.resolve({
          ok: true,
          json: async () =>
            url.endsWith("/tenants")
              ? [tenantA]
              : url.includes("/decision")
                ? rankedDecision
                : [activeLoad],
        }),
      ),
    );
    renderApp();

    await userEvent.setup().click(await screen.findByRole("button", { name: /view decision/i }));

    const carriers = await screen.findAllByRole("heading", { level: 4 });
    expect(carriers.map((carrier) => carrier.textContent)).toEqual([
      "Triangle Transport",
      "Mesa Linehaul",
    ]);
    expect(screen.getByText("Rank 1")).toBeInTheDocument();
    expect(screen.getByText("74.8 / 100")).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Triangle Transport", level: 4 }).parentElement,
    ).toHaveTextContent("High confidence");
    expect(screen.getByText("90")).toBeInTheDocument();
    expect(
      screen.getByText("Last known delivery was 18 miles from pickup 2 days earlier."),
    ).toBeInTheDocument();
    expect(screen.getAllByText("Why this carrier ranks here")).toHaveLength(2);
    await userEvent.setup().click(screen.getAllByText("Evidence by factor")[0]);
    expect(
      screen.getByText(
        "FF-1101 · Grand Prairie, TX → Katy, TX · Dry Van · Completed Jul 8, 2026 · 239.4 mi · Near-exact geography · 12 mi from pickup · 23 mi from delivery",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "FF-1401 · Last recorded delivery · 12 mi from this pickup · recorded 18 hours earlier · Historical record only, not live truck location or availability.",
      ),
    ).toBeInTheDocument();
  });

  it("makes sparse and missing-deadhead evidence explicit without operational claims", async () => {
    const sparseDecision = decision({
      pricing: {
        ...decision().pricing,
        warnings: ["SPARSE_EVIDENCE", "BROADER_FALLBACK"],
      },
      ranked_carriers: [
        {
          rank: 1,
          carrier_id: "carrier-3",
          carrier_name: "Prairie Carrier",
          adjusted_score: "52.1",
          confidence_score: "0.31",
          component_scores: { lane: "0.3", equipment: "0.5", deadhead: "0", recency: "0.2" },
          reason_codes: ["SPARSE_HISTORY_SHRINKAGE", "DEADHEAD_LOCATION_UNAVAILABLE"],
          explanation_bullets: [
            "Limited completed history pulls the score toward a neutral prior.",
          ],
          evidence_ids: [],
          evidence_status: "SUPPORTED",
          tie_group: 1,
          evidence_by_component: {},
        },
      ],
    });
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string) =>
        Promise.resolve({
          ok: true,
          json: async () =>
            url.endsWith("/tenants")
              ? [tenantA]
              : url.includes("/decision")
                ? sparseDecision
                : [activeLoad],
        }),
      ),
    );
    renderApp();

    await userEvent.setup().click(await screen.findByRole("button", { name: /view decision/i }));

    expect(await screen.findByText("More history needed")).toBeInTheDocument();
    expect(screen.getByText("Limited historical evidence")).toBeInTheDocument();
    expect(screen.queryByText("52.1 / 100")).not.toBeInTheDocument();
    expect(
      screen.getByText(
        "Limited history, this estimate is based on a small set of this broker’s completed loads, so certainty is lower.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/few close route matches were available/i)).not.toBeInTheDocument();
    expect(
      screen.getByText("Not enough matching completed work to set a call order."),
    ).toBeInTheDocument();
    expect(screen.queryByText(/carrier.*available|accept|reliable/i)).not.toBeInTheDocument();
  });
});
