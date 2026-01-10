"""
Venue model for structured location/address data.

Provides deduplication and normalization for event venues,
plus enrichment fields for classification, audience, and content.
"""

from django.db import models
from django.utils.text import slugify


class Venue(models.Model):
    """
    First-class venue model with structured address components.

    Replaces the fragile single-field address storage in Place model.
    Supports deduplication via (slug, city, state, postal_code) key.
    """

    # Venue kind choices
    VENUE_KIND_CHOICES = [
        ("library", "Library"),
        ("museum", "Museum"),
        ("park", "Park"),
        ("playground", "Playground"),
        ("beach", "Beach"),
        ("skating_rink", "Skating Rink"),
        ("dog_park", "Dog Park"),
        ("pool", "Pool"),
        ("school", "School"),
        ("community_center", "Community Center"),
        ("church", "Church"),
        ("theater", "Theater"),
        ("restaurant", "Restaurant"),
        ("senior_center", "Senior Center"),
        ("ymca", "YMCA"),
        ("sports_facility", "Sports Facility"),
        ("nature_center", "Nature Center"),
        ("zoo", "Zoo"),
        ("aquarium", "Aquarium"),
        ("other", "Other"),
        ("unknown", "Unknown"),
    ]

    VENUE_NAME_QUALITY_CHOICES = [
        ("good", "Good"),
        ("address_only", "Address Only"),
        ("empty", "Empty"),
        ("placeholder", "Placeholder"),
        ("unknown", "Unknown"),
    ]

    AUDIENCE_PRIMARY_CHOICES = [
        ("children", "Children"),
        ("dogs", "Dogs"),
        ("senior", "Senior"),
        ("general", "General"),
    ]

    ENRICHMENT_STATUS_CHOICES = [
        ("none", "Not Enriched"),
        ("partial", "Partially Enriched"),
        ("complete", "Fully Enriched"),
    ]

    # Core identity
    name = models.CharField(max_length=200, help_text="Venue name (e.g., 'Waltham Public Library')")
    slug = models.SlugField(max_length=200, db_index=True, help_text="URL-friendly venue identifier")

    # Structured address components
    street_address = models.CharField(max_length=255, blank=True, help_text="Street address (e.g., '735 Main Street')")
    city = models.CharField(max_length=100, help_text="City name")
    state = models.CharField(max_length=50, help_text="State/region (abbreviation or full name)")
    postal_code = models.CharField(max_length=20, blank=True, help_text="ZIP/postal code")
    country = models.CharField(max_length=2, default='US', help_text="ISO 3166-1 alpha-2 country code")

    # Geocoding
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True, help_text="Latitude coordinate")
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True, help_text="Longitude coordinate")

    # Source tracking
    source_domain = models.CharField(max_length=255, blank=True, help_text="Domain where venue was discovered")
    canonical_url = models.URLField(blank=True, help_text="Venue's official website")

    # Raw data preservation
    raw_schema = models.JSONField(null=True, blank=True, help_text="Original Schema.org Place data")

    # Venue classification (enrichment)
    venue_kind = models.CharField(max_length=32, choices=VENUE_KIND_CHOICES, null=True, blank=True, help_text="Type of venue")
    venue_kind_confidence = models.FloatField(null=True, blank=True, help_text="Confidence in venue_kind classification (0-1)")
    venue_name_quality = models.CharField(max_length=16, choices=VENUE_NAME_QUALITY_CHOICES, null=True, blank=True, help_text="Quality of venue name")

    # Audience suitability (enrichment)
    audience_age_groups = models.JSONField(default=list, blank=True, help_text="Age groups: infant, toddler, child, teen, adult, senior")
    audience_tags = models.JSONField(default=list, blank=True, help_text="Tags: families, dogs, stroller_friendly, wheelchair_accessible, etc.")
    audience_min_age = models.IntegerField(null=True, blank=True, help_text="Minimum recommended age")
    audience_primary = models.CharField(max_length=16, choices=AUDIENCE_PRIMARY_CHOICES, default="general", help_text="Primary audience")

    # Venue content (enrichment)
    website_url = models.URLField(null=True, blank=True, help_text="Venue's official website URL")
    website_url_confidence = models.FloatField(null=True, blank=True, help_text="Confidence in website_url (0-1)")
    description = models.TextField(blank=True, help_text="General venue description for RAG")
    kids_summary = models.TextField(blank=True, help_text="Kid-focused venue summary for RAG")

    # Enrichment metadata
    enrichment_status = models.CharField(max_length=16, choices=ENRICHMENT_STATUS_CHOICES, default="none", help_text="Enrichment status")
    last_enriched_at = models.DateTimeField(null=True, blank=True, help_text="When venue was last enriched")

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=['slug', 'city', 'state', 'postal_code']),
            models.Index(fields=['city', 'state']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['slug', 'city', 'state', 'postal_code'],
                name='unique_venue_identity'
            )
        ]

    def __str__(self) -> str:
        """Return human-readable venue string."""
        return f"{self.name}, {self.city}, {self.state}"

    def get_full_address(self) -> str:
        """Return formatted full address."""
        parts = []
        if self.street_address:
            parts.append(self.street_address)
        parts.append(f"{self.city}, {self.state}")
        if self.postal_code:
            parts[-1] = f"{parts[-1]} {self.postal_code}"
        return ", ".join(parts)

    def save(self, *args, **kwargs):
        """Auto-generate slug if not provided."""
        if not self.slug and self.name:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)


class VenueHours(models.Model):
    """Operating hours for a venue, one record per day of week."""

    DAY_OF_WEEK_CHOICES = [
        (0, "Monday"),
        (1, "Tuesday"),
        (2, "Wednesday"),
        (3, "Thursday"),
        (4, "Friday"),
        (5, "Saturday"),
        (6, "Sunday"),
    ]

    venue = models.ForeignKey(Venue, on_delete=models.CASCADE, related_name="hours")
    day_of_week = models.IntegerField(choices=DAY_OF_WEEK_CHOICES)
    open_time = models.TimeField(null=True, blank=True)
    close_time = models.TimeField(null=True, blank=True)
    is_closed = models.BooleanField(default=False, help_text="True if venue is closed this day")
    notes = models.TextField(blank=True, help_text="Additional info (e.g., 'Closed 12-1pm for lunch')")

    class Meta:
        unique_together = ["venue", "day_of_week"]
        ordering = ["day_of_week"]
        verbose_name = "Venue Hours"
        verbose_name_plural = "Venue Hours"

    def __str__(self) -> str:
        day_name = dict(self.DAY_OF_WEEK_CHOICES).get(self.day_of_week, "Unknown")
        if self.is_closed:
            return f"{self.venue.name} - {day_name}: Closed"
        return f"{self.venue.name} - {day_name}: {self.open_time} - {self.close_time}"
