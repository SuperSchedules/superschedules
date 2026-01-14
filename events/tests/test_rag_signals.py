"""
Test cases for RAG embedding signal functionality.
Tests the post_save signal that auto-generates embeddings for Event instances.
"""

import unittest.mock
from datetime import datetime, timezone
from django.test import TestCase
from model_bakery import baker

from events.models import Event
from venues.models import Venue
from api.rag_service import EventRAGService


class RagSignalTests(TestCase):
    """Test RAG embedding generation via post_save signals."""

    def setUp(self):
        """Set up test data."""
        self.test_venue = baker.make(Venue, name="Test Venue", city="Newton", state="MA")
        
        # Mock the RAG service to avoid actual ML model loading in tests
        self.rag_service_patcher = unittest.mock.patch('api.rag_service.get_rag_service')
        self.mock_get_rag_service = self.rag_service_patcher.start()
        
        # Create a mock RAG service instance
        self.mock_rag_service = unittest.mock.Mock(spec=EventRAGService)
        self.mock_get_rag_service.return_value = self.mock_rag_service
    
    def tearDown(self):
        """Clean up patches."""
        self.rag_service_patcher.stop()
    
    def test_new_event_triggers_embedding_generation(self):
        """Test that creating a new event triggers embedding generation."""
        # Create a new event
        event = Event.objects.create(
            venue=self.test_venue,
            external_id="test_001",
            title="Kids Soccer Practice",
            description="Soccer for ages 5-10",
            start_time=datetime(2024, 9, 1, 10, 0, tzinfo=timezone.utc)
        )
        
        # Verify RAG service was called for embedding generation
        self.mock_rag_service.update_event_embeddings.assert_called_once_with(
            event_ids=[event.id]
        )
    
    def test_update_without_changes_skips_embedding(self):
        """Test that saving without changes doesn't regenerate embedding."""
        # Create event with mock embedding
        event = baker.make(
            Event,
            venue=self.test_venue,
            embedding=[0.1] * 384  # Mock embedding
        )
        
        # Reset mock call count after creation
        self.mock_rag_service.reset_mock()
        
        # Save without changes (update_fields=None)
        event.save()
        
        # Should NOT call embedding generation
        self.mock_rag_service.update_event_embeddings.assert_not_called()
    
    def test_update_non_content_fields_skips_embedding(self):
        """Test that updating non-content fields doesn't regenerate embedding."""
        # Create event with mock embedding
        event = baker.make(
            Event,
            venue=self.test_venue,
            embedding=[0.1] * 384
        )
        
        # Reset mock after creation
        self.mock_rag_service.reset_mock()
        
        # Update non-content field
        event.save(update_fields=['updated_at'])
        
        # Should NOT call embedding generation
        self.mock_rag_service.update_event_embeddings.assert_not_called()
    
    def test_update_title_triggers_embedding_regeneration(self):
        """Test that updating title field triggers embedding regeneration."""
        # Create event with mock embedding
        event = baker.make(
            Event,
            venue=self.test_venue,
            title="Original Title",
            embedding=[0.1] * 384
        )
        
        # Reset mock after creation
        self.mock_rag_service.reset_mock()
        
        # Update title field
        event.title = "Updated Title"
        event.save(update_fields=['title'])
        
        # Should call embedding generation
        self.mock_rag_service.update_event_embeddings.assert_called_once_with(
            event_ids=[event.id]
        )
    
    def test_update_description_triggers_embedding_regeneration(self):
        """Test that updating description field triggers embedding regeneration."""
        event = baker.make(
            Event,
            venue=self.test_venue,
            description="Original description",
            embedding=[0.1] * 384
        )
        
        self.mock_rag_service.reset_mock()
        
        event.description = "Updated description"
        event.save(update_fields=['description'])
        
        self.mock_rag_service.update_event_embeddings.assert_called_once_with(
            event_ids=[event.id]
        )
    
    def test_update_room_name_triggers_embedding_regeneration(self):
        """Test that updating room_name field triggers embedding regeneration."""
        event = baker.make(
            Event,
            venue=self.test_venue,
            room_name="Original Room",
            embedding=[0.1] * 384
        )

        self.mock_rag_service.reset_mock()

        event.room_name = "Updated Room"
        event.save(update_fields=['room_name'])

        self.mock_rag_service.update_event_embeddings.assert_called_once_with(
            event_ids=[event.id]
        )
    
    def test_update_start_time_triggers_embedding_regeneration(self):
        """Test that updating start_time field triggers embedding regeneration."""
        event = baker.make(
            Event,
            venue=self.test_venue,
            start_time=datetime(2024, 9, 1, 10, 0, tzinfo=timezone.utc),
            embedding=[0.1] * 384
        )
        
        self.mock_rag_service.reset_mock()
        
        event.start_time = datetime(2024, 9, 2, 10, 0, tzinfo=timezone.utc)
        event.save(update_fields=['start_time'])
        
        self.mock_rag_service.update_event_embeddings.assert_called_once_with(
            event_ids=[event.id]
        )
    
    def test_update_multiple_content_fields_triggers_embedding_once(self):
        """Test that updating multiple content fields triggers embedding generation once."""
        event = baker.make(
            Event,
            venue=self.test_venue,
            title="Original Title",
            description="Original description",
            embedding=[0.1] * 384
        )
        
        self.mock_rag_service.reset_mock()
        
        event.title = "Updated Title"
        event.description = "Updated description"
        event.save(update_fields=['title', 'description'])
        
        # Should call embedding generation once
        self.mock_rag_service.update_event_embeddings.assert_called_once_with(
            event_ids=[event.id]
        )
    
    def test_missing_embedding_always_regenerates(self):
        """Test that events without embeddings always get them generated."""
        # Create event without embedding
        event = baker.make(
            Event,
            venue=self.test_venue,
            embedding=None
        )
        
        self.mock_rag_service.reset_mock()
        
        # Save without changes - should still generate embedding because it's missing
        event.save()
        
        # Should call embedding generation
        self.mock_rag_service.update_event_embeddings.assert_called_once_with(
            event_ids=[event.id]
        )
    
    def test_mixed_content_and_non_content_fields_triggers_embedding(self):
        """Test that updating both content and non-content fields triggers embedding."""
        event = baker.make(
            Event,
            venue=self.test_venue,
            title="Original Title",
            embedding=[0.1] * 384
        )
        
        self.mock_rag_service.reset_mock()
        
        event.title = "Updated Title"
        event.save(update_fields=['title', 'updated_at'])
        
        # Should call embedding generation (because title is in the update)
        self.mock_rag_service.update_event_embeddings.assert_called_once_with(
            event_ids=[event.id]
        )
    
    def test_signal_handles_rag_service_errors_gracefully(self):
        """Test that signal handles RAG service errors without crashing."""
        # Mock RAG service to raise an exception
        self.mock_rag_service.update_event_embeddings.side_effect = Exception("RAG service error")
        
        # Create event - should not crash despite RAG service error
        event = Event.objects.create(
            venue=self.test_venue,
            external_id="test_error",
            title="Test Event",
            description="Test description",
            start_time=datetime(2024, 9, 1, 10, 0, tzinfo=timezone.utc)
        )
        
        # Event should still be created successfully
        self.assertTrue(Event.objects.filter(id=event.id).exists())
        
        # RAG service should have been called (and failed gracefully)
        self.mock_rag_service.update_event_embeddings.assert_called_once_with(
            event_ids=[event.id]
        )