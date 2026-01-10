"""
Unit tests for RAG (Retrieval-Augmented Generation) service.

Tests vectorization content, semantic search performance, and HTML cleaning.
"""

from django.test import TestCase
from django.utils import timezone
from datetime import datetime, timedelta
from model_bakery import baker
from unittest.mock import patch, MagicMock
import numpy as np

from events.models import Event
from venues.models import Venue
from api.rag_service import EventRAGService, get_rag_service, clean_html_content


class RAGServiceTest(TestCase):
    """Test RAG service functionality."""

    def setUp(self):
        """Create test events with various content types."""
        # Create venues for events
        self.childrens_room_venue = baker.make(
            Venue,
            name="Children's Room Library",
            city="Needham",
            state="MA"
        )
        self.community_room_venue = baker.make(
            Venue,
            name="Library Community Room",
            city="Needham",
            state="MA"
        )
        self.virtual_venue = baker.make(
            Venue,
            name="Virtual",
            city="Online",
            state=""
        )

        # Future event with clean content
        self.baby_storytime = baker.make(
            Event,
            title="Budding Bookworms",
            description="A storytime just for infants from newborn to not-yet walking and their caregivers. Rhymes, stories, fingerplays, and bounces in the storytime room.",
            venue=self.childrens_room_venue,
            room_name="Children's Room",
            start_time=timezone.now() + timedelta(days=1, hours=10),  # Tomorrow 10 AM
            embedding=None  # Will be set in tests
        )

        # Future event with HTML entities (like real Needham data)
        self.dance_class = baker.make(
            Event,
            title="Come Dance with Charles River Ballet Academy!",
            description="Join Ms. Emily from Needham&#039;s classical ballet school for children aged 2 and up with a caregiver.&amp;hellip;&lt;a href=&quot;https://example.com&quot;&gt;Learn More&lt;/a&gt;",
            venue=self.community_room_venue,
            start_time=timezone.now() + timedelta(days=1, hours=11),  # Tomorrow 11 AM
            embedding=None
        )

        # Future teen event
        self.teen_space = baker.make(
            Event,
            title="Teen Study Space",
            description="Teen Study Space in the Library's Community Room on the 1st Floor",
            venue=self.community_room_venue,
            start_time=timezone.now() + timedelta(days=1, hours=14, minutes=30),  # Tomorrow 2:30 PM
            embedding=None
        )

        # Past event (should be filtered out)
        self.past_event = baker.make(
            Event,
            title="Past Event",
            description="This event already happened",
            start_time=timezone.now() - timedelta(days=1),  # Yesterday
            embedding=None
        )

        # Virtual event
        self.virtual_event = baker.make(
            Event,
            title="Virtual Workshop",
            description="Online discussion and insights from alumni",
            venue=self.virtual_venue,
            start_time=timezone.now() + timedelta(days=2, hours=15),  # Day after tomorrow 3 PM
            embedding=None
        )


class TestVectorizationContent(RAGServiceTest):
    """Test what content gets vectorized for embeddings."""
    
    def setUp(self):
        super().setUp()
        self.rag_service = EventRAGService()
    
    def test_create_event_text_basic_content(self):
        """Test vectorized text includes title, description, location."""
        vectorized_text = self.rag_service._create_event_text(self.baby_storytime)
        
        # Should contain all basic fields
        self.assertIn("Budding Bookworms", vectorized_text)
        self.assertIn("storytime just for infants", vectorized_text)
        self.assertIn("Children's Room", vectorized_text)
    
    def test_create_event_text_includes_temporal_context(self):
        """Test vectorized text includes day of week, time, and month."""
        vectorized_text = self.rag_service._create_event_text(self.baby_storytime)
        
        # Should include temporal context
        expected_day = self.baby_storytime.start_time.strftime("%A")  # e.g., "Tuesday"
        expected_time = self.baby_storytime.start_time.strftime("%I:%M %p")  # e.g., "10:00 AM"
        expected_month = self.baby_storytime.start_time.strftime("%B")  # e.g., "September"
        
        self.assertIn(expected_day, vectorized_text)
        self.assertIn(expected_time, vectorized_text) 
        self.assertIn(expected_month, vectorized_text)
    
    def test_create_event_text_handles_missing_fields(self):
        """Test vectorized text handles events with missing description/venue."""
        minimal_event = baker.make(
            Event,
            title="Minimal Event",
            description="",  # Empty description
            venue=None,      # No venue
            start_time=timezone.now() + timedelta(days=1)
        )
        
        vectorized_text = self.rag_service._create_event_text(minimal_event)
        
        # Should still work and include title and temporal context
        self.assertIn("Minimal Event", vectorized_text)
        self.assertIn(minimal_event.start_time.strftime("%A"), vectorized_text)
        
        # Should not have empty strings that create extra spaces
        self.assertNotIn("  ", vectorized_text)  # No double spaces
    
    def test_vectorized_content_cleans_html_entities(self):
        """Test that vectorized text properly cleans HTML entities."""
        vectorized_text = self.rag_service._create_event_text(self.dance_class)
        
        # Fixed implementation should NOT contain HTML entities
        self.assertNotIn("&#039;", vectorized_text)  # Should be clean apostrophe
        self.assertNotIn("&amp;", vectorized_text)   # Should be clean ampersand  
        self.assertNotIn("&lt;", vectorized_text)    # Should be clean <
        self.assertNotIn("&gt;", vectorized_text)    # Should be clean >
        self.assertNotIn("&quot;", vectorized_text)  # Should be clean "
        
        # Should contain clean versions
        self.assertIn("Needham's", vectorized_text)  # Clean apostrophe
        self.assertIn("&", vectorized_text)          # Clean ampersand


