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


# =============================================================================
# Venue Embedding Tasks
# =============================================================================

@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def generate_venue_embedding(self, venue_id: int):
    """
    Generate RAG embedding for a single venue.

    Follows the same pattern as events.tasks.generate_embedding.
    """
    from venues.models import Venue
    from api.rag_service import get_rag_service

    try:
        venue = Venue.objects.get(id=venue_id)
        logger.info(f"Generating embedding for venue {venue_id}: {venue.name}")

        rag_service = get_rag_service()
        rag_service.update_venue_embeddings(venue_ids=[venue_id])

        logger.info(f"Embedding generated for venue {venue_id}")
        return {'venue_id': venue_id, 'status': 'success'}

    except Venue.DoesNotExist:
        logger.warning(f"Venue {venue_id} not found for embedding generation")
        return {'venue_id': venue_id, 'status': 'not_found'}

    except Exception as exc:
        logger.error(f"Embedding generation failed for venue {venue_id}: {exc}")
        raise self.retry(exc=exc)


@shared_task
def bulk_generate_venue_embeddings(venue_ids: list = None, force: bool = False, batch_size: int = 100):
    """
    Orchestrator task that spawns batched subtasks for venue embedding generation.

    Args:
        venue_ids: Specific venue IDs to update (default: all missing)
        force: If True, regenerate all embeddings even if they exist
        batch_size: Number of venues per batch task
    """
    from venues.models import Venue

    if force:
        if venue_ids:
            Venue.objects.filter(id__in=venue_ids).update(embedding=None)
            ids_to_process = list(Venue.objects.filter(id__in=venue_ids).values_list('id', flat=True))
        else:
            Venue.objects.all().update(embedding=None)
            ids_to_process = list(Venue.objects.all().values_list('id', flat=True))
        logger.info(f"Force mode: cleared embeddings for {len(ids_to_process)} venues")
    else:
        if venue_ids:
            ids_to_process = list(Venue.objects.filter(id__in=venue_ids, embedding__isnull=True).values_list('id', flat=True))
        else:
            ids_to_process = list(Venue.objects.filter(embedding__isnull=True).values_list('id', flat=True))

    total_count = len(ids_to_process)
    if total_count == 0:
        logger.info("No venues need embedding generation")
        return {'status': 'success', 'total': 0, 'batches': 0}

    batches_created = 0
    for i in range(0, total_count, batch_size):
        batch_ids = ids_to_process[i:i + batch_size]
        generate_venue_embeddings_batch.delay(batch_ids)
        batches_created += 1

    logger.info(f"Spawned {batches_created} batch tasks for {total_count} venues")
    return {'status': 'spawned', 'total': total_count, 'batches': batches_created}


@shared_task(bind=True, max_retries=2, default_retry_delay=120)
def generate_venue_embeddings_batch(self, venue_ids: list):
    """Process a batch of venues for embedding generation."""
    from api.rag_service import get_rag_service

    try:
        logger.info(f"Processing embedding batch of {len(venue_ids)} venues")
        rag_service = get_rag_service()
        rag_service.update_venue_embeddings(venue_ids=venue_ids)
        logger.info(f"Completed embedding batch of {len(venue_ids)} venues")
        return {'status': 'success', 'count': len(venue_ids)}
    except Exception as exc:
        logger.error(f"Venue embedding batch failed: {exc}")
        raise self.retry(exc=exc)
