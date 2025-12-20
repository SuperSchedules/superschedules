"""
Canonical location/place model for deterministic location resolution.

Uses US Census Gazetteer as authoritative source for US city/place coordinates.
"""

import re

from django.db import models


def normalize_for_matching(name: str) -> str:
    """
    Normalize a location name for matching.

    - Lowercase
    - Remove "city of", "town of", etc. prefixes
    - Remove punctuation except hyphens
    - Collapse whitespace
    """
    if not name:
        return ""

    result = name.lower().strip()

    # Remove "city of", "town of", etc. prefixes
    result = re.sub(r'^(city|town|village|borough|township)\s+of\s+', '', result)

    # Remove punctuation except hyphens (for compound names like "Winston-Salem")
    result = re.sub(r'[^\w\s-]', '', result)

    # Collapse whitespace
    result = re.sub(r'\s+', ' ', result)

    return result.strip()


class Location(models.Model):
    """
    Canonical US place/city for location resolution.

    Imported from US Census Gazetteer. Provides authoritative coordinates
    for location queries like "events near Newton".
    """

    # Census identifiers
    geoid = models.CharField(max_length=10, unique=True, help_text="Census GEOID (State FIPS + Place FIPS)")

    # Names
    name = models.CharField(max_length=200, db_index=True, help_text="Place name (e.g., 'Newton')")
    normalized_name = models.CharField(max_length=200, db_index=True, help_text="Lowercase, cleaned name for matching")

    # Location
    state = models.CharField(max_length=2, db_index=True, help_text="USPS state abbreviation (e.g., 'MA')")
    country_code = models.CharField(max_length=2, default='US', help_text="ISO 3166-1 alpha-2 country code")

    # Coordinates (Census internal point)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, help_text="Latitude of internal point")
    longitude = models.DecimalField(max_digits=9, decimal_places=6, help_text="Longitude of internal point")

    # Metadata
    lsad = models.CharField(max_length=20, blank=True, help_text="Legal/Statistical Area Descriptor (city, town, CDP)")
    population = models.PositiveIntegerField(null=True, blank=True, help_text="Population for disambiguation ranking")
    land_area_sqmi = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True, help_text="Land area in square miles")

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['state', 'name']
        indexes = [
            models.Index(fields=['normalized_name', 'state']),
            models.Index(fields=['normalized_name', 'country_code']),
            models.Index(fields=['state', 'name']),
        ]
        constraints = [
            models.UniqueConstraint(fields=['normalized_name', 'state', 'country_code'], name='unique_location_by_state')
        ]

    def __str__(self) -> str:
        return f"{self.name}, {self.state}"

    def save(self, *args, **kwargs):
        """Auto-generate normalized_name from name."""
        if self.name and not self.normalized_name:
            self.normalized_name = normalize_for_matching(self.name)
        super().save(*args, **kwargs)
