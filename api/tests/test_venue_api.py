"""
Tests for venue data in API responses.

Ensures EventSchema properly serializes venue objects for frontend consumption.
Also tests venue enrichment API endpoints.
"""

import json
from django.test import TestCase
from django.utils import timezone
from datetime import timedelta
from model_bakery import baker

from events.models import Event, Source, ServiceToken
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


class VenueEnrichmentAPITest(TestCase):
    """Tests for venue enrichment API endpoints."""

    def setUp(self):
        self.service_token = baker.make(ServiceToken)
        self.source = baker.make(Source)

        # Venue with venue_kind but missing enrichment (needs enrichment)
        self.venue_needing_enrichment = baker.make(
            Venue,
            name="Newton Free Library",
            city="Newton",
            state="MA",
            venue_kind="library",
            website_url=None,
            description="",
            kids_summary="",
        )

        # Venue already enriched
        self.venue_enriched = baker.make(
            Venue,
            name="Waltham Library",
            city="Waltham",
            state="MA",
            venue_kind="library",
            website_url="https://waltham.lib",
            description="A great library",
            kids_summary="Kids love it",
        )

        # Venue without venue_kind (Phase 1 incomplete)
        self.venue_no_kind = baker.make(
            Venue,
            name="Unknown Place",
            city="Boston",
            state="MA",
            venue_kind=None,
            website_url=None,
            description="",
            kids_summary="",
        )

        # Create events at the venue
        for i in range(3):
            baker.make(
                Event,
                title=f"Event {i}",
                description=f"Description {i}",
                venue=self.venue_needing_enrichment,
                source=self.source,
                start_time=timezone.now() + timedelta(days=i),
            )

    def test_get_venues_needing_enrichment_returns_venues(self):
        """Test /api/venues/needing-enrichment returns venues missing enrichment data."""
        response = self.client.get(
            "/api/v1/venues/needing-enrichment",
            HTTP_AUTHORIZATION=f"Bearer {self.service_token.token}",
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()

        self.assertIn("venues", data)
        self.assertIn("total_count", data)
        self.assertGreaterEqual(data["total_count"], 1)

        # Should include venue needing enrichment
        venue_ids = [v["id"] for v in data["venues"]]
        self.assertIn(self.venue_needing_enrichment.id, venue_ids)

        # Should NOT include venue with venue_kind=None
        self.assertNotIn(self.venue_no_kind.id, venue_ids)

    def test_get_venues_needing_enrichment_excludes_fully_enriched(self):
        """Test endpoint excludes venues that are already enriched."""
        response = self.client.get(
            "/api/v1/venues/needing-enrichment",
            HTTP_AUTHORIZATION=f"Bearer {self.service_token.token}",
        )

        data = response.json()
        venue_ids = [v["id"] for v in data["venues"]]

        # Fully enriched venue should not be included
        self.assertNotIn(self.venue_enriched.id, venue_ids)

    def test_get_venues_needing_enrichment_filter_by_missing_field(self):
        """Test filtering by specific missing field."""
        response = self.client.get(
            "/api/v1/venues/needing-enrichment?missing=website_url",
            HTTP_AUTHORIZATION=f"Bearer {self.service_token.token}",
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()

        # All returned venues should have missing website_url
        for venue in data["venues"]:
            self.assertTrue(venue["website_url"] is None or venue["website_url"] == "")

    def test_get_venues_needing_enrichment_respects_limit(self):
        """Test limit parameter works correctly."""
        response = self.client.get(
            "/api/v1/venues/needing-enrichment?limit=1",
            HTTP_AUTHORIZATION=f"Bearer {self.service_token.token}",
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertLessEqual(len(data["venues"]), 1)

    def test_get_venues_needing_enrichment_requires_auth(self):
        """Test endpoint requires service token authentication."""
        response = self.client.get("/api/v1/venues/needing-enrichment")
        self.assertEqual(response.status_code, 401)

    def test_get_venue_events_returns_events(self):
        """Test /api/venues/{id}/events returns events at venue."""
        response = self.client.get(
            f"/api/v1/venues/{self.venue_needing_enrichment.id}/events",
            HTTP_AUTHORIZATION=f"Bearer {self.service_token.token}",
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()

        self.assertIn("events", data)
        self.assertEqual(len(data["events"]), 3)

        # Check event structure
        event = data["events"][0]
        self.assertIn("id", event)
        self.assertIn("title", event)
        self.assertIn("description", event)
        self.assertIn("start", event)

    def test_get_venue_events_respects_limit(self):
        """Test limit parameter on venue events."""
        response = self.client.get(
            f"/api/v1/venues/{self.venue_needing_enrichment.id}/events?limit=2",
            HTTP_AUTHORIZATION=f"Bearer {self.service_token.token}",
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data["events"]), 2)

    def test_get_venue_events_404_for_invalid_venue(self):
        """Test 404 for non-existent venue."""
        response = self.client.get(
            "/api/v1/venues/99999/events",
            HTTP_AUTHORIZATION=f"Bearer {self.service_token.token}",
        )

        self.assertEqual(response.status_code, 404)

    def test_get_venue_events_requires_auth(self):
        """Test endpoint requires service token authentication."""
        response = self.client.get(f"/api/v1/venues/{self.venue_needing_enrichment.id}/events")
        self.assertEqual(response.status_code, 401)

    def test_patch_venue_updates_enrichment_fields(self):
        """Test PATCH /api/venues/{id} updates enrichment fields."""
        payload = {
            "website_url": "https://newtonfreelibrary.net",
            "website_url_confidence": 0.85,
            "description": "Newton Free Library serves the Newton community.",
            "kids_summary": "Offers story times and LEGO club for kids.",
            "enrichment_status": "complete",
        }

        response = self.client.patch(
            f"/api/v1/venues/{self.venue_needing_enrichment.id}",
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {self.service_token.token}",
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()

        # Check response reflects updates
        self.assertEqual(data["website_url"], "https://newtonfreelibrary.net")
        self.assertEqual(data["description"], "Newton Free Library serves the Newton community.")
        self.assertEqual(data["kids_summary"], "Offers story times and LEGO club for kids.")
        self.assertEqual(data["enrichment_status"], "complete")

        # Verify database was updated
        self.venue_needing_enrichment.refresh_from_db()
        self.assertEqual(self.venue_needing_enrichment.website_url, "https://newtonfreelibrary.net")
        self.assertEqual(self.venue_needing_enrichment.website_url_confidence, 0.85)
        self.assertIsNotNone(self.venue_needing_enrichment.last_enriched_at)

    def test_patch_venue_partial_update(self):
        """Test PATCH only updates provided fields."""
        payload = {"description": "Just updating description"}

        response = self.client.patch(
            f"/api/v1/venues/{self.venue_needing_enrichment.id}",
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {self.service_token.token}",
        )

        self.assertEqual(response.status_code, 200)

        self.venue_needing_enrichment.refresh_from_db()
        self.assertEqual(self.venue_needing_enrichment.description, "Just updating description")
        # Other fields should be unchanged
        self.assertIsNone(self.venue_needing_enrichment.website_url)

    def test_patch_venue_404_for_invalid_venue(self):
        """Test 404 for non-existent venue."""
        payload = {"description": "Test"}

        response = self.client.patch(
            "/api/v1/venues/99999",
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {self.service_token.token}",
        )

        self.assertEqual(response.status_code, 404)

    def test_patch_venue_requires_auth(self):
        """Test endpoint requires service token authentication."""
        payload = {"description": "Test"}

        response = self.client.patch(
            f"/api/v1/venues/{self.venue_needing_enrichment.id}",
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 401)


class VenueFromOSMAPITest(TestCase):
    """Tests for POST /api/venues/from-osm/ endpoint."""

    def setUp(self):
        self.service_token = baker.make(ServiceToken)

    def test_create_venue_from_osm_returns_201(self):
        """Test creating a new venue from OSM data returns 201 with venue_id and status='created'."""
        payload = {
            "osm_type": "way",
            "osm_id": 214642596,
            "name": "Needham Free Public Library",
            "category": "library",
            "street_address": "1139 Highland Avenue",
            "city": "Needham",
            "state": "MA",
            "postal_code": "02494",
            "latitude": 42.2877508,
            "longitude": -71.2353727,
            "website": "https://needhamlibrary.org",
            "phone": "781-455-7559",
            "opening_hours": "Mo-Th 09:00-21:00; Fr 09:00-17:30; Sa 09:00-17:00; Su 13:00-17:00",
            "operator": "Town of Needham",
            "wikidata": "Q123456",
        }

        response = self.client.post(
            "/api/v1/venues/from-osm/",
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {self.service_token.token}",
        )

        self.assertEqual(response.status_code, 201)
        data = response.json()

        self.assertIn("venue_id", data)
        self.assertEqual(data["status"], "created")

        # Verify venue was created in database
        venue = Venue.objects.get(id=data["venue_id"])
        self.assertEqual(venue.name, "Needham Free Public Library")
        self.assertEqual(venue.osm_type, "way")
        self.assertEqual(venue.osm_id, 214642596)
        self.assertEqual(venue.category, "library")
        self.assertEqual(venue.city, "Needham")
        self.assertEqual(venue.state, "MA")
        self.assertEqual(venue.phone, "781-455-7559")
        self.assertEqual(venue.opening_hours_raw, "Mo-Th 09:00-21:00; Fr 09:00-17:30; Sa 09:00-17:00; Su 13:00-17:00")
        self.assertEqual(venue.operator, "Town of Needham")
        self.assertEqual(venue.wikidata_id, "Q123456")
        self.assertEqual(venue.data_source, "osm")

    def test_update_existing_osm_venue_returns_updated(self):
        """Test updating an existing OSM venue returns status='updated' with changes list."""
        # Create existing venue via OSM
        existing_venue = baker.make(
            Venue,
            name="Needham Library",
            osm_type="way",
            osm_id=214642596,
            city="Needham",
            state="MA",
            phone="",
            opening_hours_raw="",
            data_source="osm",
        )

        payload = {
            "osm_type": "way",
            "osm_id": 214642596,
            "name": "Needham Free Public Library",  # Name change
            "category": "library",
            "city": "Needham",
            "state": "MA",
            "phone": "781-455-7559",  # New phone
            "opening_hours": "Mo-Th 09:00-21:00",  # New hours
        }

        response = self.client.post(
            "/api/v1/venues/from-osm/",
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {self.service_token.token}",
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()

        self.assertEqual(data["venue_id"], existing_venue.id)
        self.assertEqual(data["status"], "updated")
        self.assertIn("changes", data)
        self.assertIn("name", data["changes"])
        self.assertIn("phone", data["changes"])
        self.assertIn("opening_hours", data["changes"])

        # Verify database was updated
        existing_venue.refresh_from_db()
        self.assertEqual(existing_venue.name, "Needham Free Public Library")
        self.assertEqual(existing_venue.phone, "781-455-7559")

    def test_unchanged_osm_venue_returns_unchanged(self):
        """Test re-submitting identical OSM data returns status='unchanged'."""
        # Explicitly set all compared fields to match the payload exactly
        existing_venue = Venue.objects.create(
            name="Needham Free Public Library",
            slug="needham-free-public-library",
            osm_type="way",
            osm_id=214642596,
            category="library",
            street_address="",
            city="Needham",
            state="MA",
            postal_code="",
            latitude=42.28775,
            longitude=-71.23537,
            canonical_url="",
            phone="781-455-7559",
            opening_hours_raw="",
            operator="",
            wikidata_id="",
            data_source="osm",
        )

        # Payload must match the existing venue exactly
        payload = {
            "osm_type": "way",
            "osm_id": 214642596,
            "name": "Needham Free Public Library",
            "category": "library",
            "city": "Needham",
            "state": "MA",
            "phone": "781-455-7559",
            "latitude": 42.28775,
            "longitude": -71.23537,
        }

        response = self.client.post(
            "/api/v1/venues/from-osm/",
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {self.service_token.token}",
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()

        self.assertEqual(data["venue_id"], existing_venue.id)
        self.assertEqual(data["status"], "unchanged")
        # changes should be None when unchanged
        self.assertIsNone(data.get("changes"))

    def test_osm_type_and_osm_id_required(self):
        """Test that osm_type and osm_id are required fields."""
        # Missing osm_id - Django Ninja returns 422 for schema validation errors
        payload = {"osm_type": "way", "name": "Test Venue", "city": "Boston", "state": "MA"}
        response = self.client.post(
            "/api/v1/venues/from-osm/",
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {self.service_token.token}",
        )
        self.assertEqual(response.status_code, 422)

        # Missing osm_type
        payload = {"osm_id": 12345, "name": "Test Venue", "city": "Boston", "state": "MA"}
        response = self.client.post(
            "/api/v1/venues/from-osm/",
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {self.service_token.token}",
        )
        self.assertEqual(response.status_code, 422)

    def test_name_and_city_required(self):
        """Test that name and city are required for venue creation."""
        # Missing name and city - Django Ninja returns 422 for schema validation errors
        payload = {
            "osm_type": "way",
            "osm_id": 12345,
            "state": "MA",
        }
        response = self.client.post(
            "/api/v1/venues/from-osm/",
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {self.service_token.token}",
        )
        self.assertEqual(response.status_code, 422)

    def test_requires_authentication(self):
        """Test endpoint requires service token authentication."""
        payload = {
            "osm_type": "way",
            "osm_id": 12345,
            "name": "Test Venue",
            "city": "Boston",
            "state": "MA",
        }
        response = self.client.post(
            "/api/v1/venues/from-osm/",
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 401)

    def test_minimal_osm_venue_creation(self):
        """Test creating venue with only required fields."""
        payload = {
            "osm_type": "node",
            "osm_id": 98765,
            "name": "Simple Park",
            "city": "Waltham",
            "state": "MA",
        }

        response = self.client.post(
            "/api/v1/venues/from-osm/",
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {self.service_token.token}",
        )

        self.assertEqual(response.status_code, 201)
        data = response.json()

        venue = Venue.objects.get(id=data["venue_id"])
        self.assertEqual(venue.name, "Simple Park")
        self.assertEqual(venue.osm_type, "node")
        self.assertEqual(venue.osm_id, 98765)
        self.assertEqual(venue.data_source, "osm")
        # Optional fields should be empty/default
        self.assertEqual(venue.phone, "")
        self.assertEqual(venue.opening_hours_raw, "")

    def test_osm_venue_sets_website_to_canonical_url(self):
        """Test that website from OSM is stored in canonical_url field."""
        payload = {
            "osm_type": "way",
            "osm_id": 11111,
            "name": "Library with Website",
            "city": "Boston",
            "state": "MA",
            "website": "https://example.org",
        }

        response = self.client.post(
            "/api/v1/venues/from-osm/",
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {self.service_token.token}",
        )

        self.assertEqual(response.status_code, 201)
        venue = Venue.objects.get(id=response.json()["venue_id"])
        self.assertEqual(venue.canonical_url, "https://example.org")

    def test_osm_venue_latitude_longitude_stored(self):
        """Test that lat/lng coordinates are stored correctly."""
        payload = {
            "osm_type": "node",
            "osm_id": 22222,
            "name": "Geo Test Venue",
            "city": "Cambridge",
            "state": "MA",
            "latitude": 42.3736,
            "longitude": -71.1097,
        }

        response = self.client.post(
            "/api/v1/venues/from-osm/",
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {self.service_token.token}",
        )

        self.assertEqual(response.status_code, 201)
        venue = Venue.objects.get(id=response.json()["venue_id"])
        self.assertAlmostEqual(float(venue.latitude), 42.3736, places=4)
        self.assertAlmostEqual(float(venue.longitude), -71.1097, places=4)
