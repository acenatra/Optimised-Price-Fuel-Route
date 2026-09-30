"""Load fuel prices + geocode every station ONCE, offline, so requests never geocode stations.

    python manage.py load_stations                      # gazetteer + committed geocode cache
    python manage.py load_stations --geocode-missing    # also query Nominatim (1 req/s) for leftovers

Steps
  1. Download the US Census Gazetteer (places + county subdivisions) if not present.
  2. Load it into the Place table (also used to resolve "City, ST" API inputs locally).
  3. Read the CSV, keep US stations, collapse duplicate OPIS IDs to their lowest price.
  4. Geocode each station to its city centroid; unmatched cities fall back to
     data/geocode_cache.json (and optionally Nominatim, whose answers are appended to it).
"""

import csv
import io
import json
import time
import zipfile
from collections import defaultdict
from decimal import Decimal, InvalidOperation

import requests
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from planner.models import FuelStation, Place
from planner.services.geocoding import in_usa_bounds
from planner.services.names import US_STATES, gazetteer_aliases, normalise
from planner.services.station_index import StationIndex

GAZETTEER_BASE = "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2024_Gazetteer/"
# (file, name column, lat column, lon column) — places first: they win on key collisions.
GAZETTEER_FILES = [
    ("2024_Gaz_place_national.txt", 3, 10, 11),
    ("2024_Gaz_cousubs_national.txt", 3, 9, 10),
]
POPULATION_URL = ("https://www2.census.gov/programs-surveys/popest/datasets/2020-2024/cities/totals/"
                  "sub-est2024.csv")
POPULATION_FILE = "sub-est2024.csv"