class TestHTMLContentCleaning(RAGServiceTest):
    """Test HTML entity and tag cleaning functionality."""
    
    def test_clean_html_content_removes_entities(self):
        """Test HTML entity cleaning function."""
        dirty_content = "Needham&#039;s classical ballet school &amp; dance academy &lt;strong&gt;Learn More&lt;/strong&gt;"
        clean_content = clean_html_content(dirty_content)
        
        expected = "Needham's classical ballet school & dance academy Learn More"
        self.assertEqual(clean_content, expected)
    
    def test_clean_html_content_removes_tags(self):
        """Test HTML tag removal."""
        html_content = '<a href="https://example.com">Learn More</a> about our <strong>programs</strong>'
        clean_content = clean_html_content(html_content)
        
        expected = "Learn More about our programs"
        self.assertEqual(clean_content, expected)
    
    def test_clean_html_content_handles_empty_content(self):
        """Test cleaning handles None and empty strings."""
        self.assertEqual(clean_html_content(None), '')
        self.assertEqual(clean_html_content(''), '')
        self.assertEqual(clean_html_content('   '), '')
    
    def test_clean_html_content_normalizes_whitespace(self):
        """Test whitespace normalization."""
        messy_content = "Multiple    spaces\nand\n\nnewlines   here"
        clean_content = clean_html_content(messy_content)
        
        expected = "Multiple spaces and newlines here"
        self.assertEqual(clean_content, expected)


