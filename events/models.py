from django.db import models
from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver
from pgvector.django import VectorField
from django.contrib.postgres.indexes import GinIndex
import secrets
import logging

logger = logging.getLogger(__name__)

# NOTE: Source model has been removed - Venue is now the first-class citizen
# Events are linked directly to Venues via events_urls


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
    venue = models.ForeignKey(
        'venues.Venue', null=True, blank=True, on_delete=models.SET_NULL, related_name='scraping_jobs'
    )
    scrape_history = models.ForeignKey(
        'ScrapeHistory', null=True, blank=True, on_delete=models.SET_NULL, related_name='jobs'
    )

    # Trigger tracking
    TRIGGERED_BY_CHOICES = [
        ('periodic', 'Periodic Schedule'),
        ('manual', 'Manual/Admin'),
        ('admin_action', 'Admin Action'),
        ('retry_degraded', 'Retry Degraded'),
        ('service', 'Service API'),
    ]
    triggered_by = models.CharField(max_length=50, choices=TRIGGERED_BY_CHOICES, blank=True)
    error_category = models.CharField(max_length=50, blank=True)  # timeout, 404, 403, parse_error, etc.

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


class ScrapeHistory(models.Model):
    """Track scraping history for a venue's event URL."""

    venue = models.ForeignKey('venues.Venue', on_delete=models.CASCADE, related_name='scrape_histories')
    url = models.URLField(db_index=True)
    domain = models.CharField(max_length=200)

    # Statistics
    total_attempts = models.PositiveIntegerField(default=0)
    successful_attempts = models.PositiveIntegerField(default=0)
    consecutive_failures = models.PositiveIntegerField(default=0)
    total_events_found = models.PositiveIntegerField(default=0)

    # Timing
    first_scraped_at = models.DateTimeField(null=True, blank=True)
    last_scraped_at = models.DateTimeField(null=True, blank=True)
    last_success_at = models.DateTimeField(null=True, blank=True)
    next_scheduled_at = models.DateTimeField(null=True, blank=True)

    # Health status
    HEALTH_STATUS_CHOICES = [
        ('healthy', 'Healthy'),
        ('degraded', 'Degraded'),  # Some failures, will retry
        ('needs_attention', 'Needs Attention'),  # 5+ failures
        ('unscrapable', 'Unscrapable'),  # 10+ failures, needs manual fix
        ('paused', 'Paused'),  # Manually paused
    ]
    health_status = models.CharField(max_length=20, choices=HEALTH_STATUS_CHOICES, default='healthy')

    # Error tracking
    last_error = models.TextField(blank=True)
    last_error_at = models.DateTimeField(null=True, blank=True)
    error_category = models.CharField(max_length=50, blank=True)  # timeout, 404, 403, parse_error, etc.

    # Scraper optimization - track which scraper works for this URL
    last_successful_scraper = models.CharField(
        max_length=50,
        blank=True,
        default='',
        help_text="Last extraction method that worked (e.g., 'jsonld', 'localist', 'llm')"
    )
    last_scraper_updated_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the last_successful_scraper was last updated"
    )

    # For web scrape writing agent
    agent_notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('venue', 'url')
        verbose_name_plural = 'Scrape histories'
        indexes = [
            models.Index(fields=['health_status']),
            models.Index(fields=['next_scheduled_at']),
            models.Index(fields=['domain']),
        ]

    def __str__(self):
        return f"{self.venue.name}: {self.url} ({self.health_status})"

    def record_attempt(
        self,
        success: bool,
        events_found: int = 0,
        error_message: str = '',
        error_category: str = '',
        extraction_method: str = ''
    ):
        """Record the result of a scraping attempt and update health status.

        Args:
            success: Whether the scrape was successful
            events_found: Number of events extracted
            error_message: Error message if failed
            error_category: Categorized error type
            extraction_method: Scraper method used (e.g., 'jsonld', 'localist', 'llm')
        """
        from django.utils import timezone

        now = timezone.now()
        self.total_attempts += 1
        self.last_scraped_at = now

        if not self.first_scraped_at:
            self.first_scraped_at = now

        if success:
            self.successful_attempts += 1
            self.consecutive_failures = 0
            self.total_events_found += events_found
            self.last_success_at = now
            self.health_status = 'healthy'
            self.last_error = ''
            self.last_error_at = None
            self.error_category = ''
            # Track successful scraper for optimization
            if extraction_method:
                self.last_successful_scraper = extraction_method
                self.last_scraper_updated_at = now
        else:
            self.consecutive_failures += 1
            self.last_error = error_message[:1000] if error_message else ''
            self.last_error_at = now
            self.error_category = error_category

            # Update health status based on consecutive failures
            if self.consecutive_failures >= 10:
                self.health_status = 'unscrapable'
            elif self.consecutive_failures >= 5:
                self.health_status = 'needs_attention'
            elif self.consecutive_failures >= 1:
                self.health_status = 'degraded'

        self.save()

    @property
    def success_rate(self) -> float:
        """Calculate success rate as a percentage."""
        if self.total_attempts == 0:
            return 0.0
        return (self.successful_attempts / self.total_attempts) * 100


