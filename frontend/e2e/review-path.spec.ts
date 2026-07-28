import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const tenants = [
  {
    id: "ff-demo-tenant",
    slug: "ff-broker",
    name: "North Star Freight",
    source_system: "FREIGHTFLOW",
  },
  { id: "hd-demo-tenant", slug: "hd-broker", name: "Alamo Brokerage", source_system: "HAULDESK" },
];

const exactLoad = load("ff-load-9001", "FF-9001", "Dallas", "Houston", "DRY_VAN");
const sparseLoad = load("hd-load-9001", "HD-9001", "Plano", "Baytown", "DRY_VAN");

function load(
  id: string,
  externalId: string,
  origin: string,
  destination: string,
  equipment: string,
) {
  return {
    id,
    external_id: externalId,
    status: "ACTIVE",
    equipment,
    distance_miles: "240",
    observed_at: "2026-07-11T06:00:00+00:00",
    expected_rate_usd: externalId === "FF-9001" ? "1250.00" : "1150.00",
    confidence: externalId === "FF-9001" ? "HIGH" : "LOW",
    stops: [
      {
        sequence: 1,
        is_pickup: true,
        is_dropoff: false,
        city: origin,
        state: "TX",
        postal_code: "75001",
        latitude: "32.9600",
        longitude: "-96.8380",
        geography_quality_flags: [],
        planned_date: "2026-07-11",
        scheduled_start_at: "2026-07-11T08:00:00+00:00",
      },
      {
        sequence: 2,
        is_pickup: false,
        is_dropoff: true,
        city: destination,
        state: "TX",
        postal_code: "77001",
        latitude: "29.8300",
        longitude: "-95.4300",
        geography_quality_flags: [],
        planned_date: "2026-07-11",
        scheduled_start_at: null,
      },
    ],
  };
}

function decision(loadData: typeof exactLoad, sparse = false) {
  return {
    as_of: "2026-07-11T06:00:00+00:00",
    ranking_model_version: "carrier-ranking-v2",
    pricing_model_version: "pricing-hierarchical-v1",
    model_parameters: {},
    load: loadData,
    pricing: {
      currency: "USD",
      point_estimate_usd: sparse ? "1150.00" : "1250.00",
      historical_comparison_lower_usd: sparse ? "1040.00" : "1100.00",
      historical_comparison_upper_usd: sparse ? "1290.00" : "1300.00",
      local_tier: sparse ? "REGIONAL" : "NEAR_EXACT",
      broader_tier: sparse ? "METRO_CORRIDOR" : null,
      blend_local_weight: sparse ? "0.25" : "1",
      raw_evidence_count: sparse ? 2 : 6,
      effective_evidence_count: sparse ? "1.4" : "5.3",
      warnings: sparse ? ["SPARSE_EVIDENCE", "BROADER_FALLBACK"] : [],
    },
    confidence: { level: sparse ? "LOW" : "HIGH", score: sparse ? "0.31" : "0.83", components: {} },
    ranked_carriers: [
      {
        rank: 1,
        carrier_id: sparse ? "hd-carrier-1" : "ff-carrier-1",
        carrier_name: sparse ? "Alamo Linehaul" : "Triangle Transport",
        adjusted_score: sparse ? "52.1" : "74.8",
        confidence_score: sparse ? "0.31" : "0.78",
        component_scores: {
          lane: sparse ? "0.3" : "0.9",
          equipment: "0.8",
          deadhead: sparse ? "0" : "0.7",
          recency: "0.6",
        },
        reason_codes: sparse ? ["SPARSE_HISTORY_SHRINKAGE", "DEADHEAD_LOCATION_UNAVAILABLE"] : [],
        explanation_bullets: sparse
          ? ["Limited completed history pulls the score toward a neutral prior."]
          : ["Last known delivery was 18 miles from pickup 2 days earlier."],
        evidence_ids: [sparse ? "hd-history-version" : "ff-history-version"],
        evidence_status: "SUPPORTED",
        tie_group: 1,
        evidence_by_component: {},
      },
    ],
    comparable_loads: [
      {
        load_id: sparse ? "HD-2101" : "FF-1101",
        load_external_id: sparse ? "HD-2101" : "FF-1101",
        carrier_rate_usd: sparse ? "1150.00" : "1180.00",
      },
    ],
    warnings: [],
  };
}

async function mockDeterministicApi(page: Page): Promise<void> {
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname === "/api/v1/tenants") {
      await route.fulfill({ json: tenants });
      return;
    }
    if (url.pathname === "/api/v1/loads") {
      const tenantId = request.headers()["x-tenant-id"];
      await route.fulfill({ json: tenantId === "hd-demo-tenant" ? [sparseLoad] : [exactLoad] });
      return;
    }
    if (url.pathname === `/api/v1/loads/${exactLoad.id}/decision`) {
      await route.fulfill({ json: decision(exactLoad) });
      return;
    }
    if (url.pathname === `/api/v1/loads/${sparseLoad.id}/decision`) {
      await route.fulfill({ json: decision(sparseLoad, true) });
      return;
    }
    await route.fulfill({ status: 404, json: { detail: "Load not found." } });
  });
}

