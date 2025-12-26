"""
Tests for traces models.
"""

from django.test import TestCase
from django.contrib.auth.models import User

from traces.models import ChatDebugRun, ChatDebugEvent


class TestChatDebugRun(TestCase):
    """Tests for ChatDebugRun model."""

    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='testpass')

    def test_create_run(self):
        run = ChatDebugRun.objects.create(
            created_by=self.user,
            request_text='activities for kids in Newton',
            settings={'max_events': 10, 'location': 'Newton, MA'},
        )

        self.assertIsNotNone(run.id)
        self.assertEqual(run.status, 'pending')
        self.assertEqual(run.request_text, 'activities for kids in Newton')
        self.assertEqual(run.settings['max_events'], 10)

    def test_run_str(self):
        run = ChatDebugRun.objects.create(
            created_by=self.user,
            request_text='this is a very long query that should be truncated in the string representation',
        )

        str_repr = str(run)
        self.assertIn('this is a very long query', str_repr)
        self.assertIn('pending', str_repr)

    def test_run_defaults(self):
        run = ChatDebugRun.objects.create(
            request_text='test',
        )

        self.assertEqual(run.status, 'pending')
        self.assertEqual(run.settings, {})
        self.assertEqual(run.final_answer_text, '')
        self.assertIsNone(run.created_by)


class TestChatDebugEvent(TestCase):
    """Tests for ChatDebugEvent model."""

    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='testpass')
        self.run = ChatDebugRun.objects.create(
            created_by=self.user,
            request_text='test query',
        )

    def test_create_event(self):
        event = ChatDebugEvent.objects.create(
            run=self.run,
            seq=1,
            stage='input',
            data={'message': 'hello world'},
        )

        self.assertIsNotNone(event.id)
        self.assertEqual(event.stage, 'input')
        self.assertEqual(event.data['message'], 'hello world')

    def test_event_ordering(self):
        ChatDebugEvent.objects.create(run=self.run, seq=3, stage='llm_response', data={})
        ChatDebugEvent.objects.create(run=self.run, seq=1, stage='input', data={})
        ChatDebugEvent.objects.create(run=self.run, seq=2, stage='retrieval', data={})

        events = list(self.run.events.all())
        self.assertEqual(events[0].seq, 1)
        self.assertEqual(events[1].seq, 2)
        self.assertEqual(events[2].seq, 3)

    def test_event_str(self):
        event = ChatDebugEvent.objects.create(
            run=self.run,
            seq=5,
            stage='retrieval',
            data={},
        )

        str_repr = str(event)
        self.assertIn('#5', str_repr)
        self.assertIn('retrieval', str_repr)

    def test_unique_run_seq_constraint(self):
        ChatDebugEvent.objects.create(run=self.run, seq=1, stage='input', data={})

        with self.assertRaises(Exception):  # IntegrityError
            ChatDebugEvent.objects.create(run=self.run, seq=1, stage='retrieval', data={})
