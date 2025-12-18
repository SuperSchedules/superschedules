"""
Tests for venue extraction and normalization pipeline.
"""

from django.test import TestCase

from venues.extraction import (
    normalize_venue_data,
    extract_from_jsonld,
    extract_from_html,
    build_venue_key,
    get_or_create_venue,
    _clean_street_address,
)
from venues.models import Venue


class NormalizeVenueDataTests(TestCase):
    """Tests for the main normalize_venue_data orchestrator."""

    def test_high_confidence_location_data_used_directly(self):
        """High-confidence location_data from collector should be used directly."""
        location_data = {
            "venue_name": "Waltham Public Library",
            "room_name": "Waltham Room",
            "street_address": "735 Main Street",
            "city": "Waltham",
            "state": "MA",
            "postal_code": "02451",
            "country": "US",
            "latitude": 42.3765,
            "longitude": -71.2356,
            "extraction_confidence": 0.95
        }

        result = normalize_venue_data(location_data=location_data)

        self.assertEqual(result["venue_name"], "Waltham Public Library")
        self.assertEqual(result["room_name"], "Waltham Room")
        self.assertEqual(result["city"], "Waltham")
        self.assertEqual(result["state"], "MA")
        self.assertEqual(result["postal_code"], "02451")

    def test_low_confidence_falls_back_to_jsonld(self):
        """Low-confidence location_data should fall back to JSON-LD parsing."""
        location_data = {
            "venue_name": "Unknown Room",
            "extraction_confidence": 0.3
        }
        place_json = {
            "@type": "Place",
            "name": "Waltham Public Library",
            "address": {
                "@type": "PostalAddress",
                "streetAddress": "735 Main Street",
                "addressLocality": "Waltham",
                "addressRegion": "MA",
                "postalCode": "02451"
            }
        }

        result = normalize_venue_data(location_data=location_data, place_json=place_json)

        self.assertEqual(result["venue_name"], "Waltham Public Library")
        self.assertEqual(result["city"], "Waltham")

    def test_missing_location_data_uses_jsonld(self):
        """When location_data is None, should use JSON-LD."""
        place_json = {
            "@type": "Place",
            "name": "Newton Free Library",
            "address": "330 Homer Street, Newton, MA 02459"
        }

        result = normalize_venue_data(place_json=place_json)

        self.assertEqual(result["venue_name"], "Newton Free Library")

    def test_missing_jsonld_uses_html_parsing(self):
        """When both location_data and JSON-LD missing, parse raw_location."""
        result = normalize_venue_data(
            raw_location="Waltham Room Waltham Public Library 735 Main Street, Waltham, MA 02451"
        )

        self.assertIn("Waltham", result.get("city", ""))

    def test_confidence_threshold_at_boundary(self):
        """Test behavior at the 0.7 confidence threshold."""
        # At 0.7 should be accepted
        location_data_high = {
            "venue_name": "Library A",
            "city": "Boston",
            "state": "MA",
            "extraction_confidence": 0.7
        }
        result_high = normalize_venue_data(location_data=location_data_high)
        self.assertEqual(result_high["venue_name"], "Library A")

        # Below 0.7 should fall back
        location_data_low = {
            "venue_name": "Library B",
            "city": "Boston",
            "state": "MA",
            "extraction_confidence": 0.69
        }
        place_json = {
            "@type": "Place",
            "name": "Better Library Name"
        }
        result_low = normalize_venue_data(location_data=location_data_low, place_json=place_json)
        self.assertEqual(result_low["venue_name"], "Better Library Name")

    def test_empty_inputs_returns_empty_result(self):
        """When all inputs are empty, return empty normalized result."""
        result = normalize_venue_data()

        self.assertEqual(result.get("venue_name", ""), "")


