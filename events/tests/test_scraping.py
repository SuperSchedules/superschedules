from datetime import timedelta
from django.utils import timezone
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient
from model_bakery import baker

from events.models import SiteStrategy, ScrapingJob, ScrapeHistory, Event, ServiceToken
from venues.models import Venue


class ScrapingTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = baker.make(User, username="tester")
        self.password = "pass1234"
        self.user.set_password(self.password)
        self.user.save()
        self.client = APIClient()
        resp = self.client.post(
            "/api/v1/token",
            {"username": self.user.username, "password": self.password},
            format="json",
        )
        token = resp.data["access"]
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        self.service_token = baker.make(ServiceToken)

    def test_strategy_and_scrape_results(self):
        """Test site strategy updates and posting scrape results via service token."""
        domain = "example.com"
        # Update strategy via service token
        svc_client = APIClient()
        svc_client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.service_token.token}")
        payload = {"best_selectors": [".event"], "success": True}
        resp = svc_client.post(
            f"/api/v1/sites/{domain}/strategy",
            payload,
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        # Retrieve strategy
        resp = self.client.get(f"/api/v1/sites/{domain}/strategy")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["best_selectors"], [".event"])

        # Create a scraping job directly (simulating periodic task)
        venue = baker.make(Venue, name="Test Venue", city="Newton", state="MA")
        job = ScrapingJob.objects.create(
            url="https://example.com/events",
            domain=domain,
            status="pending",
            venue=venue,
            triggered_by="periodic",
        )

        # Service posts results
        result_payload = {
            "events": [
                {
                    "external_id": "1",
                    "title": "Test Event",
                    "description": "Desc",
                    "start_time": (timezone.now() + timedelta(days=1)).isoformat(),
                    "location_data": {
                        "venue_name": "Test Venue",
                        "city": "Newton",
                        "state": "MA",
                    },
                }
            ],
            "events_found": 1,
            "pages_processed": 1,
            "success": True,
        }
        resp = svc_client.post(
            f"/api/v1/scrape/{job.id}/results",
            result_payload,
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        job = ScrapingJob.objects.get(id=job.id)
        self.assertEqual(job.status, "completed")
        self.assertEqual(Event.objects.filter(scraping_job=job).count(), 1)
        strategy = SiteStrategy.objects.get(domain=domain)
        self.assertIsNotNone(strategy)


class ScrapeHistoryTests(TestCase):
    """Tests for the new ScrapeHistory model."""

    def setUp(self):
        self.venue = baker.make(Venue, name="Test Library", city="Newton", state="MA")
        self.url = "https://example.com/events"
        self.history = ScrapeHistory.objects.create(
            venue=self.venue,
            url=self.url,
            domain="example.com",
        )

    def test_record_attempt_success(self):
        """Test recording a successful scrape attempt."""
        self.history.record_attempt(success=True, events_found=5)
        self.assertEqual(self.history.total_attempts, 1)
        self.assertEqual(self.history.successful_attempts, 1)
        self.assertEqual(self.history.consecutive_failures, 0)
        self.assertEqual(self.history.total_events_found, 5)
        self.assertEqual(self.history.health_status, 'healthy')
        self.assertIsNotNone(self.history.first_scraped_at)
        self.assertIsNotNone(self.history.last_success_at)

    def test_record_attempt_failure(self):
        """Test recording a failed scrape attempt."""
        self.history.record_attempt(success=False, error_message="Connection timeout", error_category="timeout")
        self.assertEqual(self.history.total_attempts, 1)
        self.assertEqual(self.history.successful_attempts, 0)
        self.assertEqual(self.history.consecutive_failures, 1)
        self.assertEqual(self.history.health_status, 'degraded')
        self.assertEqual(self.history.error_category, 'timeout')

    def test_health_status_progression(self):
        """Test health status progresses with more failures."""
        # 1-4 failures = degraded
        for _ in range(4):
            self.history.record_attempt(success=False, error_message="Error")
        self.assertEqual(self.history.health_status, 'degraded')
        self.assertEqual(self.history.consecutive_failures, 4)

        # 5-9 failures = needs_attention
        self.history.record_attempt(success=False, error_message="Error")
        self.assertEqual(self.history.health_status, 'needs_attention')
        self.assertEqual(self.history.consecutive_failures, 5)

        # 10+ failures = unscrapable
        for _ in range(5):
            self.history.record_attempt(success=False, error_message="Error")
        self.assertEqual(self.history.health_status, 'unscrapable')
        self.assertEqual(self.history.consecutive_failures, 10)

    def test_success_resets_failures(self):
        """Test that a success resets consecutive failures."""
        # Add some failures
        for _ in range(5):
            self.history.record_attempt(success=False, error_message="Error")
        self.assertEqual(self.history.consecutive_failures, 5)
        self.assertEqual(self.history.health_status, 'needs_attention')

        # Success resets failures
        self.history.record_attempt(success=True, events_found=3)
        self.assertEqual(self.history.consecutive_failures, 0)
        self.assertEqual(self.history.health_status, 'healthy')
        self.assertEqual(self.history.total_attempts, 6)

    def test_success_rate(self):
        """Test success rate calculation."""
        self.assertEqual(self.history.success_rate, 0.0)

        self.history.record_attempt(success=True, events_found=1)
        self.assertEqual(self.history.success_rate, 100.0)

        self.history.record_attempt(success=False, error_message="Error")
        self.assertEqual(self.history.success_rate, 50.0)
