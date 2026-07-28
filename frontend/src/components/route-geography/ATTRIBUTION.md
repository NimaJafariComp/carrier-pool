# Route geography attribution

The decision route panel is entirely bundled and makes no runtime map, tile, geocoding,
or routing request.

- **World boundaries:** Natural Earth country boundaries, packaged by
  [`world-atlas`](https://github.com/topojson/world-atlas) as `countries-110m.json`.
  Natural Earth data are public domain.
- **Texas boundary:** U.S. Census Bureau TIGERweb `State_County` service, Texas
  state feature `GEOID=48`, queried on 2026-07-28 and generalized at `0.075` degrees
  before bundling. U.S. federal government geographic data are public domain.
- **Route line:** simplified road geometry generated from OpenStreetMap data by the
  public OSRM demo router on 2026-07-28, then bundled for the three Day 11 routes.
  It is display geometry only, not a live route, distance calculation, traffic feed,
  or live truck location.