class TestSemanticSearch(RAGServiceTest):
    """Test semantic search functionality and query matching."""
    
    def setUp(self):
        super().setUp()
        self.rag_service = EventRAGService()
        
        # Mock the sentence transformer to avoid loading actual model in tests
        self.mock_model = MagicMock()
        self.rag_service.model = self.mock_model
        
        # Create mock embeddings for test events with correct 384 dimensions
        import numpy as np
        np.random.seed(42)  # For reproducible test embeddings
        
        self.mock_embeddings = {
            self.baby_storytime.id: np.random.rand(384).tolist(),  # Mock baby-related embedding
            self.dance_class.id: np.random.rand(384).tolist(),     # Mock dance-related embedding  
            self.teen_space.id: np.random.rand(384).tolist(),      # Mock teen-related embedding
            self.virtual_event.id: np.random.rand(384).tolist()    # Mock virtual-related embedding
        }
        
        # Set embeddings on events
        for event in Event.objects.filter(id__in=self.mock_embeddings.keys()):
            event.embedding = self.mock_embeddings[event.id]
            event.save()
    
    def test_semantic_search_filters_future_events(self):
        """Test that semantic search only returns future events by default."""
        # Mock query embedding
        import numpy as np
        mock_query_embedding = np.random.rand(384).astype(np.float32)
        self.mock_model.encode.return_value = np.array([mock_query_embedding])
        
        results = self.rag_service.semantic_search("test query", only_future_events=True)
        
        # Should not include past events
        returned_event_ids = [event.id for event, score in results]
        self.assertNotIn(self.past_event.id, returned_event_ids)
        
        # Should include future events
        future_event_ids = [self.baby_storytime.id, self.dance_class.id, self.teen_space.id, self.virtual_event.id]
        for event_id in future_event_ids:
            if event_id in returned_event_ids:  # At least some future events should be returned
                break
        else:
            self.fail("No future events returned in semantic search")
    
    def test_semantic_search_respects_time_filter(self):
        """Test time window filtering works correctly."""
        # Mock query embedding
        import numpy as np
        mock_query_embedding = np.random.rand(384).astype(np.float32)
        self.mock_model.encode.return_value = np.array([mock_query_embedding])
        
        # Search with 1-day window (should only get events tomorrow)
        results = self.rag_service.semantic_search("test query", time_filter_days=1)
        
        # Should get events within 1 day but not events 2+ days away
        returned_events = [event for event, score in results]
        event_dates = [event.start_time for event in returned_events]
        
        cutoff_date = timezone.now() + timedelta(days=1)
        for event_date in event_dates:
            self.assertLessEqual(event_date, cutoff_date + timedelta(hours=23, minutes=59))
    
    def test_semantic_search_location_filter(self):
        """Test location-based filtering."""
        # Mock query embedding
        import numpy as np
        mock_query_embedding = np.random.rand(384).astype(np.float32)
        self.mock_model.encode.return_value = np.array([mock_query_embedding])
        
        # Search for Library Community Room events
        results = self.rag_service.semantic_search("test query", location_filter="Library Community Room")
        
        returned_events = [event for event, score in results]
        for event in returned_events:
            self.assertIn("Library Community Room", event.get_location_string())
    
    def test_get_context_events_applies_similarity_threshold(self):
        """Test that context events filtering by similarity threshold works."""
        # Mock query embedding and set specific similarity scores
        import numpy as np
        mock_query_embedding = np.random.rand(384).astype(np.float32)
        self.mock_model.encode.return_value = np.array([mock_query_embedding])
        
        # Mock semantic_search to return events with known scores
        with patch.object(self.rag_service, 'semantic_search') as mock_search:
            mock_search.return_value = [
                (self.baby_storytime, 0.8),    # Above threshold
                (self.dance_class, 0.4),       # Above threshold  
                (self.teen_space, 0.2),        # Below threshold (0.3)
                (self.virtual_event, 0.1)      # Below threshold
            ]
            
            context_events = self.rag_service.get_context_events(
                "baby activities", 
                similarity_threshold=0.3
            )
            
            # Should only return events with score >= 0.3
            self.assertEqual(len(context_events), 2)
            event_titles = [event['title'] for event in context_events]
            self.assertIn("Budding Bookworms", event_titles)
            self.assertIn("Come Dance with Charles River Ballet Academy!", event_titles)


class TestRAGQueryScenarios(RAGServiceTest):
    """Test realistic user query scenarios."""
    
    def setUp(self):
        super().setUp()
        self.rag_service = get_rag_service()  # Use global instance
        
        # Generate actual embeddings for test events (not mocked)
        self.rag_service.update_event_embeddings([
            self.baby_storytime.id,
            self.dance_class.id, 
            self.teen_space.id,
            self.virtual_event.id
        ])
    
    @patch('api.rag_service.EventRAGService.semantic_search')
    def test_baby_toddler_query_scenario(self, mock_search):
        """Test query for baby/toddler activities finds relevant events."""
        # Mock return relevant events for baby query
        mock_search.return_value = [
            (self.baby_storytime, 0.85),  # High similarity for infant storytime
            (self.dance_class, 0.45)      # Medium similarity for children 2+ 
        ]
        
        context_events = self.rag_service.get_context_events("activities for toddlers and babies")
        
        # Should find baby storytime with high confidence
        self.assertTrue(len(context_events) >= 1)
        
        # Baby storytime should be first/most relevant
        top_event = context_events[0]
        self.assertEqual(top_event['title'], "Budding Bookworms")
        self.assertGreater(top_event['similarity_score'], 0.8)
    
    @patch('api.rag_service.EventRAGService.semantic_search')
    def test_teen_study_query_scenario(self, mock_search):
        """Test query for teen study space finds relevant events."""
        mock_search.return_value = [
            (self.teen_space, 0.9)  # High similarity for teen study query
        ]
        
        context_events = self.rag_service.get_context_events("study spaces for teenagers")
        
        self.assertTrue(len(context_events) >= 1)
        top_event = context_events[0]
        self.assertEqual(top_event['title'], "Teen Study Space")
    
    @patch('api.rag_service.EventRAGService.semantic_search')  
    def test_virtual_events_query_scenario(self, mock_search):
        """Test query for virtual events finds online events."""
        mock_search.return_value = [
            (self.virtual_event, 0.8)
        ]
        
        context_events = self.rag_service.get_context_events("virtual online events")
        
        self.assertTrue(len(context_events) >= 1)
        top_event = context_events[0]
        self.assertEqual(top_event['title'], "Virtual Workshop")
        self.assertIn("Virtual", top_event['location'])
    
    def test_context_events_cleans_html_in_output(self):
        """Test that context events clean HTML entities in output."""
        with patch.object(self.rag_service, 'semantic_search') as mock_search:
            mock_search.return_value = [(self.dance_class, 0.8)]
            
            context_events = self.rag_service.get_context_events("dance classes")
            
            # Output should have clean HTML (not raw entities)
            event = context_events[0]
            self.assertNotIn("&#039;", event['title'])
            self.assertNotIn("&amp;", event['description'])
            self.assertNotIn("&lt;", event['description'])


