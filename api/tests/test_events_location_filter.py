"""
Tests for event location filtering using location_id parameter.
"""

from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.contrib.auth import get_user_model
from django.utils import timezone
from model_bakery import baker
from ninja_jwt.tokens import AccessToken

from events.models import Event
from venues.models import Venue
from locations.models import Location


User = get_user_model()


class EventLocationFilterTest(TestCase):
    """Tests for GET /api/v1/events?location_id=X&radius_miles=Y"""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="testuser", password="testpass", is_active=True)

        # Create Newton location (42.337807, -71.209182)
        cls.newton = Location.objects.create(
            geoid="2545000",
            name="Newton",
            normalized_name="newton",
            state="MA",
            country_code="US",
            latitude=Decimal("42.337807"),
            longitude=Decimal("-71.209182"),
            population=88923,
        )

        # Create venue in Newton (within ~2 miles of Newton center)
        cls.newton_venue = baker.make(
            Venue,
            name="Newton Library",
            city="Newton",
            state="MA",
            latitude=Decimal("42.330"),
            longitude=Decimal("-71.200"),
        )

        # Create venue in Cambridge (~5 miles from Newton)
        cls.cambridge_venue = baker.make(
            Venue,
            name="Cambridge Library",
            city="Cambridge",
            state="MA",
            latitude=Decimal("42.373611"),
            longitude=Decimal("-71.110558"),
        )

        # Create venue far away (~50 miles from Newton)
        cls.worcester_venue = baker.make(
            Venue,
            name="Worcester Library",
            city="Worcester",
            state="MA",
            latitude=Decimal("42.262593"),
            longitude=Decimal("-71.802293"),
        )

        # Create events for each venue
        tomorrow = timezone.now() + timedelta(days=1)

        cls.newton_event = baker.make(
            Event,
            title="Newton Story Time",
                        venue=cls.newton_venue,
            start_time=tomorrow,
        )

        cls.cambridge_event = baker.make(
            Event,
            title="Cambridge Story Time",
                        venue=cls.cambridge_venue,
            start_time=tomorrow,
        )

        cls.worcester_event = baker.make(
            Event,
            title="Worcester Story Time",
                        venue=cls.worcester_venue,
            start_time=tomorrow,
        )

        # Event with venue without coordinates (should not appear in location filtered results)
        cls.online_venue = baker.make(
            Venue,
            name="Online Space",
            city="Virtual",
            state="",
            latitude=None,
            longitude=None,
        )
        cls.online_event = baker.make(
            Event,
            title="Online Event",
            venue=cls.online_venue,
            start_time=tomorrow,
        )

    def get_auth_header(self):
        token = AccessToken.for_user(self.user)
        return {"HTTP_AUTHORIZATION": f"Bearer {token}"}

    def test_location_filter_returns_nearby_events(self):
        """Test location_id filter returns events within default radius (10 miles)."""
        response = self.client.get(
            f"/api/v1/events?location_id={self.newton.id}",
            **self.get_auth_header()
        )
        self.assertEqual(response.status_code, 200)
        events = response.json()

        # Should include Newton and Cambridge events (both within 10 miles)
        titles = [e["title"] for e in events]
        self.assertIn("Newton Story Time", titles)
        self.assertIn("Cambridge Story Time", titles)
        # Worcester is >10 miles away
        self.assertNotIn("Worcester Story Time", titles)
        # Online event has venue without coordinates, so excluded from location filter
        self.assertNotIn("Online Event", titles)

    def test_location_filter_with_custom_radius(self):
        """Test radius_miles parameter adjusts search area."""
        # With 3-mile radius, only Newton event should be returned
        response = self.client.get(
            f"/api/v1/events?location_id={self.newton.id}&radius_miles=3",
            **self.get_auth_header()
        )
        self.assertEqual(response.status_code, 200)
        events = response.json()

        titles = [e["title"] for e in events]
        self.assertIn("Newton Story Time", titles)
        self.assertNotIn("Cambridge Story Time", titles)  # ~5 miles away
        self.assertNotIn("Worcester Story Time", titles)

    def test_location_filter_with_large_radius(self):
        """Test large radius includes distant events."""
        response = self.client.get(
            f"/api/v1/events?location_id={self.newton.id}&radius_miles=60",
            **self.get_auth_header()
        )
        self.assertEqual(response.status_code, 200)
        events = response.json()

        titles = [e["title"] for e in events]
        self.assertIn("Newton Story Time", titles)
        self.assertIn("Cambridge Story Time", titles)
        self.assertIn("Worcester Story Time", titles)

    def test_invalid_location_id_returns_all_events(self):
        """Test invalid location_id is silently ignored."""
        response = self.client.get(
            "/api/v1/events?location_id=99999",
            **self.get_auth_header()
        )
        self.assertEqual(response.status_code, 200)
        # Should return events without location filtering
        events = response.json()
        self.assertGreater(len(events), 0)

    def test_location_filter_with_date_filter(self):
        """Test location_id works with date filters."""
        today = timezone.now().date()
        next_week = today + timedelta(days=7)

        response = self.client.get(
            f"/api/v1/events?location_id={self.newton.id}&start={today}&end={next_week}",
            **self.get_auth_header()
        )
        self.assertEqual(response.status_code, 200)
        events = response.json()

        # Should have events within 10 miles, within date range
        titles = [e["title"] for e in events]
        self.assertIn("Newton Story Time", titles)

    def test_no_location_filter_returns_all_events(self):
        """Test without location_id, all events are returned."""
        response = self.client.get("/api/v1/events", **self.get_auth_header())
        self.assertEqual(response.status_code, 200)
        events = response.json()

        # Should include all 4 events
        self.assertEqual(len(events), 4)
