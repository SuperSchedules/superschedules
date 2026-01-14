"""
Venue model for structured location/address data.

Provides deduplication and normalization for event venues,
plus enrichment fields for classification, audience, and content.
"""

from django.core.exceptions import ValidationError
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
        # Civic/Government
        ("town_hall", "Town Hall"),
        ("city_hall", "City Hall"),
        ("government_office", "Government Office"),
        # Catch-alls
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
        ("general", "General"),  # All ages welcome
        ("families", "Families"),  # Family-oriented
        ("children", "Children"),  # Primarily for kids
        ("teens", "Teens"),  # Teen-focused
        ("adults", "Adults"),  # Adults only (18+)
        ("seniors", "Seniors"),  # Senior-focused
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

    # Event scraping URLs (from Navigator)
    events_urls = models.JSONField(default=list, blank=True, help_text="List of event calendar URLs for this venue")

    description = models.TextField(blank=True, help_text="General venue description for RAG")
    kids_summary = models.TextField(blank=True, help_text="Kid-focused venue summary for RAG")

    # Enrichment metadata
    enrichment_status = models.CharField(max_length=16, choices=ENRICHMENT_STATUS_CHOICES, default="none", help_text="Enrichment status")
    last_enriched_at = models.DateTimeField(null=True, blank=True, help_text="When venue was last enriched")

    # OSM identification (for deduplication and updates from OpenStreetMap)
    osm_type = models.CharField(max_length=10, blank=True, null=True, help_text="OSM element type: node, way, or relation")
    osm_id = models.BigIntegerField(blank=True, null=True, help_text="OpenStreetMap element ID")

    # OSM-sourced data
    category = models.CharField(max_length=50, blank=True, help_text="Raw category from OSM (library, museum, park, etc.)")
    opening_hours_raw = models.TextField(blank=True, help_text="Operating hours in OSM format")
    operator = models.CharField(max_length=255, blank=True, help_text="Operating organization (e.g., 'Town of Needham')")
    wikidata_id = models.CharField(max_length=50, blank=True, help_text="Wikidata ID for linked data enrichment")
    phone = models.CharField(max_length=50, blank=True, help_text="Phone number")

    # Track data source
    data_source = models.CharField(max_length=50, default='scraped', help_text="Data source: 'osm', 'scraped', or 'manual'")

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=['slug', 'city', 'state', 'postal_code']),
            models.Index(fields=['city', 'state']),
            # Index for address-based venue lookups (deduplication by physical address)
            models.Index(fields=['city', 'state', 'street_address'], name='venue_address_lookup'),
            # Index for OSM lookups
            models.Index(fields=['osm_type', 'osm_id']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['slug', 'city', 'state', 'postal_code'],
                name='unique_venue_identity'
            ),
            # OSM deduplication: only one venue per OSM element
            models.UniqueConstraint(
                fields=['osm_type', 'osm_id'],
                condition=models.Q(osm_id__isnull=False),
                name='unique_osm_venue'
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

    def clean(self):
        """
        Validate audience field consistency.

        Enforces rules:
        - Rule A: Adult-only venues (min_age >= 18) cannot have kid age groups
        - Rule B: audience_primary must be compatible with audience_age_groups
        - Rule C: Auto-derive audience_primary from age_groups when set to "general"
        """
        super().clean()

        groups = set(self.audience_age_groups or [])
        min_age = self.audience_min_age
        primary = self.audience_primary

        kid_groups = {"infant", "toddler", "child"}

        # Rule A: adult-only vs kid groups
        if min_age is not None and min_age >= 18:
            if groups & kid_groups:
                raise ValidationError({
                    "audience_age_groups": "Cannot include infant/toddler/child when minimum age is 18+."
                })

        # Rule B: audience_primary compatibility
        if primary == "children":
            if not (groups & kid_groups):
                raise ValidationError({
                    "audience_primary": "Primary audience 'children' requires at least one of: infant, toddler, child in age groups."
                })

        if primary == "families":
            if not (groups & kid_groups):
                raise ValidationError({
                    "audience_primary": "Primary audience 'families' requires at least one of: infant, toddler, child in age groups."
                })

        if primary == "adults":
            if groups & kid_groups:
                raise ValidationError({
                    "audience_primary": "Primary audience 'adults' cannot have infant/toddler/child in age groups."
                })

        # Rule C: auto-derive audience_primary from groups when "general"
        if primary == "general" and groups:
            if groups & kid_groups:
                self.audience_primary = "families"
            elif groups == {"adult"}:
                self.audience_primary = "adults"
            elif groups == {"senior"}:
                self.audience_primary = "seniors"

    @property
    def is_family_friendly(self) -> bool:
        """
        Check if venue is suitable for families with children.

        Used by search/recommendation code to include venues for kid-focused queries.
        Returns True if:
        - audience_primary is 'families' or 'children'
        - audience_age_groups contains infant, toddler, or child
        - audience_tags contains 'family_friendly'
        """
        groups = set(self.audience_age_groups or [])
        tags = set(self.audience_tags or [])

        if self.audience_primary in ("families", "children"):
            return True
        if groups & {"infant", "toddler", "child"}:
            return True
        if "family_friendly" in tags:
            return True
        return False

    @property
    def is_adults_only(self) -> bool:
        """
        Check if venue is restricted to adults (18+).

        Used by search/recommendation code to exclude venues from family queries.
        Returns True if:
        - audience_primary is 'adults'
        - audience_min_age is 18 or higher
        """
        if self.audience_primary == "adults":
            return True
        if self.audience_min_age is not None and self.audience_min_age >= 18:
            return True
        return False


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
