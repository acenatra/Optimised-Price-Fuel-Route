import numpy as np
from django.test import SimpleTestCase

from planner.services.geo import cumulative_miles, decimate, densify, haversine_miles
from planner.services.names import gazetteer_aliases, normalise, split_city_state
from planner.services.routing import decode_polyline


class NameTests(SimpleTestCase):
    def test_normalise_equivalences(self):
        self.assertEqual(normalise("Mc Calla"), normalise("McCalla"))
        self.assertEqual(normalise("Saint Johns"), normalise("St. Johns"))
        self.assertEqual(normalise("Fort Worth"), normalise("Ft Worth"))
        self.assertEqual(normalise("Cañon City"), normalise("Canon City"))

    def test_gazetteer_aliases(self):
        self.assertEqual(gazetteer_aliases("Baker City city")[0], "Baker City")
        self.assertIn("Athens", gazetteer_aliases("Athens-Clarke County unified government (balance)"))
        self.assertEqual(gazetteer_aliases("Bensalem township"), ["Bensalem"])

    def test_split_city_state(self):
        self.assertEqual(split_city_state("Austin, TX"), ("Austin", "TX"))
        self.assertEqual(split_city_state("Salt Lake City UT"), ("Salt Lake City", "UT"))
        self.assertEqual(split_city_state("Kansas City, Missouri, USA"), ("Kansas City", "MO"))
        self.assertEqual(split_city_state("Santa Fe New Mexico"), ("Santa Fe", "NM"))
        self.assertIsNone(split_city_state("Toronto, ON"))
        self.assertIsNone(split_city_state("somewhere"))


class GeoTests(SimpleTestCase):
    def test_haversine(self):
        # NYC -> LA great-circle distance is ~2,445 miles.
        self.assertAlmostEqual(float(haversine_miles(40.7128, -74.0060, 34.0522, -118.2437)), 2445, delta=5)

    def test_densify_preserves_length_and_limits_spacing(self):
        lats, lons = np.array([35.0, 35.0, 36.0]), np.array([-100.0, -99.0, -99.0])
        d_lat, d_lon, cum = densify(lats, lons, 0.25)
        self.assertAlmostEqual(cum[-1], cumulative_miles(lats, lons)[-1], places=6)
        self.assertLessEqual(np.diff(cumulative_miles(d_lat, d_lon)).max(), 0.2501)
        self.assertEqual((d_lat[-1], d_lon[-1]), (36.0, -99.0))

    def test_decimate_keeps_endpoints(self):
        lats, lons, cum = densify(np.array([35.0, 36.0]), np.array([-100.0, -100.0]), 0.1)
        o_lat, o_lon = decimate(lats, lons, cum, 5.0)
        self.assertLess(len(o_lat), len(lats))
        self.assertEqual((o_lat[0], o_lat[-1]), (lats[0], lats[-1]))

    def test_decode_polyline_reference_example(self):
        # Example from Google's polyline algorithm documentation (precision 5).
        lats, lons = decode_polyline("_p~iF~ps|U_ulLnnqC_mqNvxq`@", precision=5)
        np.testing.assert_allclose(lats, [38.5, 40.7, 43.252])
        np.testing.assert_allclose(lons, [-120.2, -120.95, -126.453])
