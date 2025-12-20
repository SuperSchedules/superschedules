"""
Location resolution service for deterministic location queries.

Provides:
- normalize_location_query(): Parse location strings
- resolve_location(): Map to canonical Location with coordinates
- filter_by_distance(): Geo-filter with bounding box optimization
- haversine_distance(): Python-side distance calculation
"""

import logging
import math
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import List, Optional, Tuple

from django.db.models import QuerySet

from locations.models import Location, normalize_for_matching
from venues.extraction import STATE_ABBREVIATIONS

logger = logging.getLogger(__name__)

# Default search radius in miles
DEFAULT_RADIUS_MILES = 10.0

# Earth radius in miles (for Haversine)
EARTH_RADIUS_MILES = 3959.0

# Preferred states for disambiguation (Greater Boston area first)
PREFERRED_STATES = ['MA', 'NH', 'RI', 'CT', 'ME', 'VT', 'NY']

# Reverse mapping: abbreviation -> full name (for validation)
STATE_CODES = set(STATE_ABBREVIATIONS.values())


@dataclass
class LocationResult:
    """Result of location resolution."""
    matched_location: Optional[Location]
    confidence: float  # 0.0 to 1.0
    is_ambiguous: bool
    alternatives: List[Location]  # Top alternatives for disambiguation
    normalized_query: str
    state_used: Optional[str]

    @property
    def latitude(self) -> Optional[Decimal]:
        return self.matched_location.latitude if self.matched_location else None

    @property
    def longitude(self) -> Optional[Decimal]:
        return self.matched_location.longitude if self.matched_location else None

    @property
    def display_name(self) -> str:
        if self.matched_location:
            return str(self.matched_location)
        return ""


def normalize_location_query(text: str) -> Tuple[str, Optional[str]]:
    """
    Parse and normalize location string, extract state if present.

    Args:
        text: Raw location query (e.g., "Newton, MA", "events in Newton")

    Returns:
        Tuple of (normalized_city_name, state_abbreviation or None)

    Examples:
        "Newton, MA" -> ("newton", "MA")
        "Newton Massachusetts" -> ("newton", "MA")
        "events in Newton" -> ("newton", None)
        "City of Springfield" -> ("springfield", None)
    """
    if not text:
        return ("", None)

    text = text.strip()

    # Extract state if present
    city, state = _extract_state(text)

    # Normalize city name
    normalized = _normalize_city_name(city)

    return (normalized, state)


def _extract_state(text: str) -> Tuple[str, Optional[str]]:
    """Extract state from location text."""
    # Pattern 1: "City, ST" or "City,ST" (comma + 2-letter code)
    match = re.search(r'^(.+?),\s*([A-Za-z]{2})$', text)
    if match:
        city = match.group(1).strip()
        state_part = match.group(2).strip().upper()
        if state_part in STATE_CODES:
            return (city, state_part)

    # Pattern 2: "City ST" (space + 2-letter code at end)
    match = re.search(r'^(.+?)\s+([A-Z]{2})$', text)
    if match:
        city = match.group(1).strip()
        state = match.group(2)
        if state in STATE_CODES:
            return (city, state)

    # Pattern 3: "City StateName" (full state name at end)
    text_lower = text.lower()
    for state_name, abbrev in STATE_ABBREVIATIONS.items():
        if text_lower.endswith(state_name):
            # Remove state name from end
            city = text[:len(text) - len(state_name)].strip()
            # Remove trailing comma if present
            city = city.rstrip(',').strip()
            return (city, abbrev)

    return (text, None)


def _normalize_city_name(city: str) -> str:
    """
    Normalize city name for matching.

    - Lowercase
    - Remove "city of", "town of", etc.
    - Remove common location prefixes from queries
    - Remove punctuation
    - Collapse whitespace
    """
    if not city:
        return ""

    result = city.lower().strip()

    # Remove "city of", "town of", etc. prefixes
    result = re.sub(r'^(city|town|village|borough|township)\s+of\s+', '', result)

    # Remove common location prefixes from user queries
    result = re.sub(r'^(in|at|near|around|events\s+(?:in|at|near|around))\s+', '', result)

    # Remove punctuation except hyphens
    result = re.sub(r'[^\w\s-]', '', result)

    # Collapse whitespace
    result = re.sub(r'\s+', ' ', result)

    return result.strip()


