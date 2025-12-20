"""
Tests for geo-filtering functions.
"""

import math

from django.test import TestCase
from django.utils import timezone
from datetime import timedelta

from events.models import Event, Source
from venues.models import Venue
from locations.services import (
    calculate_bounding_box,
    filter_by_distance,
    haversine_distance,
    DEFAULT_RADIUS_MILES,
)


class BoundingBoxTests(TestCase):
    """Test calculate_bounding_box function."""

    def test_bounding_box_calculation(self):
        """Test bounding box calculation."""
        # Newton, MA coordinates
        lat, lng = 42.337807, -71.209182
        radius = 10.0

        min_lat, max_lat, min_lng, max_lng = calculate_bounding_box(lat, lng, radius)

        # Check that bounding box is reasonable
        self.assertLess(min_lat, lat)
        self.assertGreater(max_lat, lat)
        self.assertLess(min_lng, lng)
        self.assertGreater(max_lng, lng)

        # Latitude delta should be ~radius/69 degrees
        expected_lat_delta = radius / 69.0
        actual_lat_delta = max_lat - lat
        self.assertAlmostEqual(actual_lat_delta, expected_lat_delta, places=4)

    def test_bounding_box_at_equator(self):
        """Test that bounding box is roughly square at equator."""
        lat, lng = 0.0, 0.0
        radius = 10.0

        min_lat, max_lat, min_lng, max_lng = calculate_bounding_box(lat, lng, radius)

        lat_delta = max_lat - min_lat
        lng_delta = max_lng - min_lng

        # At equator, lat and lng deltas should be nearly equal
        self.assertAlmostEqual(lat_delta, lng_delta, places=2)

    def test_bounding_box_at_high_latitude(self):
        """Test that longitude delta is larger at high latitudes."""
        lat, lng = 60.0, 0.0  # 60 degrees north
        radius = 10.0

        min_lat, max_lat, min_lng, max_lng = calculate_bounding_box(lat, lng, radius)

        lat_delta = max_lat - min_lat
        lng_delta = max_lng - min_lng

        # At 60 degrees, longitude delta should be ~2x latitude delta
        # cos(60) = 0.5, so lng_delta ≈ 2 * lat_delta
        self.assertGreater(lng_delta, lat_delta * 1.5)


class HaversineDistanceTests(TestCase):
    """Test haversine_distance function."""

    def test_same_point_is_zero(self):
        """Test distance from a point to itself is zero."""
        distance = haversine_distance(42.337807, -71.209182, 42.337807, -71.209182)
        self.assertEqual(distance, 0.0)

    def test_newton_to_cambridge(self):
        """Test known distance: Newton to Cambridge (~5 miles)."""
        # Newton, MA
        newton_lat, newton_lng = 42.337807, -71.209182
        # Cambridge, MA
        cambridge_lat, cambridge_lng = 42.373611, -71.110558

        distance = haversine_distance(newton_lat, newton_lng, cambridge_lat, cambridge_lng)

        # Should be approximately 5 miles (actual is ~5.5 miles)
        self.assertGreater(distance, 4.0)
        self.assertLess(distance, 7.0)

    def test_newton_to_springfield_ma(self):
        """Test longer distance: Newton to Springfield MA (~80 miles)."""
        # Newton, MA
        newton_lat, newton_lng = 42.337807, -71.209182
        # Springfield, MA
        springfield_lat, springfield_lng = 42.101483, -72.589811

        distance = haversine_distance(newton_lat, newton_lng, springfield_lat, springfield_lng)

        # Should be approximately 80 miles
        self.assertGreater(distance, 70.0)
        self.assertLess(distance, 90.0)

    def test_boston_to_new_york(self):
        """Test coast-to-coast style distance."""
        # Boston, MA
        boston_lat, boston_lng = 42.360082, -71.058880
        # New York, NY (approximate)
        ny_lat, ny_lng = 40.7128, -74.0060

        distance = haversine_distance(boston_lat, boston_lng, ny_lat, ny_lng)

        # Should be approximately 190 miles
        self.assertGreater(distance, 180.0)
        self.assertLess(distance, 220.0)

    def test_symmetry(self):
        """Test that distance is same in both directions."""
        lat1, lng1 = 42.337807, -71.209182
        lat2, lng2 = 42.373611, -71.110558

        distance1 = haversine_distance(lat1, lng1, lat2, lng2)
        distance2 = haversine_distance(lat2, lng2, lat1, lng1)

        self.assertAlmostEqual(distance1, distance2, places=6)


