from django.test import SimpleTestCase, TestCase
from django_dynamic_fixture import G
import re
import responses
from events.scraper import find_calendar_urls, parse_ics
from events.models import Source, Event


class ScraperTests(SimpleTestCase):
    def test_parse_ics(self):
        content = """\
BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//example//Test//EN
BEGIN:VEVENT
UID:test1@example.com
SUMMARY:Sample Event
DESCRIPTION:Testing
LOCATION:Boston
DTSTART:20240101T100000Z
DTEND:20240101T110000Z
URL:https://example.com/event
END:VEVENT
END:VCALENDAR
"""
        events = parse_ics(content)
        self.assertEqual(len(events), 1)
        ev = events[0]
        self.assertEqual(ev["title"], "Sample Event")
        self.assertEqual(ev["location"], "Boston")
        self.assertEqual(ev["url"], "https://example.com/event")

    @responses.activate
    def test_find_calendar_urls(self):
        html = "<html><body><a href='https://example.com/events.ics'>cal</a></body></html>"
        pattern = re.compile(r"https://duckduckgo.com/html/.*")
        responses.get(pattern, body=html)
        urls = find_calendar_urls("boston college")
        self.assertEqual(urls, ["https://example.com/events.ics"])


class EventModelTests(TestCase):
    def test_str(self):
        source = G(Source)
        event = G(Event, source=source, title="My Event")
        self.assertEqual(str(event), "My Event")
