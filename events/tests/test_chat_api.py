from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken
from events.models import Event, Source
from django.utils import timezone
from datetime import datetime, timedelta

User = get_user_model()


class ChatAPITestCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username="testuser@example.com",
            email="testuser@example.com",
            password="testpass123"
        )
        
        # Create JWT token for authentication
        refresh = RefreshToken.for_user(self.user)
        self.access_token = str(refresh.access_token)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.access_token}')
        
        # Create a test source and events
        self.source = Source.objects.create(
            base_url="https://example.com",
            name="Test Source"
        )
        
        # Create some test events
        self.event1 = Event.objects.create(
            source=self.source,
            external_id="test-1",
            title="Kids Story Time",
            description="Story time for children ages 3-6",
            location="Newton Public Library",
            start_time=timezone.now() + timedelta(days=1),
            end_time=timezone.now() + timedelta(days=1, hours=1)
        )
        
        self.event2 = Event.objects.create(
            source=self.source,
            external_id="test-2", 
            title="Family Fun Day",
            description="Activities for families with young children",
            location="Newton Community Center",
            start_time=timezone.now() + timedelta(days=2),
            end_time=timezone.now() + timedelta(days=2, hours=2)
        )

    def test_chat_endpoint_basic_message(self):
        """Test basic chat functionality"""
        url = reverse('api-1.0.0:chat_message')
        data = {
            "message": "I need activities for 5 year olds in Newton",
            "context": {
                "location": "Newton"
            }
        }
        
        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, 200)
        
        response_data = response.json()
        self.assertIn('response', response_data)
        self.assertIn('suggested_event_ids', response_data)
        self.assertIn('follow_up_questions', response_data)
        self.assertIn('session_id', response_data)
        
        # Should return a meaningful response
        self.assertIn('activities', response_data['response'].lower())

    def test_chat_endpoint_greeting(self):
        """Test greeting response"""
        url = reverse('api-1.0.0:chat_message')
        data = {
            "message": "Hello"
        }
        
        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, 200)
        
        response_data = response.json()
        self.assertIn('Hi', response_data['response'])
        self.assertEqual(response_data['suggested_event_ids'], [])
        self.assertTrue(len(response_data['follow_up_questions']) > 0)

    def test_chat_endpoint_with_session(self):
        """Test session management"""
        url = reverse('api-1.0.0:chat_message')
        data = {
            "message": "I need activities for kids",
            "session_id": "test-session-123"
        }
        
        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, 200)
        
        response_data = response.json()
        # Should return either the provided session ID or generate a new one
        self.assertIsNotNone(response_data['session_id'])

    def test_events_endpoint_with_ids(self):
        """Test events endpoint filtering by IDs"""
        url = reverse('api-1.0.0:list_events')
        
        # Test filtering by specific event IDs
        response = self.client.get(url, {'ids': [self.event1.id, self.event2.id]})
        self.assertEqual(response.status_code, 200)
        
        events = response.json()
        self.assertEqual(len(events), 2)
        
        event_ids = [event['id'] for event in events]
        self.assertIn(self.event1.id, event_ids)
        self.assertIn(self.event2.id, event_ids)

    def test_events_endpoint_with_single_id(self):
        """Test events endpoint with single ID"""
        url = reverse('api-1.0.0:list_events')
        
        response = self.client.get(url, {'ids': [self.event1.id]})
        self.assertEqual(response.status_code, 200)
        
        events = response.json()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['id'], self.event1.id)

    def test_authentication_required(self):
        """Test that authentication is required for chat endpoint"""
        self.client.credentials()  # Remove authentication
        
        url = reverse('api-1.0.0:chat_message')
        data = {"message": "Hello"}
        
        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, 401)