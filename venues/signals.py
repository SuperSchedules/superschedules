"""Django signals for the venues app."""

import logging

logger = logging.getLogger(__name__)

# Fields that affect embedding content
EMBEDDING_FIELDS = {'name', 'description', 'kids_summary', 'venue_kind', 'audience_tags', 'audience_age_groups', 'audience_primary'}


def venue_post_save(sender, instance, created, update_fields=None, **kwargs):
    """
    Queue geocoding and embedding generation for venues.

    Geocoding: Only triggers for new venues (created=True) that don't have lat/long.
    Embeddings: Triggers on create or when embedding-related fields change.
    """
    # Geocoding logic (existing)
    if created and instance.latitude is None and instance.longitude is None:
        try:
            from venues.tasks import geocode_venue_task
            geocode_venue_task.delay(instance.id)
            logger.info(f"Queued geocoding for venue {instance.id}: {instance.name}")
        except Exception as e:
            logger.error(f"Failed to queue geocoding for venue {instance.id}: {e}")

    # Embedding generation logic (new)
    should_update_embedding = False

    if created:
        should_update_embedding = True
    elif instance.embedding is None:
        should_update_embedding = True
    elif update_fields is not None:
        should_update_embedding = bool(EMBEDDING_FIELDS.intersection(update_fields))

    if should_update_embedding:
        try:
            from venues.tasks import generate_venue_embedding
            generate_venue_embedding.delay(instance.id)
            logger.info(f"Queued embedding generation for venue {instance.id}: {instance.name}")
        except Exception as e:
            logger.error(f"Failed to queue embedding for venue {instance.id}: {e}")
