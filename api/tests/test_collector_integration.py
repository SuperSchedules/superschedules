from datetime import datetime, timezone as dt_timezone
from unittest.mock import Mock, patch
from django.test import TestCase
from django.utils import timezone
from model_bakery import baker
import requests

from api import views as api_views
from events.models import Event, Source, SiteStrategy


class TriggerCollectionTests(TestCase):
    """Test the _trigger_collection function that integrates with the collector API."""

    def setUp(self):
        self.strategy = baker.make(SiteStrategy, domain="example.com", best_selectors=["article", ".event"])
        self.source = baker.make(Source, base_url="https://example.com/events", site_strategy=self.strategy,
                                status=Source.Status.NOT_RUN)

    @patch('api.views.requests.post')
    def test_successful_collection_with_events(self, mock_post):
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'success': True,
            'events': [
                {
                    'external_id': 'evt_001',
                    'title': 'Summer Concert',
                    'description': 'Outdoor music event',
                    'location': 'Central Park',
                    'start_time': '2024-07-15T18:00:00Z',
                    'end_time': '2024-07-15T20:00:00Z',
                    'url': 'https://example.com/events/001',
                    'tags': ['music', 'outdoor'],
                    'organizer': 'City Events',
                    'event_status': 'scheduled',
                    'event_attendance_mode': 'offline'
                },
                {
                    'external_id': 'evt_002',
                    'title': 'Art Workshop',
                    'description': 'Learn watercolor painting',
                    'location': 'Community Center',
                    'start_time': '2024-07-20T14:00:00Z',
                    'tags': ['art', 'workshop']
                }
            ]
        }
        mock_post.return_value = mock_response

        api_views._trigger_collection(self.source)

        # Verify API was called with correct parameters
        mock_post.assert_called_once()
        call_args = mock_post.call_args
        assert call_args[0][0] == 'http://localhost:8001/extract'
        assert call_args[1]['json']['url'] == self.source.base_url
        assert call_args[1]['json']['extraction_hints']['content_selectors'] == ["article", ".event"]
        assert call_args[1]['timeout'] == 180

        # Verify events were created
        assert Event.objects.count() == 2
        event1 = Event.objects.get(external_id='evt_001')
        assert event1.title == 'Summer Concert'
        assert event1.source == self.source
        assert event1.metadata_tags == ['music', 'outdoor']
        assert event1.organizer == 'City Events'

        event2 = Event.objects.get(external_id='evt_002')
        assert event2.title == 'Art Workshop'
        assert event2.metadata_tags == ['art', 'workshop']

        # Verify source status updated
        self.source.refresh_from_db()
        assert self.source.status == Source.Status.PROCESSED
        assert self.source.last_run_at is not None

    @patch('api.views.requests.post')
    def test_successful_collection_no_events(self, mock_post):
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {'success': True, 'events': []}
        mock_post.return_value = mock_response

        api_views._trigger_collection(self.source)

        # Verify no events created
        assert Event.objects.count() == 0

        # Verify source still marked as processed
        self.source.refresh_from_db()
        assert self.source.status == Source.Status.PROCESSED
        assert self.source.last_run_at is not None

    @patch('api.views.requests.post')
    def test_api_500_error(self, mock_post):
        mock_response = Mock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"
        mock_post.return_value = mock_response

        api_views._trigger_collection(self.source)

        # Verify no events created
        assert Event.objects.count() == 0

        # Verify source status NOT updated on error
        self.source.refresh_from_db()
        assert self.source.status == Source.Status.NOT_RUN
        assert self.source.last_run_at is None

    @patch('api.views.requests.post')
    def test_api_404_error(self, mock_post):
        mock_response = Mock()
        mock_response.status_code = 404
        mock_response.text = "Not Found"
        mock_post.return_value = mock_response

        api_views._trigger_collection(self.source)

        assert Event.objects.count() == 0
        self.source.refresh_from_db()
        assert self.source.status == Source.Status.NOT_RUN

    @patch('api.views.requests.post')
    def test_connection_timeout(self, mock_post):
        mock_post.side_effect = requests.exceptions.Timeout("Connection timed out")

        api_views._trigger_collection(self.source)

        assert Event.objects.count() == 0
        self.source.refresh_from_db()
        assert self.source.status == Source.Status.NOT_RUN

    @patch('api.views.requests.post')
    def test_connection_error(self, mock_post):
        mock_post.side_effect = requests.exceptions.ConnectionError("Failed to connect")

        api_views._trigger_collection(self.source)

        assert Event.objects.count() == 0
        self.source.refresh_from_db()
        assert self.source.status == Source.Status.NOT_RUN

    @patch('api.views.requests.post')
    def test_schema_org_place_data(self, mock_post):
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'success': True,
            'events': [
                {
                    'external_id': 'evt_place_001',
                    'title': 'Library Event',
                    'description': 'Story time for kids',
                    'location': {
                        '@type': 'Place',
                        'name': 'Newton Public Library',
                        'address': '330 Homer St, Newton, MA 02459',
                        'telephone': '617-555-0123'
                    },
                    'start_time': '2024-07-25T10:00:00Z',
                }
            ]
        }
        mock_post.return_value = mock_response

        api_views._trigger_collection(self.source)

        # Verify event created with Place data
        assert Event.objects.count() == 1
        event = Event.objects.first()
        assert event.title == 'Library Event'
        assert event.place is not None
        assert event.place.name == 'Newton Public Library'
        assert event.place.address == '330 Homer St, Newton, MA 02459'
        assert event.place.telephone == '617-555-0123'

    @patch('api.views.requests.post')
    def test_source_without_strategy(self, mock_post):
        source_no_strategy = baker.make(Source, base_url="https://newsite.com/events", site_strategy=None,
                                       status=Source.Status.NOT_RUN)
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'success': True,
            'events': [
                {
                    'external_id': 'evt_003',
                    'title': 'Test Event',
                    'description': 'Description',
                    'location': 'Somewhere',
                    'start_time': '2024-08-01T12:00:00Z',
                }
            ]
        }
        mock_post.return_value = mock_response

        api_views._trigger_collection(source_no_strategy)

        # Verify API called with None for selectors
        call_args = mock_post.call_args
        assert call_args[1]['json']['extraction_hints']['content_selectors'] is None

        # Verify event still created
        assert Event.objects.count() == 1

    @patch('api.views.requests.post')
    def test_partial_event_failure(self, mock_post):
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'success': True,
            'events': [
                {
                    'external_id': 'evt_good',
                    'title': 'Valid Event',
                    'description': 'This one works',
                    'location': 'Park',
                    'start_time': '2024-07-15T18:00:00Z',
                },
                {
                    'external_id': 'evt_bad',
                    'title': 'Invalid Event',
                    'description': 'Bad date format',
                    'location': 'Nowhere',
                    'start_time': 'not-a-valid-date',  # This will cause an error
                },
                {
                    'external_id': 'evt_also_good',
                    'title': 'Another Valid Event',
                    'description': 'This works too',
                    'location': 'Beach',
                    'start_time': '2024-07-16T10:00:00Z',
                }
            ]
        }
        mock_post.return_value = mock_response

        api_views._trigger_collection(self.source)

        # Verify only valid events created (2 out of 3)
        assert Event.objects.count() == 2
        assert Event.objects.filter(external_id='evt_good').exists()
        assert Event.objects.filter(external_id='evt_also_good').exists()
        assert not Event.objects.filter(external_id='evt_bad').exists()

        # Source should still be marked as processed
        self.source.refresh_from_db()
        assert self.source.status == Source.Status.PROCESSED

    @patch('api.views.requests.post')
    def test_custom_collector_url(self, mock_post):
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {'success': True, 'events': []}
        mock_post.return_value = mock_response

        with self.settings(COLLECTOR_URL='https://custom-collector.example.com'):
            api_views._trigger_collection(self.source)

        # Verify custom URL was used
        call_args = mock_post.call_args
        assert call_args[0][0] == 'https://custom-collector.example.com/extract'