"""
Django management command to update event embeddings for RAG search.
"""

from django.core.management.base import BaseCommand
from api.rag_service import get_rag_service


class Command(BaseCommand):
    help = 'Update event embeddings for semantic search'

    def add_arguments(self, parser):
        parser.add_argument(
            '--event-ids',
            nargs='+',
            type=int,
            help='Specific event IDs to update (default: all missing)',
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Force update all events, even if cached',
        )

    def handle(self, *args, **options):
        from events.models import Event
        
        rag_service = get_rag_service()
        
        if options['force']:
            self.stdout.write('Forcing update of all event embeddings...')
            # Clear all embeddings to force rebuild
            Event.objects.update(embedding=None)
            rag_service.update_event_embeddings()
        elif options['event_ids']:
            self.stdout.write(f'Updating embeddings for events: {options["event_ids"]}')
            # Clear embeddings for specified events
            Event.objects.filter(id__in=options['event_ids']).update(embedding=None)
            rag_service.update_event_embeddings(options['event_ids'])
        else:
            self.stdout.write('Updating embeddings for new events...')
            rag_service.update_event_embeddings()
        
        # Show status
        total_events = Event.objects.count()
        events_with_embeddings = Event.objects.exclude(embedding__isnull=True).count()
        
        self.stdout.write(
            self.style.SUCCESS(
                f'Successfully updated event embeddings! '
                f'({events_with_embeddings}/{total_events} events have embeddings)'
            )
        )