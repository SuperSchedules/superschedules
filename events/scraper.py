import requests
from bs4 import BeautifulSoup
from ics import Calendar
from urllib.parse import quote_plus, urljoin, urlparse


def find_calendar_urls(query, max_results=5):
    """Search DuckDuckGo for calendar URLs in ICS format."""
    search_url = (
        f"https://duckduckgo.com/html/?q="
        f"{quote_plus(query + ' events calendar ics')}"
    )
    resp = requests.get(search_url, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    urls = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.lower().endswith(".ics"):
            urls.append(href)
            if len(urls) >= max_results:
                break
    return urls


def parse_ics(content):
    """Parse ICS calendar content and return list of event dictionaries."""
    calendar = Calendar(content)
    events = []
    for ev in calendar.events:
        events.append({
            "uid": ev.uid,
            "title": ev.name,
            "description": ev.description,
            "location": ev.location,
            "start_time": ev.begin.datetime,
            "end_time": ev.end.datetime if ev.end else None,
            "url": str(ev.url) if ev.url else None,
        })
    return events


def fetch_ics_events(url):
    """Download an ICS file from the given URL and parse events."""
    resp = requests.get(url)
    resp.raise_for_status()
    return parse_ics(resp.text)


def find_calendar_urls_in_domain(domain, max_pages=10, max_results=5):
    """Crawl a domain looking for links to ICS calendars."""
    visited = set()
    to_visit = [f"https://{domain}"]
    urls = []

    while to_visit and len(visited) < max_pages and len(urls) < max_results:
        current = to_visit.pop(0)
        if current in visited:
            continue
        visited.add(current)
        try:
            resp = requests.get(current, headers={"User-Agent": "Mozilla/5.0"}, timeout=5)
            resp.raise_for_status()
        except requests.RequestException:
            continue
        soup = BeautifulSoup(resp.text, "html.parser")
        for a in soup.find_all("a", href=True):
            href = a["href"]
            parsed = urlparse(href)
            if not parsed.netloc:
                absolute = urljoin(current, href)
            else:
                absolute = href

            if absolute.lower().endswith(".ics"):
                if absolute not in urls:
                    urls.append(absolute)
                    if len(urls) >= max_results:
                        break
            else:
                parsed_abs = urlparse(absolute)
                if parsed_abs.netloc.endswith(domain):
                    if absolute not in visited and absolute not in to_visit:
                        to_visit.append(absolute)

    return urls



def scrape_events_for_query(query):
    """Find calendar URLs for the query and return parsed events."""
    events = []
    for url in find_calendar_urls(query):
        try:
            events.extend(fetch_ics_events(url))
        except requests.RequestException:
            continue
    return events


def scrape_events_for_domain(domain):
    """Find calendars within a domain and return parsed events."""
    events = []
    for url in find_calendar_urls_in_domain(domain):
        try:
            events.extend(fetch_ics_events(url))
        except requests.RequestException:
            continue
    return events
