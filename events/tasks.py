"""
Celery tasks for the events app.

Handles:
- RAG embedding generation (replaces post_save signal)
- ScrapingJob processing (replaces polling-based queue)
- Periodic maintenance (old event cleanup, stats)
"""

import logging
from celery import shared_task
from django.utils import timezone
from datetime import timedelta

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def generate_embedding(self, event_id: int):
    """
    Generate RAG embedding for a single event.

    Replaces the synchronous post_save signal handler.

    Args:
        event_id: ID of the Event to generate embedding for
    """
    from events.models import Event
    from api.rag_service import get_rag_service

    try:
        event = Event.objects.get(id=event_id)
        logger.info(f"Generating embedding for event {event_id}: {event.title}")

        rag_service = get_rag_service()
        rag_service.update_event_embeddings(event_ids=[event_id])

        logger.info(f"Embedding generated for event {event_id}")
        return {'event_id': event_id, 'status': 'success'}

    except Event.DoesNotExist:
        logger.warning(f"Event {event_id} not found for embedding generation")
        return {'event_id': event_id, 'status': 'not_found'}

    except Exception as exc:
        logger.error(f"Embedding generation failed for event {event_id}: {exc}")
        raise self.retry(exc=exc)


@shared_task(bind=True, max_retries=3, default_retry_delay=300)
def process_scraping_job(self, job_id: int):
    """
    Process a single scraping job via collector API.

    Replaces the polling-based queue endpoint pattern.

    Args:
        job_id: ID of the ScrapingJob to process
    """
    from events.models import ScrapingJob, Event, Source
    from django.conf import settings
    import requests

    try:
        job = ScrapingJob.objects.select_for_update().get(id=job_id, status='pending')
    except ScrapingJob.DoesNotExist:
        logger.warning(f"Job {job_id} not found or not pending")
        return {'job_id': job_id, 'status': 'skipped'}

    # Mark as processing
    job.status = 'processing'
    job.locked_at = timezone.now()
    job.locked_by = f"celery-{self.request.id}"
    job.save()

    collector_url = getattr(settings, 'COLLECTOR_URL', 'http://localhost:8001')

    try:
        response = requests.post(
            f"{collector_url}/extract",
            json={"url": job.url, "extraction_hints": {}},
            timeout=180
        )

        if response.status_code == 200:
            data = response.json()
            events_created = 0

            if data.get('success') and data.get('events'):
                source, _ = Source.objects.get_or_create(
                    base_url=job.url.rsplit('/', 1)[0] if '/' in job.url else job.url,
                    defaults={'status': 'processed'}
                )

                for event_data in data['events']:
                    try:
                        event, was_created = Event.create_with_schema_org_data(event_data, source)
                        events_created += 1
                        # Queue embedding generation
                        generate_embedding.delay(event.id)
                    except Exception as e:
                        logger.error(f"Failed to save event: {e}")

            job.status = 'completed'
            job.events_found = events_created
            job.completed_at = timezone.now()
            job.save()

            return {'job_id': job_id, 'status': 'completed', 'events': events_created}
        else:
            raise Exception(f"Collector API error: {response.status_code}")

    except Exception as exc:
        job.retry_count += 1
        if job.retry_count >= job.max_retries:
            job.status = 'failed'
            job.error_message = str(exc)
            job.completed_at = timezone.now()
            job.save()
            logger.error(f"Job {job_id} failed permanently: {exc}")
            return {'job_id': job_id, 'status': 'failed'}
        else:
            job.status = 'pending'
            job.locked_at = None
            job.locked_by = ''
            job.save()
            raise self.retry(exc=exc)


@shared_task
def bulk_generate_embeddings(event_ids: list = None, force: bool = False, batch_size: int = 100):
    """
    Orchestrator task that spawns batched subtasks for embedding generation.

    This task doesn't do the work itself - it identifies events needing embeddings
    and spawns batch subtasks to process them in chunks, avoiding timeouts.

    Args:
        event_ids: List of event IDs, or None for all events
        force: If True, regenerate ALL embeddings (clear existing first)
        batch_size: Number of events per subtask (default 100)
    """
    from events.models import Event

    if force:
        # Clear embeddings to force regeneration
        if event_ids:
            Event.objects.filter(id__in=event_ids).update(embedding=None)
            ids_to_process = list(Event.objects.filter(id__in=event_ids).values_list('id', flat=True))
        else:
            Event.objects.all().update(embedding=None)
            ids_to_process = list(Event.objects.all().values_list('id', flat=True))
        logger.info(f"Force mode: cleared embeddings for {len(ids_to_process)} events")
    else:
        if event_ids:
            ids_to_process = list(Event.objects.filter(id__in=event_ids, embedding__isnull=True).values_list('id', flat=True))
        else:
            ids_to_process = list(Event.objects.filter(embedding__isnull=True).values_list('id', flat=True))

    total_count = len(ids_to_process)
    if total_count == 0:
        logger.info("No events need embedding generation")
        return {'status': 'success', 'total': 0, 'batches': 0}

    # Spawn batch subtasks
    batches_created = 0
    for i in range(0, total_count, batch_size):
        batch_ids = ids_to_process[i:i + batch_size]
        generate_embeddings_batch.delay(batch_ids)
        batches_created += 1

    logger.info(f"Spawned {batches_created} batch tasks for {total_count} events (batch_size={batch_size})")
    return {'status': 'spawned', 'total': total_count, 'batches': batches_created}