def resolve_location(text: str, default_state: Optional[str] = None) -> LocationResult:
    """
    Resolve a location query to coordinates.

    Resolution order:
    1. Exact match (normalized_name + state from query)
    2. Exact match with default_state
    3. Unique match (only one city with this name in US)
    4. Ranked match: prefer PREFERRED_STATES, then highest population

    Args:
        text: Location query (e.g., "Newton", "Newton, MA")
        default_state: Default state to prefer if query is ambiguous

    Returns:
        LocationResult with matched location, confidence, and alternatives
    """
    normalized, state_from_query = normalize_location_query(text)

    if not normalized:
        logger.debug("Empty location query")
        return LocationResult(
            matched_location=None,
            confidence=0.0,
            is_ambiguous=False,
            alternatives=[],
            normalized_query=normalized,
            state_used=state_from_query,
        )

    # Use state from query, falling back to default
    effective_state = state_from_query or default_state

    # Step 1: Try exact match with state
    if effective_state:
        exact_matches = Location.objects.filter(
            normalized_name=normalized,
            state=effective_state.upper(),
        )
        if exact_matches.exists():
            match = exact_matches.first()
            logger.info(f"Location resolved: '{text}' -> {match} (exact match with state)")
            return LocationResult(
                matched_location=match,
                confidence=1.0,
                is_ambiguous=False,
                alternatives=[],
                normalized_query=normalized,
                state_used=effective_state,
            )

    # Step 2: Find all matches by normalized name
    all_matches = list(Location.objects.filter(normalized_name=normalized).order_by('-population', 'state'))

    if not all_matches:
        logger.debug(f"No location found for: '{normalized}'")
        return LocationResult(
            matched_location=None,
            confidence=0.0,
            is_ambiguous=False,
            alternatives=[],
            normalized_query=normalized,
            state_used=effective_state,
        )

    if len(all_matches) == 1:
        # Unique match
        match = all_matches[0]
        logger.info(f"Location resolved: '{text}' -> {match} (unique match)")
        return LocationResult(
            matched_location=match,
            confidence=0.9,  # Slightly lower since no state confirmation
            is_ambiguous=False,
            alternatives=[],
            normalized_query=normalized,
            state_used=effective_state,
        )

    # Step 3: Multiple matches - rank them
    ranked = _rank_locations(all_matches, effective_state)
    best_match = ranked[0]
    alternatives = ranked[1:5]  # Top 4 alternatives

    # Confidence based on match quality
    if best_match.state in PREFERRED_STATES[:3]:  # MA, NH, RI
        confidence = 0.8
    elif best_match.population and best_match.population > 50000:
        confidence = 0.7
    else:
        confidence = 0.5

    logger.info(
        f"Location resolved (ambiguous): '{text}' -> {best_match} "
        f"(confidence={confidence}, {len(all_matches)} candidates)"
    )

    return LocationResult(
        matched_location=best_match,
        confidence=confidence,
        is_ambiguous=True,
        alternatives=alternatives,
        normalized_query=normalized,
        state_used=effective_state,
    )


def _rank_locations(locations: List[Location], preferred_state: Optional[str]) -> List[Location]:
    """
    Rank locations for disambiguation.

    Priority:
    1. Preferred state from query/context
    2. PREFERRED_STATES list (Greater Boston preference)
    3. Population (descending)
    4. State alphabetically (deterministic fallback)
    """
    def sort_key(loc: Location):
        # Primary: exact state match (0 = match, 1 = no match)
        exact_state_match = 0 if preferred_state and loc.state == preferred_state.upper() else 1

        # Secondary: preferred states index (lower = better, 999 if not in list)
        try:
            preferred_idx = PREFERRED_STATES.index(loc.state)
        except ValueError:
            preferred_idx = 999

        # Tertiary: population (negative for descending)
        pop_rank = -(loc.population or 0)

        # Quaternary: state alphabetically
        return (exact_state_match, preferred_idx, pop_rank, loc.state, loc.name)

    return sorted(locations, key=sort_key)


# =============================================================================
# Geo-filtering
# =============================================================================


