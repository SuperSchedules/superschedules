"""
Tests for API services including RAG functionality.
"""
import json
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime, timedelta
from django.test import TestCase
from django.utils import timezone
from django.contrib.auth import get_user_model
from django_dynamic_fixture import G

from events.models import Event, Source
from api.rag_service import EventRAGService, clean_html_content
from api.llm_service import create_event_discovery_prompt


class CleanHTMLContentTests(TestCase):
    """Tests for HTML content cleaning utility function."""

    def test_clean_basic_html(self):
        """Test cleaning basic HTML tags and entities."""
        content = "<p>Hello &amp; welcome to our <strong>event</strong>!</p>"
        result = clean_html_content(content)
        self.assertEqual(result, "Hello & welcome to our event!")

    def test_clean_multiple_whitespace(self):
        """Test cleaning extra whitespace and newlines."""
        content = "Hello    world\\n\\nThis   is   a   test"
        result = clean_html_content(content)
        self.assertEqual(result, "Hello world  This is a test")

    def test_clean_empty_content(self):
        """Test handling empty or None content."""
        self.assertEqual(clean_html_content(""), "")
        self.assertEqual(clean_html_content(None), "")

    def test_clean_complex_html(self):
        """Test cleaning complex HTML with nested tags."""
        content = """
        <div class="event">
            <h2>Music &amp; Arts Festival</h2>
            <p>Join us for <em>live music</em> and <span style="color: red;">art exhibitions</span>!</p>
            <br><br>
            Location: <strong>Central Park</strong>
        </div>
        """
        result = clean_html_content(content)
        expected = "Music & Arts Festival Join us for live music and art exhibitions! Location: Central Park"
        self.assertEqual(result, expected)


