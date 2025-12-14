"""
Celery tasks for the venues app.

Handles geocoding with rate limiting.
"""

import logging
import time
from celery import shared_task

logger = logging.getLogger(__name__)

# Rate limiting for Nominatim (1 request per 1.5 seconds)
GEOCODE_DELAY = 1.5


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def geocode_venue_task(self, venue_id: int):
    """
    Geocode a single venue.

    Replaces the threading-based queue_geocoding function.
    Rate limiting is handled by Celery worker concurrency settings.

    Args:
        venue_id: ID of the Venue to geocode
    """
    from venues.models import Venue
    from venues.geocoding import geocode_address

    try:
        venue = Venue.objects.get(id=venue_id)
    except Venue.DoesNotExist:
        logger.warning(f"Venue {venue_id} not found for geocoding")
        return {'venue_id': venue_id, 'status': 'not_found'}

    # Skip if already geocoded
    if venue.latitude is not None and venue.longitude is not None:
        logger.debug(f"Venue {venue_id} already has coordinates")
        return {'venue_id': venue_id, 'status': 'already_geocoded'}

    # Build address string
    address = venue.get_full_address()
    if not address:
        logger.warning(f"Venue {venue_id} has no address to geocode")
        return {'venue_id': venue_id, 'status': 'no_address'}

    try:
        # Add delay for rate limiting
        time.sleep(GEOCODE_DELAY)

        lat, lon = geocode_address(address)

        if lat is not None and lon is not None:
            venue.latitude = lat
            venue.longitude = lon
            venue.save(update_fields=['latitude', 'longitude'])
            logger.info(f"Geocoded venue {venue_id}: ({lat}, {lon})")
            return {'venue_id': venue_id, 'status': 'success', 'lat': float(lat), 'lon': float(lon)}
        else:
            logger.warning(f"No geocoding result for venue {venue_id}")
            return {'venue_id': venue_id, 'status': 'no_result'}

    except Exception as exc:
        logger.error(f"Geocoding failed for venue {venue_id}: {exc}")
        raise self.retry(exc=exc)


@shared_task
def bulk_geocode_venues(limit: int = 100):
    """
    Geocode multiple venues missing coordinates.

    Args:
        limit: Maximum venues to geocode in this batch
    """
    from venues.models import Venue

    venues = Venue.objects.filter(
        latitude__isnull=True,
        longitude__isnull=True
    ).exclude(
        street_address='',
        city=''
    )[:limit]

    count = venues.count()
    if count == 0:
        logger.info("No venues need geocoding")
        return {'status': 'success', 'count': 0}

    logger.info(f"Queueing geocoding for {count} venues")

    for venue in venues:
        geocode_venue_task.delay(venue.id)

    return {'status': 'queued', 'count': count}