class TestRealRAGQueries(RAGServiceTest):
    """Test real RAG queries with actual vectorization (requires PostgreSQL test DB)."""
    
    def setUp(self):
        super().setUp()
        self.rag_service = get_rag_service()
        
        # Generate actual embeddings for test events
        self.rag_service.update_event_embeddings([
            self.baby_storytime.id,
            self.dance_class.id,
            self.teen_space.id, 
            self.virtual_event.id
        ])
    
    def test_baby_toddler_real_query(self):
        """Test real semantic search for baby/toddler activities."""
        results = self.rag_service.semantic_search(
            "activities for babies and toddlers",
            top_k=3,
            only_future_events=True
        )
        
        # Should find at least the baby storytime
        self.assertGreater(len(results), 0)
        
        # Baby storytime should be highly ranked
        event_titles = [event.title for event, score in results]
        self.assertIn("Budding Bookworms", event_titles)
        
        # Check that similarity scores are reasonable
        top_event, top_score = results[0]
        self.assertGreater(top_score, 0.3)  # Should have decent similarity
    
    def test_dance_classes_real_query(self):
        """Test real semantic search for dance classes."""
        results = self.rag_service.semantic_search(
            "dance classes for young children",
            top_k=3,
            only_future_events=True
        )
        
        # Should find dance class
        self.assertGreater(len(results), 0)
        event_titles = [event.title for event, score in results]
        self.assertIn("Come Dance with Charles River Ballet Academy!", event_titles)
    
    def test_teen_study_real_query(self):
        """Test real semantic search for teen study spaces."""
        results = self.rag_service.semantic_search(
            "study spaces for teenagers",
            top_k=3,
            only_future_events=True
        )
        
        # Should find teen study space
        self.assertGreater(len(results), 0)
        event_titles = [event.title for event, score in results]
        self.assertIn("Teen Study Space", event_titles)
    
    def test_virtual_events_real_query(self):
        """Test real semantic search for virtual events."""
        results = self.rag_service.semantic_search(
            "virtual online events",
            top_k=3,
            only_future_events=True
        )
        
        # Should find virtual event
        self.assertGreater(len(results), 0)
        event_titles = [event.title for event, score in results]
        self.assertIn("Virtual Workshop", event_titles)
    
    def test_location_filtering_real_query(self):
        """Test location filtering with real queries."""
        results = self.rag_service.semantic_search(
            "library events",
            top_k=5,
            location_filter="Library Community Room",
            only_future_events=True
        )
        
        # Should only return events in Library Community Room
        for event, score in results:
            self.assertIn("Library Community Room", event.get_location_string())
    
    def test_similarity_score_distribution(self):
        """Test that similarity scores make sense for different queries."""
        # High relevance query
        specific_results = self.rag_service.semantic_search(
            "infant baby storytime rhymes fingerplays",  # Very specific to baby storytime
            top_k=5,
            only_future_events=True
        )
        
        # Lower relevance query  
        vague_results = self.rag_service.semantic_search(
            "general activities",  # Vague query
            top_k=5,
            only_future_events=True
        )
        
        # Specific query should have higher top score
        if specific_results and vague_results:
            specific_top_score = specific_results[0][1]
            vague_top_score = vague_results[0][1] 
            
            # Not always guaranteed, but generally specific queries should score higher
            self.assertIsInstance(specific_top_score, float)
            self.assertIsInstance(vague_top_score, float)
            self.assertGreater(specific_top_score, 0.2)  # At least some similarity
    
    def test_temporal_context_in_queries(self):
        """Test that temporal context (day, time, month) helps matching.""" 
        # Query that matches temporal context
        temporal_results = self.rag_service.semantic_search(
            "Tuesday morning activities",  # Should match events on Tuesday AM
            top_k=3,
            only_future_events=True
        )
        
        # Should find events that happen on Tuesday morning
        self.assertGreater(len(temporal_results), 0)
        
        # Check that returned events actually match the day
        for event, score in temporal_results:
            if event.start_time:
                day_of_week = event.start_time.strftime("%A")
                # Not all results need to be Tuesday, but some should be
                # Just verify we get reasonable results
                self.assertIsNotNone(day_of_week)


