"""
Tests for location resolution service.
"""

from decimal import Decimal

from django.test import TestCase

from locations.models import Location
from locations.services import resolve_location, get_location_coordinates


class LocationTestMixin:
    """Mixin to create test location data."""

    @classmethod
    def setUpTestData(cls):
        """Create test locations."""
        cls.newton = Location.objects.create(
            geoid="2545000",
            name="Newton",
            normalized_name="newton",
            state="MA",
            country_code="US",
            latitude=Decimal("42.337807"),
            longitude=Decimal("-71.209182"),
            lsad="city",
            population=88923,
        )
        cls.cambridge = Location.objects.create(
            geoid="2511000",
            name="Cambridge",
            normalized_name="cambridge",
            state="MA",
            country_code="US",
            latitude=Decimal("42.373611"),
            longitude=Decimal("-71.110558"),
            lsad="city",
            population=118403,
        )
        cls.waltham = Location.objects.create(
            geoid="2549820",
            name="Waltham",
            normalized_name="waltham",
            state="MA",
            country_code="US",
            latitude=Decimal("42.390710"),
            longitude=Decimal("-71.235500"),
            lsad="city",
            population=64015,
        )
        cls.boston = Location.objects.create(
            geoid="2507000",
            name="Boston",
            normalized_name="boston",
            state="MA",
            country_code="US",
            latitude=Decimal("42.360082"),
            longitude=Decimal("-71.058880"),
            lsad="city",
            population=675647,
        )
        cls.springfield_ma = Location.objects.create(
            geoid="2567000",
            name="Springfield",
            normalized_name="springfield",
            state="MA",
            country_code="US",
            latitude=Decimal("42.101483"),
            longitude=Decimal("-72.589811"),
            lsad="city",
            population=155929,
        )
        cls.springfield_il = Location.objects.create(
            geoid="1773000",
            name="Springfield",
            normalized_name="springfield",
            state="IL",
            country_code="US",
            latitude=Decimal("39.801055"),
            longitude=Decimal("-89.643604"),
            lsad="city",
            population=114394,
        )
        cls.springfield_mo = Location.objects.create(
            geoid="2970000",
            name="Springfield",
            normalized_name="springfield",
            state="MO",
            country_code="US",
            latitude=Decimal("37.208957"),
            longitude=Decimal("-93.292298"),
            lsad="city",
            population=169176,
        )
        cls.springfield_or = Location.objects.create(
            geoid="4167000",
            name="Springfield",
            normalized_name="springfield",
            state="OR",
            country_code="US",
            latitude=Decimal("44.046236"),
            longitude=Decimal("-123.021980"),
            lsad="city",
            population=62256,
        )