class Command(BaseCommand):
    help = "Load fuel stations from the CSV and geocode them (offline, via US Census Gazetteer)."

    def add_arguments(self, parser):
        parser.add_argument("--csv", default=str(settings.DATA_DIR / "fuel-prices-for-be-assessment.csv"))
        parser.add_argument("--geocode-missing", action="store_true",
                            help="Query Nominatim (1 req/s) for cities not in the gazetteer or cache.")

    def handle(self, *args, **opts):
        t0 = time.perf_counter()
        lookup = self._load_places()
        stations = self._read_csv(opts["csv"])
        cache_path = settings.DATA_DIR / "geocode_cache.json"
        geocache = json.loads(cache_path.read_text()) if cache_path.exists() else {}

        unmatched = set()
        for st in stations.values():
            coords = lookup.get((st["state"], normalise(st["city"])))
            source = "gazetteer"
            if coords is None:
                cached = geocache.get(f"{st['city']}|{st['state']}")
                coords, source = (tuple(cached), "nominatim") if cached else (None, None)
            if coords is None:
                unmatched.add((st["city"], st["state"]))
            st["coords"], st["source"] = coords, source

        if unmatched and opts["geocode_missing"]:
            self._geocode_with_nominatim(unmatched, geocache, cache_path)
            for st in stations.values():
                if st["coords"] is None and (hit := geocache.get(f"{st['city']}|{st['state']}")):
                    st["coords"], st["source"] = tuple(hit), "nominatim"

        rows = [
            FuelStation(
                opis_id=oid, name=st["name"], address=st["address"], city=st["city"], state=st["state"],
                rack_id=st["rack_id"], price=st["price"], lat=st["coords"][0], lon=st["coords"][1],
                geocode_source=st["source"],
            )
            for oid, st in stations.items() if st["coords"]
        ]
        with transaction.atomic():
            FuelStation.objects.all().delete()
            FuelStation.objects.bulk_create(rows, batch_size=2000)
        StationIndex.invalidate()

        skipped = len(stations) - len(rows)
        self.stdout.write(self.style.SUCCESS(
            f"Loaded {len(rows)} stations ({skipped} not geocoded) in {time.perf_counter() - t0:.1f}s."
        ))
        if skipped:
            missing = sorted({(s["city"], s["state"]) for s in stations.values() if not s["coords"]})
            self.stdout.write(f"Not geocoded ({len(missing)} towns; use --geocode-missing): "
                              + ", ".join(f"{c}, {s}" for c, s in missing[:25]))

    # ------------------------------------------------------------------ gazetteer
    def _load_places(self):
        lookup, names = {}, {}
        pops = self._load_population()
        aliases = {}  # lower-priority keys (e.g. "Boise" for "Boise City")
        for fname, name_col, lat_col, lon_col in GAZETTEER_FILES:
            path = settings.DATA_DIR / fname
            if not path.exists():
                self._download(fname, path)
            with path.open(encoding="utf-8") as fh:
                reader = csv.reader(fh, delimiter="\t")
                next(reader)
                for row in reader:
                    state = row[0].strip()
                    if state not in US_STATES:
                        continue
                    lat, lon = float(row[lat_col]), float(row[lon_col].strip())
                    primary, *extra = gazetteer_aliases(row[name_col])
                    key = (state, normalise(primary))
                    if key[1] and key not in lookup:
                        lookup[key], names[key] = (lat, lon), primary
                    for alias in extra:
                        akey = (state, normalise(alias))
                        if akey[1]:
                            aliases.setdefault(akey, ((lat, lon), alias))
        for akey, (coords, alias) in aliases.items():
            if akey not in lookup:
                lookup[akey], names[akey] = coords, alias

        with transaction.atomic():
            Place.objects.all().delete()
            Place.objects.bulk_create(
                (Place(state=s, key=k, name=names[(s, k)], lat=c[0], lon=c[1], weight=pops.get((s, k), 0.0))
                 for (s, k), c in lookup.items()),
                batch_size=5000,
            )
        self.stdout.write(f"Gazetteer: {len(lookup)} US places loaded.")
        return lookup

    def _load_population(self):
        """{(state, key): population} for autocomplete ranking. Best-effort: ranking only."""
        path = settings.DATA_DIR / POPULATION_FILE
        try:
            if not path.exists():
                self.stdout.write(f"Downloading {POPULATION_URL} ...")
                resp = requests.get(POPULATION_URL, timeout=120)
                resp.raise_for_status()
                path.write_bytes(resp.content)
        except requests.RequestException as exc:
            self.stdout.write(self.style.WARNING(f"Population data unavailable ({exc}); ranking by name."))
            return {}
        state_codes = {name: code for code, name in US_STATES.items()}
        pops = {}
        with path.open(encoding="latin-1", newline="") as fh:
            for row in csv.DictReader(fh):
                if row["SUMLEV"] not in {"162", "170", "061"}:  # places, consolidated cities, towns
                    continue
                state = state_codes.get(row["STNAME"])
                if not state:
                    continue
                pop = float(row["POPESTIMATE2024"] or 0)
                for alias in gazetteer_aliases(row["NAME"]):
                    key = (state, normalise(alias))
                    if pop > pops.get(key, 0):
                        pops[key] = pop
        return pops

    def _download(self, fname, path):
        url = GAZETTEER_BASE + fname.replace(".txt", ".zip")
        self.stdout.write(f"Downloading {url} ...")
        resp = requests.get(url, timeout=120)
        resp.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            path.write_bytes(zf.read(fname))

    # ------------------------------------------------------------------ CSV
    def _read_csv(self, csv_path):
        try:
            fh = open(csv_path, newline="", encoding="utf-8-sig")
        except OSError as exc:
            raise CommandError(f"Cannot open {csv_path}: {exc}") from exc
        stations, bad, non_us = {}, 0, 0
        with fh:
            for row in csv.DictReader(fh):
                state = row["State"].strip().upper()
                if state not in US_STATES:
                    non_us += 1 
                    continue
                try:
                    oid = int(row["OPIS Truckstop ID"])
                    price = Decimal(row["Retail Price"].strip()).quantize(Decimal("0.00001"))
                except (ValueError, InvalidOperation):
                    bad += 1
                    continue
                cur = stations.get(oid)
                if cur is None or price < cur["price"]:
                    stations[oid] = {
                        "name": row["Truckstop Name"].strip(), "address": row["Address"].strip(),
                        "city": row["City"].strip(), "state": state, "price": price,
                        "rack_id": int(row["Rack ID"]) if row["Rack ID"].strip().isdigit() else None,
                    }
        self.stdout.write(f"CSV: {len(stations)} unique US stations ({non_us} non-US rows, {bad} bad rows).")
        return stations

    # ------------------------------------------------------------------ Nominatim fallback
    def _geocode_with_nominatim(self, towns, geocache, cache_path):
        cfg = settings.FUEL_PLANNER
        session = requests.Session()
        session.headers["User-Agent"] = cfg["HTTP_USER_AGENT"]
        todo = sorted(t for t in towns if f"{t[0]}|{t[1]}" not in geocache)
        self.stdout.write(f"Nominatim: geocoding {len(todo)} towns (~{len(todo)}s, 1 req/s policy)...")
        found = defaultdict(int)
        for city, state in todo:
            try:
                resp = session.get(cfg["NOMINATIM_URL"], timeout=30, params={
                    "city": city, "state": US_STATES[state], "country": "USA", "format": "jsonv2", "limit": 1,
                })
                data = resp.json() if resp.ok else []
            except (requests.RequestException, ValueError):
                data = []
            if data:
                lat, lon = float(data[0]["lat"]), float(data[0]["lon"])
                if in_usa_bounds(lat, lon):
                    geocache[f"{city}|{state}"] = [lat, lon]
                    found["ok"] += 1
            time.sleep(1.05)
        cache_path.write_text(json.dumps(geocache, indent=0, sort_keys=True))
        self.stdout.write(f"Nominatim: resolved {found['ok']}/{len(todo)}; cache saved to {cache_path.name}.")
