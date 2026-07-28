import type { components } from "./generated";
import { describe, expect, it } from "vitest";

describe("generated API contract", () => {
  it("exports the backend load response schema", () => {
    const load: components["schemas"]["LoadResponse"] = {
      id: "load-1",
      external_id: "FF-9001",
      status: "ACTIVE",
      equipment: "DRY_VAN",
      distance_miles: "240",
      expected_rate_usd: null,
      confidence: null,
      observed_at: "2026-07-11T06:00:00+00:00",
      stops: [],
    };
    expect(load.status).toBe("ACTIVE");
  });
});
