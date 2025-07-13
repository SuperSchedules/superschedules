from django.db import models
from pgvector.django import VectorField
from django.contrib.postgres.indexes import GinIndex

class Source(models.Model):
    name = models.CharField(max_length=100, unique=True)
    base_url = models.URLField()
    last_crawl = models.DateTimeField(null=True)

    def __str__(self):
        return self.name

class Event(models.Model):
    source = models.ForeignKey(Source, on_delete=models.CASCADE)
    external_id = models.CharField(max_length=255)
    title = models.CharField(max_length=255)
    description = models.TextField()
    location = models.CharField(max_length=255)
    start_time = models.DateTimeField()
    end_time = models.DateTimeField(null=True, blank=True)
    url = models.URLField()
    embedding = VectorField(dimensions=768, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('source', 'external_id')
        indexes = [
            GinIndex(fields=['description'], name='desc_gin_idx'),
            GinIndex(fields=['embedding'], name='embed_gin_idx'),
        ]

    def __str__(self):
        return self.title