class FilterByDistanceTests(TestCase):
    """Test filter_by_distance function."""

    @classmethod
    def setUpTestData(cls):
        """Create test venues and events."""
        # Create a source for events
        cls.source = Source.objects.create(
            base_url="https://example.com",
            name="Test Source",
        )

        # Create venue in Newton, MA
        cls.newton_venue = Venue.objects.create(
            name="Newton Library",
            slug="newton-library",
            city="Newton",
            state="MA",
            latitude=42.337807,
            longitude=-71.209182,
        )

        # Create venue in Cambridge, MA (~5 miles from Newton)
        cls.cambridge_venue = Venue.objects.create(
            name="Cambridge Library",
            slug="cambridge-library",
            city="Cambridge",
            state="MA",
            latitude=42.373611,
            longitude=-71.110558,
        )

        # Create venue in Springfield, MA (~80 miles from Newton)
        cls.springfield_venue = Venue.objects.create(
            name="Springfield Library",
            slug="springfield-library",
            city="Springfield",
            state="MA",
            latitude=42.101483,
            longitude=-72.589811,
        )

        # Create venue without coordinates
        cls.no_coords_venue = Venue.objects.create(
            name="Unknown Library",
            slug="unknown-library",
            city="Somewhere",
            state="MA",
        )

        # Create events at each venue
        future_time = timezone.now() + timedelta(days=7)

        cls.newton_event = Event.objects.create(
            title="Newton Event",
            url="https://example.com/newton",
            external_id="test-newton-1",
            description="Test event in Newton",
            start_time=future_time,
            venue=cls.newton_venue,
            source=cls.source,
        )

        cls.cambridge_event = Event.objects.create(
            title="Cambridge Event",
            url="https://example.com/cambridge",
            external_id="test-cambridge-1",
            description="Test event in Cambridge",
            start_time=future_time,
            venue=cls.cambridge_venue,
            source=cls.source,
        )

        cls.springfield_event = Event.objects.create(
            title="Springfield Event",
            url="https://example.com/springfield",
            external_id="test-springfield-1",
            description="Test event in Springfield",
            start_time=future_time,
            venue=cls.springfield_venue,
            source=cls.source,
        )

        cls.no_coords_event = Event.objects.create(
            title="No Coords Event",
            url="https://example.com/unknown",
            external_id="test-unknown-1",
            description="Test event with no coordinates",
            start_time=future_time,
            venue=cls.no_coords_venue,
            source=cls.source,
        )

    def test_filter_includes_nearby(self):
        """Test that nearby events are included."""
        # Newton coordinates, 10 mile radius
        newton_lat, newton_lng = 42.337807, -71.209182

        qs = Event.objects.all()
        filtered = filter_by_distance(qs, newton_lat, newton_lng, radius_miles=10.0)

        event_ids = list(filtered.values_list('id', flat=True))

        # Newton and Cambridge should be included (~5 miles apart)
        self.assertIn(self.newton_event.id, event_ids)
        self.assertIn(self.cambridge_event.id, event_ids)

    def test_filter_excludes_distant(self):
        """Test that distant events are excluded."""
        # Newton coordinates, 10 mile radius
        newton_lat, newton_lng = 42.337807, -71.209182

        qs = Event.objects.all()
        filtered = filter_by_distance(qs, newton_lat, newton_lng, radius_miles=10.0)

        event_ids = list(filtered.values_list('id', flat=True))

        # Springfield should be excluded (~80 miles away)
        self.assertNotIn(self.springfield_event.id, event_ids)

    def test_filter_excludes_null_coordinates(self):
        """Test that events without coordinates are excluded."""
        newton_lat, newton_lng = 42.337807, -71.209182

        qs = Event.objects.all()
        filtered = filter_by_distance(qs, newton_lat, newton_lng, radius_miles=100.0)

        event_ids = list(filtered.values_list('id', flat=True))

        # Event without coordinates should be excluded
        self.assertNotIn(self.no_coords_event.id, event_ids)

    def test_default_radius(self):
        """Test that default radius is 10 miles."""
        self.assertEqual(DEFAULT_RADIUS_MILES, 10.0)

    def test_larger_radius_includes_more(self):
        """Test that larger radius includes more events."""
        newton_lat, newton_lng = 42.337807, -71.209182

        qs = Event.objects.all()

        # 10 mile radius
        filtered_10 = filter_by_distance(qs, newton_lat, newton_lng, radius_miles=10.0)
        count_10 = filtered_10.count()

        # 100 mile radius
        filtered_100 = filter_by_distance(qs, newton_lat, newton_lng, radius_miles=100.0)
        count_100 = filtered_100.count()

        # 100 mile radius should include more or equal events
        self.assertGreaterEqual(count_100, count_10)

        # 100 miles should include Springfield (~80 miles)
        event_ids = list(filtered_100.values_list('id', flat=True))
        self.assertIn(self.springfield_event.id, event_ids)

    def test_zero_radius(self):
        """Test zero radius returns only exact matches."""
        # Exact Newton coordinates
        newton_lat, newton_lng = 42.337807, -71.209182

        qs = Event.objects.all()
        filtered = filter_by_distance(qs, newton_lat, newton_lng, radius_miles=0.1)

        # Should only include Newton event (exact location)
        event_ids = list(filtered.values_list('id', flat=True))
        self.assertIn(self.newton_event.id, event_ids)
        self.assertEqual(len(event_ids), 1)


