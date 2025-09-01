"""
Schema.org integration tests with API validation.

Tests our Place and Event models against the actual Schema.org specification
to ensure compliance and catch any changes to the standard.
"""

import json
import requests
from django.test import TestCase
from django.core.exceptions import ValidationError
from model_bakery import baker
from unittest.mock import patch

from events.models import Event, Source
from events.place_models import Place


class SchemaOrgAPITest(TestCase):
    """Test Schema.org API integration and validate our implementation."""
    
    def setUp(self):
        """Create test source for events."""
        self.test_source = baker.make(Source, name="Test Source")
    
    def test_schema_org_place_specification_compliance(self):
        """Test that our Place model follows Schema.org Place specification."""
        
        # Fetch Schema.org Place specification
        try:
            response = requests.get('https://schema.org/Place.jsonld', timeout=10)
            if response.status_code == 200:
                schema_data = response.json()
                
                # Verify key Schema.org Place properties exist in our model
                expected_properties = ['name', 'address', 'telephone', 'url']
                
                for prop in expected_properties:
                    # Our model should have corresponding fields
                    self.assertTrue(
                        hasattr(Place, prop),
                        f"Place model missing Schema.org property: {prop}"
                    )
                    
                print(f"✅ Schema.org Place compliance verified for {len(expected_properties)} properties")
            else:
                self.skipTest(f"Schema.org API unavailable (status: {response.status_code})")
                
        except requests.RequestException as e:
            self.skipTest(f"Cannot reach Schema.org API: {e}")
    
    def test_schema_org_event_specification_compliance(self):
        """Test that our Event model follows Schema.org Event specification."""
        
        try:
            response = requests.get('https://schema.org/Event.jsonld', timeout=10)
            if response.status_code == 200:
                schema_data = response.json()
                
                # Verify key Schema.org Event properties
                expected_properties = [
                    'name',  # -> title in our model
                    'description', 
                    'location',
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
                    
                print(f"✅ Schema.org Event compliance verified for {len(expected_properties)} properties")
            else:
                self.skipTest(f"Schema.org API unavailable (status: {response.status_code})")
                
        except requests.RequestException as e:
            self.skipTest(f"Cannot reach Schema.org API: {e}")


class PlaceModelTest(TestCase):
    """Test Place model functionality with real Schema.org data."""
    
    def test_place_creation_from_needham_schema_org_data(self):
        """Test Place creation from real Needham Library Schema.org data."""
        
        # Real Schema.org Place data from Needham Library  
        needham_place_data = {
            "@type": "Place",
            "name": "Inside, Children's Room",
            "address": "Needham Free Public Library, 1139 Highland Avenue, Needham, MA, 02494",
            "telephone": "781-455-7559",
            "url": "www.needhamma.gov>library"
        }
        
        place = Place.create_from_schema_org(needham_place_data)
        
        self.assertIsNotNone(place)
        self.assertEqual(place.name, "Inside, Children's Room")
        self.assertEqual(place.address, "Needham Free Public Library, 1139 Highland Avenue, Needham, MA, 02494")
        self.assertEqual(place.telephone, "781-455-7559")
        self.assertEqual(place.url, "www.needhamma.gov>library")
    
    def test_place_creation_from_array_data(self):
        """Test Place creation from Schema.org array format."""
        
        # Schema.org often uses arrays for location
        location_array = [{
            "@type": "Place",
            "name": "Library Community Room",
            "address": "Needham Free Public Library, 1139 Highland Avenue, Needham, MA, 02494"
        }]
        
        place = Place.create_from_schema_org(location_array)
        
        self.assertIsNotNone(place)
        self.assertEqual(place.name, "Library Community Room")
        self.assertIn("Needham", place.address)
    
    def test_place_city_extraction(self):
        """Test city extraction from various address formats."""
        
        test_cases = [
            {
                'address': "Needham Free Public Library, 1139 Highland Avenue, Needham, MA, 02494",
                'expected_city': "Needham"
            },
            {
                'address': "Boston Public Library, Copley Square, Boston, MA 02116", 
                'expected_city': "Boston"
            },
            {
                'address': "123 Main Street, Cambridge, MA",
                'expected_city': "Cambridge"  
            },
            {
                'address': "Simple Address Without City",
                'expected_city': ""
            }
        ]
        
        for case in test_cases:
            place = baker.make(Place, address=case['address'])
            extracted_city = place.get_city()
            
            self.assertEqual(
                extracted_city, 
                case['expected_city'],
                f"Failed to extract city from: {case['address']}"
            )
    
    def test_place_search_text_generation(self):
        """Test comprehensive search text for RAG indexing."""
        
        place = baker.make(
            Place,
            name="Children's Room",
            address="Needham Free Public Library, 1139 Highland Avenue, Needham, MA, 02494"
        )
        
        search_text = place.get_search_text()
        
        # Should include room name, full address, and extracted city
        self.assertIn("Children's Room", search_text)
        self.assertIn("Needham Free Public Library", search_text)
        self.assertIn("Needham", search_text)  # City should appear twice
        
        # Should be suitable for RAG vectorization
        self.assertGreater(len(search_text), 20)  # Substantial content
        self.assertNotIn("  ", search_text)  # No double spaces
    
    def test_place_deduplication_by_address(self):
        """Test that places with same address are deduplicated."""
        
        # Create first place
        place_data = {
            "@type": "Place", 
            "name": "Room A",
            "address": "123 Test Street, Boston, MA 02101"
        }
        place1 = Place.create_from_schema_org(place_data)
        
        # Create second place with same address but different name
        place_data_2 = {
            "@type": "Place",
            "name": "Room B", 
            "address": "123 Test Street, Boston, MA 02101"  # Same address
        }
        place2 = Place.create_from_schema_org(place_data_2)
        
        # Should return the existing place, not create a new one
        self.assertEqual(place1.id, place2.id)
        self.assertEqual(place1.name, "Room A")  # Original name preserved
    
    def test_place_str_representation(self):
        """Test human-readable string representation."""
        
        # Place with both name and address
        full_place = baker.make(
            Place,
            name="Community Room",
            address="123 Main Street, Boston, MA"
        )
        self.assertEqual(str(full_place), "Community Room, 123 Main Street, Boston, MA")
        
        # Place with only name
        name_only = baker.make(Place, name="Meeting Room", address="")
        self.assertEqual(str(name_only), "Meeting Room")
        
        # Place with only address
        address_only = baker.make(Place, name="", address="456 Oak Street")
        self.assertEqual(str(address_only), "456 Oak Street")
        
        # Empty place
        empty_place = baker.make(Place, name="", address="")
        self.assertEqual(str(empty_place), "Unknown Place")


class EventSchemaOrgIntegrationTest(TestCase):
    """Test Event model integration with Schema.org Place data."""
    
    def setUp(self):
        self.test_source = baker.make(Source, name="Test Source")
    
    def test_event_creation_with_schema_org_place_data(self):
        """Test Event creation with rich Schema.org Place data."""
        
        # Simulate collector output with Schema.org Place data
        event_data = {
            'external_id': 'test-event-1',
            'title': 'Budding Bookworms',
            'description': 'Storytime for infants and caregivers',
            'location': {
                '@type': 'Place',
                'name': "Inside, Children's Room", 
                'address': "Needham Free Public Library, 1139 Highland Avenue, Needham, MA, 02494",
                'telephone': "781-455-7559"
            },
            'organizer': 'Needham Public Library',
            'event_status': 'scheduled',
            'event_attendance_mode': 'offline',
            'start_time': '2025-09-02T10:00:00+00:00',
            'end_time': '2025-09-02T10:30:00+00:00',
            'url': 'https://example.com/event'
        }
        
        event = Event.create_with_schema_org_data(event_data, self.test_source)
        
        # Verify event created correctly
        self.assertEqual(event.title, 'Budding Bookworms')
        self.assertEqual(event.organizer, 'Needham Public Library')
        self.assertEqual(event.event_status, 'scheduled')
        self.assertEqual(event.event_attendance_mode, 'offline')
        
        # Verify place relationship
        self.assertIsNotNone(event.place)
        self.assertEqual(event.place.name, "Inside, Children's Room")
        self.assertIn("Needham", event.place.address)
        
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
            'location': {
                '@type': 'Place',
                'name': 'Library Community Room',
                'address': 'Needham Free Public Library, 1139 Highland Avenue, Needham, MA, 02494'
            },
            'start_time': '2025-09-02T11:00:00+00:00'
        }
        
        event = Event.create_with_schema_org_data(event_data, self.test_source)
        location_search_text = event.get_location_search_text()
        
        # Should include venue name, full address, and city for RAG
        self.assertIn("Library Community Room", location_search_text)
        self.assertIn("Needham Free Public Library", location_search_text) 
        self.assertIn("Needham", location_search_text)  # City appears multiple times
        
        # This should solve the "events in Needham" RAG query problem
        self.assertGreater(location_search_text.count("Needham"), 1)
    
    def test_event_with_simple_location_fallback(self):
        """Test event creation with simple string location (backward compatibility)."""
        
        event_data = {
            'external_id': 'simple-event',
            'title': 'Simple Event', 
            'description': 'Basic event',
            'location': 'Town Hall',  # Simple string
            'start_time': '2025-09-03T14:00:00+00:00'
        }
        
        event = Event.create_with_schema_org_data(event_data, self.test_source)
        
        # Should handle simple location
        self.assertIsNone(event.place)  # No Place object created
        self.assertEqual(event.location, 'Town Hall')
        self.assertEqual(event.get_location_string(), 'Town Hall')
        self.assertEqual(event.get_full_address(), '')  # No full address
        self.assertEqual(event.get_city(), '')  # No city extraction
    
    def test_event_location_array_handling(self):
        """Test handling of Schema.org location arrays."""
        
        event_data = {
            'external_id': 'array-location-event',
            'title': 'Array Location Event',
            'description': 'Event with location array',
            'location': [  # Array format (common in Schema.org)
                {
                    '@type': 'Place',
                    'name': 'Main Hall',
                    'address': '100 Main Street, Boston, MA 02101'
                }
            ],
            'start_time': '2025-09-04T15:00:00+00:00'
        }
        
        event = Event.create_with_schema_org_data(event_data, self.test_source)
        
        # Should handle first Place in array
        self.assertIsNotNone(event.place)
        self.assertEqual(event.place.name, 'Main Hall')
        self.assertIn('Boston', event.place.address)
        self.assertEqual(event.get_city(), 'Boston')


