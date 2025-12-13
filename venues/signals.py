"""Django signals for the venues app."""

import logging

logger = logging.getLogger(__name__)


def venue_post_save(sender, instance, created, **kwargs):
    """
    Queue geocoding for newly created venues without coordinates.

    Only triggers for new venues (created=True) that don't have lat/long.
    Geocoding runs asynchronously with a delay to respect rate limits.
    """
    if not created:
        return

    if instance.latitude is not None and instance.longitude is not None:
        logger.debug(f"Venue {instance.id} already has coordinates, skipping geocoding")
        return

    from venues.geocoding import queue_geocoding
    queue_geocoding(instance.id)
