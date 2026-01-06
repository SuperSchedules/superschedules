"""
Date extraction service for natural language date references.

Extracts date ranges from queries like:
- "tomorrow" -> single day
- "this weekend" -> Saturday to Sunday
- "next Saturday" -> single day
- "Friday through Sunday" -> date range
- "activities for tomorrow and Saturday" -> multiple dates combined into range
"""

import re
from datetime import datetime, timedelta
from typing import Optional, Tuple, List
from dataclasses import dataclass

from dateparser.search import search_dates


@dataclass
class DateExtractionResult:
    """Result of date extraction from a query."""
    date_from: Optional[datetime]
    date_to: Optional[datetime]
    extracted_phrases: List[str]
    confidence: float  # 0.0 to 1.0


def extract_dates_from_query(
    query: str,
    reference_date: Optional[datetime] = None,
) -> DateExtractionResult:
    """
    Extract date range from a natural language query.

    Args:
        query: User's query text
        reference_date: Base date for relative references (defaults to now)

    Returns:
        DateExtractionResult with date_from, date_to, and metadata
    """
    if reference_date is None:
        reference_date = datetime.now()

    query_lower = query.lower()
    extracted_phrases = []
    dates_found = []

    # Step 1: Check for common patterns that dateparser misses
    common_dates = _extract_common_patterns(query_lower, reference_date)
    if common_dates:
        dates_found.extend(common_dates)
        extracted_phrases.extend([p for p, _ in common_dates])

    # Step 2: Use dateparser's search_dates for additional dates
    dateparser_settings = {
        'PREFER_DATES_FROM': 'future',
        'RELATIVE_BASE': reference_date,
    }

    try:
        search_results = search_dates(query, settings=dateparser_settings)
        if search_results:
            for phrase, dt in search_results:
                # Avoid duplicates from common patterns
                phrase_lower = phrase.lower()
                if not any(phrase_lower in p or p in phrase_lower for p in extracted_phrases):
                    # Filter out false positives (age ranges, numbers without context)
                    if _is_false_positive(phrase_lower, query_lower):
                        continue
                    dates_found.append((phrase, dt))
                    extracted_phrases.append(phrase)
    except Exception:
        pass  # dateparser can fail on some inputs

    # Step 3: Filter out past dates (event discovery is about future activities)
    today_start = reference_date.replace(hour=0, minute=0, second=0, microsecond=0)
    future_dates = [(phrase, dt) for phrase, dt in dates_found if dt.replace(hour=0, minute=0, second=0, microsecond=0) >= today_start]

    # If all dates were in the past, return no extraction
    if not future_dates:
        return DateExtractionResult(
            date_from=None,
            date_to=None,
            extracted_phrases=[],
            confidence=0.0,
        )

    # Update extracted_phrases to only include future dates
    extracted_phrases = [p for p, _ in future_dates]

    # Get all datetime objects
    all_dates = [dt for _, dt in future_dates]

    # Find the range
    min_date = min(all_dates)
    max_date = max(all_dates)

    # Normalize to start/end of day
    date_from = min_date.replace(hour=0, minute=0, second=0, microsecond=0)
    date_to = max_date.replace(hour=23, minute=59, second=59, microsecond=0)

    # Calculate confidence based on extraction quality
    confidence = _calculate_confidence(query_lower, extracted_phrases, dates_found)

    return DateExtractionResult(
        date_from=date_from,
        date_to=date_to,
        extracted_phrases=extracted_phrases,
        confidence=confidence,
    )


