from django.db import models
from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver
from pgvector.django import VectorField
from django.contrib.postgres.indexes import GinIndex
import secrets
import logging

logger = logging.getLogger(__name__)

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

    # Queue management fields
    priority = models.IntegerField(default=5)  # 1=highest, 10=lowest
    retry_count = models.PositiveIntegerField(default=0)
    max_retries = models.PositiveIntegerField(default=3)
    locked_at = models.DateTimeField(null=True, blank=True)
    locked_by = models.CharField(max_length=100, blank=True)
    source = models.ForeignKey(
        Source, null=True, blank=True, on_delete=models.SET_NULL, related_name='scraping_jobs'
    )

    # Cost tracking
    worker_type = models.CharField(max_length=20, blank=True)  # 'local', 'spot_t3_small'
    estimated_cost = models.DecimalField(max_digits=8, decimal_places=6, null=True, blank=True)

    # Collector metadata
    extraction_method = models.CharField(max_length=50, blank=True)  # 'rss', 'localist', 'llm'
    confidence_score = models.FloatField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=['status', 'priority', 'created_at']),
            models.Index(fields=['locked_at']),
        ]

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

    # Structured venue system
    venue = models.ForeignKey(
        'venues.Venue',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='events',
        help_text="Structured venue with normalized address components"
    )
    room_name = models.CharField(
        max_length=200,
        blank=True,
        help_text="Room/space within venue (e.g., 'Children's Room')"
    )
    raw_place_json = models.JSONField(
        null=True,
        blank=True,
        help_text="Original Schema.org Place JSON-LD for re-parsing"
    )
    raw_location_data = models.JSONField(
        null=True,
        blank=True,
        help_text="Full location_data from collector for debugging"
    )

    # Additional Schema.org Event fields
    organizer = models.CharField(
        max_length=200, 
        blank=True,
        help_text="Event organizer name from Schema.org"
    )
    event_status = models.CharField(
        max_length=50, 
        blank=True,
        help_text="scheduled/cancelled/postponed from Schema.org"
    )
    event_attendance_mode = models.CharField(
        max_length=50, 
        blank=True,
        help_text="offline/online/mixed from Schema.org"
    )
    
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
    
    def get_location_string(self) -> str:
        """Get location as string for display."""
        if self.venue:
            if self.room_name:
                return f"{self.room_name}, {self.venue.name}"
            return str(self.venue)
        return ""

    def get_full_address(self) -> str:
        """Get full address for geocoding and location searches."""
        if self.venue:
            return self.venue.get_full_address()
        return ""

    def get_city(self) -> str:
        """Extract city from location for geographic searches."""
        if self.venue:
            return self.venue.city
        return ""

    def get_location_search_text(self) -> str:
        """Get comprehensive location text for RAG search."""
        if self.venue:
            parts = [self.venue.name, self.venue.get_full_address()]
            if self.room_name:
                parts.insert(0, self.room_name)
            return " ".join(filter(None, parts))
        return ""
    
    @classmethod
    def create_with_schema_org_data(cls, event_data: dict, source):
        """
        Create or update Event with Venue normalization from collector's location_data.

        Args:
            event_data: Event data from collector with location_data dict
            source: Source instance

        Returns:
            Tuple of (Event instance, was_created boolean)
        """
        from venues.extraction import normalize_venue_data, get_or_create_venue
        from urllib.parse import urlparse

        # Extract source domain for venue tracking
        source_domain = ""
        if source and source.base_url:
            try:
                source_domain = urlparse(source.base_url).netloc
            except Exception:
                pass

        # Get location_data from collector (required format)
        location_data = event_data.get('location_data')
        raw_place_json = location_data.get('raw_place_json') if location_data else None

        # Normalize venue data using the pipeline
        normalized = normalize_venue_data(location_data=location_data, place_json=raw_place_json)

        # Get or create Venue
        venue_obj = None
        room_name = ""
        if normalized.get('venue_name') and normalized.get('city'):
            venue_obj, _ = get_or_create_venue(normalized, source_domain)
            room_name = normalized.get('room_name', '')[:200]  # Truncate to max_length

        # Create or update event with venue reference
        # Truncate string fields to match model max_length constraints
        return cls.objects.update_or_create(
            source=source,
            external_id=event_data.get('external_id', '')[:255],
            defaults={
                "title": event_data.get('title', '')[:255],
                "description": event_data.get('description', ''),
                "venue": venue_obj,
                "room_name": room_name,
                "raw_place_json": raw_place_json,
                "raw_location_data": location_data,
                "organizer": event_data.get('organizer', '')[:200],
                "event_status": event_data.get('event_status', '')[:50],
                "event_attendance_mode": event_data.get('event_attendance_mode', '')[:50],
                "start_time": event_data.get('start_time'),
                "end_time": event_data.get('end_time'),
                "url": event_data.get('url', ''),
                "metadata_tags": event_data.get('tags', []),
            }
        )

    class Meta:
        unique_together = ('source', 'external_id')
        indexes = [
            GinIndex(fields=['description'], name='desc_gin_idx', opclasses=['gin_trgm_ops']),
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


@receiver(post_save, sender=Event)
def queue_event_embedding(sender, instance, created, update_fields=None, **kwargs):
    """
    Queue RAG embedding generation when Event is created or updated.

    Uses Celery task for async processing instead of synchronous generation.
    Uses update_fields to intelligently detect when embedding needs regeneration:
    - Always generate for new events
    - For updates, only regenerate if content fields changed or no embedding exists
    """
    should_update = False

    if created:
        should_update = True
    elif instance.embedding is None:
        # Always generate if missing
        should_update = True
    elif update_fields is None:
        # Full save() called - can't detect changes, so be conservative
        # Only update if no embedding exists to avoid unnecessary work
        should_update = False
    else:
        # Specific fields updated - check if any affect embedding content
        embedding_fields = {'title', 'description', 'start_time', 'venue', 'room_name'}
        should_update = bool(embedding_fields.intersection(update_fields))

    if should_update:
        try:
            from events.tasks import generate_embedding
            generate_embedding.delay(instance.id)
            logger.info(f"Queued embedding generation for event: {instance.title}")
        except Exception as e:
            logger.error(f"Failed to queue embedding for event {instance.id}: {e}")