class FilterByDistanceIntegrationTests(TestCase):
    """Integration tests for filter_by_distance with real queries."""

    @classmethod
    def setUpTestData(cls):
        """Create test data."""
        cls.source = Source.objects.create(
            base_url="https://example.com",
            name="Test Source",
        )

        # Create multiple venues at varying distances
        cls.venues = []
        cls.events = []

        # Create venues at different distances from Newton
        distances = [
            ("Very Close", 42.340, -71.210, 0.3),   # ~0.3 miles
            ("Close", 42.350, -71.200, 1.0),        # ~1 mile
            ("Medium", 42.370, -71.150, 4.0),       # ~4 miles
            ("Far", 42.450, -71.050, 10.0),         # ~10 miles
            ("Very Far", 42.600, -70.800, 30.0),    # ~30 miles
        ]

        future_time = timezone.now() + timedelta(days=7)

        for i, (name, lat, lng, _) in enumerate(distances):
            venue = Venue.objects.create(
                name=f"{name} Venue",
                slug=f"{name.lower().replace(' ', '-')}-venue",
                city="Test",
                state="MA",
                latitude=lat,
                longitude=lng,
            )
            cls.venues.append(venue)

            event = Event.objects.create(
                title=f"{name} Event",
                url=f"https://example.com/{name.lower()}",
                external_id=f"test-distance-{i}",
                description=f"Test event at {name} distance",
                start_time=future_time,
                venue=venue,
                source=cls.source,
            )
            cls.events.append(event)

    def test_progressive_radius(self):
        """Test that increasing radius includes more events."""
        newton_lat, newton_lng = 42.337807, -71.209182

        qs = Event.objects.all()

        # Count events at different radii
        counts = {}
        for radius in [1, 5, 15, 50]:
            filtered = filter_by_distance(qs, newton_lat, newton_lng, radius_miles=radius)
            counts[radius] = filtered.count()

        # Each larger radius should include >= previous
        self.assertLessEqual(counts[1], counts[5])
        self.assertLessEqual(counts[5], counts[15])
        self.assertLessEqual(counts[15], counts[50])