class ExtractFromJsonldTests(TestCase):
    """Tests for JSON-LD Schema.org extraction."""

    def test_full_postal_address_object(self):
        """Extract from complete PostalAddress object."""
        json_ld = {
            "@type": "Place",
            "name": "Waltham Public Library",
            "address": {
                "@type": "PostalAddress",
                "streetAddress": "735 Main Street",
                "addressLocality": "Waltham",
                "addressRegion": "MA",
                "postalCode": "02451",
                "addressCountry": "US"
            },
            "geo": {
                "@type": "GeoCoordinates",
                "latitude": 42.3765,
                "longitude": -71.2356
            }
        }

        result = extract_from_jsonld(json_ld)

        self.assertEqual(result["venue_name"], "Waltham Public Library")
        self.assertEqual(result["street_address"], "735 Main Street")
        self.assertEqual(result["city"], "Waltham")
        self.assertEqual(result["state"], "MA")
        self.assertEqual(result["postal_code"], "02451")
        self.assertEqual(result["country"], "US")
        self.assertEqual(result["latitude"], 42.3765)
        self.assertEqual(result["longitude"], -71.2356)

    def test_string_address(self):
        """Extract from simple string address."""
        json_ld = {
            "@type": "Place",
            "name": "Newton Free Library",
            "address": "330 Homer Street, Newton, MA 02459"
        }

        result = extract_from_jsonld(json_ld)

        self.assertEqual(result["venue_name"], "Newton Free Library")
        # String address should be parsed
        self.assertEqual(result.get("city", ""), "Newton")

    def test_place_without_type(self):
        """Handle location data without @type field."""
        json_ld = {
            "name": "Community Center",
            "address": "123 Main St, Boston, MA 02101"
        }

        result = extract_from_jsonld(json_ld)

        # Should still extract what it can
        self.assertEqual(result["venue_name"], "Community Center")

    def test_nested_location_array(self):
        """Handle location as array of Place objects."""
        json_ld = [{
            "@type": "Place",
            "name": "First Venue",
            "address": "123 First St, Boston, MA"
        }]

        result = extract_from_jsonld(json_ld)

        self.assertEqual(result["venue_name"], "First Venue")

    def test_empty_jsonld(self):
        """Handle empty or None JSON-LD."""
        self.assertEqual(extract_from_jsonld(None), {})
        self.assertEqual(extract_from_jsonld({}), {})

    def test_postal_address_partial_fields(self):
        """Handle PostalAddress with some fields missing."""
        json_ld = {
            "@type": "Place",
            "name": "Community Hall",
            "address": {
                "@type": "PostalAddress",
                "addressLocality": "Cambridge",
                "addressRegion": "MA"
            }
        }

        result = extract_from_jsonld(json_ld)

        self.assertEqual(result["venue_name"], "Community Hall")
        self.assertEqual(result["city"], "Cambridge")
        self.assertEqual(result["state"], "MA")
        self.assertEqual(result.get("street_address", ""), "")


class ExtractFromHtmlTests(TestCase):
    """Tests for HTML/text heuristic parsing."""

    def test_waltham_library_example(self):
        """Parse the canonical Waltham example."""
        text = "Waltham Room Waltham Public Library 735 Main Street, Waltham, MA 02451"

        result = extract_from_html(text)

        self.assertIn("Library", result.get("venue_name", ""))
        self.assertEqual(result.get("room_name", ""), "Waltham Room")
        self.assertEqual(result.get("street_address", ""), "735 Main Street")
        self.assertEqual(result.get("city", ""), "Waltham")
        self.assertEqual(result.get("state", ""), "MA")
        self.assertEqual(result.get("postal_code", ""), "02451")

    def test_street_address_patterns(self):
        """Detect various street address formats."""
        test_cases = [
            ("123 Main Street, Boston, MA 02101", "123 Main Street"),
            ("456 Elm Ave, Cambridge, MA", "456 Elm Ave"),
            ("789 Oak Blvd Suite 100, Newton, MA", "789 Oak Blvd Suite 100"),
            ("1 Harvard Square, Cambridge, MA 02138", "1 Harvard Square"),
        ]

        for text, expected_street in test_cases:
            with self.subTest(text=text):
                result = extract_from_html(text)
                self.assertEqual(result.get("street_address", ""), expected_street)

    def test_city_state_zip_patterns(self):
        """Detect city, state, ZIP patterns."""
        test_cases = [
            ("Something, Boston, MA 02101", "Boston", "MA", "02101"),
            ("Location in Cambridge, MA", "Cambridge", "MA", ""),
            ("Venue, Newton, Massachusetts 02458", "Newton", "MA", "02458"),
        ]

        for text, expected_city, expected_state, expected_zip in test_cases:
            with self.subTest(text=text):
                result = extract_from_html(text)
                self.assertEqual(result.get("city", ""), expected_city)
                self.assertEqual(result.get("state", ""), expected_state)
                if expected_zip:
                    self.assertEqual(result.get("postal_code", ""), expected_zip)

    def test_venue_keywords(self):
        """Detect venue names with common keywords."""
        test_cases = [
            "Event at Newton Free Library",
            "Meeting at Community Center",
            "Concert at Symphony Hall",
            "Service at First Church",
            "Class at Lincoln School",
            "Exhibition at Art Museum",
        ]

        for text in test_cases:
            with self.subTest(text=text):
                result = extract_from_html(text)
                self.assertTrue(len(result.get("venue_name", "")) > 0, f"Failed to extract venue from: {text}")

    def test_room_patterns(self):
        """Detect room names."""
        test_cases = [
            ("Children's Room", "Children's Room"),
            ("Meeting Room A", "Meeting Room A"),
            ("Conference Room 101", "Conference Room 101"),
            ("Main Hall", "Main Hall"),
        ]

        for text, expected_room in test_cases:
            with self.subTest(text=text):
                result = extract_from_html(text)
                self.assertEqual(result.get("room_name", ""), expected_room)

    def test_no_address_detected(self):
        """Handle text with no detectable address."""
        result = extract_from_html("Just a random event description")

        self.assertEqual(result.get("venue_name", ""), "")
        self.assertEqual(result.get("street_address", ""), "")

    def test_room_only_input(self):
        """Handle input that's just a room name."""
        result = extract_from_html("Main Conference Room")

        self.assertEqual(result.get("room_name", ""), "Main Conference Room")
        self.assertEqual(result.get("venue_name", ""), "")


