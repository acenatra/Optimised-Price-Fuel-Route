# Loom video script (5 minutes)

What to say, what to show, and what to run, in order. Times are targets.
Line numbers are approximate; search for the function name if they have moved.

## Before you press record (2 minutes of prep)

```bash
cd ~/Desktop/OptimisedFuelPricingRoute
source .venv/bin/activate
python manage.py migrate
python manage.py load_stations          # makes sure the data is loaded
python manage.py runserver              # leave running in a terminal
```

1. Warm up once so the first demo is not the slow one:
   `curl "http://127.0.0.1:8000/api/v1/route/?start=Austin,%20TX&finish=Dallas,%20TX&geometry=false"`
2. Postman: create a collection called "Fuel Route Planner" and paste in the requests from section "Requests to hit" below (Import > Raw text).
3. Open these tabs in your editor, in this order:
   `README.md`, `data/fuel-prices-for-be-assessment.csv`, `planner/management/commands/load_stations.py`,
   `planner/views.py`, `planner/services/planner.py`, `planner/services/routing.py`,
   `planner/services/station_index.py`, `planner/services/optimizer.py`, `planner/templates/planner/map.html`.
4. Open `docs/architecture.html` in a browser tab and `http://127.0.0.1:8000/` in another.
5. Close notifications. Zoom the editor font up so it is readable on video.

---

## 0:00 to 0:30. Introduction

**Show:** the browser tab with `docs/architecture.html`.

**Say:**
"Hi, this is my submission for the fuel route planner. It is a Django application. You give it a start and a finish
location in the USA, and it returns the driving route, the cheapest places to buy fuel along the way for a vehicle with a
500-mile range at 10 miles per gallon, and the total money spent on fuel. It also shows the result on a map.
This diagram shows the whole system: a request comes into the Django views, the trip planner coordinates everything, and
the only outside call in a normal request is a single call to a free routing service. I will show the API working first,
then walk through the code."

---

## 0:30 to 2:15. Demo the API in Postman

**Show:** Postman.

### Request 1: the headline demo (about 40 seconds)
```
GET http://127.0.0.1:8000/api/v1/route/?start=New York, NY&finish=Los Angeles, CA&geometry=false
```
**Say:** "New York to Los Angeles. In the response, `route.distance_miles` is about 2,810 miles. `fuel_stops` is the list
of where to fill up, in driving order. Each stop has the station name, its price per gallon, how many gallons to buy and
what that costs. `summary.total_fuel_cost` is the total, about 860 dollars. Look at `meta.routing_api_calls`: it is 1.
That is the requirement of calling the map service as little as possible."

Point at: `fuel_stops[0]`, `summary.total_fuel_cost`, `meta.routing_api_calls`, `meta.total_ms`.

### Request 2: the same request again (about 15 seconds)
Send the exact same request a second time.
**Say:** "Same request again. Now `meta.cached` is true, `routing_api_calls` is 0, and it comes back in a few
milliseconds, because the answer is cached."

### Request 3: pure cheapest versus practical (about 30 seconds)
```
GET http://127.0.0.1:8000/api/v1/route/?start=New York, NY&finish=Los Angeles, CA&geometry=false&stop_penalty=0
```
**Say:** "By default, a stop has to save at least 5 dollars to be worth making. That is the `stop_penalty`. If I set it
to zero, the optimiser finds the mathematically cheapest plan, but it makes tiny one-gallon stops to save a few cents:
19 stops instead of 7, for about 7 dollars less. I chose the practical default, and it is a parameter."

### Request 4: POST with a JSON body (about 15 seconds)
```
POST http://127.0.0.1:8000/api/v1/route/
Body (raw JSON): {"start": "Seattle, WA", "finish": "Miami, FL", "start_full": true, "geometry": false}
```
**Say:** "It also accepts POST. With `start_full` true the vehicle leaves with a full tank, so only fuel bought along the
way is counted."

### Request 5: errors (about 15 seconds)
```
GET http://127.0.0.1:8000/api/v1/route/?start=51.5,-0.12&finish=Denver, CO
GET http://127.0.0.1:8000/api/v1/route/?start=Chicago, IL
```
**Say:** "Bad input gets a clear error. A location outside the USA, like this London coordinate, is rejected with status
400, and so is a missing parameter."

---

## 2:15 to 3:00. The data: loading the CSV

**Show:** `data/fuel-prices-for-be-assessment.csv` (scroll the top rows), then
`planner/management/commands/load_stations.py`, function `handle` (about line 51).

**Say:**
"The fuel price file has names, cities and prices, but no latitude and longitude. To match stations to a route we need
coordinates, and looking up thousands of addresses on every request would be far too slow. So a one-time command,
`load_stations`, does that work up front.
It reads the CSV, drops the Canadian rows, and keeps the lowest price for each station. Then it gives every station
coordinates using the US Census list of about 48,000 places, which I download once and store in a table called Place.
City names are spelled differently across files, like 'Mc Calla' versus 'McCalla' or 'Saint' versus 'St.', so a helper
in `names.py` called `normalise` converts both to the same simple form before matching. About 98 percent match directly,
and the remaining 130 or so small towns are in `data/geocode_cache.json`, which I looked up once. The result is 6,626
stations, all with coordinates, saved in the database."

Scroll to: `_load_places` (about line 98), `_read_csv` (about line 173).

---

## 3:00 to 4:15. The code flow for one request

**Show:** `planner/views.py`, then `planner/services/planner.py`, function `plan_trip` (line 32), and follow it down.

**Say (view):** "A request comes into `RouteView` in `views.py`. It reads the start and finish, checks them, and calls
`plan_trip`."

