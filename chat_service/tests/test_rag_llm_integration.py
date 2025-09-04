"""
Tests for RAG/LLM integration issues.

This test reproduces the specific issue where:
- RAG service finds relevant events (8 events near Needham)  
- Chat service receives those events
- But LLM response says "no events found"
"""

import asyncio
from unittest.mock import patch, MagicMock, AsyncMock
from django.test import TestCase
from model_bakery import baker
from datetime import datetime, timedelta

from events.models import Event, Source
from chat_service.app import get_relevant_events, stream_model_response
from api.llm_service import create_event_discovery_prompt


class RAGLLMIntegrationTest(TestCase):
    """Test the full RAG -> Chat -> LLM pipeline"""
    
    def setUp(self):
        """Create test events that should be found by RAG"""
        source = baker.make(Source, name="Needham Library")
        
        # Create events that should be found for "needham with kids" query
        self.needham_events = [
            baker.make(Event,
                title="Kids Story Time at Needham Library",
                description="Interactive storytime for children ages 3-6 with crafts and songs",
                location="Needham Public Library Children's Room", 
                start_time=datetime.now() + timedelta(days=2),
                source=source,
                embedding=[0.1] * 384  # Mock embedding
            ),
            baker.make(Event,
                title="Family Fun Day in Needham",
                description="Outdoor activities for families with children including games and snacks",
                location="Needham Town Common",
                start_time=datetime.now() + timedelta(days=3), 
                source=source,
                embedding=[0.2] * 384
            ),
            baker.make(Event,
                title="Children's Art Workshop Needham", 
                description="Creative art class for kids aged 5-10 with all supplies provided",
                location="Needham Community Center",
                start_time=datetime.now() + timedelta(days=4),
                source=source,
                embedding=[0.3] * 384
            )
        ]
    
    def test_rag_finds_events_but_llm_says_none_found(self):
        """
        Reproduce the exact issue: RAG finds events but LLM says no events found.
        
        This test should FAIL initially, then we fix the underlying issue.
        """
        query = "can you help me find something to do near needham? in the next few days with kids?"
        
        # Step 1: Verify RAG service finds events (should work)
        with patch('api.rag_service.EventRAGService') as mock_rag_class:
            mock_rag = MagicMock()
            mock_rag_class.return_value = mock_rag
            
            # Mock RAG to return our test events (simulates the working RAG)
            mock_rag.get_context_events.return_value = [
                {
                    'id': event.id,
                    'title': event.title,
                    'description': event.description,
                    'location': event.location,
                    'start_time': event.start_time.isoformat(),
                    'similarity_score': 0.6,
                    'url': event.url
                }
                for event in self.needham_events
            ]
            
            # Step 2: Call get_relevant_events (simulates chat service)
            async def run_test():
                relevant_events = await get_relevant_events(query)
                
                # Verify RAG found events
                self.assertEqual(len(relevant_events), 3, "RAG should find 3 relevant events")
                self.assertTrue(all('needham' in event['title'].lower() or 'needham' in event['location'].lower() 
                                  for event in relevant_events), "All events should be Needham-related")
                
                # Step 3: Create LLM prompt with those events
                system_prompt, user_prompt = create_event_discovery_prompt(
                    query, relevant_events, {
                        'current_date': datetime.now().isoformat(),
                        'location': None,
                        'preferences': {}
                    }
                )
                
                # Verify events are in the prompt
                self.assertIn("Kids Story Time", user_prompt, "Event should be in LLM prompt")
                self.assertIn("Family Fun Day", user_prompt, "Event should be in LLM prompt") 
                self.assertIn("Children's Art Workshop", user_prompt, "Event should be in LLM prompt")
                self.assertNotIn("(No matching upcoming events found in database)", user_prompt, 
                                "Should not show 'no events' message when events exist")
                
                # Step 4: Mock LLM to return "no events found" response (the bug)
                mock_llm_response = """I don't see any upcoming events that match what you're looking for. You might want to check local libraries or community centers for activities and events near Needham."""
                
                # Step 5: Verify this is the problematic behavior
                # This assertion should FAIL initially because the LLM ignores the events
                self.assertNotIn("don't see any upcoming events", mock_llm_response,
                                "LLM should NOT say no events when RAG provided 3 relevant events")
                self.assertIn("Story Time", mock_llm_response,
                            "LLM should mention the specific events found by RAG")
                
                return relevant_events, user_prompt, mock_llm_response
            
            # Run the async test
            result = asyncio.run(run_test())
            return result
    
    def test_llm_prompt_format_with_events(self):
        """Test that events are properly formatted in the LLM prompt"""
        
        # Create sample events data (as would come from RAG)
        sample_events = [
            {
                'id': 1,
                'title': 'Kids Story Time',
                'description': 'Fun stories for children',
                'location': 'Needham Library',
                'start_time': '2025-01-15T10:00:00',
                'url': 'https://needham.library/storytime'
            }
        ]
        
        query = "activities for kids in needham"
        system_prompt, user_prompt = create_event_discovery_prompt(
            query, sample_events, {
                'current_date': '2025-01-13T20:00:00',
                'location': None,
                'preferences': {}
            }
        )
        
        # Verify proper formatting
        self.assertIn("1. Kids Story Time", user_prompt, "Event should be numbered")
        self.assertIn("Needham Library", user_prompt, "Location should be included")
        self.assertIn("January 15", user_prompt, "Date should be formatted") 
        self.assertIn("10:00 AM", user_prompt, "Time should be formatted")
        self.assertIn("needham.library", user_prompt, "URL should be included")
        
        # Verify system prompt has correct instructions
        self.assertIn("ONLY use events from the provided list", system_prompt)
        self.assertIn("DO NOT invent events", system_prompt)
        self.assertIn("Here are the upcoming events I found", system_prompt)
        
        # Most importantly: verify there's no "no events" message
        self.assertNotIn("No matching upcoming events found", user_prompt)
        self.assertNotIn("don't see any upcoming events", user_prompt)
    
    def test_empty_events_handling(self):
        """Test behavior when RAG truly finds no events"""
        
        query = "activities for kids in mars" 
        empty_events = []
        
        system_prompt, user_prompt = create_event_discovery_prompt(
            query, empty_events, {
                'current_date': '2025-01-13T20:00:00',
                'location': None,
                'preferences': {}
            }
        )
        
        # When no events found, should have appropriate message
        self.assertIn("(No matching upcoming events found in database)", user_prompt)
        self.assertIn("do not invent any events", user_prompt)
        
        # System prompt should still have "no events" handling
        self.assertIn('If no events: "I don\'t see any upcoming events', system_prompt)


