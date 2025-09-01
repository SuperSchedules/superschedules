"""
Schema.org Place models for rich location data.

Following Schema.org Place specification:
https://schema.org/Place
"""

from django.db import models
from django.core.validators import RegexValidator


class Place(models.Model):
    """
    Schema.org Place object for rich venue data.
    
    Based on https://schema.org/Place specification.
    Supports venue deduplication and geocoding.
    """
    
    # Core Schema.org Place fields
    name = models.CharField(
        max_length=200, 
        blank=True, 
        help_text="Venue/room name (e.g., 'Children's Room')"
    )
    
    address = models.TextField(
        blank=True, 
        help_text="Full address for geocoding and location searches"
    ) 
    
    telephone = models.CharField(
        max_length=50, 
        blank=True,
        validators=[
            RegexValidator(
                regex=r'^[\+]?[1-9]?[\d\s\-\(\)\.]+$',
                message="Enter a valid phone number"
            )
        ],
        help_text="Contact phone number"
    )
    
    url = models.URLField(
        blank=True, 
        help_text="Venue website"
    )
    
    # Geocoding fields (for future mapping features)
    latitude = models.DecimalField(
        max_digits=9, 
        decimal_places=6, 
        null=True, 
        blank=True,
        help_text="Latitude coordinate for mapping"
    )
    
    longitude = models.DecimalField(
        max_digits=9, 
        decimal_places=6, 
        null=True, 
        blank=True,
        help_text="Longitude coordinate for mapping"
    )
    
    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def __str__(self):
        """Return human-readable location string."""
        if self.name and self.address:
            return f"{self.name}, {self.address}"
        elif self.name:
            return self.name
        elif self.address:
            return self.address
        else:
            return "Unknown Place"
    
    def get_city(self) -> str:
        """Extract city from address for search purposes."""
        if not self.address:
            return ""
        
        # Simple city extraction - look for common patterns
        import re
        
        # Pattern: "City, State" or "City, ST"
        city_match = re.search(r',\s*([^,]+),\s*[A-Z]{2}(?:\s|$|,)', self.address)
        if city_match:
            return city_match.group(1).strip()
        
        # Pattern: "City State" (less reliable)
        parts = self.address.split(',')
        if len(parts) >= 2:
            # Take second-to-last part as potential city
            potential_city = parts[-2].strip()
            if potential_city and not potential_city.isdigit():
                return potential_city
        
        return ""
    
    def get_search_text(self) -> str:
        """Get comprehensive text for search indexing and RAG."""
        parts = [
            self.name or "",
            self.address or "",
            self.get_city(),  # Extract city for location searches
        ]
        
        return " ".join(filter(None, parts))
    
    @classmethod
    def create_from_schema_org(cls, location_data):
        """
        Create Place from Schema.org JSON-LD location data.
        
        Args:
            location_data: Dict or list of Schema.org Place objects
            
        Returns:
            Place instance or None
        """
        if isinstance(location_data, list):
            # Take first Place object from array
            if location_data and isinstance(location_data[0], dict):
                location_data = location_data[0]
            else:
                return None
        
        if not isinstance(location_data, dict):
            return None
        
        # Only process if it's a Schema.org Place
        if location_data.get("@type") != "Place":
            return None
        
        place_data = {
            'name': location_data.get('name', ''),
            'address': location_data.get('address', ''),
            'telephone': location_data.get('telephone', ''),
            'url': location_data.get('url', '')
        }
        
        # Try to find existing venue by address to avoid duplicates
        if place_data['address']:
            existing_place, created = cls.objects.get_or_create(
                address=place_data['address'],
                defaults=place_data
            )
            return existing_place
        else:
            # Create new place without address constraint
            return cls.objects.create(**place_data)
    
    class Meta:
        # Avoid duplicate venues - same address = same place
        constraints = [
            models.UniqueConstraint(
                fields=['address'], 
                name='unique_venue_address',
                condition=models.Q(address__isnull=False) & ~models.Q(address='')
            )
        ]
        
        indexes = [
            models.Index(fields=['name']),
            models.Index(fields=['address']),  
        ]