from django.test import TestCase
from django_dynamic_fixture import G
from events.models import Source, Event


class EventModelTests(TestCase):
    def test_str(self):
        source = G(Source)
        event = G(Event, source=source, title="My Event")
        self.assertEqual(str(event), "My Event")