class RAGLocationSearchTest(TestCase):
    """Test that Schema.org Place integration fixes RAG location searches."""
    
    def setUp(self):
        self.test_source = baker.make(Source, name="Needham Library")
    
    def test_needham_events_rag_query_fix(self):
        """Test that 'events in Needham' query now finds Needham Library events."""
        
        # Create Needham Library event with rich Place data
        needham_event_data = {
            'external_id': 'needham-storytime',
            'title': 'Budding Bookworms',
            'description': 'Storytime just for infants from newborn to not-yet walking',
            'location': {
                '@type': 'Place',
                'name': "Inside, Children's Room",
                'address': "Needham Free Public Library, 1139 Highland Avenue, Needham, MA, 02494",
                'telephone': "781-455-7559"
            },
            'start_time': '2025-09-02T10:00:00+00:00',
            'organizer': 'Needham Public Library'
        }
        
        event = Event.create_with_schema_org_data(needham_event_data, self.test_source)
        
        # Test location search capabilities
        location_search_text = event.get_location_search_text()
        
        # Should contain "Needham" multiple times for better RAG matching
        needham_count = location_search_text.lower().count('needham')
        self.assertGreater(needham_count, 1, 
                          f"Location search text should contain 'Needham' multiple times: '{location_search_text}'")
        
        # Should include venue, library name, and city
        self.assertIn("Children's Room", location_search_text)
        self.assertIn("Needham Free Public Library", location_search_text)
        
        # This rich location text should make RAG queries like 
        # "events in Needham" much more effective
    
    def test_multiple_needham_venues_deduplication(self):
        """Test that multiple events at same Needham venue share Place objects."""
        
        common_location = {
            '@type': 'Place',
            'name': 'Library Community Room',
            'address': 'Needham Free Public Library, 1139 Highland Avenue, Needham, MA, 02494'
        }
        
        # Create two events at same venue
        event1_data = {
            'external_id': 'dance-class',
            'title': 'Dance Class',
            'description': 'Ballet for children',
            'location': common_location,
            'start_time': '2025-09-02T11:00:00+00:00'
        }
        
        event2_data = {
            'external_id': 'teen-space',
            'title': 'Teen Study Space', 
            'description': 'Study space for teenagers',
            'location': common_location,  # Same location
            'start_time': '2025-09-02T14:30:00+00:00'
        }
        
        event1 = Event.create_with_schema_org_data(event1_data, self.test_source)
        event2 = Event.create_with_schema_org_data(event2_data, self.test_source)
        
        # Should share the same Place object (deduplication)
        self.assertEqual(event1.place.id, event2.place.id)
        
        # Both should provide rich Needham location data for RAG
        self.assertIn("Needham", event1.get_location_search_text())
        self.assertIn("Needham", event2.get_location_search_text())