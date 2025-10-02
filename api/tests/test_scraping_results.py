from datetime import datetime, timezone as dt_timezone
from django.test import TestCase
from django.utils import timezone
from ninja.testing import TestClient
from model_bakery import baker

from api.views import router
from events.models import Event, Source, SiteStrategy, ScrapingJob, ServiceToken
from django.contrib.auth import get_user_model

User = get_user_model()


class SaveScrapeResultsTests(TestCase):
    """Test the save_scrape_results endpoint that processes scraping job results."""

    def setUp(self):
        self.client = TestClient(router)
        self.user = baker.make(User, username="testuser")
        self.service_token = baker.make(ServiceToken, name="test_service")
        self.strategy = baker.make(SiteStrategy, domain="example.com")
        self.job = baker.make(ScrapingJob, url="https://example.com/events", domain="example.com",
                             submitted_by=self.user, status="pending")

    def test_successful_results_with_events(self):
        payload = {
            "success": True,
            "events_found": 2,
            "pages_processed": 1,
            "processing_time": 5.2,
            "events": [
                {
                    "external_id": "evt_001",
                    "title": "Summer Concert",
                    "description": "Outdoor music event",
                    "location": "Central Park",
                    "start_time": "2024-07-15T18:00:00Z",
                    "end_time": "2024-07-15T20:00:00Z",
                    "url": "https://example.com/events/001",
                    "metadata_tags": ["music", "outdoor"],
                    "affiliate_link": "https://affiliate.example.com/001",
                    "revenue_source": "partner_program",
                    "commission_rate": 5.5,
                    "affiliate_tracking_id": "track_123"
                },
                {
                    "external_id": "evt_002",
                    "title": "Art Workshop",
                    "description": "Learn watercolor",
                    "location": "Community Center",
                    "start_time": "2024-07-20T14:00:00Z",
                    "metadata_tags": ["art"]
                }
            ]
        }

        response = self.client.post(f"/scrape/{self.job.id}/results", json=payload,
                                    headers={"Authorization": f"Bearer {self.service_token.token}"})

        assert response.status_code == 200
        data = response.json()
        assert len(data["created_event_ids"]) == 2

        # Verify events created
        assert Event.objects.count() == 2
        event1 = Event.objects.get(external_id="evt_001")
        assert event1.title == "Summer Concert"
        assert event1.scraping_job == self.job
        assert event1.metadata_tags == ["music", "outdoor"]
        assert event1.affiliate_link == "https://affiliate.example.com/001"
        assert event1.revenue_source == "partner_program"
        assert float(event1.commission_rate) == 5.5
        assert event1.affiliate_tracking_id == "track_123"

        event2 = Event.objects.get(external_id="evt_002")
        assert event2.title == "Art Workshop"
        assert event2.scraping_job == self.job

        # Verify job updated
        self.job.refresh_from_db()
        assert self.job.status == "completed"
        assert self.job.events_found == 2
        assert self.job.pages_processed == 1
        assert self.job.processing_time == 5.2
        assert self.job.completed_at is not None

    def test_failed_scraping_job(self):
        payload = {"success": False, "events_found": 0, "pages_processed": 1, "error_message": "Connection timeout",
                  "events": []}

        response = self.client.post(f"/scrape/{self.job.id}/results", json=payload,
                                    headers={"Authorization": f"Bearer {self.service_token.token}"})

        assert response.status_code == 200
        assert Event.objects.count() == 0

        self.job.refresh_from_db()
        assert self.job.status == "failed"
        assert self.job.error_message == "Connection timeout"
        assert self.job.events_found == 0
        assert self.job.completed_at is not None

    def test_creates_new_source_for_domain(self):
        assert not Source.objects.filter(base_url="https://example.com").exists()

        payload = {"success": True, "events_found": 1, "pages_processed": 1,
                  "events": [{"external_id": "evt_001", "title": "Event", "description": "Desc", "location": "Place",
                             "start_time": "2024-07-15T18:00:00Z"}]}

        response = self.client.post(f"/scrape/{self.job.id}/results", json=payload,
                                    headers={"Authorization": f"Bearer {self.service_token.token}"})

        assert response.status_code == 200
        assert Source.objects.filter(base_url="https://example.com").exists()

        source = Source.objects.get(base_url="https://example.com")
        assert source.user == self.user
        assert source.search_method == Source.SearchMethod.MANUAL
        assert source.site_strategy == self.strategy

    def test_reuses_existing_source(self):
        existing_source = baker.make(Source, base_url="https://example.com", user=self.user)

        payload = {"success": True, "events_found": 1, "pages_processed": 1,
                  "events": [{"external_id": "evt_001", "title": "Event", "description": "Desc", "location": "Place",
                             "start_time": "2024-07-15T18:00:00Z"}]}

        response = self.client.post(f"/scrape/{self.job.id}/results", json=payload,
                                    headers={"Authorization": f"Bearer {self.service_token.token}"})

        assert response.status_code == 200
        assert Source.objects.filter(base_url="https://example.com").count() == 1

        event = Event.objects.first()
        assert event.source == existing_source

    def test_updates_source_strategy_if_changed(self):
        existing_source = baker.make(Source, base_url="https://example.com", site_strategy=None)

        payload = {"success": True, "events_found": 1, "pages_processed": 1,
                  "events": [{"external_id": "evt_001", "title": "Event", "description": "Desc", "location": "Place",
                             "start_time": "2024-07-15T18:00:00Z"}]}

        response = self.client.post(f"/scrape/{self.job.id}/results", json=payload,
                                    headers={"Authorization": f"Bearer {self.service_token.token}"})

        assert response.status_code == 200

        existing_source.refresh_from_db()
        assert existing_source.site_strategy == self.strategy

    def test_optional_event_fields(self):
        payload = {"success": True, "events_found": 1, "pages_processed": 1,
                  "events": [{"external_id": "evt_min", "title": "Minimal Event", "description": "Description",
                             "location": "Location", "start_time": "2024-07-15T18:00:00Z"}]}

        response = self.client.post(f"/scrape/{self.job.id}/results", json=payload,
                                    headers={"Authorization": f"Bearer {self.service_token.token}"})

        assert response.status_code == 200

        event = Event.objects.first()
        assert event.end_time is None
        assert event.url is None
        assert event.metadata_tags == []
        assert event.affiliate_link == ""
        assert event.revenue_source == ""
        assert event.commission_rate is None
        assert event.affiliate_tracking_id == ""

    def test_requires_service_token_auth(self):
        payload = {"success": True, "events_found": 0, "pages_processed": 1, "events": []}

        # Request without authentication
        response = self.client.post(f"/scrape/{self.job.id}/results", json=payload)
        assert response.status_code == 401

    def test_nonexistent_job_returns_404(self):
        payload = {"success": True, "events_found": 0, "pages_processed": 1, "events": []}

        response = self.client.post("/scrape/99999/results", json=payload,
                                    headers={"Authorization": f"Bearer {self.service_token.token}"})
        assert response.status_code == 404