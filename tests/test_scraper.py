import os
import sys
import re
import responses

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from events.scraper import find_calendar_urls, parse_ics


def test_parse_ics():
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
    assert len(events) == 1
    ev = events[0]
    assert ev["title"] == "Sample Event"
    assert ev["location"] == "Boston"
    assert ev["url"] == "https://example.com/event"


@responses.activate
def test_find_calendar_urls():
    html = "<html><body><a href='https://example.com/events.ics'>cal</a></body></html>"
    pattern = re.compile(r"https://duckduckgo.com/html/.*")
    responses.get(pattern, body=html)
    urls = find_calendar_urls("boston college")
    assert urls == ["https://example.com/events.ics"]