def _extract_common_patterns(query: str, reference_date: datetime) -> List[Tuple[str, datetime]]:
    """
    Extract dates from common patterns that dateparser misses.

    Returns list of (phrase, datetime) tuples.
    """
    results = []
    today = reference_date.replace(hour=0, minute=0, second=0, microsecond=0)
    weekday = today.weekday()  # Monday=0, Sunday=6

    # Today
    if re.search(r'\btoday\b', query):
        results.append(('today', today))

    # Tonight
    if re.search(r'\btonight\b', query):
        tonight = today.replace(hour=18)
        results.append(('tonight', tonight))

    # Tomorrow
    if re.search(r'\btomorrow\b', query):
        tomorrow = today + timedelta(days=1)
        results.append(('tomorrow', tomorrow))

    # This weekend (upcoming Saturday and Sunday)
    if re.search(r'\bthis\s+weekend\b', query):
        days_until_saturday = (5 - weekday) % 7
        if days_until_saturday == 0 and weekday != 5:
            days_until_saturday = 7
        saturday = today + timedelta(days=days_until_saturday)
        sunday = saturday + timedelta(days=1)
        results.append(('this weekend', saturday))
        results.append(('this weekend (end)', sunday))

    # Next weekend (Saturday/Sunday of next week)
    if re.search(r'\bnext\s+weekend\b', query):
        days_until_saturday = (5 - weekday) % 7
        if days_until_saturday == 0:
            days_until_saturday = 7
        next_saturday = today + timedelta(days=days_until_saturday + 7)
        next_sunday = next_saturday + timedelta(days=1)
        results.append(('next weekend', next_saturday))
        results.append(('next weekend (end)', next_sunday))

    # Day names: "this Friday", "next Saturday", etc.
    day_names = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']
    for i, day_name in enumerate(day_names):
        # "this [day]" or just "[day]"
        this_pattern = rf'\b(?:this\s+)?{day_name}\b'
        next_pattern = rf'\bnext\s+{day_name}\b'

        if re.search(next_pattern, query):
            # "next [day]" - always the one in the following week
            days_ahead = (i - weekday) % 7
            if days_ahead == 0:
                days_ahead = 7
            target = today + timedelta(days=days_ahead + 7)
            results.append((f'next {day_name}', target))
        elif re.search(this_pattern, query):
            # "this [day]" or just "[day]" - the upcoming one
            days_ahead = (i - weekday) % 7
            if days_ahead == 0 and i != weekday:
                days_ahead = 7
            # If it's today and they say "Friday" on Friday, that's today
            target = today + timedelta(days=days_ahead)
            results.append((day_name, target))

    # "in X days/hours"
    in_days_match = re.search(r'\bin\s+(\d+)\s+days?\b', query)
    if in_days_match:
        days = int(in_days_match.group(1))
        target = today + timedelta(days=days)
        results.append((in_days_match.group(0), target))

    in_hours_match = re.search(r'\bin\s+(\d+)\s+hours?\b', query)
    if in_hours_match:
        hours = int(in_hours_match.group(1))
        target = reference_date + timedelta(hours=hours)
        results.append((in_hours_match.group(0), target))

    # "next X days/hours"
    next_days_match = re.search(r'\bnext\s+(\d+)\s+days?\b', query)
    if next_days_match:
        days = int(next_days_match.group(1))
        target = today + timedelta(days=days)
        results.append((next_days_match.group(0), target))

    next_hours_match = re.search(r'\bnext\s+(\d+)\s+hours?\b', query)
    if next_hours_match:
        hours = int(next_hours_match.group(1))
        target = reference_date + timedelta(hours=hours)
        results.append((next_hours_match.group(0), target))

    return results


def _is_false_positive(phrase: str, full_query: str) -> bool:
    """
    Check if a parsed date phrase is likely a false positive.

    Examples of false positives:
    - "3 year" (from "3 year old")
    - "3-5 year" (from "3-5 year olds")
    - Just a number without date context
    """
    phrase = phrase.strip()

    # Age patterns: "X year old", "X-Y year olds", "X years old"
    age_patterns = [
        r'^\d+[-–]\d+\s*year',  # "3-5 year"
        r'^\d+\s*year',          # "3 year"
        r'^\d+\s*years?\s*old',  # "3 years old"
    ]
    for pattern in age_patterns:
        if re.match(pattern, phrase, re.IGNORECASE):
            return True

    # Check if phrase appears in age context in full query
    # e.g., "3 year" in "3 year old" or "3-5 year" in "3-5 year olds"
    age_context_patterns = [
        rf'{re.escape(phrase)}\s*olds?\b',
        rf'{re.escape(phrase)}\s+old\b',
    ]
    for pattern in age_context_patterns:
        if re.search(pattern, full_query, re.IGNORECASE):
            return True

    # Just a bare number (no date context)
    if re.match(r'^\d+$', phrase):
        return True

    # Common words that dateparser might misinterpret as dates
    # - "time" = story time, lunch time (not a date)
    # - "do" = "I want to do something" (dateparser thinks it's Thursday in some languages)
    # - Short words without clear date meaning
    false_positive_words = {'time', 'do', 'to', 'at', 'on', 'in', 'for', 'the', 'a', 'an'}
    if phrase.lower() in false_positive_words:
        return True

    return False


def _calculate_confidence(query: str, extracted_phrases: List[str], dates_found: List[Tuple[str, datetime]]) -> float:
    """Calculate confidence score for the extraction."""
    if not dates_found:
        return 0.0

    # Base confidence from having found dates
    confidence = 0.5

    # Higher confidence for explicit date keywords
    explicit_keywords = ['tomorrow', 'today', 'weekend', 'saturday', 'sunday', 'monday', 'tuesday', 'wednesday', 'thursday', 'friday']
    if any(kw in query for kw in explicit_keywords):
        confidence += 0.3

    # Lower confidence if query is very long (dates might be incidental)
    if len(query) > 100:
        confidence -= 0.1

    # Higher confidence if multiple dates found (user is being specific)
    if len(dates_found) > 1:
        confidence += 0.1

    return min(max(confidence, 0.0), 1.0)
