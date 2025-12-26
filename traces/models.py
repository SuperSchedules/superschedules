"""
Models for storing chat debug runs and trace events.

ChatDebugRun - A single debug chat execution with settings and results
ChatDebugEvent - Individual trace events within a run (retrieval, context, prompt, etc.)
"""

import uuid
from django.db import models
from django.contrib.auth.models import User


class ChatDebugRun(models.Model):
    """A single debug chat execution with full trace recording."""

    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('running', 'Running'),
        ('success', 'Success'),
        ('error', 'Error'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='debug_runs')

    # Input
    request_text = models.TextField(help_text='The user query to test')

    # Settings used for this run
    settings = models.JSONField(
        default=dict,
        help_text='Pipeline settings: model, temperature, max_events, location, radius, etc.'
    )

    # Execution state
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')

    # Results
    final_answer_text = models.TextField(blank=True, help_text='The LLM response')
    total_latency_ms = models.IntegerField(null=True, blank=True, help_text='Total execution time in ms')

    # Error info
    error_message = models.TextField(blank=True)
    error_stack = models.TextField(blank=True)

    # Computed diagnostics (populated after run completes)
    diagnostics = models.JSONField(null=True, blank=True, help_text='Analysis of what might be causing issues')

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Chat Debug Run'
        verbose_name_plural = 'Chat Debug Runs'
        indexes = [
            models.Index(fields=['status']),
            models.Index(fields=['created_at']),
            models.Index(fields=['created_by', '-created_at']),
        ]

    def __str__(self):
        return f'{self.request_text[:50]}... ({self.status})'


class ChatDebugEvent(models.Model):
    """Individual trace event within a debug run."""

    STAGE_CHOICES = [
        ('input', 'Input'),
        ('location_resolution', 'Location Resolution'),
        ('retrieval', 'Retrieval'),
        ('rerank', 'Rerank'),
        ('context_block', 'Context Block'),
        ('prompt_final', 'Final Prompt'),
        ('llm_request', 'LLM Request'),
        ('llm_chunk', 'LLM Chunk'),
        ('llm_response', 'LLM Response'),
        ('error', 'Error'),
    ]

    id = models.BigAutoField(primary_key=True)
    run = models.ForeignKey(ChatDebugRun, on_delete=models.CASCADE, related_name='events')
    seq = models.PositiveIntegerField(help_text='Sequence number for ordering')
    created_at = models.DateTimeField(auto_now_add=True)
    stage = models.CharField(max_length=30, choices=STAGE_CHOICES)
    data = models.JSONField(help_text='Full payload for this stage')
    latency_ms = models.IntegerField(null=True, blank=True, help_text='Time taken for this stage')

    class Meta:
        ordering = ['seq']
        verbose_name = 'Chat Debug Event'
        verbose_name_plural = 'Chat Debug Events'
        indexes = [
            models.Index(fields=['run', 'seq']),
            models.Index(fields=['stage']),
        ]
        constraints = [
            models.UniqueConstraint(fields=['run', 'seq'], name='unique_run_seq'),
        ]

    def __str__(self):
        return f'{self.run_id} #{self.seq}: {self.stage}'
