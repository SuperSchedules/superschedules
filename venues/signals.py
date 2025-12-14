"""Django signals for the venues app."""

import logging

logger = logging.getLogger(__name__)


def venue_post_save(sender, instance, created, **kwargs):
    """
    Queue geocoding for newly created venues without coordinates.

    Only triggers for new venues (created=True) that don't have lat/long.
    Geocoding runs asynchronously via Celery with rate limiting.
    """
    if not created:
        return

    if instance.latitude is not None and instance.longitude is not None:
        logger.debug(f"Venue {instance.id} already has coordinates, skipping geocoding")
        return

    try:
        from venues.tasks import geocode_venue_task
        geocode_venue_task.delay(instance.id)
        logger.info(f"Queued geocoding for venue {instance.id}: {instance.name}")
    except Exception as e:
        logger.error(f"Failed to queue geocoding for venue {instance.id}: {e}")
