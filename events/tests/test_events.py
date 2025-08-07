from django.test import TestCase
from django.contrib.auth import get_user_model
from django_dynamic_fixture import G
from events.models import Source, Event


class EventModelTests(TestCase):
    def test_str(self):
        user = G(get_user_model())
        source = G(Source, user=user)
        event = G(Event, source=source, title="My Event")
        self.assertEqual(str(event), "My Event")
