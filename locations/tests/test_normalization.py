"""
Tests for location query normalization.
"""

from django.test import TestCase

from locations.models import normalize_for_matching
from locations.services import normalize_location_query, _normalize_city_name, _extract_state


class NormalizationTests(TestCase):
    """Test normalize_for_matching and normalize_location_query."""

    def test_normalize_for_matching_basic(self):
        """Test basic normalization."""
        self.assertEqual(normalize_for_matching("Newton"), "newton")
        self.assertEqual(normalize_for_matching("CAMBRIDGE"), "cambridge")
        self.assertEqual(normalize_for_matching("New York"), "new york")

    def test_normalize_for_matching_city_of_prefix(self):
        """Test removal of 'city of', 'town of' prefixes."""
        self.assertEqual(normalize_for_matching("City of Springfield"), "springfield")
        self.assertEqual(normalize_for_matching("Town of Brookline"), "brookline")
        self.assertEqual(normalize_for_matching("Village of Hempstead"), "hempstead")

    def test_normalize_for_matching_punctuation(self):
        """Test punctuation removal."""
        self.assertEqual(normalize_for_matching("St. Louis"), "st louis")
        self.assertEqual(normalize_for_matching("O'Fallon"), "ofallon")

    def test_normalize_for_matching_preserves_hyphens(self):
        """Test that hyphens are preserved in compound names."""
        self.assertEqual(normalize_for_matching("Winston-Salem"), "winston-salem")

    def test_normalize_for_matching_whitespace(self):
        """Test whitespace normalization."""
        self.assertEqual(normalize_for_matching("  Newton  "), "newton")
        self.assertEqual(normalize_for_matching("New   York"), "new york")

    def test_normalize_for_matching_empty(self):
        """Test empty/null inputs."""
        self.assertEqual(normalize_for_matching(""), "")
        self.assertEqual(normalize_for_matching(None), "")


class NormalizeLocationQueryTests(TestCase):
    """Test normalize_location_query function."""

    def test_city_comma_state_abbreviation(self):
        """Test 'City, ST' format."""
        city, state = normalize_location_query("Newton, MA")
        self.assertEqual(city, "newton")
        self.assertEqual(state, "MA")

    def test_city_comma_state_no_space(self):
        """Test 'City,ST' format (no space after comma)."""
        city, state = normalize_location_query("Cambridge,MA")
        self.assertEqual(city, "cambridge")
        self.assertEqual(state, "MA")

    def test_city_space_state_abbreviation(self):
        """Test 'City ST' format."""
        city, state = normalize_location_query("Boston MA")
        self.assertEqual(city, "boston")
        self.assertEqual(state, "MA")

    def test_city_full_state_name(self):
        """Test 'City StateName' format."""
        city, state = normalize_location_query("Springfield Massachusetts")
        self.assertEqual(city, "springfield")
        self.assertEqual(state, "MA")

    def test_city_full_state_name_with_comma(self):
        """Test 'City, StateName' format."""
        city, state = normalize_location_query("Springfield, Massachusetts")
        self.assertEqual(city, "springfield")
        self.assertEqual(state, "MA")

    def test_city_only(self):
        """Test city without state."""
        city, state = normalize_location_query("Newton")
        self.assertEqual(city, "newton")
        self.assertIsNone(state)

    def test_with_location_prefix(self):
        """Test queries with 'in', 'near', etc. prefixes."""
        city, state = normalize_location_query("events in Newton")
        self.assertEqual(city, "newton")
        self.assertIsNone(state)

        city, state = normalize_location_query("near Cambridge")
        self.assertEqual(city, "cambridge")
        self.assertIsNone(state)

        city, state = normalize_location_query("around Boston")
        self.assertEqual(city, "boston")
        self.assertIsNone(state)

    def test_city_of_prefix(self):
        """Test 'City of X' format."""
        city, state = normalize_location_query("City of Springfield")
        self.assertEqual(city, "springfield")
        self.assertIsNone(state)

    def test_case_insensitive_state(self):
        """Test case-insensitive state parsing."""
        city, state = normalize_location_query("Newton, ma")
        self.assertEqual(city, "newton")
        self.assertEqual(state, "MA")

        city, state = normalize_location_query("Boston massachusetts")
        self.assertEqual(city, "boston")
        self.assertEqual(state, "MA")

    def test_empty_input(self):
        """Test empty input."""
        city, state = normalize_location_query("")
        self.assertEqual(city, "")
        self.assertIsNone(state)

    def test_multiword_city_with_state(self):
        """Test multi-word city names."""
        city, state = normalize_location_query("New York, NY")
        self.assertEqual(city, "new york")
        self.assertEqual(state, "NY")

    def test_invalid_state_code_not_extracted(self):
        """Test that invalid state codes are not extracted."""
        # "XX" is not a valid state code
        city, state = normalize_location_query("Newton, XX")
        # Should not extract XX as state, and punctuation is removed
        self.assertEqual(city, "newton xx")
        self.assertIsNone(state)


class ExtractStateTests(TestCase):
    """Test _extract_state helper function."""

    def test_comma_state_format(self):
        """Test comma-separated format."""
        city, state = _extract_state("Newton, MA")
        self.assertEqual(city, "Newton")
        self.assertEqual(state, "MA")

    def test_space_state_format(self):
        """Test space-separated format."""
        city, state = _extract_state("Newton MA")
        self.assertEqual(city, "Newton")
        self.assertEqual(state, "MA")

    def test_full_state_name(self):
        """Test full state name format."""
        city, state = _extract_state("Newton Massachusetts")
        self.assertEqual(city, "Newton")
        self.assertEqual(state, "MA")

    def test_no_state(self):
        """Test when no state is present."""
        city, state = _extract_state("Newton")
        self.assertEqual(city, "Newton")
        self.assertIsNone(state)