class Event(models.Model):
    scraping_job = models.ForeignKey(
        'ScrapingJob', on_delete=models.CASCADE, null=True, blank=True
    )
    external_id = models.CharField(max_length=255)
    title = models.CharField(max_length=255)
    description = models.TextField()

    # Venue is the first-class citizen (required)
    venue = models.ForeignKey(
        'venues.Venue',
        on_delete=models.CASCADE,
        related_name='events',
        help_text="Venue that hosts this event (required)"
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

    # Audience targeting (for RAG matching)
    age_range = models.CharField(max_length=50, blank=True, help_text="Target age: 0-5, 6-9, 10-12, 13-18, adults, all-ages")
    audience_tags = models.JSONField(default=list, blank=True, help_text="Audience categories: Children, Families, Teens, Seniors, etc.")

    # Event logistics
    is_cancelled = models.BooleanField(default=False, help_text="Event is cancelled")
    is_virtual = models.BooleanField(default=False, help_text="Virtual/online event")
    requires_registration = models.BooleanField(default=False, help_text="Registration required")
    is_full = models.BooleanField(default=False, help_text="Registration full/waitlist")

    # Quality score from collector
    validation_score = models.FloatField(null=True, blank=True, help_text="LLM confidence in extraction (0.0-1.0)")

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
    def create_with_schema_org_data(cls, event_data: dict, venue=None, source_url: str = ""):
        """
        Create or update Event with Venue normalization from collector's location_data.

        Venue-first architecture: Events are deduplicated by (venue, external_id).
        Venue is required - either pass an existing venue or provide location_data for creation.

        Args:
            event_data: Event data from collector with location_data dict
            venue: Existing Venue instance (optional, will be created from location_data if not provided)
            source_url: URL being scraped (for domain extraction if no venue provided)

        Returns:
            Tuple of (Event instance, was_created boolean)

        Raises:
            ValueError: If venue cannot be determined/created
        """
        from venues.extraction import normalize_venue_data, get_or_create_venue
        from urllib.parse import urlparse

        # Determine source domain for venue tracking
        source_domain = ""
        if source_url:
            try:
                source_domain = urlparse(source_url).netloc
            except Exception:
                pass

        # Get or create venue
        venue_obj = venue
        room_name = ""

        if not venue_obj:
            # Get location_data from collector (required format)
            location_data = event_data.get('location_data')
            raw_place_json = location_data.get('raw_place_json') if location_data else None

            # Normalize venue data using the pipeline
            normalized = normalize_venue_data(location_data=location_data, place_json=raw_place_json)

            if normalized.get('venue_name') and normalized.get('city'):
                venue_obj, _ = get_or_create_venue(normalized, source_domain)
                room_name = normalized.get('room_name', '')[:200]
            else:
                raise ValueError("Cannot create event: venue could not be determined from location_data")
        else:
            # Use provided venue, extract room_name from location_data if available
            location_data = event_data.get('location_data')
            raw_place_json = location_data.get('raw_place_json') if location_data else None
            if location_data:
                normalized = normalize_venue_data(location_data=location_data, place_json=raw_place_json)
                room_name = normalized.get('room_name', '')[:200]

        # Create or update event - dedup by venue + external_id
        return cls.objects.update_or_create(
            venue=venue_obj,
            external_id=event_data.get('external_id', '')[:255],
            defaults={
                "title": event_data.get('title', '')[:255],
                "description": event_data.get('description', ''),
                "room_name": room_name,
                "raw_place_json": raw_place_json if not venue else event_data.get('location_data', {}).get('raw_place_json'),
                "raw_location_data": event_data.get('location_data'),
                "organizer": event_data.get('organizer', '')[:200],
                "event_status": event_data.get('event_status', '')[:50],
                "event_attendance_mode": event_data.get('event_attendance_mode', '')[:50],
                # Audience targeting
                "age_range": event_data.get('age_range', '')[:50],
                "audience_tags": event_data.get('audience_tags', []),
                # Event logistics
                "is_cancelled": event_data.get('is_cancelled', False),
                "is_virtual": event_data.get('is_virtual', False),
                "requires_registration": event_data.get('requires_registration', False),
                "is_full": event_data.get('is_full', False),
                # Quality
                "validation_score": event_data.get('validation_score'),
                # Core fields
                "start_time": event_data.get('start_time'),
                "end_time": event_data.get('end_time'),
                "url": event_data.get('url', ''),
                "metadata_tags": event_data.get('tags', []),
            }
        )

    class Meta:
        unique_together = ('venue', 'external_id')
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


class ChatSession(models.Model):
    """A chat conversation session. Groups messages for context."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="chat_sessions")
    title = models.CharField(max_length=200, blank=True)  # Auto-generated from first message
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_active = models.BooleanField(default=True)  # Inactive = archived
    context = models.JSONField(default=dict, blank=True)  # Persistent: location, preferences, etc.

    class Meta:
        ordering = ['-updated_at']
        indexes = [models.Index(fields=['user', '-updated_at']), models.Index(fields=['user', 'is_active'])]

    def __str__(self):
        return f"{self.user.username}: {self.title or 'Untitled'}"

    def get_recent_messages(self, limit: int = 10):
        """Get the most recent messages for LLM context."""
        return list(self.messages.order_by('-created_at')[:limit])[::-1]


class ChatMessage(models.Model):
    """A single message in a chat session (user or assistant)."""
    class Role(models.TextChoices):
        USER = 'user', 'User'
        ASSISTANT = 'assistant', 'Assistant'
        SYSTEM = 'system', 'System'

    session = models.ForeignKey(ChatSession, on_delete=models.CASCADE, related_name="messages")
    role = models.CharField(max_length=20, choices=Role.choices)
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    metadata = models.JSONField(default=dict, blank=True)  # Token counts, model used, etc.
    referenced_events = models.ManyToManyField(Event, blank=True, related_name="chat_references")

    class Meta:
        ordering = ['created_at']
        indexes = [models.Index(fields=['session', 'created_at'])]

    def __str__(self):
        preview = self.content[:50] + "..." if len(self.content) > 50 else self.content
        return f"{self.role}: {preview}"
