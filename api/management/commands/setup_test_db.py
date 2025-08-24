"""
Management command to set up pgvector extension in test database.
"""
from django.core.management.base import BaseCommand
from django.db import connection


class Command(BaseCommand):
    help = 'Set up pgvector extension for testing'

    def handle(self, *args, **options):
        """Enable pgvector extension and create indexes."""
        try:
            with connection.cursor() as cursor:
                # Enable pgvector extension
                cursor.execute("CREATE EXTENSION IF NOT EXISTS vector;")
                
                # Create the index for better performance
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS events_embedding_cosine_idx 
                    ON events_event USING ivfflat (embedding vector_cosine_ops) 
                    WITH (lists = 100);
                """)
                
            self.stdout.write(
                self.style.SUCCESS('Successfully set up pgvector extension and indexes')
            )
        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'Failed to set up pgvector: {e}')
            )