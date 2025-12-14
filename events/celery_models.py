"""
Unmanaged Django models for Celery/Kombu database tables.

These models provide read-only access to Celery's message queue tables
for monitoring purposes. They are marked as unmanaged so Django won't
try to create/modify these tables (they're managed by Celery/Kombu).
"""

from django.db import models


class KombuMessage(models.Model):
    """
    Unmanaged model for kombu_message table.

    Shows pending tasks in the Celery queue (when using database broker).
    """

    id = models.BigAutoField(primary_key=True)
    payload = models.BinaryField()
    timestamp = models.DateTimeField(auto_now_add=True)
    visible = models.BooleanField(default=True)

    class Meta:
        managed = False
        db_table = 'kombu_message'
        verbose_name = 'Queued Task'
        verbose_name_plural = 'Task Queue (Pending Tasks)'
        ordering = ['-timestamp']

    def __str__(self):
        return f"Task {self.id} at {self.timestamp}"

    def get_task_info(self):
        """Extract task information from JSON payload."""
        try:
            import json
            # The payload is stored as bytes, decode it
            payload_str = self.payload.decode('utf-8') if isinstance(self.payload, bytes) else self.payload
            data = json.loads(payload_str)
            return {
                'task_name': data.get('task'),
                'task_id': data.get('id'),
                'args': data.get('args', []),
                'kwargs': data.get('kwargs', {}),
            }
        except Exception as e:
            return {'error': str(e)}


class KombuQueue(models.Model):
    """
    Unmanaged model for kombu_queue table.

    Shows configured Celery queues.
    """

    id = models.BigAutoField(primary_key=True)
    name = models.CharField(max_length=200)

    class Meta:
        managed = False
        db_table = 'kombu_queue'
        verbose_name = 'Celery Queue'
        verbose_name_plural = 'Celery Queues'

    def __str__(self):
        return self.name
