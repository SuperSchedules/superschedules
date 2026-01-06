"""
Tests for date extraction service.
"""

from datetime import datetime
from django.test import TestCase

from api.date_extraction import extract_dates_from_query, _is_false_positive


class TestExtractDatesFromQuery(TestCase):
    """Tests for extract_dates_from_query function."""

    def setUp(self):
        # Monday, January 5, 2026 at 2pm
        self.ref_date = datetime(2026, 1, 5, 14, 0, 0)

    def test_tomorrow(self):
        result = extract_dates_from_query("something to do tomorrow", self.ref_date)

        self.assertIsNotNone(result.date_from)
        self.assertEqual(result.date_from.date(), datetime(2026, 1, 6).date())
        self.assertEqual(result.date_to.date(), datetime(2026, 1, 6).date())
        self.assertIn('tomorrow', result.extracted_phrases)

    def test_today(self):
        result = extract_dates_from_query("what can we do today", self.ref_date)

        self.assertEqual(result.date_from.date(), datetime(2026, 1, 5).date())
        self.assertEqual(result.date_to.date(), datetime(2026, 1, 5).date())

    def test_this_weekend(self):
        result = extract_dates_from_query("activities this weekend", self.ref_date)

        # Weekend should be Saturday Jan 10 to Sunday Jan 11
        self.assertEqual(result.date_from.date(), datetime(2026, 1, 10).date())
        self.assertEqual(result.date_to.date(), datetime(2026, 1, 11).date())

    def test_next_saturday(self):
        result = extract_dates_from_query("events for next Saturday", self.ref_date)

        # "next Saturday" from Monday should be the Saturday after this one
        self.assertEqual(result.date_from.date(), datetime(2026, 1, 17).date())

    def test_this_friday(self):
        result = extract_dates_from_query("this Friday", self.ref_date)

        self.assertEqual(result.date_from.date(), datetime(2026, 1, 9).date())

    def test_multiple_dates_creates_range(self):
        result = extract_dates_from_query(
            "I'm taking the kids tomorrow but on Saturday we have other plans",
            self.ref_date
        )

        # Should create range from Tuesday (tomorrow) to Saturday
        self.assertEqual(result.date_from.date(), datetime(2026, 1, 6).date())
        self.assertEqual(result.date_to.date(), datetime(2026, 1, 10).date())

    def test_friday_through_sunday(self):
        result = extract_dates_from_query("Friday through Sunday", self.ref_date)

        self.assertEqual(result.date_from.date(), datetime(2026, 1, 9).date())
        self.assertEqual(result.date_to.date(), datetime(2026, 1, 11).date())

    def test_no_dates_in_query(self):
        result = extract_dates_from_query("activities for kids in Newton", self.ref_date)

        self.assertIsNone(result.date_from)
        self.assertIsNone(result.date_to)
        self.assertEqual(result.confidence, 0.0)

    def test_age_range_not_parsed_as_date(self):
        result = extract_dates_from_query(
            "activities for 3-5 year olds in Newton tomorrow",
            self.ref_date
        )

        # Should only find "tomorrow", not "3-5 year"
        self.assertEqual(len(result.extracted_phrases), 1)
        self.assertIn('tomorrow', result.extracted_phrases)
        self.assertNotIn('3-5 year', result.extracted_phrases)

    def test_age_not_parsed_as_date(self):
        result = extract_dates_from_query(
            "find something for my 3 year old tomorrow",
            self.ref_date
        )

        self.assertEqual(result.date_from.date(), datetime(2026, 1, 6).date())
        self.assertNotIn('3 year', result.extracted_phrases)

    def test_in_3_days(self):
        result = extract_dates_from_query("in 3 days", self.ref_date)

        self.assertEqual(result.date_from.date(), datetime(2026, 1, 8).date())

    def test_tonight(self):
        result = extract_dates_from_query("something to do tonight", self.ref_date)

        self.assertEqual(result.date_from.date(), datetime(2026, 1, 5).date())

    def test_confidence_higher_with_explicit_keywords(self):
        result_explicit = extract_dates_from_query("tomorrow", self.ref_date)
        result_vague = extract_dates_from_query("in 3 days", self.ref_date)

        self.assertGreater(result_explicit.confidence, result_vague.confidence)

    def test_yesterday_filtered_out(self):
        """Past dates should be filtered out for event discovery."""
        result = extract_dates_from_query("what happened yesterday", self.ref_date)

        # "yesterday" is in the past, so no dates should be extracted
        self.assertIsNone(result.date_from)
        self.assertIsNone(result.date_to)
        self.assertEqual(result.confidence, 0.0)

    def test_mixed_past_and_future_uses_future_only(self):
        """When query has both past and future dates, only future dates are used."""
        # Reference: Monday Jan 5
        # "yesterday" = Sunday Jan 4 (past, filtered)
        # "tomorrow" = Tuesday Jan 6 (future, kept)
        result = extract_dates_from_query("yesterday was fun but tomorrow I want activities", self.ref_date)

        self.assertIsNotNone(result.date_from)
        self.assertEqual(result.date_from.date(), datetime(2026, 1, 6).date())
        self.assertEqual(result.date_to.date(), datetime(2026, 1, 6).date())
        self.assertIn('tomorrow', result.extracted_phrases)


class TestIsFalsePositive(TestCase):
    """Tests for _is_false_positive function."""

    def test_age_range_is_false_positive(self):
        self.assertTrue(_is_false_positive("3-5 year", "activities for 3-5 year olds"))

    def test_age_is_false_positive(self):
        self.assertTrue(_is_false_positive("3 year", "my 3 year old"))

    def test_bare_number_is_false_positive(self):
        self.assertTrue(_is_false_positive("5", "activities for 5 year olds"))

    def test_day_name_not_false_positive(self):
        self.assertFalse(_is_false_positive("saturday", "events on saturday"))

    def test_tomorrow_not_false_positive(self):
        self.assertFalse(_is_false_positive("tomorrow", "something to do tomorrow"))

    def test_time_is_false_positive(self):
        """'time' alone is not a date (e.g., 'story time', 'lunch time')."""
        self.assertTrue(_is_false_positive("time", "story time for kids"))

    def test_common_words_are_false_positives(self):
        """Common English words shouldn't be parsed as dates."""
        self.assertTrue(_is_false_positive("do", "I want to do something"))
        self.assertTrue(_is_false_positive("to", "what to do tomorrow"))
        self.assertTrue(_is_false_positive("on", "events on Saturday"))
