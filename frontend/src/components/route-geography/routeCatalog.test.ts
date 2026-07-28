import { describe, expect, it } from "vitest";

import { routeCorridor } from "./routeCatalog";

describe("routeCorridor", () => {
  const grandPrairie = { latitude: 32.745964, longitude: -96.997785 };
  const katy = { latitude: 29.835, longitude: -95.73 };

  it("returns the deterministic Day 11 corridor in its travel direction", () => {
    const corridor = routeCorridor("75050", "77449", grandPrairie, katy);

    expect(corridor.label).toBe("Bundled road route");
    expect(corridor.isRoadRoute).toBe(true);
    expect(corridor.points[0]).toEqual(grandPrairie);
    expect(corridor.points.at(-1)).toEqual(katy);
  });

  it("does not reverse a directional corridor or invent road geometry", () => {
    const corridor = routeCorridor("77449", "75050", katy, grandPrairie);

    expect(corridor.label).toBe("Endpoint geography only");
    expect(corridor.isRoadRoute).toBe(false);
    expect(corridor.points).toEqual([katy, grandPrairie]);
  });
});