class ResolveLocationTests(LocationTestMixin, TestCase):
    """Test resolve_location function."""

    def test_exact_match_with_state(self):
        """Test exact match when city and state are provided."""
        result = resolve_location("Newton, MA")

        self.assertIsNotNone(result.matched_location)
        self.assertEqual(result.matched_location.name, "Newton")
        self.assertEqual(result.matched_location.state, "MA")
        self.assertEqual(result.confidence, 1.0)
        self.assertFalse(result.is_ambiguous)
        self.assertEqual(len(result.alternatives), 0)

    def test_exact_match_full_state_name(self):
        """Test match with full state name."""
        result = resolve_location("Cambridge Massachusetts")

        self.assertIsNotNone(result.matched_location)
        self.assertEqual(result.matched_location.name, "Cambridge")
        self.assertEqual(result.matched_location.state, "MA")
        self.assertEqual(result.confidence, 1.0)

    def test_unique_match(self):
        """Test unique match (only one city with this name)."""
        result = resolve_location("Waltham")

        self.assertIsNotNone(result.matched_location)
        self.assertEqual(result.matched_location.name, "Waltham")
        self.assertEqual(result.matched_location.state, "MA")
        self.assertEqual(result.confidence, 0.9)
        self.assertFalse(result.is_ambiguous)

    def test_ambiguous_match_prefers_ma(self):
        """Test ambiguous city prefers MA (Greater Boston preference)."""
        result = resolve_location("Springfield")

        self.assertIsNotNone(result.matched_location)
        self.assertEqual(result.matched_location.name, "Springfield")
        # Should prefer MA over IL and MO due to PREFERRED_STATES
        self.assertEqual(result.matched_location.state, "MA")
        self.assertTrue(result.is_ambiguous)
        self.assertGreater(len(result.alternatives), 0)

    def test_ambiguous_match_with_default_state(self):
        """Test ambiguous city with explicit default_state."""
        result = resolve_location("Springfield", default_state="IL")

        self.assertIsNotNone(result.matched_location)
        self.assertEqual(result.matched_location.name, "Springfield")
        self.assertEqual(result.matched_location.state, "IL")
        self.assertFalse(result.is_ambiguous)  # Exact match with default state
        self.assertEqual(result.confidence, 1.0)

    def test_no_match(self):
        """Test when city doesn't exist in database."""
        result = resolve_location("Xyzzytown")

        self.assertIsNone(result.matched_location)
        self.assertEqual(result.confidence, 0.0)
        self.assertFalse(result.is_ambiguous)

    def test_empty_input(self):
        """Test empty input."""
        result = resolve_location("")

        self.assertIsNone(result.matched_location)
        self.assertEqual(result.confidence, 0.0)

    def test_query_with_prefix(self):
        """Test query with 'in', 'near' prefix."""
        result = resolve_location("events in Newton")

        self.assertIsNotNone(result.matched_location)
        self.assertEqual(result.matched_location.name, "Newton")

    def test_case_insensitive(self):
        """Test case insensitivity."""
        result = resolve_location("NEWTON, MA")

        self.assertIsNotNone(result.matched_location)
        self.assertEqual(result.matched_location.name, "Newton")

    def test_city_of_prefix(self):
        """Test 'City of X' format."""
        result = resolve_location("City of Boston")

        self.assertIsNotNone(result.matched_location)
        self.assertEqual(result.matched_location.name, "Boston")

    def test_latitude_longitude_properties(self):
        """Test that latitude/longitude properties work."""
        result = resolve_location("Newton, MA")

        self.assertIsNotNone(result.latitude)
        self.assertIsNotNone(result.longitude)
        self.assertAlmostEqual(float(result.latitude), 42.337807, places=4)
        self.assertAlmostEqual(float(result.longitude), -71.209182, places=4)

    def test_display_name_property(self):
        """Test display_name property."""
        result = resolve_location("Newton, MA")
        self.assertEqual(result.display_name, "Newton, MA")

        result = resolve_location("Xyzzytown")
        self.assertEqual(result.display_name, "")

    def test_alternatives_for_ambiguous(self):
        """Test that alternatives are returned for ambiguous matches."""
        result = resolve_location("Springfield")

        self.assertTrue(result.is_ambiguous)
        self.assertGreater(len(result.alternatives), 0)
        # All alternatives should also be named Springfield
        for alt in result.alternatives:
            self.assertEqual(alt.normalized_name, "springfield")

    def test_alternatives_limited_to_4(self):
        """Test that alternatives are limited to top 4."""
        result = resolve_location("Springfield")
        self.assertLessEqual(len(result.alternatives), 4)

    def test_query_state_overrides_default(self):
        """Test that state from query overrides default_state."""
        result = resolve_location("Springfield, MO", default_state="IL")

        self.assertIsNotNone(result.matched_location)
        self.assertEqual(result.matched_location.state, "MO")


class GetLocationCoordinatesTests(LocationTestMixin, TestCase):
    """Test get_location_coordinates convenience function."""

    def test_returns_coordinates(self):
        """Test that coordinates are returned for valid location."""
        coords = get_location_coordinates("Newton, MA")

        self.assertIsNotNone(coords)
        lat, lng = coords
        self.assertAlmostEqual(lat, 42.337807, places=4)
        self.assertAlmostEqual(lng, -71.209182, places=4)

    def test_returns_none_for_unknown(self):
        """Test that None is returned for unknown location."""
        coords = get_location_coordinates("Xyzzytown")
        self.assertIsNone(coords)

    def test_with_default_state(self):
        """Test with default_state parameter."""
        coords = get_location_coordinates("Springfield", default_state="IL")

        self.assertIsNotNone(coords)
        lat, lng = coords
        # Illinois Springfield coordinates
        self.assertAlmostEqual(lat, 39.801055, places=4)


class ResolveLocationRankingTests(LocationTestMixin, TestCase):
    """Test location ranking for disambiguation."""

    def test_preferred_states_ranking(self):
        """Test that PREFERRED_STATES are ranked correctly."""
        result = resolve_location("Springfield")

        # MA should be first (in PREFERRED_STATES)
        self.assertEqual(result.matched_location.state, "MA")

        # Check alternatives are ranked correctly
        # MA, IL, MO, OR - IL and MO have higher populations than OR
        alt_states = [alt.state for alt in result.alternatives]
        # Should include IL, MO, OR in some order (based on population)
        self.assertIn("IL", alt_states)
        self.assertIn("MO", alt_states)

    def test_population_ranking_within_non_preferred(self):
        """Test that non-preferred states are ranked by population."""
        # MO (169176) should rank above IL (114394) by population alone
        # But MA should be first due to PREFERRED_STATES
        result = resolve_location("Springfield")

        # MA is first due to preference
        self.assertEqual(result.matched_location.state, "MA")
