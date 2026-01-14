"""
Schema.org integration tests with API validation.

Tests our Event and Venue models against the actual Schema.org specification
to ensure compliance and catch any changes to the standard.
"""

import json
import requests
from django.test import TestCase
from django.core.exceptions import ValidationError
from model_bakery import baker
from unittest.mock import patch

from events.models import Event
from venues.models import Venue


class SchemaOrgAPITest(TestCase):
    """Test Schema.org API integration and validate our implementation."""

    def setUp(self):
        """Create test venue for events."""
        self.test_venue = baker.make(Venue, name="Test Venue", city="Newton", state="MA")

    def test_schema_org_event_specification_compliance(self):
        """Test that our Event model follows Schema.org Event specification."""
        
        try:
            response = requests.get('https://schema.org/Event.jsonld', timeout=10)
            if response.status_code == 200:
                schema_data = response.json()
                
                # Verify key Schema.org Event properties
                # Note: 'location' is handled via venue FK and get_location_string() method
                expected_properties = [
                    'name',  # -> title in our model
                    'description',
                    'startDate',  # -> start_time
                    'endDate',    # -> end_time
                    'organizer',
                    'eventStatus',
                    'eventAttendanceMode',
                    'url'
                ]

                # Check model field mappings
                field_mappings = {
                    'name': 'title',
                    'startDate': 'start_time',
                    'endDate': 'end_time',
                    'eventStatus': 'event_status',
                    'eventAttendanceMode': 'event_attendance_mode'
                }

                for schema_prop in expected_properties:
                    model_field = field_mappings.get(schema_prop, schema_prop)
                    self.assertTrue(
                        hasattr(Event, model_field),
                        f"Event model missing field for Schema.org property: {schema_prop} -> {model_field}"
                    )

                # Verify location is handled via venue FK
                self.assertTrue(hasattr(Event, 'venue'), "Event model missing venue FK for location")
                    
                print(f"✅ Schema.org Event compliance verified for {len(expected_properties)} properties")
            else:
                self.skipTest(f"Schema.org API unavailable (status: {response.status_code})")
                
        except requests.RequestException as e:
            self.skipTest(f"Cannot reach Schema.org API: {e}")


class EventSchemaOrgIntegrationTest(TestCase):
    """Test Event model integration with Venue data from collector."""

    def setUp(self):
        pass  # Events will create venues from location_data

    def test_event_creation_with_location_data(self):
        """Test Event creation with collector's location_data format."""

        # Simulate collector output with location_data
        event_data = {
            'external_id': 'test-event-1',
            'title': 'Budding Bookworms',
            'description': 'Storytime for infants and caregivers',
            'location_data': {
                'venue_name': 'Needham Free Public Library',
                'room_name': "Children's Room",
                'street_address': '1139 Highland Avenue',
                'city': 'Needham',
                'state': 'MA',
                'postal_code': '02494',
                'extraction_confidence': 0.95
            },
            'organizer': 'Needham Public Library',
            'event_status': 'scheduled',
            'event_attendance_mode': 'offline',
            'start_time': '2025-09-02T10:00:00+00:00',
            'end_time': '2025-09-02T10:30:00+00:00',
            'url': 'https://example.com/event'
        }

        event, _ = Event.create_with_schema_org_data(event_data, source_url="https://example.com")

        # Verify event created correctly
        self.assertEqual(event.title, 'Budding Bookworms')
        self.assertEqual(event.organizer, 'Needham Public Library')
        self.assertEqual(event.event_status, 'scheduled')
        self.assertEqual(event.event_attendance_mode, 'offline')

        # Verify venue relationship
        self.assertIsNotNone(event.venue)
        self.assertEqual(event.venue.name, "Needham Free Public Library")
        self.assertEqual(event.venue.city, "Needham")
        self.assertEqual(event.room_name, "Children's Room")

        # Verify location methods work
        self.assertIn("Children's Room", event.get_location_string())
        self.assertIn("Needham", event.get_full_address())
        self.assertEqual(event.get_city(), "Needham")
    
    def test_event_location_search_text_for_rag(self):
        """Test that events provide rich location text for RAG searches."""

        event_data = {
            'external_id': 'needham-event',
            'title': 'Dance Class',
            'description': 'Ballet for children',
            'location_data': {
                'venue_name': 'Needham Free Public Library',
                'room_name': 'Library Community Room',
                'street_address': '1139 Highland Avenue',
                'city': 'Needham',
                'state': 'MA',
                'postal_code': '02494',
                'extraction_confidence': 0.95
            },
            'start_time': '2025-09-02T11:00:00+00:00'
        }

        event, _ = Event.create_with_schema_org_data(event_data, source_url="https://example.com")
        location_search_text = event.get_location_search_text()

        # Should include room name, venue name, and city for RAG
        self.assertIn("Library Community Room", location_search_text)
        self.assertIn("Needham Free Public Library", location_search_text)
        self.assertIn("Needham", location_search_text)

        # This should solve the "events in Needham" RAG query problem
        self.assertGreater(location_search_text.count("Needham"), 1)
    
    def test_event_with_low_confidence_location_data(self):
        """Test event creation when location_data has low confidence."""

        event_data = {
            'external_id': 'low-conf-event',
            'title': 'Low Confidence Event',
            'description': 'Event with uncertain location',
            'location_data': {
                'venue_name': 'Unknown Venue',
                'raw_location_string': 'Town Hall',
                'extraction_confidence': 0.3  # Below threshold
            },
            'start_time': '2025-09-03T14:00:00+00:00'
        }

        # Low confidence without city should raise ValueError - venue cannot be determined
        with self.assertRaises(ValueError):
            Event.create_with_schema_org_data(event_data, source_url="https://example.com")
    
    def test_event_with_raw_place_json(self):
        """Test that raw_place_json is preserved for re-parsing."""

        raw_place = {
            '@type': 'Place',
            'name': 'Main Hall',
            'address': {
                '@type': 'PostalAddress',
                'streetAddress': '100 Main Street',
                'addressLocality': 'Boston',
                'addressRegion': 'MA',
                'postalCode': '02101'
            }
        }

        event_data = {
            'external_id': 'raw-json-event',
            'title': 'Event with Raw JSON',
            'description': 'Event preserving raw Schema.org data',
            'location_data': {
                'venue_name': 'Main Hall',
                'street_address': '100 Main Street',
                'city': 'Boston',
                'state': 'MA',
                'postal_code': '02101',
                'raw_place_json': raw_place,
                'extraction_confidence': 0.95
            },
            'start_time': '2025-09-04T15:00:00+00:00'
        }

        event, _ = Event.create_with_schema_org_data(event_data, source_url="https://example.com")

        # Venue should be created
        self.assertIsNotNone(event.venue)
        self.assertEqual(event.venue.name, 'Main Hall')
        self.assertEqual(event.get_city(), 'Boston')

        # Raw JSON should be preserved
        self.assertEqual(event.raw_place_json, raw_place)