class MockLLMStreamTest(TestCase):
    """Test LLM streaming with mocked responses"""
    
    def test_llm_ignores_provided_events(self):
        """
        Test that reproduces LLM saying no events when events are provided.
        This simulates the exact bug we're experiencing.
        """
        
        # Mock events that RAG found
        mock_events = [
            {
                'id': 1,
                'title': 'Needham Kids Festival',
                'description': 'Annual festival with activities for children',
                'location': 'Needham Town Common',
                'start_time': '2025-01-16T14:00:00',
                'similarity_score': 0.7
            }
        ]
        
        query = "events for kids in needham"
        
        # Create prompt with events
        system_prompt, user_prompt = create_event_discovery_prompt(
            query, mock_events, {'current_date': '2025-01-13T20:00:00'}
        )
        
        # Mock LLM service that ignores the events (reproduces the bug)
        with patch('api.llm_service.get_llm_service') as mock_llm_service:
            mock_service = MagicMock()
            mock_llm_service.return_value = mock_service
            
            # Mock LLM response that ignores the provided events
            mock_service.stream_chat.return_value = [
                {'token': "I don't see any upcoming events that match what you're looking for.", 'done': False},
                {'token': " You might want to check local libraries.", 'done': False}, 
                {'token': '', 'done': True, 'success': True}
            ]
            
            # This should fail because LLM is ignoring the events in the prompt
            full_response = ""
            for chunk in mock_service.stream_chat.return_value:
                if not chunk['done']:
                    full_response += chunk['token']
            
            # The bug: LLM says no events despite events being in prompt
            self.assertIn("Needham Kids Festival", user_prompt, "Event should be in prompt")
            self.assertNotIn("don't see any upcoming events", full_response, 
                            "LLM should not say no events when events are provided in prompt")
            self.assertIn("Needham Kids Festival", full_response,
                         "LLM should mention the specific event from the prompt")


