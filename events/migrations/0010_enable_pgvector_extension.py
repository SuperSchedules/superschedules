# Enable pgvector extension for PostgreSQL

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0009_alter_event_embedding'),
    ]

    operations = [
        migrations.RunSQL(
            sql="CREATE EXTENSION IF NOT EXISTS vector;",
            reverse_sql="DROP EXTENSION IF EXISTS vector;",
        ),
        # Add vector index for better performance
        migrations.RunSQL(
            sql="CREATE INDEX IF NOT EXISTS events_embedding_cosine_idx ON events_event USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);",
            reverse_sql="DROP INDEX IF EXISTS events_embedding_cosine_idx;",
        ),
    ]