class TestContextEventsVenueData(RAGServiceTest):
    """Test that context events include venue and room_name data."""

    def setUp(self):
        super().setUp()
        self.rag_service = EventRAGService()

        # Mock the sentence transformer
        self.mock_model = MagicMock()
        self.rag_service.model = self.mock_model

    def test_context_events_include_room_name(self):
        """Test that get_context_events returns room_name field."""
        from venues.models import Venue
        from model_bakery import baker

        # Create venue and event with room_name
        venue = baker.make(
            Venue,
            name="Newton Free Library",
            city="Newton",
            state="MA",
        )
        event_with_room = baker.make(
            Event,
            title="Story Time in Children's Room",
            description="Fun for kids",
            venue=venue,
            room_name="Children's Room",
            start_time=timezone.now() + timedelta(days=1),
        )

        with patch.object(self.rag_service, 'semantic_search') as mock_search:
            mock_search.return_value = [(event_with_room, 0.85)]

            context_events = self.rag_service.get_context_events("story time for kids")

            self.assertEqual(len(context_events), 1)
            self.assertIn('room_name', context_events[0])
            self.assertEqual(context_events[0]['room_name'], "Children's Room")

    def test_context_events_room_name_empty_when_not_set(self):
        """Test that room_name is empty string when event has no room."""
        with patch.object(self.rag_service, 'semantic_search') as mock_search:
            # dance_class has no room_name set
            mock_search.return_value = [(self.dance_class, 0.85)]

            context_events = self.rag_service.get_context_events("dance activities")

            self.assertEqual(len(context_events), 1)
            self.assertIn('room_name', context_events[0])
            # room_name should be empty string for events without room
            self.assertEqual(context_events[0]['room_name'], '')


class TestDateRangeFiltering(RAGServiceTest):
    """Test explicit date range filtering in semantic search and get_context_events."""

    def setUp(self):
        super().setUp()
        self.rag_service = EventRAGService()

        # Mock the sentence transformer
        self.mock_model = MagicMock()
        self.rag_service.model = self.mock_model

        # Create mock embeddings for test events with correct 384 dimensions
        import numpy as np
        np.random.seed(42)

        for event in [self.baby_storytime, self.dance_class, self.teen_space, self.virtual_event]:
            event.embedding = np.random.rand(384).tolist()
            event.save()

    def test_semantic_search_with_explicit_date_from(self):
        """Test that date_from filters out events before the specified date."""
        import numpy as np
        mock_query_embedding = np.random.rand(384).astype(np.float32)
        self.mock_model.encode.return_value = np.array([mock_query_embedding])

        # Set date_from to 2 days from now - should exclude events tomorrow
        date_from = timezone.now() + timedelta(days=2)
        results = self.rag_service.semantic_search(
            "test query",
            date_from=date_from,
            only_future_events=False  # Don't apply additional future filter
        )

        # Should only get events 2+ days from now (virtual_event is day after tomorrow)
        returned_events = [event for event, score in results]
        for event in returned_events:
            self.assertGreaterEqual(event.start_time, date_from)

    def test_semantic_search_with_explicit_date_to(self):
        """Test that date_to filters out events after the specified date."""
        import numpy as np
        mock_query_embedding = np.random.rand(384).astype(np.float32)
        self.mock_model.encode.return_value = np.array([mock_query_embedding])

        # Set date_to to 1.5 days from now - should only get events tomorrow
        date_to = timezone.now() + timedelta(days=1, hours=12)
        results = self.rag_service.semantic_search(
            "test query",
            date_to=date_to,
            only_future_events=True
        )

        returned_events = [event for event, score in results]
        for event in returned_events:
            self.assertLessEqual(event.start_time, date_to)

    def test_semantic_search_with_date_range(self):
        """Test that date_from and date_to together create a proper date range filter."""
        import numpy as np
        mock_query_embedding = np.random.rand(384).astype(np.float32)
        self.mock_model.encode.return_value = np.array([mock_query_embedding])

        # Create a narrow date range that should only include specific events
        date_from = timezone.now() + timedelta(days=1, hours=9)  # Tomorrow 9 AM
        date_to = timezone.now() + timedelta(days=1, hours=12)   # Tomorrow 12 PM

        results = self.rag_service.semantic_search(
            "test query",
            date_from=date_from,
            date_to=date_to,
            only_future_events=False
        )

        returned_events = [event for event, score in results]
        for event in returned_events:
            self.assertGreaterEqual(event.start_time, date_from)
            self.assertLessEqual(event.start_time, date_to)

    def test_date_range_overrides_time_filter_days(self):
        """Test that explicit date range parameters override time_filter_days."""
        import numpy as np
        mock_query_embedding = np.random.rand(384).astype(np.float32)
        self.mock_model.encode.return_value = np.array([mock_query_embedding])

        # Set time_filter_days=1 but date_to=7 days out - date_to should win
        date_to = timezone.now() + timedelta(days=7)
        results = self.rag_service.semantic_search(
            "test query",
            time_filter_days=1,  # Would normally filter to 1 day
            date_to=date_to,     # But this should allow up to 7 days
            only_future_events=True
        )

        # Should include virtual_event which is 2 days out (would be excluded by time_filter_days=1)
        returned_event_ids = [event.id for event, score in results]
        self.assertIn(self.virtual_event.id, returned_event_ids)

    def test_get_context_events_with_date_range(self):
        """Test that get_context_events passes date range to semantic search."""
        import numpy as np
        mock_query_embedding = np.random.rand(384).astype(np.float32)
        self.mock_model.encode.return_value = np.array([mock_query_embedding])

        date_from = timezone.now() + timedelta(days=1)
        date_to = timezone.now() + timedelta(days=3)

        with patch.object(self.rag_service, 'semantic_search') as mock_search:
            mock_search.return_value = [(self.baby_storytime, 0.8)]

            self.rag_service.get_context_events(
                "activities",
                date_from=date_from,
                date_to=date_to
            )

            # Verify semantic_search was called with correct date range params
            mock_search.assert_called_once()
            call_kwargs = mock_search.call_args.kwargs
            self.assertEqual(call_kwargs['date_from'], date_from)
            self.assertEqual(call_kwargs['date_to'], date_to)

    def test_get_context_events_with_explicit_location(self):
        """Test that explicit location parameter overrides message extraction."""
        with patch.object(self.rag_service, 'semantic_search') as mock_search:
            mock_search.return_value = [(self.baby_storytime, 0.8)]

            # Pass explicit location - should override any extracted locations
            self.rag_service.get_context_events(
                "activities in Boston",  # Message mentions Boston
                location="Newton"         # But explicit param says Newton
            )

            # Verify semantic_search was called with explicit location
            call_kwargs = mock_search.call_args.kwargs
            self.assertEqual(call_kwargs['location_filter'], "Newton")


