"""Django management command to update venue embeddings for RAG search."""

from django.core.management.base import BaseCommand

from api.rag_service import get_rag_service


class Command(BaseCommand):
    help = 'Update venue embeddings for semantic search'

    def add_arguments(self, parser):
        parser.add_argument(
            '--venue-ids',
            nargs='+',
            type=int,
            help='Specific venue IDs to update (default: all missing)',
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Force update all venues, even if they have embeddings',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be updated without making changes',
        )

    def handle(self, *args, **options):
        from venues.models import Venue

        if options['dry_run']:
            if options['force']:
                count = Venue.objects.count()
            elif options['venue_ids']:
                count = Venue.objects.filter(id__in=options['venue_ids'], embedding__isnull=True).count()
            else:
                count = Venue.objects.filter(embedding__isnull=True).count()
            self.stdout.write(f'Would update embeddings for {count} venues')
            return

        rag_service = get_rag_service()

        if options['force']:
            self.stdout.write('Forcing update of all venue embeddings...')
            if options['venue_ids']:
                Venue.objects.filter(id__in=options['venue_ids']).update(embedding=None)
                rag_service.update_venue_embeddings(options['venue_ids'])
            else:
                Venue.objects.update(embedding=None)
                rag_service.update_venue_embeddings()
        elif options['venue_ids']:
            self.stdout.write(f'Updating embeddings for venues: {options["venue_ids"]}')
            Venue.objects.filter(id__in=options['venue_ids']).update(embedding=None)
            rag_service.update_venue_embeddings(options['venue_ids'])
        else:
            self.stdout.write('Updating embeddings for new venues...')
            rag_service.update_venue_embeddings()

        # Show status
        total_venues = Venue.objects.count()
        venues_with_embeddings = Venue.objects.exclude(embedding__isnull=True).count()

        self.stdout.write(
            self.style.SUCCESS(
                f'Successfully updated venue embeddings! '
                f'({venues_with_embeddings}/{total_venues} venues have embeddings)'
            )
        )
