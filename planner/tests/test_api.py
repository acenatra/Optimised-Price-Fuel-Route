from unittest import mock

import numpy as np
from django.core.cache import cache
from django.test import TestCase

from planner.models import FuelStation, Place
from planner.services.http import ExternalServiceError
from planner.services.names import normalise
from planner.services.station_index import StationIndex


def encode_polyline(coords, precision=6):
    out, prev = [], (0, 0)
    for lat, lon in coords:
        cur = (round(lat * 10**precision), round(lon * 10**precision))
        for delta in (cur[0] - prev[0], cur[1] - prev[1]):
            v = ~(delta << 1) if delta < 0 else delta << 1
            while v >= 0x20:
                out.append(chr((0x20 | (v & 0x1F)) + 63))
                v >>= 5
            out.append(chr(v + 63))
        prev = cur
    return "".join(out)


# A straight east-west "road" along latitude 35 from lon -100 to -88 (~680 miles).
ROUTE_COORDS = [(35.0, lon) for lon in np.linspace(-100, -88, 40)]
OSRM_OK = {
    "code": "Ok",
    "routes": [{"distance": 680 * 1609.344, "duration": 36000, "geometry": encode_polyline(ROUTE_COORDS)}],
}


class RouteApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Place.objects.create(state="OK", key=normalise("Alpha"), name="Alpha", lat=35.0, lon=-100.0)
        Place.objects.create(state="TN", key=normalise("Omega"), name="Omega", lat=35.0, lon=-88.0)
        stations = [  # (lon, price, off-route latitude offset)
            (-100.0, 3.50, 0.0),
            (-97.0, 2.90, 0.05),  # ~3.5 miles off route
            (-94.0, 3.40, 0.0),
            (-91.0, 3.10, 0.0),
            (-95.0, 1.00, 1.0),  # super cheap but ~69 miles off route -> must be ignored
        ]
        for n, (lon, price, dlat) in enumerate(stations):
            FuelStation.objects.create(
                opis_id=n + 1, name=f"Station {n + 1}", address="I-40", city="X", state="OK",
                price=price, lat=35.0 + dlat, lon=lon,
            )

    def setUp(self):
        cache.clear()
        StationIndex.invalidate()
        patcher = mock.patch("planner.services.routing.get_json", return_value=(200, OSRM_OK))
        self.osrm = patcher.start()
        self.addCleanup(patcher.stop)

    def get(self, **params):
        return self.client.get("/api/v1/route/", {"start": "Alpha, OK", "finish": "Omega, TN", **params})

    def test_plan_shape_and_single_routing_call(self):
        resp = self.get()
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()
        self.assertEqual(self.osrm.call_count, 1)
        self.assertEqual(data["start"]["source"], "gazetteer")
        self.assertEqual(data["route"]["geometry"]["type"], "LineString")
        self.assertIn("map_url", data)

        summary = data["summary"]
        self.assertAlmostEqual(summary["total_gallons_purchased"], 68.0, delta=0.2)
        self.assertAlmostEqual(
            summary["total_fuel_cost"], sum(s["cost"] for s in data["fuel_stops"]), delta=0.05
        )
        names = [s["name"] for s in data["fuel_stops"]]
        self.assertNotIn("Station 5", names)  # outside the corridor
        self.assertIn("Station 2", names)  # the cheap on-route station is used
        self.assertTrue(data["fuel_stops"][0]["is_origin_fillup"])
        for stop in data["fuel_stops"]:
            self.assertLessEqual(stop["fuel_on_departure_gallons"], 50.0)

    def test_repeat_request_is_served_from_cache(self):
        self.get()
        resp = self.get()
        self.assertEqual(self.osrm.call_count, 1)
        self.assertTrue(resp.json()["meta"]["cached"])

    def test_start_full_skips_origin_fillup(self):
        data = self.get(start_full="true").json()
        self.assertTrue(all(not s["is_origin_fillup"] for s in data["fuel_stops"]))
        self.assertAlmostEqual(data["summary"]["total_gallons_purchased"], 18.0, delta=0.2)

    def test_post_json(self):
        resp = self.client.post(
            "/api/v1/route/", {"start": "35.0,-100.0", "finish": "Omega, TN", "geometry": False},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertIsNone(resp.json()["route"]["geometry"])

    def test_validation_errors(self):
        self.assertEqual(self.client.get("/api/v1/route/", {"start": "Alpha, OK"}).status_code, 400)
        self.assertEqual(self.get(start="51.5,-0.12").status_code, 400)  # London
        self.assertEqual(self.get(stop_penalty="abc").status_code, 400)

    def test_upstream_failure_returns_502(self):
        self.osrm.side_effect = ExternalServiceError("boom")
        self.assertEqual(self.get().status_code, 502)

    def test_no_route_returns_422(self):
        self.osrm.return_value = (400, {"code": "NoRoute"})
        self.assertEqual(self.get().status_code, 422)

    def test_map_page_renders(self):
        resp = self.client.get("/map/", {"start": "Alpha, OK", "finish": "Omega, TN"})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "plan-data")
        self.assertContains(resp, "Station 2")

    def test_health(self):
        self.assertEqual(self.client.get("/api/v1/health/").json()["stations_loaded"], 5)


class SuggestApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        for state, name, weight in [("IL", "Chicago", 2_700_000), ("CA", "Chico", 100_000),
                                    ("TX", "Austin", 960_000), ("MN", "Austin", 25_000)]:
            Place.objects.create(state=state, key=normalise(name), name=name, lat=1, lon=1, weight=weight)

    def setUp(self):
        cache.clear()

    def labels(self, q):
        return [r["label"] for r in self.client.get("/api/v1/suggest/", {"q": q}).json()["results"]]

    def test_prefix_ranked_by_population(self):
        self.assertEqual(self.labels("chi"), ["Chicago, IL", "Chico, CA"])
        self.assertEqual(self.labels("austin"), ["Austin, TX", "Austin, MN"])

    def test_state_narrowing(self):
        self.assertEqual(self.labels("Austin, M"), ["Austin, MN"])
        self.assertEqual(self.labels("Austin Texas"), ["Austin, TX"])

    def test_too_short_or_unknown(self):
        self.assertEqual(self.labels("c"), [])
        self.assertEqual(self.labels("zzzz"), [])

    def test_state_code_lookalikes_do_not_hijack_the_name(self):
        Place.objects.create(state="LA", key=normalise("New Orleans"), name="New Orleans", lat=1, lon=1, weight=370_000)
        Place.objects.create(state="OR", key=normalise("Newberg"), name="Newberg", lat=1, lon=1, weight=25_000)
        self.assertEqual(self.labels("new or"), ["New Orleans, LA"])