class BuildVenueKeyTests(TestCase):
    """Tests for venue deduplication key generation."""

    def test_basic_key_generation(self):
        """Generate key from normalized data."""
        normalized = {
            "venue_name": "Waltham Public Library",
            "city": "Waltham",
            "state": "MA",
            "postal_code": "02451"
        }

        key = build_venue_key(normalized)

        self.assertEqual(key, ("waltham-public-library", "waltham", "MA", "02451"))

    def test_case_normalization(self):
        """Key should normalize case correctly."""
        normalized = {
            "venue_name": "WALTHAM PUBLIC LIBRARY",
            "city": "WALTHAM",
            "state": "ma",
            "postal_code": "02451"
        }

        key = build_venue_key(normalized)

        self.assertEqual(key[0], "waltham-public-library")  # slug lowercase
        self.assertEqual(key[1], "waltham")  # city lowercase
        self.assertEqual(key[2], "MA")  # state uppercase
        self.assertEqual(key[3], "02451")

    def test_missing_postal_code(self):
        """Handle missing postal code."""
        normalized = {
            "venue_name": "Library",
            "city": "Boston",
            "state": "MA"
        }

        key = build_venue_key(normalized)

        self.assertEqual(key, ("library", "boston", "MA", ""))

    def test_special_characters_in_name(self):
        """Handle special characters in venue name."""
        normalized = {
            "venue_name": "St. John's Church & Community Center",
            "city": "Newton",
            "state": "MA",
            "postal_code": "02458"
        }

        key = build_venue_key(normalized)

        self.assertEqual(key[0], "st-johns-church-community-center")


