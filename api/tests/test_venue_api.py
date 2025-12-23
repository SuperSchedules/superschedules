"""
Tests for venue data in API responses.

Ensures EventSchema properly serializes venue objects for frontend consumption.
"""

from django.test import TestCase
from django.utils import timezone
from datetime import timedelta
from model_bakery import baker

from events.models import Event, Source
from venues.models import Venue
from api.views import EventSchema, VenueSchema


class VenueSchemaTest(TestCase):
    """Test VenueSchema serialization."""

    def setUp(self):
        self.venue = baker.make(
            Venue,
            name="Waltham Public Library",
            street_address="735 Main Street",
            city="Waltham",
            state="MA",
            postal_code="02451",
        )

    def test_venue_schema_serializes_all_fields(self):
        """Test VenueSchema includes all expected fields."""
        schema = VenueSchema.from_orm(self.venue)

        self.assertEqual(schema.name, "Waltham Public Library")
        self.assertEqual(schema.street_address, "735 Main Street")
        self.assertEqual(schema.city, "Waltham")
        self.assertEqual(schema.state, "MA")
        self.assertEqual(schema.postal_code, "02451")

    def test_venue_schema_handles_empty_street_address(self):
        """Test VenueSchema handles venue without street address."""
        venue = baker.make(
            Venue,
            name="Mystery Location",
            street_address="",
            city="Boston",
            state="MA",
            postal_code="",
        )
        schema = VenueSchema.from_orm(venue)

        self.assertEqual(schema.name, "Mystery Location")
        self.assertEqual(schema.street_address, "")
        self.assertEqual(schema.city, "Boston")


class EventSchemaVenueTest(TestCase):
    """Test EventSchema includes venue and room_name fields."""

    def setUp(self):
        self.source = baker.make(Source)
        self.venue = baker.make(
            Venue,
            name="Newton Free Library",
            street_address="330 Homer Street",
            city="Newton",
            state="MA",
            postal_code="02459",
        )
        self.event_with_venue = baker.make(
            Event,
            title="Story Time",
            description="Kids story time",
            source=self.source,
            venue=self.venue,
            room_name="Children's Room",
            start_time=timezone.now() + timedelta(days=1),
        )
        self.event_without_venue = baker.make(
            Event,
            title="Virtual Event",
            description="Online workshop",
            source=self.source,
            venue=None,
            room_name="",
            start_time=timezone.now() + timedelta(days=2),
        )

    def test_event_schema_includes_venue_object(self):
        """Test EventSchema serializes venue as nested object."""
        schema = EventSchema.from_orm(self.event_with_venue)

        self.assertIsNotNone(schema.venue)
        self.assertEqual(schema.venue.name, "Newton Free Library")
        self.assertEqual(schema.venue.city, "Newton")
        self.assertEqual(schema.venue.state, "MA")
        self.assertEqual(schema.venue.street_address, "330 Homer Street")
        self.assertEqual(schema.venue.postal_code, "02459")

    def test_event_schema_includes_room_name(self):
        """Test EventSchema includes room_name field."""
        schema = EventSchema.from_orm(self.event_with_venue)

        self.assertEqual(schema.room_name, "Children's Room")

    def test_event_schema_handles_null_venue(self):
        """Test EventSchema handles events without venue."""
        schema = EventSchema.from_orm(self.event_without_venue)

        self.assertIsNone(schema.venue)
        self.assertEqual(schema.room_name, "")

    def test_event_schema_still_includes_location_string(self):
        """Test EventSchema still includes location string for backward compatibility."""
        schema = EventSchema.from_orm(self.event_with_venue)

        self.assertEqual(schema.location, "Children's Room, Newton Free Library")

    def test_event_list_endpoint_returns_venue_data(self):
        """Test /api/v1/events/ endpoint returns venue data in response."""
        from django.contrib.auth import get_user_model
        from ninja_jwt.tokens import AccessToken

        User = get_user_model()
        user = User.objects.create_user(username="testuser", password="testpass")
        token = AccessToken.for_user(user)

        response = self.client.get(
            "/api/v1/events",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )

        self.assertEqual(response.status_code, 200)
        events = response.json()

        # Find event with venue
        event_with_venue = next((e for e in events if e["title"] == "Story Time"), None)
        self.assertIsNotNone(event_with_venue)
        self.assertIn("venue", event_with_venue)
        self.assertIn("room_name", event_with_venue)

        venue = event_with_venue["venue"]
        self.assertEqual(venue["name"], "Newton Free Library")
        self.assertEqual(venue["city"], "Newton")

    def test_event_detail_endpoint_returns_venue_data(self):
        """Test /api/v1/events/{id} endpoint returns venue data."""
        from django.contrib.auth import get_user_model
        from ninja_jwt.tokens import AccessToken

        User = get_user_model()
        user = User.objects.create_user(username="testuser2", password="testpass")
        token = AccessToken.for_user(user)

        response = self.client.get(
            f"/api/v1/events/{self.event_with_venue.id}",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )

        self.assertEqual(response.status_code, 200)
        event = response.json()

        self.assertIn("venue", event)
        self.assertEqual(event["venue"]["name"], "Newton Free Library")
        self.assertEqual(event["room_name"], "Children's Room")
