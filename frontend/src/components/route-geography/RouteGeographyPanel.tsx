import { useEffect, useState } from "react";

import type { Load } from "../../api/client";
import { GlobeToTexasScene } from "./GlobeToTexasScene";
import { routeCorridor, type RoutePoint } from "./routeCatalog";

type Props = { load: Load };

function endpoint(load: Load, pickup: boolean): Load["stops"][number] | undefined {
  const stops = pickup ? load.stops : [...load.stops].reverse();
  return stops.find((stop) => (pickup ? stop.is_pickup : stop.is_dropoff));
}

function coordinate(stop: Load["stops"][number] | undefined): RoutePoint | null {
  const latitude =
    stop?.latitude === null || stop?.latitude === undefined ? NaN : Number(stop.latitude);
  const longitude =
    stop?.longitude === null || stop?.longitude === undefined ? NaN : Number(stop.longitude);
  return Number.isFinite(latitude) && Number.isFinite(longitude) ? { latitude, longitude } : null;
}

function place(stop: Load["stops"][number]): string {
  return `${stop.city}, ${stop.state}`;
}

export function RouteGeographyPanel({ load }: Props) {
  const pickup = endpoint(load, true);
  const delivery = endpoint(load, false);
  const origin = coordinate(pickup);
  const destination = coordinate(delivery);
  const [reducedMotion, setReducedMotion] = useState(false);
  const [resetKey, setResetKey] = useState(0);
  const [autoPlay, setAutoPlay] = useState(true);
  const [replay, setReplay] = useState({ loadId: load.id, sequence: 0 });

  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    const update = () => setReducedMotion(query.matches);
    update();
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);

  useEffect(() => {
    setAutoPlay(true);
    setReplay({ loadId: load.id, sequence: 0 });
  }, [load.id]);

  if (!pickup || !delivery || !origin || !destination) {
    return (
      <section className="route-geography route-geography--unavailable">
        <div>
          <p className="section-heading__label">Route geography</p>
          <h3 id="route-geography-title">Map unavailable</h3>
          <p>Valid source ZIP coordinates are not available for both route endpoints.</p>
        </div>
      </section>
    );
  }

  const corridor = routeCorridor(pickup.postal_code, delivery.postal_code, origin, destination);
  const miles = load.distance_miles === null ? null : Number(load.distance_miles);
  const distance =
    miles !== null && Number.isFinite(miles) ? `${miles.toFixed(1)} mi` : "Distance unavailable";
  return (
    <section className="route-geography">
      <header className="route-geography__heading">
        <div>
          <p className="section-heading__label">Route geography</p>
          <h3 id="route-geography-title">
            {place(pickup)} to {place(delivery)}
          </h3>
        </div>
        <div className="route-geography__controls">
          <button
            type="button"
            onClick={() => {
              setAutoPlay(true);
              setReplay((current) => ({ loadId: load.id, sequence: current.sequence + 1 }));
              setResetKey((value) => value + 1);
            }}
          >
            Reset view
          </button>
          <span>Drag to rotate, scroll to zoom</span>
        </div>
      </header>
      <GlobeToTexasScene
        corridor={corridor.points}
        isRoadRoute={corridor.isRoadRoute}
        pickupLabel={place(pickup)}
        deliveryLabel={place(delivery)}
        reducedMotion={reducedMotion}
        resetKey={resetKey}
        autoPlay={autoPlay}
        immediateIntro={replay.loadId === load.id && replay.sequence > 0}
      />
      <div className="route-geography__legend" aria-label="Route geography details">
        <span>
          <i className="route-geography__marker route-geography__marker--pickup" />
          Pickup
        </span>
        <span>
          <i className="route-geography__marker route-geography__marker--delivery" />
          Delivery
        </span>
        <span>{distance}</span>
      </div>
      <p className="route-geography__note">
        Bundled road reference, not live routing, traffic, or truck location.
      </p>
    </section>
  );
}
