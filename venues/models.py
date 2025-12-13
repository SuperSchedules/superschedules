"""
Venue model for structured location/address data.

Provides deduplication and normalization for event venues.
"""

from django.db import models
from django.utils.text import slugify


class Venue(models.Model):
    """
    First-class venue model with structured address components.

    Replaces the fragile single-field address storage in Place model.
    Supports deduplication via (slug, city, state, postal_code) key.
    """

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