class GetOrCreateVenueTests(TestCase):
    """Tests for venue get_or_create logic."""

    def test_creates_new_venue(self):
        """Create new venue when none exists."""
        normalized = {
            "venue_name": "Waltham Public Library",
            "street_address": "735 Main Street",
            "city": "Waltham",
            "state": "MA",
            "postal_code": "02451",
            "latitude": 42.3765,
            "longitude": -71.2356
        }

        venue, created = get_or_create_venue(normalized, "waltham.assabetinteractive.com")

        self.assertTrue(created)
        self.assertEqual(venue.name, "Waltham Public Library")
        self.assertEqual(venue.street_address, "735 Main Street")
        self.assertEqual(venue.city, "Waltham")
        self.assertEqual(venue.source_domain, "waltham.assabetinteractive.com")

    def test_returns_existing_venue(self):
        """Return existing venue on matching key."""
        normalized = {
            "venue_name": "Waltham Public Library",
            "city": "Waltham",
            "state": "MA",
            "postal_code": "02451"
        }

        venue1, created1 = get_or_create_venue(normalized, "domain1.com")
        venue2, created2 = get_or_create_venue(normalized, "domain2.com")

        self.assertTrue(created1)
        self.assertFalse(created2)
        self.assertEqual(venue1.id, venue2.id)

    def test_different_cities_creates_separate_venues(self):
        """Same name in different cities = different venues."""
        normalized1 = {
            "venue_name": "Public Library",
            "city": "Waltham",
            "state": "MA",
            "postal_code": "02451"
        }
        normalized2 = {
            "venue_name": "Public Library",
            "city": "Newton",
            "state": "MA",
            "postal_code": "02458"
        }

        venue1, _ = get_or_create_venue(normalized1, "domain.com")
        venue2, _ = get_or_create_venue(normalized2, "domain.com")

        self.assertNotEqual(venue1.id, venue2.id)

    def test_updates_missing_fields_on_existing(self):
        """Optionally update existing venue with new information."""
        # First create with minimal data
        normalized1 = {
            "venue_name": "Library",
            "city": "Boston",
            "state": "MA",
            "postal_code": "02101"
        }
        venue1, _ = get_or_create_venue(normalized1, "domain.com")

        # Try again with more data - should not update (immutable design)
        normalized2 = {
            "venue_name": "Library",
            "street_address": "123 Main St",
            "city": "Boston",
            "state": "MA",
            "postal_code": "02101"
        }
        venue2, created = get_or_create_venue(normalized2, "domain.com")

        self.assertFalse(created)
        self.assertEqual(venue1.id, venue2.id)

    def test_handles_empty_normalized_data(self):
        """Handle empty or minimal normalized data gracefully."""
        normalized = {}

        venue, created = get_or_create_venue(normalized, "domain.com")

        self.assertIsNone(venue)
        self.assertFalse(created)

    def test_stores_raw_schema_when_provided(self):
        """Store raw Schema.org data for future reference."""
        raw_schema = {"@type": "Place", "name": "Original Name"}
        normalized = {
            "venue_name": "Library",
            "city": "Boston",
            "state": "MA",
            "postal_code": "02101",
            "raw_schema": raw_schema
        }

        venue, _ = get_or_create_venue(normalized, "domain.com")

        self.assertEqual(venue.raw_schema, raw_schema)

    def test_truncates_long_venue_name(self):
        """Venue names longer than 200 chars should be truncated."""
        long_name = "A" * 250  # Exceeds max_length=200
        normalized = {
            "venue_name": long_name,
            "city": "Boston",
            "state": "MA",
            "postal_code": "02101"
        }

        venue, created = get_or_create_venue(normalized, "domain.com")

        self.assertTrue(created)
        self.assertEqual(len(venue.name), 200)
        self.assertEqual(venue.name, "A" * 200)

    def test_truncates_long_street_address(self):
        """Street addresses longer than 255 chars should be truncated."""
        long_address = "B" * 300  # Exceeds max_length=255
        normalized = {
            "venue_name": "Test Venue",
            "street_address": long_address,
            "city": "Boston",
            "state": "MA",
            "postal_code": "02101"
        }

        venue, created = get_or_create_venue(normalized, "domain.com")

        self.assertTrue(created)
        self.assertEqual(len(venue.street_address), 255)

    def test_truncates_long_source_domain(self):
        """Source domain longer than 255 chars should be truncated."""
        long_domain = "x" * 300 + ".com"  # Exceeds max_length=255

        normalized = {
            "venue_name": "Domain Test Venue",
            "city": "Cambridge",
            "state": "MA",
            "postal_code": "02139"
        }

        venue, created = get_or_create_venue(normalized, long_domain)

        self.assertTrue(created)
        self.assertEqual(len(venue.source_domain), 255)


class CleanStreetAddressTests(TestCase):
    """Tests for street address cleanup function."""

    def test_removes_city_state_zip_country_from_street(self):
        """Full address in street_address should be cleaned to just street."""
        street, postal = _clean_street_address(
            "205 Hartford Ave, Bellingham, MA 02019-3001, United States",
            "Bellingham",
            "MA",
            ""
        )
        self.assertEqual(street, "205 Hartford Ave")
        self.assertEqual(postal, "02019")

    def test_extracts_postal_code_when_missing(self):
        """Extract postal code from street_address if not provided."""
        street, postal = _clean_street_address(
            "123 Main St, Boston, MA 02101",
            "Boston",
            "MA",
            ""
        )
        self.assertEqual(street, "123 Main St")
        self.assertEqual(postal, "02101")

    def test_preserves_existing_postal_code(self):
        """Don't overwrite existing postal_code."""
        street, postal = _clean_street_address(
            "123 Main St, Boston, MA 02101",
            "Boston",
            "MA",
            "99999"  # Already have one
        )
        self.assertEqual(postal, "99999")

    def test_handles_zip_plus_four(self):
        """Strip +4 extension from ZIP code."""
        street, postal = _clean_street_address(
            "456 Oak Rd, Newton, MA 02458-1234, United States",
            "Newton",
            "MA",
            ""
        )
        self.assertEqual(street, "456 Oak Rd")
        self.assertEqual(postal, "02458")

    def test_handles_already_clean_address(self):
        """Clean address should pass through unchanged."""
        street, postal = _clean_street_address(
            "735 Main Street",
            "Waltham",
            "MA",
            "02451"
        )
        self.assertEqual(street, "735 Main Street")
        self.assertEqual(postal, "02451")

    def test_handles_empty_street_address(self):
        """Empty street_address returns empty string."""
        street, postal = _clean_street_address("", "Boston", "MA", "02101")
        self.assertEqual(street, "")
        self.assertEqual(postal, "02101")

    def test_removes_usa_variants(self):
        """Remove various USA/United States suffixes."""
        for suffix in [", United States", ", USA", ", US"]:
            street, _ = _clean_street_address(
                f"100 Test St, Boston, MA 02101{suffix}",
                "Boston",
                "MA",
                ""
            )
            self.assertEqual(street, "100 Test St")
