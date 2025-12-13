"""
Tests for Venue model.
"""

from django.test import TestCase
from django.db import IntegrityError
from django.utils.text import slugify

from venues.models import Venue


class VenueModelTests(TestCase):
    """Tests for Venue model creation and constraints."""

    def test_venue_creation_all_fields(self):
        """Test creating a Venue with all fields populated."""
        venue = Venue.objects.create(
            name="Waltham Public Library",
            slug="waltham-public-library",
            street_address="735 Main Street",
            city="Waltham",
            state="MA",
            postal_code="02451",
            country="US",
            latitude=42.3765,
            longitude=-71.2356,
            source_domain="waltham.assabetinteractive.com",
            canonical_url="https://www.waltham.lib.ma.us",
            raw_schema={"@type": "Place", "name": "Waltham Public Library"}
        )

        self.assertEqual(venue.name, "Waltham Public Library")
        self.assertEqual(venue.slug, "waltham-public-library")
        self.assertEqual(venue.city, "Waltham")
        self.assertEqual(venue.state, "MA")
        self.assertEqual(venue.postal_code, "02451")
        self.assertEqual(venue.country, "US")
        self.assertIsNotNone(venue.created_at)
        self.assertIsNotNone(venue.updated_at)

    def test_venue_creation_minimal_fields(self):
        """Test creating a Venue with only required fields."""
        venue = Venue.objects.create(
            name="Newton Library",
            slug="newton-library",
            city="Newton",
            state="MA"
        )

        self.assertEqual(venue.name, "Newton Library")
        self.assertEqual(venue.city, "Newton")
        self.assertEqual(venue.country, "US")  # Default value
        self.assertEqual(venue.street_address, "")
        self.assertEqual(venue.postal_code, "")

    def test_venue_unique_constraint_same_key(self):
        """Test that venues with same (slug, city, state, postal_code) are rejected."""
        Venue.objects.create(
            name="Waltham Public Library",
            slug="waltham-public-library",
            city="Waltham",
            state="MA",
            postal_code="02451"
        )

        # Same dedupe key should fail
        with self.assertRaises(IntegrityError):
            Venue.objects.create(
                name="Waltham Public Library - Different Name",
                slug="waltham-public-library",  # Same slug
                city="Waltham",
                state="MA",
                postal_code="02451"
            )

    def test_venue_allows_different_postal_codes(self):
        """Test that same venue name in different locations is allowed."""
        venue1 = Venue.objects.create(
            name="Public Library",
            slug="public-library",
            city="Waltham",
            state="MA",
            postal_code="02451"
        )

        venue2 = Venue.objects.create(
            name="Public Library",
            slug="public-library",
            city="Newton",
            state="MA",
            postal_code="02458"
        )

        self.assertNotEqual(venue1.id, venue2.id)

    def test_venue_str_representation(self):
        """Test the string representation of a Venue."""
        venue = Venue.objects.create(
            name="Waltham Public Library",
            slug="waltham-public-library",
            city="Waltham",
            state="MA",
            postal_code="02451"
        )

        expected = "Waltham Public Library, Waltham, MA"
        self.assertEqual(str(venue), expected)

    def test_venue_str_with_full_address(self):
        """Test string representation includes address when available."""
        venue = Venue.objects.create(
            name="Waltham Public Library",
            slug="waltham-public-library",
            street_address="735 Main Street",
            city="Waltham",
            state="MA",
            postal_code="02451"
        )

        venue_str = str(venue)
        self.assertIn("Waltham Public Library", venue_str)
        self.assertIn("Waltham", venue_str)

    def test_venue_get_full_address(self):
        """Test the get_full_address method."""
        venue = Venue.objects.create(
            name="Waltham Public Library",
            slug="waltham-public-library",
            street_address="735 Main Street",
            city="Waltham",
            state="MA",
            postal_code="02451",
            country="US"
        )

        full_address = venue.get_full_address()
        self.assertEqual(full_address, "735 Main Street, Waltham, MA 02451")

    def test_venue_get_full_address_without_street(self):
        """Test get_full_address when street is missing."""
        venue = Venue.objects.create(
            name="Waltham Public Library",
            slug="waltham-public-library",
            city="Waltham",
            state="MA",
            postal_code="02451"
        )

        full_address = venue.get_full_address()
        self.assertEqual(full_address, "Waltham, MA 02451")


class VenueSlugTests(TestCase):
    """Tests for Venue slug generation."""

    def test_generate_slug_from_name(self):
        """Test that slugify works correctly for venue names."""
        test_cases = [
            ("Waltham Public Library", "waltham-public-library"),
            ("Newton Free Library", "newton-free-library"),
            ("St. John's Church", "st-johns-church"),
            ("YMCA Community Center", "ymca-community-center"),
        ]

        for name, expected_slug in test_cases:
            with self.subTest(name=name):
                self.assertEqual(slugify(name), expected_slug)


class VenueDeduplicationTests(TestCase):
    """Tests for venue deduplication logic."""

    def test_build_venue_key(self):
        """Test building the venue deduplication key."""
        from venues.extraction import build_venue_key

        normalized = {
            "venue_name": "Waltham Public Library",
            "city": "Waltham",
            "state": "MA",
            "postal_code": "02451"
        }

        key = build_venue_key(normalized)

        self.assertEqual(key, ("waltham-public-library", "waltham", "MA", "02451"))

    def test_build_venue_key_normalizes_case(self):
        """Test that venue key normalizes case correctly."""
        from venues.extraction import build_venue_key

        normalized = {
            "venue_name": "WALTHAM PUBLIC LIBRARY",
            "city": "WALTHAM",
            "state": "ma",
            "postal_code": "02451"
        }

        key = build_venue_key(normalized)

        # slug is lowercase, city is lowercase, state is uppercase
        self.assertEqual(key[0], "waltham-public-library")
        self.assertEqual(key[1], "waltham")
        self.assertEqual(key[2], "MA")

    def test_get_or_create_venue_creates_new(self):
        """Test that get_or_create_venue creates a new venue when none exists."""
        from venues.extraction import get_or_create_venue

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
        self.assertEqual(venue.city, "Waltham")
        self.assertEqual(venue.source_domain, "waltham.assabetinteractive.com")

    def test_get_or_create_venue_returns_existing(self):
        """Test that get_or_create_venue returns existing venue on match."""
        from venues.extraction import get_or_create_venue

        # Create first venue
        normalized = {
            "venue_name": "Waltham Public Library",
            "city": "Waltham",
            "state": "MA",
            "postal_code": "02451"
        }

        venue1, created1 = get_or_create_venue(normalized, "waltham.assabetinteractive.com")
        self.assertTrue(created1)

        # Try to create again with same key
        venue2, created2 = get_or_create_venue(normalized, "different-domain.com")
        self.assertFalse(created2)
        self.assertEqual(venue1.id, venue2.id)
