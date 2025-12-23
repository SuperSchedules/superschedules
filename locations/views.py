"""
Location autocomplete API endpoints.

Provides:
- GET /api/v1/locations/suggest?q=... - Autocomplete suggestions
- GET /api/v1/locations/{id} - Get location by ID
"""

import re
from typing import List, Optional

from django.db.models import Case, When, Value, IntegerField, Q
from django.shortcuts import get_object_or_404
from ninja import Router, Schema, Query
from ninja.errors import HttpError

from locations.models import Location, normalize_for_matching
from venues.extraction import STATE_ABBREVIATIONS

router = Router()

# Reverse mapping: abbreviation -> full name (for validation)
STATE_CODES = set(STATE_ABBREVIATIONS.values())

# Country code to display name mapping
COUNTRY_NAMES = {
    "US": "United States",
    "CA": "Canada",
}


class LocationResultSchema(Schema):
    """Schema for location autocomplete result."""
    id: int
    name: str
    admin1: str  # State/province code (e.g., MA, ON)
    country_code: str
    lat: float
    lng: float
    label: str  # Human-readable: "Newton, MA, United States"


class SuggestResponseSchema(Schema):
    """Response schema for suggest endpoint."""
    results: List[LocationResultSchema]


def _parse_query_for_state(query: str) -> tuple[str, Optional[str]]:
    """
    Parse query string to extract city and state hints.

    Handles patterns like:
    - "Cambridge, MA" -> ("Cambridge", "MA")
    - "Newton, NJ" -> ("Newton", "NJ")
    - "Springfield" -> ("Springfield", None)
    """
    # Pattern: "City, ST" or "City,ST" (comma + 2-letter code)
    match = re.search(r'^(.+?),\s*([A-Za-z]{2})$', query.strip())
    if match:
        city = match.group(1).strip()
        state_part = match.group(2).strip().upper()
        if state_part in STATE_CODES:
            return (city, state_part)
    return (query.strip(), None)


def _build_label(location: Location) -> str:
    """Build human-readable label for location."""
    country_name = COUNTRY_NAMES.get(location.country_code, location.country_code)
    return f"{location.name}, {location.state}, {country_name}"


def _location_to_result(location: Location) -> LocationResultSchema:
    """Convert Location model to result schema."""
    return LocationResultSchema(
        id=location.id,
        name=location.name,
        admin1=location.state,
        country_code=location.country_code,
        lat=float(location.latitude),
        lng=float(location.longitude),
        label=_build_label(location),
    )


@router.get("/suggest", response=SuggestResponseSchema, auth=None)
def suggest_locations(
    request,
    q: str = Query(..., description="Search query (min 2 characters)"),
    country: Optional[str] = Query(None, description="Country filter: US, CA, or comma-separated list"),
    limit: int = Query(10, description="Max results (default 10, max 20)"),
    admin1: Optional[str] = Query(None, description="State/province filter (e.g., MA, ON)"),
):
    """
    Autocomplete endpoint for location search.

    Returns locations matching the query prefix, ranked by:
    1. Exact match on normalized name
    2. Prefix match
    3. Population (descending)
    4. State, then name (deterministic)
    """
    # Input validation
    if not q or len(q.strip()) < 2:
        raise HttpError(400, "Query must be at least 2 characters")

    # Cap limit at 20
    limit = min(limit, 20)

    # Parse query for state hints (e.g., "Cambridge, MA")
    parsed_query, parsed_state = _parse_query_for_state(q)

    # Use explicit admin1 param if provided, otherwise use parsed state
    effective_admin1 = admin1.upper() if admin1 else parsed_state

    # Normalize for matching
    normalized_q = normalize_for_matching(parsed_query)
    if not normalized_q:
        return SuggestResponseSchema(results=[])

    # Build query
    queryset = Location.objects.all()

    # Apply country filter
    if country:
        countries = [c.strip().upper() for c in country.split(",")]
        queryset = queryset.filter(country_code__in=countries)

    # Apply admin1/state filter
    if effective_admin1:
        queryset = queryset.filter(state=effective_admin1)

    # Prefix matching on normalized_name
    queryset = queryset.filter(normalized_name__startswith=normalized_q)

    # Add ranking annotation:
    # - exact_match = 0 if normalized_name == normalized_q, else 1
    # - population descending (handled in order_by with negative)
    queryset = queryset.annotate(
        exact_match=Case(
            When(normalized_name=normalized_q, then=Value(0)),
            default=Value(1),
            output_field=IntegerField(),
        )
    )

    # Order by:
    # 1. Exact match first (0 before 1)
    # 2. Population descending (nulls last)
    # 3. State (for deterministic ordering)
    # 4. Name (for deterministic ordering)
    queryset = queryset.order_by('exact_match', '-population', 'state', 'name')

    # Apply limit
    locations = list(queryset[:limit])

    # Convert to response schema
    results = [_location_to_result(loc) for loc in locations]

    return SuggestResponseSchema(results=results)


@router.get("/{location_id}", response=LocationResultSchema, auth=None)
def get_location(request, location_id: int):
    """
    Get a single location by ID.

    Used to restore saved filters with canonical location data.
    """
    location = get_object_or_404(Location, id=location_id)
    return _location_to_result(location)
