"""
Geocoding service for venues using OpenStreetMap/Nominatim.

Provides async geocoding with rate limiting to respect Nominatim's usage policy.
"""

import logging
import threading
from decimal import Decimal
from typing import Optional, Tuple

from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut, GeocoderServiceError

logger = logging.getLogger(__name__)

# Delay between geocoding requests (seconds) to respect Nominatim rate limits
GEOCODE_DELAY = 1.5

# User agent for Nominatim (required by their usage policy)
USER_AGENT = "superschedules-events-platform"


def geocode_address(address: str) -> Tuple[Optional[Decimal], Optional[Decimal]]:
    """
    Geocode an address string to latitude/longitude.

    Args:
        address: Full address string to geocode

    Returns:
        Tuple of (latitude, longitude) as Decimals, or (None, None) if not found
    """
    if not address or not address.strip():
        return (None, None)

    try:
        geocoder = Nominatim(user_agent=USER_AGENT, timeout=10)
        location = geocoder.geocode(address)

        if location:
            lat = Decimal(str(location.latitude)).quantize(Decimal('0.000001'))
            lon = Decimal(str(location.longitude)).quantize(Decimal('0.000001'))
            logger.info(f"Geocoded '{address}' to ({lat}, {lon})")
            return (lat, lon)

        logger.warning(f"No geocoding result for: {address}")
        return (None, None)

    except (GeocoderTimedOut, GeocoderServiceError) as e:
        logger.error(f"Geocoding service error for '{address}': {e}")
        return (None, None)
    except Exception as e:
        logger.error(f"Unexpected geocoding error for '{address}': {e}")
        return (None, None)


def geocode_venue(venue_id: int) -> bool:
    """
    Geocode a venue by ID and update its coordinates.

    Args:
        venue_id: ID of the Venue to geocode

    Returns:
        True if coordinates were updated, False otherwise
    """
    from venues.models import Venue

    try:
        venue = Venue.objects.get(id=venue_id)
    except Venue.DoesNotExist:
        logger.warning(f"Venue {venue_id} not found for geocoding")
        return False

    # Skip if already geocoded
    if venue.latitude is not None and venue.longitude is not None:
        logger.debug(f"Venue {venue_id} already has coordinates, skipping")
        return False

    # Build address string
    address = venue.get_full_address()
    if not address:
        logger.warning(f"Venue {venue_id} has no address to geocode")
        return False

    lat, lon = geocode_address(address)

    if lat is not None and lon is not None:
        venue.latitude = lat
        venue.longitude = lon
        venue.save(update_fields=['latitude', 'longitude'])
        logger.info(f"Updated venue {venue_id} coordinates: ({lat}, {lon})")
        return True

    return False


def queue_geocoding(venue_id: int) -> None:
    """
    Queue a venue for async geocoding with rate-limiting delay.

    Runs geocoding in a background thread after GEOCODE_DELAY seconds.

    Args:
        venue_id: ID of the Venue to geocode
    """
    def delayed_geocode():
        import time
        time.sleep(GEOCODE_DELAY)
        try:
            geocode_venue(venue_id)
        except Exception as e:
            logger.error(f"Error in delayed geocoding for venue {venue_id}: {e}")

    thread = threading.Thread(target=delayed_geocode, daemon=True)
    thread.start()
    logger.debug(f"Queued geocoding for venue {venue_id}")