**Say and scroll (planner.py):** "`plan_trip` is the heart of the app, and it reads top to bottom.
- **Line 42 and 43:** it turns the two inputs into coordinates using `geocoding.resolve`. 'City, State' is looked up in
  the local Place table, so no internet is needed.
- **Line 57:** `get_route`, the one call to the routing service. Let me show it."

**Show:** `planner/services/routing.py`, function `get_route` (line 51).
"This is the single call to the free routing service, OSRM, which stands for Open Source Routing Machine. It returns the
distance, the driving time and the road path. The path arrives as compressed text, and `decode_polyline` unpacks it into
coordinates. The result is cached, so the same trip never calls it twice."

**Back in planner.py, line 70:** "Next, `along_route` in `station_index.py` finds every station within 10 miles of that
road. At server start, all stations are loaded into memory in a data structure called a KD-tree, which finds nearby points
in milliseconds. So no database work happens per request."

**Show:** `planner/services/optimizer.py`, function `optimise_dp` (line 149).
"Line 80 calls the optimiser. This is the part that decides where to fill up. The tank holds 500 miles, which is 50
gallons. The idea is: buy more fuel where it is cheap, and only enough to reach the next cheap station where it is
expensive. It uses dynamic programming: for every station and every possible amount of fuel in the tank, it works out the
cheapest way to get there. It takes about 10 milliseconds. The cost of each stop is gallons times price, and the total
is the sum of the stops."

---

## 4:15 to 4:45. The frontend and the map

**Show:** the browser at `http://127.0.0.1:8000/`.

**Do:** click in the Start box and type `chi`. The dropdown appears. Use the down arrow and Enter to choose Chicago, IL.
In Finish, type `den` and choose Denver, CO. Click Plan route.

**Say:**
"This is the map page. As I type, the browser asks `/api/v1/suggest/` for matching places. It searches the same Place
table, ranked by population, so Chicago comes before smaller towns. When I plan the route, the server runs the same
`plan_trip`, and the page draws it with Leaflet. The map tiles come from OpenStreetMap. The blue line is our route: the
server sends its coordinates in the page, and Leaflet joins them up. The numbered circles are the fuel stops, and the
panel on the left shows the total cost and each stop."

**Show in `map.html` (optional, 10 seconds):** the `L.geoJSON` line and the `attachAutocomplete` function.

---

## 4:45 to 5:00. Tests and wrap-up

**Show:** the terminal. Run `python manage.py test planner`.

**Say:**
"There are 29 automated tests, covering the optimiser, the name matching, the API and the autocomplete, and they pass.
To summarise: one call to the routing service per trip, none when cached, stations located in advance, the cheapest
plan found in about 10 milliseconds, and the result available as JSON or on a map. Thanks for watching."

---

## Requests to hit (copy into Postman: Import > Raw text)

Server address is `http://127.0.0.1:8000`. In Postman, spaces in query values are fine; they are encoded for you.

```bash
# 1. Headline demo
curl "http://127.0.0.1:8000/api/v1/route/?start=New%20York,%20NY&finish=Los%20Angeles,%20CA&geometry=false"

# 2. Same again (shows cached: true)
curl "http://127.0.0.1:8000/api/v1/route/?start=New%20York,%20NY&finish=Los%20Angeles,%20CA&geometry=false"

# 3. Pure cheapest (more, smaller stops)
curl "http://127.0.0.1:8000/api/v1/route/?start=New%20York,%20NY&finish=Los%20Angeles,%20CA&geometry=false&stop_penalty=0"

# 4. POST with a full tank at the start
curl -X POST http://127.0.0.1:8000/api/v1/route/ \
  -H "Content-Type: application/json" \
  -d '{"start": "Seattle, WA", "finish": "Miami, FL", "start_full": true, "geometry": false}'

# 5. Errors
curl -i "http://127.0.0.1:8000/api/v1/route/?start=51.5,-0.12&finish=Denver,%20CO"
curl -i "http://127.0.0.1:8000/api/v1/route/?start=Chicago,%20IL"

# Extras if you have time
curl "http://127.0.0.1:8000/api/v1/suggest/?q=chi"
curl "http://127.0.0.1:8000/api/v1/health/"
curl "http://127.0.0.1:8000/api/v1/route/?start=30.2672,-97.7431&finish=Dallas,%20TX&geometry=false"
```

## Fields to point at in the response

| Field | Meaning |
|---|---|
| `route.distance_miles`, `route.duration_hours` | Length and driving time |
| `fuel_stops[].name / city / state` | Which station |
| `fuel_stops[].price_per_gallon` | Its price |
| `fuel_stops[].gallons`, `cost` | How much to buy and what it costs |
| `fuel_stops[].route_mile` | How far along the trip it is |
| `fuel_stops[].is_origin_fillup` | True for the first fill-up before departure |
| `summary.total_fuel_cost` | The answer to the assignment: total money spent on fuel |
| `meta.routing_api_calls` | 1 for a new trip, 0 when cached |
| `meta.cached`, `meta.total_ms` | Cache hit and time taken |
| `map_url` | Open in a browser to see the map |

## Questions you might be asked

- **Why only one call to the map service?** Station coordinates are prepared in advance, the route is one call, and
  everything else (matching, optimising) runs locally in memory.
- **Why a stop penalty?** Without it the cheapest plan makes one-gallon stops to save cents. A stop must save 5 dollars
  by default; it is a request parameter, and the reported cost never includes the penalty.
- **How accurate are station locations?** They use the town's centre, so they are within a few miles. The 10-mile search
  strip around the route absorbs that.
- **What if there is no station along a stretch?** The search widens to 25, then 50 miles without a new routing call.
  If still impossible, the API returns a 422 with an explanation.
- **What assumptions were made?** The vehicle starts with an empty tank (unless `start_full` is true), 500-mile range,
  10 miles per gallon, and the small detour to reach a station off the road is not counted.
