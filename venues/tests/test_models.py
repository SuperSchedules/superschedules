"""
Tests for Venue model.
"""

from django.core.exceptions import ValidationError
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


class VenueKindChoicesTests(TestCase):
    """Tests for venue_kind choices including civic/government types."""

    def test_civic_venue_kinds_exist(self):
        """Test that civic/government venue kinds are available."""
        kind_values = [choice[0] for choice in Venue.VENUE_KIND_CHOICES]

        self.assertIn("town_hall", kind_values)
        self.assertIn("city_hall", kind_values)
        self.assertIn("government_office", kind_values)

    def test_create_venue_with_civic_kind(self):
        """Test creating venues with civic/government venue kinds."""
        venue = Venue.objects.create(
            name="Newton City Hall",
            slug="newton-city-hall",
            city="Newton",
            state="MA",
            venue_kind="city_hall"
        )
        self.assertEqual(venue.venue_kind, "city_hall")

        venue2 = Venue.objects.create(
            name="Waltham Town Hall",
            slug="waltham-town-hall",
            city="Waltham",
            state="MA",
            venue_kind="town_hall"
        )
        self.assertEqual(venue2.venue_kind, "town_hall")


class VenueAudienceValidationTests(TestCase):
    """Tests for audience field validation via clean() method."""

    def test_rule_a_adult_only_rejects_kid_groups(self):
        """Test that min_age >= 18 rejects infant/toddler/child age groups."""
        venue = Venue(
            name="Adult Club",
            slug="adult-club",
            city="Boston",
            state="MA",
            audience_min_age=21,
            audience_age_groups=["adult", "child"]  # Invalid combo
        )

        with self.assertRaises(ValidationError) as ctx:
            venue.clean()

        self.assertIn("audience_age_groups", ctx.exception.message_dict)
        self.assertIn("18+", str(ctx.exception))

    def test_rule_a_adult_only_allows_adult_groups(self):
        """Test that min_age >= 18 allows adult/senior age groups."""
        venue = Venue(
            name="Adult Club",
            slug="adult-club",
            city="Boston",
            state="MA",
            audience_min_age=21,
            audience_age_groups=["adult", "senior"],
            audience_primary="adults"
        )

        # Should not raise
        venue.clean()

    def test_rule_b_children_primary_requires_kid_groups(self):
        """Test audience_primary='children' requires kid age groups."""
        venue = Venue(
            name="Kids Place",
            slug="kids-place",
            city="Boston",
            state="MA",
            audience_primary="children",
            audience_age_groups=["teen", "adult"]  # Missing kid groups
        )

        with self.assertRaises(ValidationError) as ctx:
            venue.clean()

        self.assertIn("audience_primary", ctx.exception.message_dict)

    def test_rule_b_children_primary_accepts_kid_groups(self):
        """Test audience_primary='children' works with kid age groups."""
        venue = Venue(
            name="Kids Place",
            slug="kids-place",
            city="Boston",
            state="MA",
            audience_primary="children",
            audience_age_groups=["infant", "toddler", "child"]
        )

        # Should not raise
        venue.clean()

    def test_rule_b_families_primary_requires_kid_groups(self):
        """Test audience_primary='families' requires kid age groups."""
        venue = Venue(
            name="Family Center",
            slug="family-center",
            city="Boston",
            state="MA",
            audience_primary="families",
            audience_age_groups=["adult"]  # Missing kid groups
        )

        with self.assertRaises(ValidationError) as ctx:
            venue.clean()

        self.assertIn("audience_primary", ctx.exception.message_dict)

    def test_rule_b_families_primary_accepts_kid_groups(self):
        """Test audience_primary='families' works with kid age groups."""
        venue = Venue(
            name="Family Center",
            slug="family-center",
            city="Boston",
            state="MA",
            audience_primary="families",
            audience_age_groups=["child", "adult"]
        )

        # Should not raise
        venue.clean()

    def test_rule_b_adults_primary_rejects_kid_groups(self):
        """Test audience_primary='adults' rejects kid age groups."""
        venue = Venue(
            name="Adult Venue",
            slug="adult-venue",
            city="Boston",
            state="MA",
            audience_primary="adults",
            audience_age_groups=["toddler", "adult"]  # Invalid combo
        )

        with self.assertRaises(ValidationError) as ctx:
            venue.clean()

        self.assertIn("audience_primary", ctx.exception.message_dict)

    def test_rule_b_adults_primary_accepts_adult_groups(self):
        """Test audience_primary='adults' works with adult/senior groups."""
        venue = Venue(
            name="Adult Venue",
            slug="adult-venue",
            city="Boston",
            state="MA",
            audience_primary="adults",
            audience_age_groups=["adult", "senior"]
        )

        # Should not raise
        venue.clean()

    def test_rule_c_auto_derive_families_from_kid_groups(self):
        """Test auto-derivation of 'families' from kid age groups."""
        venue = Venue(
            name="Library",
            slug="library",
            city="Boston",
            state="MA",
            audience_primary="general",
            audience_age_groups=["child", "teen", "adult"]
        )

        venue.clean()

        self.assertEqual(venue.audience_primary, "families")

    def test_rule_c_auto_derive_adults_from_adult_only_groups(self):
        """Test auto-derivation of 'adults' from adult-only groups."""
        venue = Venue(
            name="Bar",
            slug="bar",
            city="Boston",
            state="MA",
            audience_primary="general",
            audience_age_groups=["adult"]
        )

        venue.clean()

        self.assertEqual(venue.audience_primary, "adults")

    def test_rule_c_auto_derive_seniors_from_senior_only_groups(self):
        """Test auto-derivation of 'seniors' from senior-only groups."""
        venue = Venue(
            name="Senior Center",
            slug="senior-center",
            city="Boston",
            state="MA",
            audience_primary="general",
            audience_age_groups=["senior"]
        )

        venue.clean()

        self.assertEqual(venue.audience_primary, "seniors")

    def test_rule_c_no_change_when_not_general(self):
        """Test auto-derivation does NOT happen when primary is not 'general'."""
        venue = Venue(
            name="Teen Club",
            slug="teen-club",
            city="Boston",
            state="MA",
            audience_primary="teens",
            audience_age_groups=["teen", "adult"]
        )

        venue.clean()

        # Should remain 'teens', not change to something else
        self.assertEqual(venue.audience_primary, "teens")

    def test_empty_groups_passes_validation(self):
        """Test that empty age groups with general primary passes validation."""
        venue = Venue(
            name="General Venue",
            slug="general-venue",
            city="Boston",
            state="MA",
            audience_primary="general",
            audience_age_groups=[]
        )

        # Should not raise
        venue.clean()
        self.assertEqual(venue.audience_primary, "general")