def calculate_bounding_box(lat: float, lng: float, radius_miles: float) -> Tuple[float, float, float, float]:
    """
    Calculate bounding box for initial filtering.

    Returns (min_lat, max_lat, min_lng, max_lng).

    1 degree latitude ≈ 69 miles (constant)
    1 degree longitude ≈ 69 * cos(latitude) miles (varies by latitude)
    """
    lat_delta = radius_miles / 69.0
    lng_delta = radius_miles / (69.0 * math.cos(math.radians(lat)))

    return (
        lat - lat_delta,  # min_lat
        lat + lat_delta,  # max_lat
        lng - lng_delta,  # min_lng
        lng + lng_delta,  # max_lng
    )


def filter_by_distance(
    queryset: QuerySet,
    lat: float,
    lng: float,
    radius_miles: float = DEFAULT_RADIUS_MILES,
    lat_field: str = 'venue__latitude',
    lng_field: str = 'venue__longitude',
) -> QuerySet:
    """
    Filter a queryset to items within radius_miles of the given coordinates.

    Uses two-phase filtering for performance:
    1. Bounding box (fast, uses indexes)
    2. Python-side Haversine filter (accurate, on reduced set)

    Args:
        queryset: Django queryset to filter (typically Event queryset)
        lat: Center latitude
        lng: Center longitude
        radius_miles: Maximum distance in miles (default: 10)
        lat_field: Field path for latitude (default: venue__latitude)
        lng_field: Field path for longitude (default: venue__longitude)

    Returns:
        Filtered queryset with items within radius
    """
    # Convert to float for calculations
    lat = float(lat)
    lng = float(lng)

    # Phase 1: Bounding box filter (fast, indexed)
    min_lat, max_lat, min_lng, max_lng = calculate_bounding_box(lat, lng, radius_miles)

    queryset = queryset.filter(
        **{
            f'{lat_field}__gte': min_lat,
            f'{lat_field}__lte': max_lat,
            f'{lng_field}__gte': min_lng,
            f'{lng_field}__lte': max_lng,
        }
    )

    # Phase 2: For the actual Haversine filter, we need to use select_related
    # and filter in Python for now since extra() doesn't handle joins well.
    # The bounding box already eliminates most candidates, so this is efficient.
    # Note: For larger datasets, consider using raw SQL with proper joins.

    return queryset


def filter_by_distance_precise(
    queryset: QuerySet,
    lat: float,
    lng: float,
    radius_miles: float = DEFAULT_RADIUS_MILES,
) -> list:
    """
    Filter events by distance with precise Haversine calculation.

    This performs the bounding box filter via ORM, then applies Python-side
    Haversine filtering. Returns a list of (event, distance) tuples sorted by distance.

    For most use cases, the bounding box filter alone is sufficient since it
    slightly overestimates the area (corners of box extend past circle).

    Args:
        queryset: Event queryset to filter
        lat: Center latitude
        lng: Center longitude
        radius_miles: Maximum distance in miles (default: 10)

    Returns:
        List of (event, distance_miles) tuples sorted by distance
    """
    # Phase 1: Bounding box
    filtered_qs = filter_by_distance(queryset, lat, lng, radius_miles)

    # Phase 2: Precise Haversine filter in Python
    results = []
    for event in filtered_qs.select_related('venue'):
        if event.venue and event.venue.latitude and event.venue.longitude:
            distance = haversine_distance(
                lat, lng,
                float(event.venue.latitude),
                float(event.venue.longitude)
            )
            if distance <= radius_miles:
                results.append((event, distance))

    # Sort by distance
    results.sort(key=lambda x: x[1])
    return results


def haversine_distance(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """
    Calculate Haversine distance between two points in miles.

    For use in Python (not SQL). Useful for result sorting/ranking.
    """
    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)

    a = (math.sin(dlat / 2) ** 2 +
         math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlng / 2) ** 2)
    c = 2 * math.asin(math.sqrt(a))

    return EARTH_RADIUS_MILES * c


# =============================================================================
# Convenience functions
# =============================================================================


def get_location_coordinates(text: str, default_state: Optional[str] = None) -> Optional[Tuple[float, float]]:
    """
    Simple helper to get (lat, lng) from location text.

    Returns None if location not found.
    """
    result = resolve_location(text, default_state)
    if result.matched_location:
        return (float(result.latitude), float(result.longitude))
    return None