class EventRAGServiceTests(TestCase):
    """Tests for the EventRAGService class."""

    def setUp(self):
        """Set up test data."""
        self.user = G(get_user_model())
        self.source = G(Source, user=self.user, name="Test Source")
        
        # Create test events with different dates
        self.future_event1 = G(
            Event,
            source=self.source,
            title="Future Concert",
            description="A great music concert",
            location="Concert Hall",
            start_time=timezone.now() + timedelta(days=7)
        )
        
        self.future_event2 = G(
            Event,
            source=self.source,
            title="Art Exhibition",
            description="Modern art display",
            location="Art Museum",
            start_time=timezone.now() + timedelta(days=14)
        )
        
        self.past_event = G(
            Event,
            source=self.source,
            title="Past Concert",
            description="Already happened",
            location="Old Venue",
            start_time=timezone.now() - timedelta(days=7)
        )

    def test_create_event_text(self):
        """Test event text creation for embeddings."""
        # Mock the RAG service without loading the actual model
        with patch('api.rag_service.SentenceTransformer'):
            rag_service = EventRAGService()
            
            event_text = rag_service._create_event_text(self.future_event1)
            
            # Should include title, description, location
            self.assertIn("Future Concert", event_text)
            self.assertIn("A great music concert", event_text)
            self.assertIn("Concert Hall", event_text)
            
            # Should include temporal context
            day_name = self.future_event1.start_time.strftime("%A")
            self.assertIn(day_name, event_text)

    @patch('api.rag_service.SentenceTransformer')
    def test_update_event_embeddings(self, mock_transformer_class):
        """Test embedding generation for events."""
        import numpy as np
        
        # Mock the sentence transformer
        mock_model = Mock()
        mock_embedding = np.array([0.1] * 384)  # 384-dimensional embedding
        mock_model.encode.return_value = np.array([mock_embedding])  # Array of embeddings
        mock_transformer_class.return_value = mock_model
        
        rag_service = EventRAGService()
        
        # Update embeddings for specific event
        rag_service.update_event_embeddings([self.future_event1.id])
        
        # Verify model was called
        mock_model.encode.assert_called_once()
        
        # Verify event was updated with embedding
        self.future_event1.refresh_from_db()
        self.assertIsNotNone(self.future_event1.embedding)
        self.assertEqual(len(self.future_event1.embedding), 384)  # Should be 384 dimensions
        self.assertEqual(self.future_event1.embedding[0], 0.1)  # First element should be 0.1

    @patch('api.rag_service.SentenceTransformer')
    def test_semantic_search_filters_past_events(self, mock_transformer_class):
        """Test that semantic search filters out past events by default."""
        import numpy as np
        
        # Mock the sentence transformer
        mock_model = Mock()
        mock_embedding = np.array([0.1] * 384)  # Numpy array, not list
        mock_model.encode.return_value = np.array([mock_embedding])
        mock_transformer_class.return_value = mock_model
        
        # Add embeddings to events for testing (384 dimensions for compatibility)
        embedding_384 = [0.1] * 384  # Create 384-dimensional embedding
        self.future_event1.embedding = embedding_384
        self.future_event1.save()
        self.past_event.embedding = embedding_384
        self.past_event.save()
        
        rag_service = EventRAGService()
        
        with patch('api.rag_service.Event.objects') as mock_queryset:
            # Mock the queryset chain
            mock_queryset.exclude.return_value = mock_queryset
            mock_queryset.filter.return_value = mock_queryset
            mock_queryset.annotate.return_value = mock_queryset
            mock_queryset.order_by.return_value = []
            
            results = rag_service.semantic_search("music concert", only_future_events=True)
            
            # Verify the method ran without error and filtering was attempted
            self.assertTrue(mock_queryset.filter.called)
            self.assertTrue(mock_queryset.exclude.called)  # Should exclude events without embeddings
            self.assertEqual(results, [])  # Should return empty list from our mock

    @patch('api.rag_service.SentenceTransformer')
    def test_get_context_events_returns_formatted_data(self, mock_transformer_class):
        """Test that get_context_events returns properly formatted event data."""
        import numpy as np
        # Mock the sentence transformer
        mock_model = Mock()
        mock_model.encode.return_value = np.array([[0.1] * 384])  # Mock query embedding (384 dimensions)
        mock_transformer_class.return_value = mock_model
        
        # Mock the semantic_search method to return test data
        rag_service = EventRAGService()
        
        with patch.object(rag_service, 'semantic_search') as mock_search:
            mock_search.return_value = [(self.future_event1, 0.8)]
            
            context_events = rag_service.get_context_events("music concert")
            
            self.assertEqual(len(context_events), 1)
            event_data = context_events[0]
            
            # Verify required fields are present
            self.assertEqual(event_data['id'], self.future_event1.id)
            self.assertEqual(event_data['title'], "Future Concert")
            self.assertEqual(event_data['location'], "Concert Hall")
            self.assertEqual(event_data['similarity_score'], 0.8)
            self.assertIn('start_time', event_data)

    @patch('api.rag_service.SentenceTransformer')
    def test_get_context_events_filters_by_threshold(self, mock_transformer_class):
        """Test that get_context_events filters by similarity threshold."""
        # Mock the sentence transformer
        mock_model = Mock()
        mock_model.encode.return_value = [[0.1, 0.2, 0.3]]
        mock_transformer_class.return_value = mock_model
        
        rag_service = EventRAGService()
        
        with patch.object(rag_service, 'semantic_search') as mock_search:
            # Return events with different similarity scores
            mock_search.return_value = [
                (self.future_event1, 0.8),  # Above threshold
                (self.future_event2, 0.2),  # Below threshold
            ]
            
            context_events = rag_service.get_context_events(
                "music concert", 
                similarity_threshold=0.5
            )
            
            # Should only return the event above threshold
            self.assertEqual(len(context_events), 1)
            self.assertEqual(context_events[0]['title'], "Future Concert")

    @patch('api.rag_service.SentenceTransformer')
    def test_fallback_event_search(self, mock_transformer_class):
        """Test fallback search when RAG fails."""
        mock_transformer_class.return_value = Mock()
        
        rag_service = EventRAGService()
        
        # Test the fallback method directly
        fallback_events = rag_service._fallback_event_search("test query", max_events=5)
        
        # Should return events in the correct format
        self.assertIsInstance(fallback_events, list)
        if fallback_events:  # If there are any future events
            event_data = fallback_events[0]
            self.assertIn('id', event_data)
            self.assertIn('title', event_data)
            self.assertIn('similarity_score', event_data)
            self.assertEqual(event_data['similarity_score'], 0.0)  # No semantic score

    def test_extract_location_hints(self):
        """Test location extraction from user messages."""
        with patch('api.rag_service.SentenceTransformer'):
            rag_service = EventRAGService()
            
            # Test various location patterns
            test_cases = [
                ("Looking for events in Boston", "Boston"),
                ("Events near San Francisco, CA", "Francisco"),  # Regex captures "Francisco, CA"
                ("What's happening in New York City", "New York"),
                ("Shows around Los Angeles area", "Los Angeles"),
            ]
            
            for message, expected_substring in test_cases:
                locations = rag_service._extract_location_hints(message)
                found = any(expected_substring in loc for loc in locations)
                self.assertTrue(
                    found,
                    f"Expected to find substring '{expected_substring}' in {locations} for message '{message}'"
                )


