from django.test import TestCase
from django_dynamic_fixture import G
from events.models import Event
from venues.models import Venue


class EventModelTests(TestCase):
    def test_str(self):
        venue = G(Venue, name="Test Venue", city="Newton", state="MA")
        event = G(Event, venue=venue, title="My Event")
        self.assertEqual(str(event), "My Event")