@shared_task(bind=True, max_retries=2, default_retry_delay=120)
def generate_embeddings_batch(self, event_ids: list):
    """
    Process a batch of events for embedding generation.

    This is a subtask spawned by bulk_generate_embeddings. Each batch is
    small enough to complete within the task time limit.

    Args:
        event_ids: List of event IDs to process in this batch
    """
    from api.rag_service import get_rag_service

    try:
        logger.info(f"Processing embedding batch of {len(event_ids)} events")
        rag_service = get_rag_service()
        rag_service.update_event_embeddings(event_ids=event_ids)
        logger.info(f"Completed embedding batch of {len(event_ids)} events")
        return {'status': 'success', 'count': len(event_ids)}

    except Exception as exc:
        logger.error(f"Embedding batch failed: {exc}")
        raise self.retry(exc=exc)


@shared_task
def cleanup_old_events(days: int = 90):
    """
    Clean up events older than specified days.

    Periodic maintenance task.

    Args:
        days: Delete events with start_time older than this many days ago
    """
    from events.models import Event

    cutoff = timezone.now() - timedelta(days=days)
    old_events = Event.objects.filter(start_time__lt=cutoff)
    count = old_events.count()

    if count > 0:
        old_events.delete()
        logger.info(f"Deleted {count} events older than {days} days")

    return {'deleted': count, 'cutoff_date': cutoff.isoformat()}


@shared_task
def cleanup_old_scraping_jobs(days: int = 30):
    """
    Clean up completed/failed scraping jobs older than specified days.

    Args:
        days: Delete jobs completed more than this many days ago
    """
    from events.models import ScrapingJob

    cutoff = timezone.now() - timedelta(days=days)
    old_jobs = ScrapingJob.objects.filter(
        status__in=['completed', 'failed'],
        completed_at__lt=cutoff
    )
    count = old_jobs.count()

    if count > 0:
        old_jobs.delete()
        logger.info(f"Deleted {count} old scraping jobs")

    return {'deleted': count}


@shared_task
def mark_source_never_scrape(source_id: int, reason: str):
    """
    Mark a source as 'never scrape' based on repeated failures or other conditions.

    Args:
        source_id: ID of the Source to mark
        reason: Reason for marking (logged and stored in notes)
    """
    from events.models import Source

    try:
        source = Source.objects.get(id=source_id)
        source.status = 'never_scrape'
        source.save()

        # Update site strategy notes if present
        if source.site_strategy:
            strategy = source.site_strategy
            notes = strategy.notes or ''
            strategy.notes = notes + f"\nMarked never_scrape: {reason} ({timezone.now().isoformat()})"
            strategy.save()

        logger.info(f"Source {source_id} marked as never_scrape: {reason}")
        return {'source_id': source_id, 'status': 'marked'}

    except Source.DoesNotExist:
        logger.warning(f"Source {source_id} not found")
        return {'source_id': source_id, 'status': 'not_found'}


@shared_task
def generate_daily_stats():
    """
    Generate daily statistics for monitoring.

    Periodic task to track system health.
    """
    from events.models import Event, ScrapingJob, Source
    from venues.models import Venue

    today = timezone.now().date()
    yesterday = today - timedelta(days=1)

    stats = {
        'date': today.isoformat(),
        'events': {
            'total': Event.objects.count(),
            'with_embeddings': Event.objects.exclude(embedding__isnull=True).count(),
            'created_yesterday': Event.objects.filter(created_at__date=yesterday).count(),
        },
        'venues': {
            'total': Venue.objects.count(),
            'with_coordinates': Venue.objects.exclude(latitude__isnull=True).count(),
        },
        'scraping': {
            'pending': ScrapingJob.objects.filter(status='pending').count(),
            'completed_yesterday': ScrapingJob.objects.filter(status='completed', completed_at__date=yesterday).count(),
            'failed_yesterday': ScrapingJob.objects.filter(status='failed', completed_at__date=yesterday).count(),
        },
        'sources': {
            'total': Source.objects.count(),
            'active': Source.objects.filter(status='processed').count(),
        },
    }

    logger.info(f"Daily stats: {stats}")
    return stats