class LLMServiceTests(TestCase):
    """Tests for LLM service functions."""

    def setUp(self):
        """Set up test data."""
        self.sample_events = [
            {
                'id': 1,
                'title': 'Jazz Concert',
                'description': 'Amazing live jazz music',
                'location': 'Blue Note Club',
                'start_time': '2024-08-30T19:00:00+00:00',
                'end_time': '2024-08-30T22:00:00+00:00',
                'url': 'https://example.com/jazz-concert'
            },
            {
                'id': 2,
                'title': 'Art Workshop',
                'description': 'Hands-on painting experience',
                'location': 'Community Center',
                'start_time': '2024-08-31T14:00:00+00:00',
                'url': None
            }
        ]

    def test_create_event_discovery_prompt_structure(self):
        """Test that the prompt is properly structured."""
        message = "Looking for music events this weekend"
        context = {
            'current_date': '2024-08-25',
            'location': 'Boston',
            'preferences': {'music': True}
        }
        
        system_prompt, user_prompt = create_event_discovery_prompt(
            message, self.sample_events, context
        )
        
        # Test system prompt
        self.assertIn("expert local events concierge", system_prompt)
        self.assertIn("bullet points", system_prompt)
        self.assertIn("Here's what we found", system_prompt)
        
        # Test user prompt structure
        self.assertIn(message, user_prompt)
        self.assertIn("Current date: 2024-08-25", user_prompt)
        self.assertIn("Location preference: Boston", user_prompt)

    def test_create_event_discovery_prompt_formatting(self):
        """Test that events are formatted correctly in the prompt."""
        message = "Looking for events"
        context = {'current_date': '2024-08-25'}
        
        system_prompt, user_prompt = create_event_discovery_prompt(
            message, self.sample_events, context
        )
        
        # Check event formatting - title on its own line
        self.assertIn("1. Jazz Concert", user_prompt)
        self.assertIn("2. Art Workshop", user_prompt)
        
        # Check date/time formatting - should be on separate line
        self.assertIn("Friday, August 30 - 07:00 PM", user_prompt)
        
        # Check location on separate line
        self.assertIn("Blue Note Club", user_prompt)
        
        # Check description truncation and placement
        self.assertIn("Amazing live jazz music", user_prompt)
        
        # Check URL inclusion
        self.assertIn("https://example.com/jazz-concert", user_prompt)

    def test_create_event_discovery_prompt_no_events(self):
        """Test prompt creation when no events are found."""
        message = "Looking for events"
        context = {'current_date': '2024-08-25'}
        
        system_prompt, user_prompt = create_event_discovery_prompt(
            message, [], context
        )
        
        self.assertIn("No specific events found", user_prompt)
        self.assertIn("suggest general alternatives", user_prompt)

    def test_create_event_discovery_prompt_long_description(self):
        """Test that long descriptions are properly truncated."""
        long_event = {
            'id': 1,
            'title': 'Long Event',
            'description': 'A' * 200,  # 200 character description
            'location': 'Test Venue',
            'start_time': '2024-08-30T19:00:00+00:00',
        }
        
        message = "Looking for events"
        context = {'current_date': '2024-08-25'}
        
        system_prompt, user_prompt = create_event_discovery_prompt(
            message, [long_event], context
        )
        
        # Should truncate long descriptions
        self.assertIn('A' * 150 + '...', user_prompt)  # Should contain truncated version


class RAGIntegrationTests(TestCase):
    """Integration tests for RAG service usage."""

    def setUp(self):
        """Set up test data."""
        self.user = G(get_user_model())
        self.source = G(Source, user=self.user)
        
        # Create events with embeddings
        self.music_event = G(
            Event,
            source=self.source,
            title="Jazz Concert",
            description="Live jazz music performance",
            location="Music Hall",
            start_time=timezone.now() + timedelta(days=1),
            embedding=[0.1] * 384  # 384-dimensional embedding
        )

    @patch('api.rag_service.SentenceTransformer')
    def test_rag_service_singleton(self, mock_transformer_class):
        """Test that RAG service maintains singleton pattern."""
        from api.rag_service import get_rag_service
        
        service1 = get_rag_service()
        service2 = get_rag_service()
        
        self.assertIs(service1, service2)

    def test_event_text_creation_with_temporal_context(self):
        """Test that temporal context is properly added to event text."""
        with patch('api.rag_service.SentenceTransformer'):
            rag_service = EventRAGService()
            
            event_text = rag_service._create_event_text(self.music_event)
            
            # Should include day of week and month
            day_name = self.music_event.start_time.strftime("%A")
            month_name = self.music_event.start_time.strftime("%B")
            
            self.assertIn(day_name, event_text)
            self.assertIn(month_name, event_text)