class VenueSearchPropertiesTests(TestCase):
    """Tests for is_family_friendly and is_adults_only properties."""

    def test_is_family_friendly_true_for_families_primary(self):
        """Test is_family_friendly returns True for audience_primary='families'."""
        venue = Venue(
            name="Family Center",
            city="Boston",
            state="MA",
            audience_primary="families"
        )
        self.assertTrue(venue.is_family_friendly)

    def test_is_family_friendly_true_for_children_primary(self):
        """Test is_family_friendly returns True for audience_primary='children'."""
        venue = Venue(
            name="Kids Zone",
            city="Boston",
            state="MA",
            audience_primary="children"
        )
        self.assertTrue(venue.is_family_friendly)

    def test_is_family_friendly_true_for_kid_age_groups(self):
        """Test is_family_friendly returns True when age groups include kids."""
        for age_group in ["infant", "toddler", "child"]:
            with self.subTest(age_group=age_group):
                venue = Venue(
                    name="Test Venue",
                    city="Boston",
                    state="MA",
                    audience_primary="general",
                    audience_age_groups=[age_group, "adult"]
                )
                self.assertTrue(venue.is_family_friendly)

    def test_is_family_friendly_true_for_family_friendly_tag(self):
        """Test is_family_friendly returns True for 'family_friendly' tag."""
        venue = Venue(
            name="Restaurant",
            city="Boston",
            state="MA",
            audience_primary="general",
            audience_age_groups=["adult"],
            audience_tags=["family_friendly"]
        )
        self.assertTrue(venue.is_family_friendly)

    def test_is_family_friendly_false_for_adults_only(self):
        """Test is_family_friendly returns False for adults-only venues."""
        venue = Venue(
            name="Bar",
            city="Boston",
            state="MA",
            audience_primary="adults",
            audience_age_groups=["adult"]
        )
        self.assertFalse(venue.is_family_friendly)

    def test_is_family_friendly_false_for_general_without_kids(self):
        """Test is_family_friendly returns False for general venues without kid indicators."""
        venue = Venue(
            name="Office",
            city="Boston",
            state="MA",
            audience_primary="general",
            audience_age_groups=["adult", "senior"]
        )
        self.assertFalse(venue.is_family_friendly)

    def test_is_adults_only_true_for_adults_primary(self):
        """Test is_adults_only returns True for audience_primary='adults'."""
        venue = Venue(
            name="Nightclub",
            city="Boston",
            state="MA",
            audience_primary="adults"
        )
        self.assertTrue(venue.is_adults_only)

    def test_is_adults_only_true_for_min_age_18(self):
        """Test is_adults_only returns True for min_age >= 18."""
        venue = Venue(
            name="Casino",
            city="Boston",
            state="MA",
            audience_min_age=21
        )
        self.assertTrue(venue.is_adults_only)

    def test_is_adults_only_false_for_family_venue(self):
        """Test is_adults_only returns False for family venues."""
        venue = Venue(
            name="Library",
            city="Boston",
            state="MA",
            audience_primary="families",
            audience_age_groups=["child", "adult"]
        )
        self.assertFalse(venue.is_adults_only)

    def test_is_adults_only_false_for_general_venue(self):
        """Test is_adults_only returns False for general venues."""
        venue = Venue(
            name="Park",
            city="Boston",
            state="MA",
            audience_primary="general"
        )
        self.assertFalse(venue.is_adults_only)

    def test_is_adults_only_false_for_min_age_under_18(self):
        """Test is_adults_only returns False for min_age < 18."""
        venue = Venue(
            name="Teen Club",
            city="Boston",
            state="MA",
            audience_min_age=13,
            audience_primary="teens"
        )
        self.assertFalse(venue.is_adults_only)
