from django.db import models
from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver
from pgvector.django import VectorField
from django.contrib.postgres.indexes import GinIndex
import secrets
import logging

# Import Place model for Schema.org location support
from .place_models import Place

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
    
    # Enhanced location fields with Schema.org Place support
    place = models.ForeignKey(
        Place, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        help_text="Rich Schema.org Place object with full venue details"
    )
    location = models.CharField(
        max_length=255,
        help_text="Fallback location string for backward compatibility"
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
        """Get location as string for backward compatibility."""
        try:
            if self.place:
                return str(self.place)
        except:
            # Fallback for events without place relationship
            pass
        return self.location or ""
    
    def get_full_address(self) -> str:
        """Get full address for geocoding and location searches."""
        try:
            if self.place and self.place.address:
                return self.place.address
        except:
            # Fallback for events without place relationship
            pass
        return ""
    
    def get_city(self) -> str:
        """Extract city from location for geographic searches."""
        try:
            if self.place:
                return self.place.get_city()
        except:
            # Fallback for events without place relationship
            pass
        return ""
    
    def get_location_search_text(self) -> str:
        """Get comprehensive location text for RAG search."""
        try:
            if self.place:
                return self.place.get_search_text()
        except:
            # Fallback for events without place relationship
            pass
        return self.location or ""
    
    @classmethod
    def create_with_schema_org_data(cls, event_data: dict, source):
        """
        Create Event with rich Schema.org data including Place objects.
        
        Args:
            event_data: Event data from collector with potential Schema.org location
            source: Source instance
            
        Returns:
            Event instance
        """
        # Extract location data and create Place if it's Schema.org format
        location_data = event_data.get('location')
        place_obj = None
        location_text = ""
        
        if isinstance(location_data, dict) and (location_data.get('type') == 'Place' or location_data.get('@type') == 'Place'):
            # Create Place from Schema.org data
            place_obj = Place.create_from_schema_org(location_data)
            location_text = str(place_obj) if place_obj else ""
        elif isinstance(location_data, list):
            # Handle array of Place objects
            if location_data and isinstance(location_data[0], dict):
                place_obj = Place.create_from_schema_org(location_data[0])
                location_text = str(place_obj) if place_obj else ""
        else:
            # Simple string location
            location_text = str(location_data) if location_data else ""
        
        # Create event with place reference
        return cls.objects.create(
            source=source,
            external_id=event_data.get('external_id', ''),
            title=event_data.get('title', ''),
            description=event_data.get('description', ''),
            place=place_obj,
            location=location_text,
            organizer=event_data.get('organizer', ''),
            event_status=event_data.get('event_status', ''),
            event_attendance_mode=event_data.get('event_attendance_mode', ''),
            start_time=event_data.get('start_time'),
            end_time=event_data.get('end_time'),
            url=event_data.get('url', ''),
            metadata_tags=event_data.get('tags', [])
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
def update_event_embedding(sender, instance, created, update_fields=None, **kwargs):
    """
    Auto-generate RAG embedding when Event is created or updated.
    
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
        embedding_fields = {'title', 'description', 'location', 'start_time'}
        should_update = bool(embedding_fields.intersection(update_fields))
    
    if should_update:
        try:
            # Import here to avoid circular imports
            from api.rag_service import get_rag_service
            
            logger.info(f"Generating embedding for event: {instance.title}")
            rag_service = get_rag_service()
            rag_service.update_event_embeddings(event_ids=[instance.id])
            
        except Exception as e:
            logger.error(f"Failed to generate embedding for event {instance.id}: {e}")
