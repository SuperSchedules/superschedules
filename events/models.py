from django.db import models
from django.conf import settings
from pgvector.django import VectorField
from django.contrib.postgres.indexes import GinIndex
import secrets

class Source(models.Model):
    class Status(models.TextChoices):
        NOT_RUN = "not_run", "Not run"
        IN_PROGRESS = "in_progress", "In Progress"
        PROCESSED = "processed", "Processed"

    class SearchMethod(models.TextChoices):
        MANUAL = "manual", "Manual scrape"
        LLM = "llm", "LLM search"
        CALENDAR = "calendar", "Calendar file"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="sources",
        null=True,
        blank=True,
    )
    name = models.CharField(max_length=100, blank=True, null=True)
    base_url = models.URLField(blank=True)
    site_strategy = models.ForeignKey(
        'SiteStrategy',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='sources'
    )
    search_method = models.CharField(
        max_length=20,
        choices=SearchMethod.choices,
        default=SearchMethod.MANUAL,
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.NOT_RUN
    )
    event = models.ForeignKey(
        "Event",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="source_submissions",
    )
    last_run_at = models.DateTimeField(null=True, blank=True)
    date_added = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name or self.base_url


class SiteStrategy(models.Model):
    domain = models.CharField(max_length=200, unique=True)
    best_selectors = models.JSONField(default=list, blank=True)
    pagination_pattern = models.CharField(max_length=500, blank=True)
    cancellation_indicators = models.JSONField(default=list, blank=True)
    success_rate = models.FloatField(default=0.0)
    total_attempts = models.PositiveIntegerField(default=0)
    successful_attempts = models.PositiveIntegerField(default=0)
    last_successful = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.domain


class ScrapingJob(models.Model):
    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("processing", "Processing"),
        ("completed", "Completed"),
        ("failed", "Failed"),
    ]

    url = models.URLField()
    domain = models.CharField(max_length=200)
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default="pending"
    )
    strategy_used = models.CharField(max_length=100, blank=True)
    events_found = models.PositiveIntegerField(default=0)
    pages_processed = models.PositiveIntegerField(default=1)
    processing_time = models.FloatField(null=True)
    error_message = models.TextField(blank=True)
    lambda_request_id = models.CharField(max_length=100, blank=True)
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.url} ({self.status})"


class ScrapeBatch(models.Model):
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    jobs = models.ManyToManyField(ScrapingJob, related_name="batches")

    def __str__(self):
        return f"Batch {self.id}"

class Event(models.Model):
    source = models.ForeignKey(
        Source,
        on_delete=models.CASCADE,
        related_name="events",
        related_query_name="source_event",
    )
    scraping_job = models.ForeignKey(
        'ScrapingJob', on_delete=models.CASCADE, null=True, blank=True
    )
    external_id = models.CharField(max_length=255)
    title = models.CharField(max_length=255)
    description = models.TextField()
    location = models.CharField(max_length=255)
    start_time = models.DateTimeField()
    end_time = models.DateTimeField(null=True, blank=True)
    url = models.URLField(blank=True, null=True)
    metadata_tags = models.JSONField(default=list, blank=True)
    affiliate_link = models.URLField(blank=True)
    revenue_source = models.CharField(max_length=100, blank=True)
    commission_rate = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True
    )
    affiliate_tracking_id = models.CharField(max_length=200, blank=True)
    embedding = VectorField(dimensions=384, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('source', 'external_id')
        indexes = [
            GinIndex(fields=['description'], name='desc_gin_idx'),
            GinIndex(fields=['embedding'], name='embed_gin_idx'),
        ]

    def __str__(self):
        return self.title


def generate_token():
    return secrets.token_hex(20)


class ServiceToken(models.Model):
    name = models.CharField(max_length=100, unique=True)
    token = models.CharField(max_length=40, unique=True, default=generate_token)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name
