# Optimised Fuel Pricing Route API

A Django 6.1 API that takes a start and finish location in the USA and returns the driving
route, the cheapest places to refuel along it (500-mile range, 10 MPG), the total fuel cost,
and an interactive map.

- **1 external routing call per request** (OSRM), **0 when cached**.
- **0 geocoding calls** for `"City, ST"` or `"lat,lon"` inputs (resolved from a local
  US Census gazetteer); a free-text address falls back to 1 cached Nominatim call.
- **~10–25 ms** of our own compute after the route arrives; a cold coast-to-coast request
  takes ~1.5 s, almost all of which is the public OSRM server. Repeat requests: ~5 ms.

## Quick start

```bash
python3.13 -m venv .venv && source .venv/bin/activate   # Django 6.1 needs Python >= 3.12
pip install -r requirements.txt
python manage.py migrate
python manage.py load_stations        # ~1 s; downloads the Census Gazetteer + population file on first run
python manage.py runserver
```

Open <http://127.0.0.1:8000/> for the map UI, or call the API:

```bash
curl "http://127.0.0.1:8000/api/v1/route/?start=New%20York,%20NY&finish=Los%20Angeles,%20CA"

curl -X POST http://127.0.0.1:8000/api/v1/route/ \
     -H 'Content-Type: application/json' \
     -d '{"start": "Chicago, IL", "finish": "39.7392,-104.9903", "start_full": false}'
```

Run tests: `python manage.py test planner`

## API

`GET /api/v1/route/` (or `POST` with a JSON body)

| Param | Default | Meaning |
|---|---|---|
| `start`, `finish` | required | `"City, ST"`, `"City, State"`, `"lat,lon"`, or any US address |
| `start_full` | `false` | `false`: depart with an empty tank and fill up near the origin. `true`: depart full; only en-route purchases are costed |
| `stop_penalty` | `5` | $ a stop must save to be worth making (see below). `0` = pure cheapest fuel |
| `geometry` | `true` | include the route GeoJSON LineString |

Response (abridged):

```json
{
  "start":  {"name": "New York, NY", "lat": 40.66, "lon": -73.94, "source": "gazetteer"},
  "finish": {"name": "Los Angeles, CA", "...": "..."},
  "route":  {"distance_miles": 2810.4, "duration_hours": 50.3, "geometry": {"type": "LineString", "coordinates": [[-73.9, 40.6], "..."]}},
  "fuel_stops": [
    {"order": 1, "name": "BOLLA MARKET", "city": "Elizabeth", "state": "NJ", "lat": 40.66, "lon": -74.19,
     "price_per_gallon": 3.099, "route_mile": 0.0, "off_route_miles": 13.4, "is_origin_fillup": true,
     "gallons": 39.6, "cost": 122.72, "fuel_on_arrival_gallons": 0.0, "fuel_on_departure_gallons": 39.6}
  ],
  "summary": {"number_of_stops": 7, "total_fuel_cost": 860.37, "total_gallons_purchased": 281.04,
              "average_price_paid": 3.0607, "corridor_miles": 10.0},
  "map_url": "http://127.0.0.1:8000/map/?start=...&finish=...",
  "meta": {"cached": false, "routing_api_calls": 1, "optimisation_ms": 12.5, "total_ms": 1420.0}
}
```

Errors: `400` invalid input / outside the USA, `422` no route or no feasible fuel plan,
`502` upstream routing/geocoding failure.

`GET /api/v1/suggest/?q=chi` powers the map UI's location dropdown: an indexed prefix search over
the local gazetteer, ranked by Census population (Chicago before Chico), in ~2-7 ms with no external
calls. `"Austin, T"` narrows to states starting with T, and `"Austin TX"` works too. Elasticsearch isn't
needed for ~48k place names; a SQLite index is faster and adds no infrastructure.

`GET /map/?start=...&finish=...` renders the same plan on a Leaflet map (reuses the cached
plan, so no extra routing call). `GET /api/v1/health/` reports loaded stations.

## How it works

1. **Offline station geocoding** (`load_stations`). The CSV has no coordinates, and
   geocoding ~6.6k stations per request is out of the question. Each station is placed at
   its city's centroid from the US Census Gazetteer (places + county subdivisions, with
   name normalisation for `Mc Calla`/`McCalla`, `Saint`/`St.`, accents, consolidated cities).
   That covers ~98%; the remaining towns were resolved once via Nominatim and committed in
   `data/geocode_cache.json` (`--geocode-missing` refreshes it). Canadian rows are dropped;
   duplicate station IDs keep their lowest price. Result: all 6,626 US stations.
2. **Routing.** One OSRM call with `overview=full&geometries=polyline6` (≈5x smaller than
   GeoJSON). Routes are cached.
3. **Corridor matching.** Stations live in an in-memory numpy/KD-tree index (loaded once per
   process at startup). The route is densified to 0.25-mile spacing, and one bounded KD-tree
   query finds every station within 10 miles of the route plus its position along it. If no
   feasible plan exists, the corridor widens to 25 then 50 miles without calling OSRM again.
4. **Optimisation.** A dynamic programme over (station, fuel in tank) at 1-mile resolution,
   vectorised with numpy (~6 ms for 500 stations). It minimises
   `fuel cost + stop_penalty × stops`. With a penalty of 0 it matches the provably optimal
   greedy for the gas-station problem (checked in the tests), but that optimum happily
   makes 1-gallon stops to save a few cents: NY→LA takes 19 stops. With the default $5
   penalty it takes 7 stops and costs $7.69 more (+0.9%). The reported cost is always the
   real fuel spend, with no penalty included.

## Assumptions & trade-offs

- **Start with an empty tank** (default): the first stop is the cheapest station within 15
  miles of the origin (the nearest one if none is that close). The trip ends with an empty
  tank, so total fuel purchased = trip distance / 10 MPG.
- Detours to off-route stations (≤ 10 miles, reported as `off_route_miles`) are not added
  to distance or fuel.
- Station coordinates are city centroids, accurate to a few miles, which the 10-mile corridor
  absorbs.
- The public OSRM and OSM tile servers are demo services. For production, point
  `OSRM_BASE_URL` at a self-hosted OSRM and use Redis instead of the local-memory cache
  (see `.env.example`).

## Layout

```
config/                     Django settings/urls (+ startup warm-up in wsgi/asgi)
planner/
  models.py                 Place (gazetteer), FuelStation
  services/
    suggest.py              autocomplete (prefix search, population-ranked)
    geocoding.py            lat,lon | local gazetteer | Nominatim fallback
    routing.py              OSRM client + polyline6 decoder
    station_index.py        in-memory KD-tree corridor search
    optimizer.py            DP optimiser (+ greedy oracle)
    planner.py              orchestration, caching, response shaping
  management/commands/load_stations.py
  templates/planner/map.html   Leaflet map UI
  tests/                    optimiser, helpers, API (OSRM mocked)
data/                       fuel CSV, geocode cache (gazetteer auto-downloaded)
```