class PromptFlowDebugTest(TestCase):
    """Debug tests for the exact RAG -> Prompt -> LLM flow"""
    
    def test_prompt_creation_with_mock_events(self):
        """Test that events from RAG are properly formatted in LLM prompts"""
        
        # Simulate events returned by RAG (this part works)
        mock_events = [
            {
                'id': 1,
                'title': 'Kids Story Time at Needham Library',
                'description': 'Interactive storytime for children ages 3-6 with crafts and songs',
                'location': 'Needham Public Library Children\'s Room',
                'start_time': (datetime.now() + timedelta(days=2)).isoformat(),
                'url': 'https://needham.library/storytime',
                'similarity_score': 0.7
            },
            {
                'id': 2,
                'title': 'Family Fun Day in Needham',
                'description': 'Outdoor activities for families with children including games and snacks',
                'location': 'Needham Town Common',
                'start_time': (datetime.now() + timedelta(days=3)).isoformat(),
                'url': None,
                'similarity_score': 0.6
            }
        ]
        
        query = "can you help me find something to do near needham? in the next few days with kids?"
        
        # Test prompt creation (this is where issues might occur)
        system_prompt, user_prompt = create_event_discovery_prompt(
            query, mock_events, {
                'current_date': datetime.now().strftime('%A, %B %d, %Y at %I:%M %p'),
                'location': None,
                'preferences': {}
            }
        )
        
        # Debug output
        print(f"\n=== DEBUGGING PROMPT FLOW ===")
        print(f"Query: {query}")
        print(f"Events from RAG: {len(mock_events)}")
        for event in mock_events:
            print(f"  - {event['title']} (score: {event['similarity_score']})")
        
        print(f"\nSystem prompt length: {len(system_prompt)}")
        print(f"User prompt length: {len(user_prompt)}")
        
        # Check if events are properly included in prompt
        events_in_prompt = []
        for event in mock_events:
            if event['title'] in user_prompt:
                events_in_prompt.append(event['title'])
        
        print(f"Events found in prompt: {len(events_in_prompt)}/{len(mock_events)}")
        print(f"Events in prompt: {events_in_prompt}")
        
        # Check for problematic "no events" message
        has_no_events_msg = "(No matching upcoming events found in database)" in user_prompt
        print(f"Has 'no events' message: {has_no_events_msg}")
        
        # Show first 500 chars of user prompt for inspection
        print(f"\nFirst 500 chars of user prompt:")
        print(user_prompt[:500] + "..." if len(user_prompt) > 500 else user_prompt)
        
        # Assertions to catch the bug
        self.assertEqual(len(events_in_prompt), len(mock_events), 
                        f"All {len(mock_events)} events should appear in prompt, only found {len(events_in_prompt)}")
        self.assertFalse(has_no_events_msg, 
                        "'No events' message should not appear when events are provided")
        
        # Verify key event details are included
        self.assertIn("Kids Story Time", user_prompt, "First event title should be in prompt")
        self.assertIn("Family Fun Day", user_prompt, "Second event title should be in prompt")
        self.assertIn("Needham", user_prompt, "Location should be in prompt")
    
    async def test_actual_rag_flow_integration(self):
        """Test the complete RAG flow as used in the chat service"""
        
        # Create test events for RAG to find
        source = baker.make(Source, name="Test Source")
        test_events = [
            baker.make(Event,
                title="Needham Kids Activity",
                description="Fun activities for children in Needham",
                location="Needham Community Center", 
                start_time=datetime.now() + timedelta(days=1),
                source=source,
                embedding=[0.5] * 384  # Mock embedding that should match
            )
        ]
        
        query = "activities for kids in needham"
        
        # Mock RAG service to return our test events
        with patch('api.rag_service.get_rag_service') as mock_rag_service:
            mock_rag = MagicMock()
            mock_rag_service.return_value = mock_rag
            
            # Mock RAG to return events (simulates working RAG)
            mock_rag.get_context_events.return_value = [
                {
                    'id': event.id,
                    'title': event.title,
                    'description': event.description,
                    'location': event.location,
                    'start_time': event.start_time.isoformat(),
                    'url': event.url,
                    'similarity_score': 0.6
                }
                for event in test_events
            ]
            
            # Call the actual chat service function
            relevant_events = await get_relevant_events(query)
            
            print(f"\n=== ACTUAL RAG FLOW TEST ===")
            print(f"Query: {query}")
            print(f"Events returned by get_relevant_events: {len(relevant_events)}")
            
            # Verify RAG integration
            self.assertEqual(len(relevant_events), 1, "Should return 1 test event")
            self.assertEqual(relevant_events[0]['title'], "Needham Kids Activity")
            
            # Test prompt creation with actual RAG results
            if relevant_events:
                system_prompt, user_prompt = create_event_discovery_prompt(
                    query, relevant_events, {
                        'current_date': datetime.now().strftime('%A, %B %d, %Y at %I:%M %p'),
                        'location': None,
                        'preferences': {}
                    }
                )
                
                print(f"Prompt created with {len(relevant_events)} events")
                print(f"User prompt includes event: {'Needham Kids Activity' in user_prompt}")
                
                # Critical assertions
                self.assertIn("Needham Kids Activity", user_prompt, 
                            "Event from RAG should appear in LLM prompt")
                self.assertNotIn("(No matching upcoming events found in database)", user_prompt,
                                "Should not show 'no events' when RAG found events")
                
                print("✅ RAG -> Prompt flow working correctly")
            else:
                self.fail("RAG returned no events - this is the core issue!")


def run_async_test_method(test_method):
    """Helper to run async test methods in Django TestCase"""
    def wrapper(self):
        return asyncio.run(test_method(self))
    return wrapper


# Apply async wrapper to test methods
RAGLLMIntegrationTest.test_rag_finds_events_but_llm_says_none_found = run_async_test_method(
    RAGLLMIntegrationTest.test_rag_finds_events_but_llm_says_none_found
)

PromptFlowDebugTest.test_actual_rag_flow_integration = run_async_test_method(
    PromptFlowDebugTest.test_actual_rag_flow_integration
)