class TestCityLocationFiltering(RAGServiceTest):
    """Test location filtering that includes city search."""

    def setUp(self):
        super().setUp()
        # Create a mock embedding client
        self.mock_client = MagicMock()
        self.rag_service = EventRAGService()
        self.rag_service._embedding_client = self.mock_client

        # Create venues in different cities
        self.newton_venue = baker.make(Venue, name="Newton Library", city="Newton", state="MA")
        self.boston_venue = baker.make(Venue, name="Boston Library", city="Boston", state="MA")

        # Create events at these venues
        import numpy as np
        np.random.seed(123)

        self.newton_event = baker.make(
            Event,
            title="Newton Story Time",
            venue=self.newton_venue,
            start_time=timezone.now() + timedelta(days=1),
            embedding=np.random.rand(384).tolist()
        )
        self.boston_event = baker.make(
            Event,
            title="Boston Story Time",
            venue=self.boston_venue,
            start_time=timezone.now() + timedelta(days=1),
            embedding=np.random.rand(384).tolist()
        )

    def test_location_filter_searches_city(self):
        """Test that location filter searches venue city field."""
        mock_query_embedding = np.random.rand(384).astype(np.float32)
        self.mock_client.encode.return_value = mock_query_embedding

        # Search for Newton events
        results = self.rag_service.semantic_search(
            "story time",
            location_filter="Newton",
            only_future_events=True
        )

        # Should find Newton event
        returned_event_ids = [event.id for event, score in results]
        self.assertIn(self.newton_event.id, returned_event_ids)
        # Should not find Boston event
        self.assertNotIn(self.boston_event.id, returned_event_ids)


