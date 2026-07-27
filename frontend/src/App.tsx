export function App() {
  return (
    <main className="app-shell">
      <section aria-labelledby="app-title" className="app-shell__content">
        <p className="app-shell__eyebrow">Freight decision support</p>
        <h1 id="app-title">Carrier Pool</h1>
        <p className="app-shell__status" role="status">
          Backend health: not checked
        </p>
      </section>
    </main>
  );
}
