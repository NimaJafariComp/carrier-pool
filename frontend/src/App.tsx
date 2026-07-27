import { useEffect, useState } from "react";

import type { components } from "./api/generated";

type Load = components["schemas"]["LoadResponse"];

const loadId =
  new URLSearchParams(window.location.search).get("loadId") ?? import.meta.env.VITE_DEMO_LOAD_ID;

export function App() {
  const [state, setState] = useState<{ load?: Load; error?: string; loading: boolean }>({
    loading: true,
  });
  useEffect(() => {
    if (!loadId) {
      setState({ loading: false, error: "No demo load selected." });
      return;
    }
    void fetch(`/api/v1/loads/${loadId}`, {
      headers: { "X-Tenant-ID": import.meta.env.VITE_DEMO_TENANT_ID ?? "" },
    })
      .then(async (response) =>
        response.ok
          ? (response.json() as Promise<Load>)
          : Promise.reject(
              response.status === 404 ? "Load not found." : "Unable to load shipment.",
            ),
      )
      .then((load) => setState({ load, loading: false }))
      .catch((error: unknown) => setState({ loading: false, error: String(error) }));
  }, []);
  return (
    <main className="app-shell">
      <section aria-labelledby="app-title" className="app-shell__content">
        <p className="app-shell__eyebrow">Freight decision support</p>
        <h1 id="app-title">Carrier Pool</h1>
        {state.loading && <p role="status">Loading load…</p>}
        {state.error && <p role="alert">{state.error}</p>}
        {state.load && (
          <article aria-label="Load details">
            <h2>{state.load.id}</h2>
            <p>Reference: {state.load.external_id}</p>
            <p>Status: {state.load.status}</p>
            <p>Equipment: {state.load.equipment ?? "Unknown"}</p>
            <p>Distance: {state.load.distance_miles ?? "Not known"}</p>
            <ol>
              {state.load.stops.map((stop) => (
                <li key={stop.sequence}>
                  {stop.sequence}. {stop.city}, {stop.state} {stop.postal_code} —{" "}
                  {stop.is_pickup ? "Pickup" : ""}
                  {stop.is_pickup && stop.is_dropoff ? "/" : ""}
                  {stop.is_dropoff ? "Drop-off" : ""}
                </li>
              ))}
            </ol>
          </article>
        )}
      </section>
    </main>
  );
}