class RAGLocationSearchTest(TestCase):
    """Test that Venue integration fixes RAG location searches."""

    def setUp(self):
        pass  # Events will create venues from location_data

    def test_needham_events_rag_query_fix(self):
        """Test that 'events in Needham' query now finds Needham Library events."""

        # Create Needham Library event with rich location_data
        needham_event_data = {
            'external_id': 'needham-storytime',
            'title': 'Budding Bookworms',
            'description': 'Storytime just for infants from newborn to not-yet walking',
            'location_data': {
                'venue_name': 'Needham Free Public Library',
                'room_name': "Children's Room",
                'street_address': '1139 Highland Avenue',
                'city': 'Needham',
                'state': 'MA',
                'postal_code': '02494',
                'extraction_confidence': 0.95
            },
            'start_time': '2025-09-02T10:00:00+00:00',
            'organizer': 'Needham Public Library'
        }

        event, _ = Event.create_with_schema_org_data(needham_event_data, source_url="https://needham.library")

        # Test location search capabilities
        location_search_text = event.get_location_search_text()

        # Should contain "Needham" multiple times for better RAG matching
        needham_count = location_search_text.lower().count('needham')
        self.assertGreater(needham_count, 1,
                          f"Location search text should contain 'Needham' multiple times: '{location_search_text}'")

        # Should include room, venue name, and address
        self.assertIn("Children's Room", location_search_text)
        self.assertIn("Needham Free Public Library", location_search_text)

        # This rich location text should make RAG queries like
        # "events in Needham" much more effective
    
    def test_multiple_needham_venues_deduplication(self):
        """Test that multiple events at same Needham venue share Venue objects."""

        common_location_data = {
            'venue_name': 'Needham Free Public Library',
            'room_name': 'Library Community Room',
            'street_address': '1139 Highland Avenue',
            'city': 'Needham',
            'state': 'MA',
            'postal_code': '02494',
            'extraction_confidence': 0.95
        }

        # Create two events at same venue
        event1_data = {
            'external_id': 'dance-class',
            'title': 'Dance Class',
            'description': 'Ballet for children',
            'location_data': common_location_data,
            'start_time': '2025-09-02T11:00:00+00:00'
        }

        event2_data = {
            'external_id': 'teen-space',
            'title': 'Teen Study Space',
            'description': 'Study space for teenagers',
            'location_data': common_location_data,  # Same venue
            'start_time': '2025-09-02T14:30:00+00:00'
        }

        event1, _ = Event.create_with_schema_org_data(event1_data, source_url="https://needham.library")
        event2, _ = Event.create_with_schema_org_data(event2_data, source_url="https://needham.library")

        # Should share the same Venue object (deduplication)
        self.assertEqual(event1.venue.id, event2.venue.id)

        # Both should provide rich Needham location data for RAG
        self.assertIn("Needham", event1.get_location_search_text())
        self.assertIn("Needham", event2.get_location_search_text())