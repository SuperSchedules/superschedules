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

    def test_record_attempt_stores_extraction_method(self):
        """Test that successful attempts store the extraction_method."""
        self.history.record_attempt(success=True, events_found=3, extraction_method='jsonld')
        self.assertEqual(self.history.last_successful_scraper, 'jsonld')
        self.assertIsNotNone(self.history.last_scraper_updated_at)

    def test_extraction_method_not_stored_on_failure(self):
        """Test that extraction_method is not updated on failures."""
        # First, record a success with a method
        self.history.record_attempt(success=True, events_found=3, extraction_method='jsonld')
        original_scraper = self.history.last_successful_scraper
        original_updated_at = self.history.last_scraper_updated_at

        # Record a failure - should not change the scraper
        self.history.record_attempt(success=False, error_message="Error", extraction_method='llm')
        self.assertEqual(self.history.last_successful_scraper, original_scraper)
        self.assertEqual(self.history.last_scraper_updated_at, original_updated_at)

    def test_extraction_method_updates_on_new_success(self):
        """Test that extraction_method updates when a different scraper succeeds."""
        self.history.record_attempt(success=True, events_found=3, extraction_method='jsonld')
        self.assertEqual(self.history.last_successful_scraper, 'jsonld')

        # Different scraper succeeds
        self.history.record_attempt(success=True, events_found=5, extraction_method='localist')
        self.assertEqual(self.history.last_successful_scraper, 'localist')

    def test_extraction_method_not_cleared_if_empty(self):
        """Test that empty extraction_method doesn't clear existing value."""
        self.history.record_attempt(success=True, events_found=3, extraction_method='jsonld')
        self.assertEqual(self.history.last_successful_scraper, 'jsonld')

        # Success without extraction_method should not clear it
        self.history.record_attempt(success=True, events_found=5, extraction_method='')
        self.assertEqual(self.history.last_successful_scraper, 'jsonld')


class ScraperStatsEndpointTests(TestCase):
    """Tests for the /api/v1/stats/scrapers endpoint."""

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

        # Create test data
        self.venue = baker.make(Venue, name="Test Library", city="Newton", state="MA")

    def test_scraper_stats_endpoint_returns_200(self):
        """Test that the stats endpoint returns 200."""
        resp = self.client.get("/api/v1/stats/scrapers")
        self.assertEqual(resp.status_code, 200)

    def test_scraper_stats_response_structure(self):
        """Test the response structure of the stats endpoint."""
        resp = self.client.get("/api/v1/stats/scrapers")
        data = resp.json()

        # Check top-level keys
        self.assertIn("jobs_by_method", data)
        self.assertIn("urls_by_known_scraper", data)
        self.assertIn("summary", data)

        # Check summary keys
        self.assertIn("total_urls_tracked", data["summary"])
        self.assertIn("urls_with_scraper_hint", data["summary"])
        self.assertIn("hint_coverage_percent", data["summary"])
        self.assertIn("period_days", data["summary"])

    def test_scraper_stats_counts_jobs_by_method(self):
        """Test that stats endpoint correctly counts jobs by extraction method."""
        # Create completed jobs with extraction methods
        for method, count in [('jsonld', 5), ('localist', 3), ('llm', 2)]:
            for _ in range(count):
                ScrapingJob.objects.create(
                    url=f"https://example.com/{method}/{_}",
                    domain="example.com",
                    status="completed",
                    extraction_method=method,
                    events_found=10,
                    completed_at=timezone.now(),
                    venue=self.venue,
                )

        resp = self.client.get("/api/v1/stats/scrapers")
        data = resp.json()

        # Should have all three methods
        methods = {item['extraction_method']: item['job_count'] for item in data['jobs_by_method']}
        self.assertEqual(methods.get('jsonld'), 5)
        self.assertEqual(methods.get('localist'), 3)
        self.assertEqual(methods.get('llm'), 2)

    def test_scraper_stats_counts_urls_with_known_scraper(self):
        """Test that stats endpoint counts URLs with known scrapers."""
        # Create ScrapeHistory entries with known scrapers
        ScrapeHistory.objects.create(
            venue=self.venue,
            url="https://example.com/events1",
            domain="example.com",
            last_successful_scraper='jsonld',
            last_scraper_updated_at=timezone.now(),
        )
        ScrapeHistory.objects.create(
            venue=self.venue,
            url="https://example.com/events2",
            domain="example.com",
            last_successful_scraper='jsonld',
            last_scraper_updated_at=timezone.now(),
        )
        ScrapeHistory.objects.create(
            venue=self.venue,
            url="https://example.com/events3",
            domain="example.com",
            last_successful_scraper='localist',
            last_scraper_updated_at=timezone.now(),
        )
        # One without a known scraper
        ScrapeHistory.objects.create(
            venue=self.venue,
            url="https://example.com/events4",
            domain="example.com",
        )

        resp = self.client.get("/api/v1/stats/scrapers")
        data = resp.json()

        # Check summary counts
        self.assertEqual(data["summary"]["total_urls_tracked"], 4)
        self.assertEqual(data["summary"]["urls_with_scraper_hint"], 3)
        self.assertEqual(data["summary"]["hint_coverage_percent"], 75.0)

        # Check URL counts by scraper
        scrapers = {item['scraper']: item['url_count'] for item in data['urls_by_known_scraper']}
        self.assertEqual(scrapers.get('jsonld'), 2)
        self.assertEqual(scrapers.get('localist'), 1)

    def test_scraper_stats_requires_auth(self):
        """Test that the stats endpoint requires authentication."""
        anon_client = APIClient()
        resp = anon_client.get("/api/v1/stats/scrapers")
        self.assertEqual(resp.status_code, 401)
