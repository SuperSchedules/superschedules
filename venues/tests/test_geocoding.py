"""Tests for venue geocoding service."""

from decimal import Decimal
from unittest.mock import patch, MagicMock
import threading
import time

from django.test import TestCase, override_settings

from venues.models import Venue
from venues.geocoding import geocode_venue, geocode_address, queue_geocoding


class TestGeocodeAddress(TestCase):
    """Tests for the geocode_address function."""

    @patch('venues.geocoding.Nominatim')
    def test_geocode_address_returns_coordinates(self, mock_nominatim_class):
        """Should return lat/long for valid address."""
        mock_geocoder = MagicMock()
        mock_location = MagicMock()
        mock_location.latitude = 42.3601
        mock_location.longitude = -71.0589
        mock_geocoder.geocode.return_value = mock_location
        mock_nominatim_class.return_value = mock_geocoder

        result = geocode_address("735 Main Street, Waltham, MA 02451")

        self.assertEqual(result, (Decimal('42.360100'), Decimal('-71.058900')))
        mock_geocoder.geocode.assert_called_once()

    @patch('venues.geocoding.Nominatim')
    def test_geocode_address_returns_none_for_not_found(self, mock_nominatim_class):
        """Should return None tuple when address not found."""
        mock_geocoder = MagicMock()
        mock_geocoder.geocode.return_value = None
        mock_nominatim_class.return_value = mock_geocoder

        result = geocode_address("Invalid Address That Does Not Exist")

        self.assertEqual(result, (None, None))

    @patch('venues.geocoding.Nominatim')
    def test_geocode_address_handles_exception(self, mock_nominatim_class):
        """Should return None tuple on geocoder exception."""
        mock_geocoder = MagicMock()
        mock_geocoder.geocode.side_effect = Exception("API Error")
        mock_nominatim_class.return_value = mock_geocoder

        result = geocode_address("735 Main Street, Waltham, MA")

        self.assertEqual(result, (None, None))


class TestGeocodeVenue(TestCase):
    """Tests for the geocode_venue function."""

    def setUp(self):
        self.venue = Venue.objects.create(
            name="Waltham Public Library",
            street_address="735 Main Street",
            city="Waltham",
            state="MA",
            postal_code="02451"
        )

    @patch('venues.geocoding.geocode_address')
    def test_geocode_venue_updates_coordinates(self, mock_geocode):
        """Should update venue with geocoded coordinates."""
        mock_geocode.return_value = (Decimal('42.360100'), Decimal('-71.058900'))

        result = geocode_venue(self.venue.id)

        self.venue.refresh_from_db()
        self.assertTrue(result)
        self.assertEqual(self.venue.latitude, Decimal('42.360100'))
        self.assertEqual(self.venue.longitude, Decimal('-71.058900'))

    @patch('venues.geocoding.geocode_address')
    def test_geocode_venue_skips_if_already_geocoded(self, mock_geocode):
        """Should not call geocoder if venue already has coordinates."""
        self.venue.latitude = Decimal('42.0')
        self.venue.longitude = Decimal('-71.0')
        self.venue.save()

        result = geocode_venue(self.venue.id)

        self.assertFalse(result)
        mock_geocode.assert_not_called()

    @patch('venues.geocoding.geocode_address')
    def test_geocode_venue_handles_missing_venue(self, mock_geocode):
        """Should return False for non-existent venue."""
        result = geocode_venue(99999)

        self.assertFalse(result)
        mock_geocode.assert_not_called()

    @patch('venues.geocoding.geocode_address')
    def test_geocode_venue_handles_geocode_failure(self, mock_geocode):
        """Should return False when geocoding fails."""
        mock_geocode.return_value = (None, None)

        result = geocode_venue(self.venue.id)

        self.venue.refresh_from_db()
        self.assertFalse(result)
        self.assertIsNone(self.venue.latitude)
        self.assertIsNone(self.venue.longitude)


class TestQueueGeocoding(TestCase):
    """Tests for async geocoding queue."""

    @patch('venues.geocoding.geocode_venue')
    @patch('venues.geocoding.GEOCODE_DELAY', 0.1)  # Short delay for testing
    def test_queue_geocoding_calls_geocode_after_delay(self, mock_geocode):
        """Should call geocode_venue after delay."""
        # Create venue with coordinates to skip signal trigger
        venue = Venue.objects.create(
            name="Test Library",
            city="Boston",
            state="MA",
            latitude=Decimal('42.0'),
            longitude=Decimal('-71.0')
        )
        # Clear coordinates to test geocoding
        Venue.objects.filter(id=venue.id).update(latitude=None, longitude=None)

        # Record call count before our queue call
        initial_call_count = mock_geocode.call_count

        queue_geocoding(venue.id)

        # Should not be called immediately (within the delay window)
        self.assertEqual(mock_geocode.call_count, initial_call_count)

        # Wait for delay + execution time
        time.sleep(0.3)

        # Verify our venue was called (check that call count increased and our venue is in calls)
        self.assertGreater(mock_geocode.call_count, initial_call_count)
        venue_ids_called = [call[0][0] for call in mock_geocode.call_args_list]
        self.assertIn(venue.id, venue_ids_called)

    @patch('venues.geocoding.geocode_venue')
    def test_queue_geocoding_runs_in_background(self, mock_geocode):
        """Should return immediately without blocking."""
        # Create venue with coordinates to skip signal trigger
        venue = Venue.objects.create(
            name="Test Library",
            city="Boston",
            state="MA",
            latitude=Decimal('42.0'),
            longitude=Decimal('-71.0')
        )

        start = time.time()
        queue_geocoding(venue.id)
        elapsed = time.time() - start

        # Should return almost immediately (less than 100ms)
        self.assertLess(elapsed, 0.1)


class TestVenueGeocodingSignal(TestCase):
    """Tests for automatic geocoding on venue save."""

    @patch('venues.geocoding.queue_geocoding')
    def test_new_venue_triggers_geocoding(self, mock_queue):
        """Should queue geocoding when new venue is created without coordinates."""
        venue = Venue.objects.create(
            name="Newton Free Library",
            street_address="330 Homer Street",
            city="Newton",
            state="MA",
            postal_code="02459"
        )

        mock_queue.assert_called_once_with(venue.id)

    @patch('venues.geocoding.queue_geocoding')
    def test_venue_with_coordinates_skips_geocoding(self, mock_queue):
        """Should not queue geocoding when venue has coordinates."""
        venue = Venue.objects.create(
            name="Boston Library",
            city="Boston",
            state="MA",
            latitude=Decimal('42.3601'),
            longitude=Decimal('-71.0589')
        )

        mock_queue.assert_not_called()

    @patch('venues.geocoding.queue_geocoding')
    def test_venue_update_does_not_retrigger_geocoding(self, mock_queue):
        """Should not re-geocode on subsequent saves."""
        venue = Venue.objects.create(
            name="Cambridge Library",
            city="Cambridge",
            state="MA"
        )
        mock_queue.reset_mock()

        # Update venue
        venue.street_address = "123 Main St"
        venue.save()

        # Should not queue again (already attempted or has coords)
        mock_queue.assert_not_called()
