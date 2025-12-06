"""
Tests for job queue duplicate checking logic.
"""
from django.test import TestCase
from django.utils import timezone
from datetime import timedelta
from model_bakery import baker
from unittest.mock import Mock

from events.models import Source, ScrapingJob
from django.contrib.auth import get_user_model

User = get_user_model()


class QueueDuplicateCheckingTests(TestCase):
    """Test duplicate job prevention logic."""

    def setUp(self):
        self.user = baker.make(User, username="testuser@example.com")

    def test_scrape_endpoint_creates_new_job(self):
        """Test creating a new job when none exists."""
        from api.views import submit_scrape, ScrapeRequestSchema

        request = Mock()
        request.user = self.user

        payload = ScrapeRequestSchema(url='https://example.com/events')
        result = submit_scrape(request, payload)

        # Verify job was created
        self.assertEqual(ScrapingJob.objects.count(), 1)
        job = ScrapingJob.objects.first()

        self.assertEqual(job.url, 'https://example.com/events')
        self.assertEqual(job.status, 'pending')
        self.assertEqual(job.priority, 5)
        self.assertEqual(job.submitted_by, self.user)
        self.assertIsNotNone(job.source)

    def test_returns_existing_pending_job(self):
        """Test that submitting same URL returns existing pending job."""
        from api.views import submit_scrape, ScrapeRequestSchema

        # Create existing pending job
        existing_job = baker.make(
            ScrapingJob,
            url='https://example.com/events',
            domain='example.com',
            status='pending',
            submitted_by=self.user,
            priority=5
        )

        request = Mock()
        request.user = self.user
        payload = ScrapeRequestSchema(url='https://example.com/events')
        result = submit_scrape(request, payload)

        # Should return existing job, not create new one
        self.assertEqual(result.id, existing_job.id)
        self.assertEqual(ScrapingJob.objects.count(), 1)

    def test_returns_existing_processing_job(self):
        """Test that submitting same URL returns existing processing job."""
        from api.views import submit_scrape, ScrapeRequestSchema

        # Create existing processing job
        existing_job = baker.make(
            ScrapingJob,
            url='https://example.com/events',
            domain='example.com',
            status='processing',
            submitted_by=self.user,
            locked_by='worker-1'
        )

        request = Mock()
        request.user = self.user
        payload = ScrapeRequestSchema(url='https://example.com/events')
        result = submit_scrape(request, payload)

        # Should return existing job
        self.assertEqual(result.id, existing_job.id)
        self.assertEqual(ScrapingJob.objects.count(), 1)

    def test_returns_recent_success(self):
        """Test that submitting recently completed URL returns that job."""
        from api.views import submit_scrape, ScrapeRequestSchema

        # Create recently completed job (within 14 days)
        recent_job = baker.make(
            ScrapingJob,
            url='https://example.com/events',
            domain='example.com',
            status='completed',
            submitted_by=self.user,
            completed_at=timezone.now() - timedelta(days=7)
        )

        request = Mock()
        request.user = self.user
        payload = ScrapeRequestSchema(url='https://example.com/events')
        result = submit_scrape(request, payload)

        # Should return recent job
        self.assertEqual(result.id, recent_job.id)
        self.assertEqual(ScrapingJob.objects.count(), 1)

    def test_creates_new_job_after_14_days(self):
        """Test that submitting URL after 14 days creates new job."""
        from api.views import submit_scrape, ScrapeRequestSchema

        # Create old completed job (more than 14 days ago)
        old_job = baker.make(
            ScrapingJob,
            url='https://example.com/events',
            domain='example.com',
            status='completed',
            submitted_by=self.user,
            completed_at=timezone.now() - timedelta(days=15)
        )

        request = Mock()
        request.user = self.user
        payload = ScrapeRequestSchema(url='https://example.com/events')
        result = submit_scrape(request, payload)

        # Should create new job (old one expired)
        self.assertNotEqual(result.id, old_job.id)
        self.assertEqual(ScrapingJob.objects.count(), 2)

    def test_queue_submit_prevents_duplicates(self):
        """Test that /queue/submit also prevents duplicate jobs."""
        from api.views import submit_url_to_queue, ScrapeRequestSchema

        # Create existing pending job
        existing_job = baker.make(
            ScrapingJob,
            url='https://library.example.com/events',
            domain='library.example.com',
            status='pending',
            submitted_by=self.user
        )

        request = Mock()
        request.user = self.user
        payload = ScrapeRequestSchema(url='https://library.example.com/events')
        result = submit_url_to_queue(request, payload)

        # Should return existing job
        self.assertEqual(result.id, existing_job.id)
        self.assertEqual(ScrapingJob.objects.count(), 1)

    def test_bulk_submit_prevents_duplicates(self):
        """Test that /queue/bulk-submit prevents duplicate jobs."""
        from api.views import bulk_submit_urls, BatchRequestSchema

        # Create existing pending job
        existing_job = baker.make(
            ScrapingJob,
            url='https://example.com/events1',
            domain='example.com',
            status='pending',
            submitted_by=self.user
        )

        request = Mock()
        request.user = self.user
        payload = BatchRequestSchema(urls=[
            'https://example.com/events1',  # Existing
            'https://example.com/events2',  # New
            'https://example.com/events3'   # New
        ])
        result = bulk_submit_urls(request, payload)

        # Should have 3 jobs total (1 existing + 2 new)
        self.assertEqual(ScrapingJob.objects.count(), 3)
        self.assertEqual(len(result['job_ids']), 3)
        self.assertIn(existing_job.id, result['job_ids'])

    def test_source_creation_on_submit(self):
        """Test that Source is created when submitting URL."""
        from api.views import submit_scrape, ScrapeRequestSchema

        self.assertEqual(Source.objects.count(), 0)

        request = Mock()
        request.user = self.user
        payload = ScrapeRequestSchema(url='https://example.com/events')
        result = submit_scrape(request, payload)

        # Should have created a Source
        self.assertEqual(Source.objects.count(), 1)
        source = Source.objects.first()
        self.assertEqual(source.base_url, 'https://example.com/events')
        self.assertEqual(source.user, self.user)