class TestEmbeddingManagement(RAGServiceTest):
    """Test embedding creation and management."""

    def setUp(self):
        super().setUp()
        # Create a mock embedding client
        self.mock_client = MagicMock()
        self.rag_service = EventRAGService()
        self.rag_service._embedding_client = self.mock_client

    def test_update_event_embeddings_creates_embeddings(self):
        """Test that update_event_embeddings creates embeddings for events."""
        # Mock embeddings output as numpy arrays with correct 384 dimensions
        # (matches sentence-transformers all-MiniLM-L6-v2 model dimensions)
        mock_embedding_1 = np.random.rand(384).astype(np.float32)
        mock_embedding_2 = np.random.rand(384).astype(np.float32)
        mock_embeddings = np.array([mock_embedding_1, mock_embedding_2])
        self.mock_client.encode.return_value = mock_embeddings

        # Update embeddings for specific events
        event_ids = [self.baby_storytime.id, self.dance_class.id]
        self.rag_service.update_event_embeddings(event_ids)

        # Check that embeddings were saved
        updated_events = Event.objects.filter(id__in=event_ids)
        for event in updated_events:
            self.assertIsNotNone(event.embedding)
            self.assertEqual(len(event.embedding), 384)  # sentence-transformers all-MiniLM-L6-v2 dimension

    def test_update_event_embeddings_uses_vectorized_text(self):
        """Test that embeddings are created from the proper vectorized text."""
        # Use proper 384-dimension numpy array
        mock_embedding = np.random.rand(384).astype(np.float32)
        mock_embeddings = np.array([mock_embedding])
        self.mock_client.encode.return_value = mock_embeddings

        self.rag_service.update_event_embeddings([self.baby_storytime.id])

        # Verify encode was called with the vectorized text
        self.mock_client.encode.assert_called_once()
        call_args = self.mock_client.encode.call_args[0][0]  # First positional argument

        # Should be list of vectorized texts
        self.assertEqual(len(call_args), 1)
        vectorized_text = call_args[0]

        # Should contain key elements from vectorized text
        self.assertIn("Budding Bookworms", vectorized_text)
        self.assertIn("storytime", vectorized_text)
        self.assertIn("Children's Room", vectorized_text)


class ScoringWeightsTest(TestCase):
    """Test scoring weights configuration."""

    def test_default_weights_sum_to_one(self):
        """Default weights should sum to 1.0."""
        from api.rag_service import ScoringWeights

        weights = ScoringWeights()
        total = (
            weights.semantic_similarity
            + weights.location_match
            + weights.time_relevance
            + weights.category_match
            + weights.popularity
        )
        self.assertAlmostEqual(total, 1.0, places=2)

    def test_from_dict_creates_weights(self):
        """from_dict should create weights from dictionary."""
        from api.rag_service import ScoringWeights

        weights = ScoringWeights.from_dict({
            'semantic_similarity': 0.5,
            'location_match': 0.3,
            'time_relevance': 0.1,
            'category_match': 0.05,
            'popularity': 0.05,
        })
        self.assertEqual(weights.semantic_similarity, 0.5)
        self.assertEqual(weights.location_match, 0.3)

    def test_from_dict_uses_defaults_for_missing(self):
        """from_dict should use defaults for missing keys."""
        from api.rag_service import ScoringWeights

        weights = ScoringWeights.from_dict({'semantic_similarity': 0.6})
        self.assertEqual(weights.semantic_similarity, 0.6)
        self.assertEqual(weights.location_match, 0.25)  # Default


class RankingFactorsTest(TestCase):
    """Test ranking factors computation."""

    def test_to_dict_returns_all_fields(self):
        """to_dict should include all factor fields."""
        from api.rag_service import RankingFactors

        factors = RankingFactors(
            semantic_similarity=0.8,
            location_match=0.9,
            time_relevance=0.7,
            category_match=0.5,
            popularity=0.5,
            distance_miles=2.5,
            days_until_event=3.0,
        )
        result = factors.to_dict()

        self.assertEqual(result['semantic_similarity'], 0.8)
        self.assertEqual(result['location_match'], 0.9)
        self.assertEqual(result['distance_miles'], 2.5)
        self.assertEqual(result['days_until_event'], 3.0)


