from datetime import timedelta
from django.utils import timezone
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient
from model_bakery import baker

from events.models import SiteStrategy, ScrapingJob, Event, ServiceToken, Source


class ScrapingTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = baker.make(User, username="tester")
        self.password = "pass1234"
        self.user.set_password(self.password)
        self.user.save()
        self.client = APIClient()
        resp = self.client.post(
            "/api/v1/token/",
            {"username": self.user.username, "password": self.password},
            format="json",
        )
        token = resp.data["access"]
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        self.service_token = baker.make(ServiceToken)

    def test_strategy_and_scrape_flow(self):
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

        # Submit scrape job
        resp = self.client.post(
            "/api/v1/scrape",
            {"url": "https://example.com/events"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        job_id = resp.json()["id"]
        self.assertTrue(ScrapingJob.objects.filter(id=job_id).exists())

        # Lambda posts results
        result_payload = {
            "events": [
                {
                    "external_id": "1",
                    "title": "Test Event",
                    "description": "Desc",
                    "start_time": (timezone.now() + timedelta(days=1)).isoformat(),
                }
            ],
            "events_found": 1,
            "pages_processed": 1,
            "success": True,
        }
        resp = svc_client.post(
            f"/api/v1/scrape/{job_id}/results",
            result_payload,
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        job = ScrapingJob.objects.get(id=job_id)
        self.assertEqual(job.status, "completed")
        self.assertEqual(Event.objects.filter(scraping_job=job).count(), 1)
        source = Source.objects.get(base_url="https://example.com")
        strategy = SiteStrategy.objects.get(domain=domain)
        self.assertEqual(source.site_strategy, strategy)

        # Batch submission
        resp = self.client.post(
            "/api/v1/scrape/batch/",
            {"urls": ["https://example.com/a", "https://example.com/b"]},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        batch_id = resp.json()["batch_id"]
        resp = self.client.get(f"/api/v1/scrape/batch/{batch_id}/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.json()), 2)
