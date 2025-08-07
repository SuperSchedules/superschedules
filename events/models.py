from django.db import models
from django.conf import settings
from pgvector.django import VectorField
from django.contrib.postgres.indexes import GinIndex
import secrets

class Source(models.Model):
    class Status(models.TextChoices):
        SUBMITTED = "submitted", "Submitted"
        IN_PROGRESS = "in_progress", "In Progress"
        COMPLETED = "completed", "Completed"

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
    search_method = models.CharField(
        max_length=20,
        choices=SearchMethod.choices,
        default=SearchMethod.MANUAL,
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.SUBMITTED
    )
    event = models.ForeignKey(
        "Event",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="source_submissions",
    )
    last_crawl = models.DateTimeField(null=True)

    def __str__(self):
        return self.name or self.base_url

class Event(models.Model):
    source = models.ForeignKey(
        Source,
        on_delete=models.CASCADE,
        related_name="events",
        related_query_name="source_event",
    )
    external_id = models.CharField(max_length=255)
    title = models.CharField(max_length=255)
    description = models.TextField()
    location = models.CharField(max_length=255)
    start_time = models.DateTimeField()
    end_time = models.DateTimeField(null=True, blank=True)
    url = models.URLField(blank=True, null=True)
    embedding = VectorField(dimensions=768, blank=True, null=True)
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