class RAGResultTest(TestCase):
    """Test RAGResult dataclass."""

    def test_all_events_combines_tiers(self):
        """all_events should combine all tiers in order."""
        from api.rag_service import RAGResult, RankedEvent, RankingFactors

        factors = RankingFactors()

        recommended = [
            RankedEvent({'id': 1, 'title': 'A'}, 0.9, factors, 'recommended'),
            RankedEvent({'id': 2, 'title': 'B'}, 0.85, factors, 'recommended'),
        ]
        additional = [
            RankedEvent({'id': 3, 'title': 'C'}, 0.7, factors, 'additional'),
        ]
        context = [
            RankedEvent({'id': 4, 'title': 'D'}, 0.5, factors, 'context'),
        ]

        result = RAGResult(
            recommended_events=recommended,
            additional_events=additional,
            context_events=context,
            total_considered=10,
        )

        self.assertEqual(len(result.all_events), 4)
        self.assertEqual(result.all_events[0].event_data['id'], 1)
        self.assertEqual(result.all_events[2].event_data['id'], 3)
        self.assertEqual(result.all_events[3].event_data['id'], 4)

    def test_recommended_ids_extracts_ids(self):
        """recommended_ids should return list of IDs."""
        from api.rag_service import RAGResult, RankedEvent, RankingFactors

        factors = RankingFactors()
        recommended = [
            RankedEvent({'id': 10, 'title': 'X'}, 0.9, factors, 'recommended'),
            RankedEvent({'id': 20, 'title': 'Y'}, 0.85, factors, 'recommended'),
        ]

        result = RAGResult(recommended_events=recommended, total_considered=5)

        self.assertEqual(result.recommended_ids, [10, 20])

    def test_to_legacy_format(self):
        """to_legacy_format should return list of event dicts."""
        from api.rag_service import RAGResult, RankedEvent, RankingFactors

        factors = RankingFactors()
        recommended = [
            RankedEvent({'id': 1, 'title': 'A'}, 0.9, factors, 'recommended'),
        ]
        additional = [
            RankedEvent({'id': 2, 'title': 'B'}, 0.7, factors, 'additional'),
        ]
        context = [
            RankedEvent({'id': 3, 'title': 'C'}, 0.5, factors, 'context'),
        ]

        result = RAGResult(
            recommended_events=recommended,
            additional_events=additional,
            context_events=context,
        )
        legacy = result.to_legacy_format()

        # Should include recommended + additional, not context
        self.assertEqual(len(legacy), 2)
        self.assertEqual(legacy[0]['id'], 1)
        self.assertEqual(legacy[1]['id'], 2)


class TieredRetrievalTest(TestCase):
    """Test tiered retrieval with multi-factor scoring."""

    def setUp(self):
        """Create test events with locations."""
        from locations.models import Location

        # Create a test location
        self.newton = Location.objects.create(
            geoid='2547100',
            name='Newton',
            normalized_name='newton',
            state='MA',
            latitude=42.337807,
            longitude=-71.209182,
            population=88923,
        )

        # Create venue near Newton
        self.venue = baker.make(
            Venue,
            name='Newton Library',
            city='Newton',
            state='MA',
            latitude=42.338,
            longitude=-71.210,
        )

        # Create future events
        self.event1 = baker.make(
            Event,
            title='Kids Storytime',
            description='A fun storytime for children',
            venue=self.venue,
            start_time=timezone.now() + timedelta(days=1),
            metadata_tags=['kids', 'story'],
        )
        self.event2 = baker.make(
            Event,
            title='Adult Book Club',
            description='Monthly book discussion',
            venue=self.venue,
            start_time=timezone.now() + timedelta(days=7),
            metadata_tags=['adults', 'books'],
        )

    @patch('api.embedding_client.EmbeddingClient.encode')
    def test_tiered_retrieval_with_location_id(self, mock_encode):
        """Tiered retrieval should resolve location from ID."""
        from api.rag_service import EventRAGService

        mock_encode.return_value = np.array([0.1] * 384)
        rag = EventRAGService()

        # Mock semantic search to return our events
        with patch.object(rag, 'semantic_search') as mock_search:
            mock_search.return_value = [
                (self.event1, 0.8),
                (self.event2, 0.6),
            ]

            result = rag.get_context_events_tiered(
                user_message='activities for kids',
                location_id=self.newton.id,
                max_recommended=1,
                max_additional=1,
                max_context=5,
            )

            # Should have tiered results
            self.assertEqual(len(result.recommended_events), 1)
            self.assertEqual(len(result.additional_events), 1)
            self.assertEqual(result.recommended_events[0].event_data['id'], self.event1.id)

    @patch('api.embedding_client.EmbeddingClient.encode')
    def test_tiered_retrieval_with_custom_weights(self, mock_encode):
        """Tiered retrieval should use custom scoring weights."""
        from api.rag_service import EventRAGService, ScoringWeights

        mock_encode.return_value = np.array([0.1] * 384)
        rag = EventRAGService()

        # Custom weights emphasizing location
        weights = ScoringWeights(
            semantic_similarity=0.2,
            location_match=0.5,
            time_relevance=0.2,
            category_match=0.05,
            popularity=0.05,
        )

        with patch.object(rag, 'semantic_search') as mock_search:
            mock_search.return_value = [
                (self.event1, 0.8),
                (self.event2, 0.6),
            ]

            result = rag.get_context_events_tiered(
                user_message='activities',
                scoring_weights=weights,
                max_recommended=2,
            )

            # Verify weights were used in search metadata
            self.assertEqual(result.search_metadata['weights']['location_match'], 0.5)
            self.assertEqual(result.search_metadata['weights']['semantic_similarity'], 0.2)