test.beforeEach(async ({ page }) => {
  const manifest = JSON.parse(
    readFileSync(resolve(import.meta.dirname, "../../data/scenarios.json"), "utf8"),
  ) as { scenario_ids: string[] };
  expect(manifest.scenario_ids).toEqual(expect.arrayContaining(["SC-24", "SC-26"]));
  await mockDeterministicApi(page);
  await page.goto("/");
});

test("reviews exact and sparse Day 11 decisions without tenant cache leakage", async ({ page }) => {
  await page.getByLabel("Broker").selectOption("ff-demo-tenant");
  await page.getByRole("button", { name: "View decision for FF-9001" }).click();

  const exactDecision = page.getByRole("region", { name: "Dallas, TX to Houston, TX" });
  await expect(exactDecision.getByText("$1,250.00")).toBeVisible();
  await expect(exactDecision.getByText("$1,100.00–$1,300.00")).toBeVisible();
  await expect(exactDecision.getByText("Strong evidence", { exact: true })).toBeVisible();
  await expect(
    exactDecision.getByRole("heading", { name: "Triangle Transport", level: 4 }),
  ).toBeVisible();
  await expect(
    exactDecision.getByText("Last known delivery was 18 miles from pickup 2 days earlier."),
  ).toBeVisible();
  await expect(exactDecision.getByText("Route geography", { exact: true })).toBeVisible();
  await expect(exactDecision.locator(".route-geography__scene")).toBeVisible();

  await page.getByLabel("Broker").selectOption("hd-demo-tenant");
  await expect(
    page.getByRole("heading", { name: "Plano, TX to Baytown, TX", level: 3 }),
  ).toBeVisible();
  await expect(page.getByText("FF-9001")).not.toBeVisible();
  await page.getByRole("button", { name: "View decision for HD-9001" }).click();

  const sparseDecision = page.getByRole("region", { name: "Plano, TX to Baytown, TX" });
  await expect(sparseDecision.getByText("Limited evidence", { exact: true })).toBeVisible();
  await expect(
    sparseDecision.getByText("Regional lane, blended with metro corridor"),
  ).toBeVisible();
  await expect(sparseDecision.getByText("More history needed")).toBeVisible();
  await expect(sparseDecision.getByText("No call order")).toBeVisible();
  await expect(
    sparseDecision.getByText(
      "Limited history, this estimate is based on a small set of this broker’s completed loads, so certainty is lower.",
    ),
  ).toBeVisible();
});

test("returns the same generic not-found response for cross-tenant and unknown load IDs", async ({
  page,
}) => {
  const responses = await page.evaluate(async (crossTenantLoadId) => {
    const headers = { "X-Tenant-ID": "hd-demo-tenant" };
    const crossTenant = await fetch(`/api/v1/loads/${crossTenantLoadId}`, { headers });
    const unknown = await fetch("/api/v1/loads/unknown-load", { headers });
    return [
      { status: crossTenant.status, body: await crossTenant.json() },
      { status: unknown.status, body: await unknown.json() },
    ];
  }, exactLoad.id);

  expect(responses).toEqual([
    { status: 404, body: { detail: "Load not found." } },
    { status: 404, body: { detail: "Load not found." } },
  ]);
});

test("keeps the decision workspace inside a narrow viewport", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByLabel("Broker").selectOption("hd-demo-tenant");
  await page.getByRole("button", { name: "View decision for HD-9001" }).click();

  await expect(page.getByRole("region", { name: "Plano, TX to Baytown, TX" })).toBeVisible();
  const pageWidths = await page.evaluate(() => ({
    clientWidth: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }));
  expect(pageWidths.scrollWidth).toBe(pageWidths.clientWidth);
});

test("keeps active-load controls inside their card without overlap", async ({ page }) => {
  await page.setViewportSize({ width: 1024, height: 844 });
  await page.getByLabel("Broker").selectOption("hd-demo-tenant");

  const card = page.getByLabel("Active load HD-9001");
  const rate = card.getByText("$1,150.00", { exact: true });
  const status = card.getByText("ACTIVE", { exact: true });
  const action = card.getByRole("button", { name: "View decision for HD-9001" });
  await expect(rate).toBeVisible();
  await expect(status).toBeVisible();
  await expect(action).toBeVisible();

  const [cardBox, rateBox, statusBox, actionBox] = await Promise.all([
    card.boundingBox(),
    rate.boundingBox(),
    status.boundingBox(),
    action.boundingBox(),
  ]);
  expect(cardBox).not.toBeNull();
  expect(rateBox).not.toBeNull();
  expect(statusBox).not.toBeNull();
  expect(actionBox).not.toBeNull();
  if (!cardBox || !rateBox || !statusBox || !actionBox) return;

  for (const box of [rateBox, statusBox, actionBox]) {
    expect(box.x).toBeGreaterThanOrEqual(cardBox.x);
    expect(box.y).toBeGreaterThanOrEqual(cardBox.y);
    expect(box.x + box.width).toBeLessThanOrEqual(cardBox.x + cardBox.width);
    expect(box.y + box.height).toBeLessThanOrEqual(cardBox.y + cardBox.height);
  }
  expect(actionBox.x).toBeGreaterThanOrEqual(rateBox.x + rateBox.width - 2);
  expect(statusBox.x).toBeGreaterThanOrEqual(rateBox.x + rateBox.width - 2);
});
