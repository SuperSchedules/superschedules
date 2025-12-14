"""
Tests for job queue management endpoints.
"""
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.utils import timezone
from datetime import timedelta
from unittest.mock import Mock
from ninja.testing import TestClient
from ninja_jwt.tokens import AccessToken
from model_bakery import baker

from api.views import router
from events.models import Source, ScrapingJob, ServiceToken

User = get_user_model()


class QueueEndpointsTests(TestCase):
    """Test queue management endpoints."""

    def setUp(self):
        """Set up test fixtures."""
        self.client = TestClient(router)
        self.user = baker.make(User, username="testuser@example.com")
        self.jwt_token = str(AccessToken.for_user(self.user))
        self.service_token = baker.make(ServiceToken, name="Test Worker Token")

    def _create_auth_request(self, user=None):
        """Create a mock request with authenticated user."""
        request = Mock()
        request.user = user or self.user
        return request

    def _create_service_token_request(self):
        """Create a mock request with service token auth."""
        request = Mock()
        request.auth = self.service_token
        return request

    def test_submit_url_to_queue(self):
        """Test submitting a URL to the queue."""
        response = self.client.post(
            '/queue/submit',
            json={'url': 'https://example.com/events'},
            headers={'Authorization': f'Bearer {self.jwt_token}'}
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()

        self.assertEqual(data['url'], 'https://example.com/events')
        self.assertEqual(data['status'], 'pending')
        self.assertEqual(data['priority'], 5)
        self.assertEqual(data['domain'], 'example.com')

        # Verify job was created in database
        job = ScrapingJob.objects.get(id=data['id'])
        self.assertEqual(job.status, 'pending')
        self.assertEqual(job.submitted_by, self.user)

    def test_submit_requires_auth(self):
        """Test that submit endpoint requires authentication."""
        response = self.client.post(
            '/queue/submit',
            json={'url': 'https://example.com/events'}
        )

        self.assertEqual(response.status_code, 401)

    def test_get_next_job_atomic_claim(self):
        """Test getting next job with atomic claim."""
        # Create a pending job
        job = ScrapingJob.objects.create(
            url='https://example.com/events',
            domain='example.com',
            status='pending',
            submitted_by=self.user,
            priority=5
        )

        # Worker claims job
        response = self.client.get(
            '/queue/next?worker_id=test-worker-1',
            headers={'Authorization': f'Bearer {self.service_token.token}'}
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()

        self.assertEqual(data['id'], job.id)
        self.assertEqual(data['status'], 'processing')
        self.assertEqual(data['locked_by'], 'test-worker-1')

        # Verify job was claimed in database
        job.refresh_from_db()
        self.assertEqual(job.status, 'processing')
        self.assertEqual(job.locked_by, 'test-worker-1')
        self.assertIsNotNone(job.locked_at)

    def test_get_next_job_priority_ordering(self):
        """Test that jobs are returned in priority order."""
        # Create jobs with different priorities
        job_low = ScrapingJob.objects.create(
            url='https://example.com/low',
            domain='example.com',
            status='pending',
            submitted_by=self.user,
            priority=10
        )
        job_high = ScrapingJob.objects.create(
            url='https://example.com/high',
            domain='example.com',
            status='pending',
            submitted_by=self.user,
            priority=1
        )
        job_med = ScrapingJob.objects.create(
            url='https://example.com/med',
            domain='example.com',
            status='pending',
            submitted_by=self.user,
            priority=5
        )

        # Should get highest priority (lowest number)
        response = self.client.get(
            '/queue/next?worker_id=test-worker-1',
            headers={'Authorization': f'Bearer {self.service_token.token}'}
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['id'], job_high.id)

    def test_get_next_job_empty_queue(self):
        """Test getting next job when queue is empty."""
        response = self.client.get(
            '/queue/next?worker_id=test-worker-1',
            headers={'Authorization': f'Bearer {self.service_token.token}'}
        )

        self.assertEqual(response.status_code, 404)

    def test_get_next_job_requires_service_token(self):
        """Test that get next job requires service token."""
        ScrapingJob.objects.create(
            url='https://example.com/events',
            domain='example.com',
            status='pending',
            submitted_by=self.user
        )

        # Try with JWT token (should fail)
        response = self.client.get(
            '/queue/next?worker_id=test-worker-1',
            headers={'Authorization': f'Bearer {self.jwt_token}'}
        )

        self.assertEqual(response.status_code, 401)

    def test_complete_job_success(self):
        """Test completing a job successfully."""
        job = ScrapingJob.objects.create(
            url='https://example.com/events',
            domain='example.com',
            status='processing',
            submitted_by=self.user,
            locked_by='test-worker-1'
        )

        response = self.client.post(
            f'/queue/{job.id}/complete',
            json={
                'success': True,
                'events': [
                    {
                        'external_id': 'evt-123',
                        'title': 'Test Event',
                        'description': 'Test Description',
                        'start_time': '2025-01-01T10:00:00Z',
                        'end_time': '2025-01-01T12:00:00Z',
                        'url': 'https://example.com/event/123',
                        'metadata_tags': ['test']
                    }
                ],
                'events_found': 1,
                'pages_processed': 1,
                'processing_time': 2.5
            },
            headers={'Authorization': f'Bearer {self.service_token.token}'}
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data['created_event_ids']), 1)

        # Verify job was completed
        job.refresh_from_db()
        self.assertEqual(job.status, 'completed')
        self.assertEqual(job.events_found, 1)
        self.assertEqual(job.processing_time, 2.5)
        self.assertIsNotNone(job.completed_at)

    def test_complete_job_failure(self):
        """Test completing a job with failure."""
        job = ScrapingJob.objects.create(
            url='https://example.com/events',
            domain='example.com',
            status='processing',
            submitted_by=self.user
        )

        response = self.client.post(
            f'/queue/{job.id}/complete',
            json={
                'success': False,
                'events': [],
                'events_found': 0,
                'pages_processed': 1,
                'processing_time': 1.0,
                'error_message': 'Connection timeout'
            },
            headers={'Authorization': f'Bearer {self.service_token.token}'}
        )

        self.assertEqual(response.status_code, 200)

        # Verify job was marked failed
        job.refresh_from_db()
        self.assertEqual(job.status, 'failed')
        self.assertEqual(job.error_message, 'Connection timeout')
        self.assertEqual(job.events_found, 0)

    def test_queue_status(self):
        """Test queue status endpoint."""
        # Create jobs with different statuses
        ScrapingJob.objects.create(
            url='https://example.com/1',
            domain='example.com',
            status='pending',
            submitted_by=self.user
        )
        ScrapingJob.objects.create(
            url='https://example.com/2',
            domain='example.com',
            status='pending',
            submitted_by=self.user
        )
        ScrapingJob.objects.create(
            url='https://example.com/3',
            domain='example.com',
            status='processing',
            submitted_by=self.user
        )
        ScrapingJob.objects.create(
            url='https://example.com/4',
            domain='example.com',
            status='completed',
            submitted_by=self.user,
            completed_at=timezone.now()
        )
        ScrapingJob.objects.create(
            url='https://example.com/5',
            domain='example.com',
            status='failed',
            submitted_by=self.user,
            completed_at=timezone.now() - timedelta(days=2)  # Old failure
        )

        response = self.client.get(
            '/queue/status',
            headers={'Authorization': f'Bearer {self.jwt_token}'}
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()

        self.assertEqual(data['queue_depth'], 2)
        self.assertEqual(data['processing'], 1)
        self.assertEqual(data['completed_24h'], 1)
        self.assertEqual(data['failed_24h'], 0)  # Old failure not counted

    def test_bulk_submit_urls(self):
        """Test bulk URL submission."""
        urls = [
            'https://example.com/events1',
            'https://example.com/events2',
            'https://example.com/events3'
        ]

        response = self.client.post(
            '/queue/bulk-submit',
            json={'urls': urls},
            headers={'Authorization': f'Bearer {self.jwt_token}'}
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()

        self.assertEqual(data['submitted'], 3)
        self.assertEqual(len(data['job_ids']), 3)

        # Verify all jobs have lower priority (bulk)
        for job_id in data['job_ids']:
            job = ScrapingJob.objects.get(id=job_id)
            self.assertEqual(job.status, 'pending')
            self.assertEqual(job.priority, 7)  # Bulk priority

    def test_source_created_on_submit(self):
        """Test that Source is created when submitting URL."""
        self.assertEqual(Source.objects.count(), 0)

        response = self.client.post(
            '/queue/submit',
            json={'url': 'https://example.com/events'},
            headers={'Authorization': f'Bearer {self.jwt_token}'}
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Source.objects.count(), 1)

        source = Source.objects.first()
        self.assertEqual(source.base_url, 'https://example.com/events')
        self.assertEqual(source.user, self.user)

    def test_concurrent_job_claims(self):
        """Test that multiple workers can't claim the same job."""
        job = ScrapingJob.objects.create(
            url='https://example.com/events',
            domain='example.com',
            status='pending',
            submitted_by=self.user
        )

        # Worker 1 claims job
        response1 = self.client.get(
            '/queue/next?worker_id=worker-1',
            headers={'Authorization': f'Bearer {self.service_token.token}'}
        )
        self.assertEqual(response1.status_code, 200)
        self.assertEqual(response1.json()['id'], job.id)

        # Worker 2 tries to claim (should get 404 - no jobs available)
        response2 = self.client.get(
            '/queue/next?worker_id=worker-2',
            headers={'Authorization': f'Bearer {self.service_token.token}'}
        )
        self.assertEqual(response2.status_code, 404)

    def test_scrape_endpoint_creates_job(self):
        """Test that /scrape endpoint creates a new job with queue fields."""
        self.assertEqual(ScrapingJob.objects.count(), 0)

        response = self.client.post(
            '/scrape',
            json={'url': 'https://example.com/events'},
            headers={'Authorization': f'Bearer {self.jwt_token}'}
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()

        # Verify job was created
        self.assertEqual(ScrapingJob.objects.count(), 1)
        job = ScrapingJob.objects.first()

        self.assertEqual(job.url, 'https://example.com/events')
        self.assertEqual(job.status, 'pending')
        self.assertEqual(job.priority, 5)
        self.assertEqual(job.submitted_by, self.user)
        self.assertIsNotNone(job.source)

    def test_scrape_endpoint_returns_existing_pending_job(self):
        """Test that submitting same URL returns existing pending job."""
        # Create existing pending job
        existing_job = baker.make(
            ScrapingJob,
            url='https://example.com/events',
            domain='example.com',
            status='pending',
            submitted_by=self.user,
            priority=5
        )

        response = self.client.post(
            '/scrape',
            json={'url': 'https://example.com/events'},
            headers={'Authorization': f'Bearer {self.jwt_token}'}
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()

        # Should return existing job, not create new one
        self.assertEqual(data['id'], existing_job.id)
        self.assertEqual(ScrapingJob.objects.count(), 1)

    def test_scrape_endpoint_returns_existing_processing_job(self):
        """Test that submitting same URL returns existing processing job."""
        # Create existing processing job
        existing_job = baker.make(
            ScrapingJob,
            url='https://example.com/events',
            domain='example.com',
            status='processing',
            submitted_by=self.user,
            locked_by='worker-1'
        )

        response = self.client.post(
            '/scrape',
            json={'url': 'https://example.com/events'},
            headers={'Authorization': f'Bearer {self.jwt_token}'}
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()

        # Should return existing job
        self.assertEqual(data['id'], existing_job.id)
        self.assertEqual(ScrapingJob.objects.count(), 1)

    def test_scrape_endpoint_returns_recent_success(self):
        """Test that submitting recently completed URL returns that job."""
        # Create recently completed job (within 24 hours)
        recent_job = baker.make(
            ScrapingJob,
            url='https://example.com/events',
            domain='example.com',
            status='completed',
            submitted_by=self.user,
            completed_at=timezone.now() - timedelta(hours=12)
        )

        response = self.client.post(
            '/scrape',
            json={'url': 'https://example.com/events'},
            headers={'Authorization': f'Bearer {self.jwt_token}'}
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()

        # Should return recent job
        self.assertEqual(data['id'], recent_job.id)
        self.assertEqual(ScrapingJob.objects.count(), 1)

    def test_scrape_endpoint_creates_new_after_14_days(self):
        """Test that submitting URL after 14 days creates new job."""
        # Create old completed job (more than 14 days ago)
        old_job = baker.make(
            ScrapingJob,
            url='https://example.com/events',
            domain='example.com',
            status='completed',
            submitted_by=self.user,
            completed_at=timezone.now() - timedelta(days=15)
        )

        response = self.client.post(
            '/scrape',
            json={'url': 'https://example.com/events'},
            headers={'Authorization': f'Bearer {self.jwt_token}'}
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()

        # Should create new job (old one expired)
        self.assertNotEqual(data['id'], old_job.id)
        self.assertEqual(ScrapingJob.objects.count(), 2)

    def test_queue_submit_prevents_duplicates(self):
        """Test that /queue/submit also prevents duplicate jobs."""
        # Create existing pending job
        existing_job = baker.make(
            ScrapingJob,
            url='https://library.example.com/events',
            domain='library.example.com',
            status='pending',
            submitted_by=self.user
        )

        response = self.client.post(
            '/queue/submit',
            json={'url': 'https://library.example.com/events'},
            headers={'Authorization': f'Bearer {self.jwt_token}'}
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()

        # Should return existing job
        self.assertEqual(data['id'], existing_job.id)
        self.assertEqual(ScrapingJob.objects.count(), 1)

    def test_bulk_submit_prevents_duplicates(self):
        """Test that /queue/bulk-submit prevents duplicate jobs."""
        # Create existing pending job
        existing_job = baker.make(
            ScrapingJob,
            url='https://example.com/events1',
            domain='example.com',
            status='pending',
            submitted_by=self.user
        )

        response = self.client.post(
            '/queue/bulk-submit',
            json={'urls': [
                'https://example.com/events1',  # Existing
                'https://example.com/events2',  # New
                'https://example.com/events3'   # New
            ]},
            headers={'Authorization': f'Bearer {self.jwt_token}'}
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()

        # Should have 3 jobs total (1 existing + 2 new)
        self.assertEqual(ScrapingJob.objects.count(), 3)
        self.assertEqual(len(data['job_ids']), 3)
        self.assertIn(existing_job.id, data['job_ids'])

    def test_complete_job_with_venue_location_data(self):
        """Test completing a job with events that have location_data for venue creation."""
        from venues.models import Venue

        job = ScrapingJob.objects.create(
            url='https://example.com/events',
            domain='example.com',
            status='processing',
            submitted_by=self.user,
            locked_by='test-worker-1'
        )

        response = self.client.post(
            f'/queue/{job.id}/complete',
            json={
                'success': True,
                'events': [
                    {
                        'external_id': 'evt-venue-123',
                        'title': 'Story Time',
                        'description': 'Fun stories for kids',
                        'start_time': '2025-01-01T10:00:00Z',
                        'url': 'https://example.com/event/storytime',
                        'location_data': {
                            'venue_name': 'Wellesley Free Library',
                            'street_address': '530 Washington St',
                            'city': 'Wellesley',
                            'state': 'MA',
                            'postal_code': '02482',
                            'room_name': "Children's Room",
                            'extraction_confidence': 0.9
                        }
                    }
                ],
                'events_found': 1,
                'pages_processed': 1,
                'processing_time': 2.5
            },
            headers={'Authorization': f'Bearer {self.service_token.token}'}
        )

        self.assertEqual(response.status_code, 200, f"Got {response.status_code}: {response.json()}")
        data = response.json()
        self.assertEqual(len(data['created_event_ids']), 1)

        # Verify event was created with venue
        from events.models import Event
        event = Event.objects.get(external_id='evt-venue-123')
        self.assertEqual(event.title, 'Story Time')
        self.assertIsNotNone(event.venue, "Event should have a venue")
        self.assertEqual(event.venue.name, 'Wellesley Free Library')
        self.assertEqual(event.venue.city, 'Wellesley')
        self.assertEqual(event.room_name, "Children's Room")

        # Verify venue was created
        venue = Venue.objects.get(name='Wellesley Free Library')
        self.assertEqual(venue.street_address, '530 Washington St')
        self.assertEqual(venue.postal_code, '02482')

    def test_complete_job_real_acton_maine_payload(self):
        """Test /queue/complete with real payload that was causing 500 errors."""
        from venues.models import Venue
        from events.models import Event

        job = ScrapingJob.objects.create(
            url='https://www.actonmaine.org/mc-events/',
            domain='www.actonmaine.org',
            status='processing',
            submitted_by=self.user,
            locked_by='test-worker-1'
        )

        payload = {
            "success": True,
            "events": [
                {
                    "external_id": "https://www.actonmaine.org/mc-events/select-board-81/",
                    "title": "Select Board",
                    "description": "",
                    "location_data": {
                        "venue_name": "Town Hall",
                        "street_address": "35 H Road",
                        "city": "Acton",
                        "state": "ME",
                        "postal_code": "04001",
                        "extraction_confidence": 0.9
                    },
                    "start_time": "2025-12-17T18:00:00-05:00",
                    "end_time": "2025-12-17T19:00:00-05:00",
                    "url": "https://www.actonmaine.org/mc-events/select-board-81/",
                    "metadata_tags": []
                }
            ],
            "events_found": 1,
            "pages_processed": 1,
            "processing_time": 2.11098051071167
        }

        response = self.client.post(
            f'/queue/{job.id}/complete',
            json=payload,
            headers={'Authorization': f'Bearer {self.service_token.token}'}
        )

        self.assertEqual(response.status_code, 200, f"Got {response.status_code}: {response.json()}")

        event = Event.objects.get(external_id="https://www.actonmaine.org/mc-events/select-board-81/")
        self.assertEqual(event.title, "Select Board")
        self.assertIsNotNone(event.venue)
        self.assertEqual(event.venue.name, "Town Hall")
        self.assertEqual(event.venue.city, "Acton")
        self.assertEqual(event.venue.state, "ME")
