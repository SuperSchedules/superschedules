"""
Venue extraction and normalization pipeline.

Handles extraction from:
- Collector's pre-normalized location_data (primary, when confidence >= 0.7)
- Schema.org JSON-LD Place objects (fallback)
- Raw HTML/text parsing (fallback)
"""

import re
from typing import Optional
from decimal import Decimal

from django.utils.text import slugify

from venues.models import Venue


# Confidence threshold for trusting collector's location_data
CONFIDENCE_THRESHOLD = 0.7

# US state abbreviations and full names
STATE_ABBREVIATIONS = {
    'alabama': 'AL', 'alaska': 'AK', 'arizona': 'AZ', 'arkansas': 'AR', 'california': 'CA',
    'colorado': 'CO', 'connecticut': 'CT', 'delaware': 'DE', 'florida': 'FL', 'georgia': 'GA',
    'hawaii': 'HI', 'idaho': 'ID', 'illinois': 'IL', 'indiana': 'IN', 'iowa': 'IA',
    'kansas': 'KS', 'kentucky': 'KY', 'louisiana': 'LA', 'maine': 'ME', 'maryland': 'MD',
    'massachusetts': 'MA', 'michigan': 'MI', 'minnesota': 'MN', 'mississippi': 'MS', 'missouri': 'MO',
    'montana': 'MT', 'nebraska': 'NE', 'nevada': 'NV', 'new hampshire': 'NH', 'new jersey': 'NJ',
    'new mexico': 'NM', 'new york': 'NY', 'north carolina': 'NC', 'north dakota': 'ND', 'ohio': 'OH',
    'oklahoma': 'OK', 'oregon': 'OR', 'pennsylvania': 'PA', 'rhode island': 'RI', 'south carolina': 'SC',
    'south dakota': 'SD', 'tennessee': 'TN', 'texas': 'TX', 'utah': 'UT', 'vermont': 'VT',
    'virginia': 'VA', 'washington': 'WA', 'west virginia': 'WV', 'wisconsin': 'WI', 'wyoming': 'WY',
    'district of columbia': 'DC'
}

# Venue keywords for identification
VENUE_KEYWORDS = [
    'library', 'museum', 'center', 'centre', 'hall', 'school', 'church',
    'community', 'theater', 'theatre', 'arena', 'stadium', 'park', 'garden',
    'gallery', 'institute', 'university', 'college', 'academy', 'ymca', 'ywca'
]

# Room indicators - order matters, more specific patterns first
ROOM_PATTERNS = [
    r"Main\s+Conference\s+Room",
    r"Conference\s+Room\s*[A-Z0-9]*",
    r"Meeting\s+Room\s*[A-Z0-9]*",
    r"[\w']+(?:\s+[\w']+)?\s+Room\b",  # "Waltham Room", "Children's Room"
    r"Room\s+[A-Z0-9]+",
    r"Main\s+Hall\b",
    r"Hall\s+[A-Z0-9]+",
]

# Street suffix patterns
STREET_SUFFIXES = r'(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Drive|Dr|Lane|Ln|Way|Court|Ct|Circle|Cir|Place|Pl|Square|Sq|Highway|Hwy|Parkway|Pkwy|Trail|Trl)'


def normalize_venue_data(
    location_data: Optional[dict] = None,
    raw_location: Optional[str] = None,
    place_json: Optional[dict] = None,
    html: Optional[str] = None
) -> dict:
    """
    Main orchestrator for venue normalization.

    Priority order:
    1. If location_data exists with confidence >= 0.7 and required fields, use it
    2. Parse place_json (Schema.org JSON-LD)
    3. Parse raw_location or html using heuristics
    4. Merge results with higher priority sources taking precedence

    Args:
        location_data: Pre-normalized data from collector with extraction_confidence
        raw_location: Raw location string (e.g., "Waltham Room")
        place_json: Schema.org Place JSON-LD object
        html: Raw HTML or text to parse

    Returns:
        Normalized dict with venue_name, room_name, street_address, city, state, postal_code, etc.
    """
    result = {
        "venue_name": "",
        "room_name": "",
        "street_address": "",
        "city": "",
        "state": "",
        "postal_code": "",
        "country": "US",
        "latitude": None,
        "longitude": None,
    }

    # Step 1: Check collector's location_data
    if location_data and _is_high_confidence(location_data):
        return _normalize_from_location_data(location_data)

    # Step 2: Try JSON-LD extraction
    if place_json:
        jsonld_result = extract_from_jsonld(place_json)
        _merge_results(result, jsonld_result)

    # Step 3: Try HTML/text parsing if still incomplete
    if not _has_required_fields(result):
        text_to_parse = html or raw_location
        if text_to_parse:
            html_result = extract_from_html(text_to_parse)
            _merge_results(result, html_result, overwrite=False)

    return result


def _is_high_confidence(location_data: dict) -> bool:
    """Check if location_data has high confidence and required fields."""
    confidence = location_data.get("extraction_confidence", 0)
    if confidence < CONFIDENCE_THRESHOLD:
        return False

    # Must have at least venue_name or city
    has_venue = bool(location_data.get("venue_name"))
    has_city = bool(location_data.get("city"))

    return has_venue or has_city


def _clean_street_address(street_address: str, city: str, state: str, postal_code: str) -> tuple[str, str]:
    """
    Clean street_address that may contain full address with city/state/zip/country.

    Returns:
        Tuple of (cleaned_street_address, extracted_postal_code)
        extracted_postal_code is returned if postal_code was empty but found in street_address
    """
    if not street_address:
        return "", postal_code

    cleaned = street_address.strip()

    # Remove country suffixes
    for country in [", United States", ", USA", ", US", " United States", " USA"]:
        if cleaned.endswith(country):
            cleaned = cleaned[:-len(country)].strip().rstrip(",")

    # Try to extract postal code if we don't have one
    extracted_postal = postal_code
    if not postal_code:
        # Match 5-digit ZIP, optionally with +4
        zip_match = re.search(r'\b(\d{5})(?:-\d{4})?\b', cleaned)
        if zip_match:
            extracted_postal = zip_match.group(1)

    # Remove ZIP+4 or ZIP from end (e.g., "02019-3001" or "02019")
    cleaned = re.sub(r',?\s*\d{5}(?:-\d{4})?$', '', cleaned).strip().rstrip(",")

    # Remove state abbreviation from end if it matches
    if state:
        state_pattern = rf',?\s*{re.escape(state)}$'
        cleaned = re.sub(state_pattern, '', cleaned, flags=re.IGNORECASE).strip().rstrip(",")

    # Remove city from end if it matches
    if city:
        city_pattern = rf',?\s*{re.escape(city)}$'
        cleaned = re.sub(city_pattern, '', cleaned, flags=re.IGNORECASE).strip().rstrip(",")

    return cleaned, extracted_postal


def _normalize_from_location_data(location_data: dict) -> dict:
    """Convert collector's location_data to normalized format."""
    city = location_data.get("city", "")
    state = _normalize_state(location_data.get("state", ""))
    postal_code = location_data.get("postal_code", "")

    # Clean street_address - remove city/state/zip if accidentally included
    raw_street = location_data.get("street_address", "")
    cleaned_street, extracted_postal = _clean_street_address(raw_street, city, state, postal_code)

    # Use extracted postal code if we didn't have one
    if not postal_code and extracted_postal:
        postal_code = extracted_postal

    return {
        "venue_name": location_data.get("venue_name", ""),
        "room_name": location_data.get("room_name", ""),
        "street_address": cleaned_street,
        "city": city,
        "state": state,
        "postal_code": postal_code,
        "country": location_data.get("country", "US"),
        "latitude": location_data.get("latitude"),
        "longitude": location_data.get("longitude"),
    }


def _has_required_fields(result: dict) -> bool:
    """Check if result has minimum required fields."""
    return bool(result.get("venue_name") and result.get("city"))


def _merge_results(base: dict, new: dict, overwrite: bool = True) -> None:
    """Merge new results into base dict."""
    for key, value in new.items():
        if value:  # Only merge non-empty values
            if overwrite or not base.get(key):
                base[key] = value


def extract_from_jsonld(json_ld: Optional[dict]) -> dict:
    """
    Extract venue data from Schema.org JSON-LD Place object.

    Handles:
    - Full PostalAddress objects
    - Simple string addresses
    - GeoCoordinates
    - Array of Place objects (takes first)

    Args:
        json_ld: Schema.org Place JSON-LD object or list

    Returns:
        Dict with extracted venue fields
    """
    if not json_ld:
        return {}

    # Handle array input
    if isinstance(json_ld, list):
        if not json_ld:
            return {}
        json_ld = json_ld[0]

    if not isinstance(json_ld, dict):
        return {}

    result = {
        "venue_name": json_ld.get("name", ""),
        "street_address": "",
        "city": "",
        "state": "",
        "postal_code": "",
        "country": "",
        "latitude": None,
        "longitude": None,
    }

    # Extract address
    address = json_ld.get("address", "")
    if isinstance(address, dict):
        # PostalAddress object
        result["street_address"] = address.get("streetAddress", "")
        result["city"] = address.get("addressLocality", "")
        result["state"] = _normalize_state(address.get("addressRegion", ""))
        result["postal_code"] = address.get("postalCode", "")
        result["country"] = address.get("addressCountry", "") or "US"
    elif isinstance(address, str):
        # Parse string address
        parsed = _parse_address_string(address)
        _merge_results(result, parsed, overwrite=False)

    # Extract geo coordinates
    geo = json_ld.get("geo", {})
    if isinstance(geo, dict):
        result["latitude"] = geo.get("latitude")
        result["longitude"] = geo.get("longitude")

    # Also check top-level lat/lon
    if not result["latitude"]:
        result["latitude"] = json_ld.get("latitude")
    if not result["longitude"]:
        result["longitude"] = json_ld.get("longitude")

    return result


def extract_from_html(text: Optional[str]) -> dict:
    """
    Extract venue data from raw HTML or text using regex heuristics.

    Detects:
    - Street addresses (number + street name + suffix)
    - City, State ZIP patterns
    - Venue names (keywords like Library, Museum, Center)
    - Room names (patterns like "X Room", "Room X", "Hall X")

    Args:
        text: Raw HTML or text to parse

    Returns:
        Dict with extracted venue fields
    """
    if not text:
        return {}

    result = {
        "venue_name": "",
        "room_name": "",
        "street_address": "",
        "city": "",
        "state": "",
        "postal_code": "",
    }

    # Extract room name first (often at beginning)
    room_name = _extract_room_name(text)
    if room_name:
        result["room_name"] = room_name
        # Remove room from text for further parsing
        text = text.replace(room_name, "", 1).strip()

    # Extract street address
    street_match = re.search(
        rf'\b(\d+\s+[\w\s]+{STREET_SUFFIXES}(?:\s+(?:Suite|Ste|Unit|Apt|#)\s*\w+)?)\b',
        text,
        re.IGNORECASE
    )
    if street_match:
        result["street_address"] = street_match.group(1).strip()

    # Extract city, state, ZIP - look for pattern: City, ST 12345
    # Try strict comma pattern first: City, ST 12345
    city_state_zip = re.search(
        r',\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?),\s*([A-Z]{2}|[A-Za-z]+)\s*(\d{5}(?:-\d{4})?)?',
        text
    )
    if not city_state_zip:
        # Try looser pattern: in/at City, ST or just City, ST
        city_state_zip = re.search(
            r'(?:in|at)?\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?),\s*([A-Z]{2})\s*(\d{5}(?:-\d{4})?)?',
            text
        )
    if city_state_zip:
        result["city"] = city_state_zip.group(1).strip()
        result["state"] = _normalize_state(city_state_zip.group(2).strip())
        if city_state_zip.group(3):
            result["postal_code"] = city_state_zip.group(3)

    # Extract venue name (look for venue keywords)
    venue_name = _extract_venue_name(text)
    if venue_name:
        result["venue_name"] = venue_name

    return result


def _extract_room_name(text: str) -> str:
    """Extract room name from text."""
    for pattern in ROOM_PATTERNS:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(0).strip()
    return ""


def _extract_venue_name(text: str) -> str:
    """Extract venue name based on keywords."""
    # Common words to exclude from venue names
    stop_words = {'at', 'the', 'in', 'on', 'a', 'an', 'to', 'for', 'of', 'and', 'or', 'event', 'concert', 'meeting', 'class', 'service', 'exhibition'}

    # Look for phrases containing venue keywords
    for keyword in VENUE_KEYWORDS:
        # Match: (optional capitalized word(s) + space) + keyword (case insensitive) + (space + optional capitalized word(s))
        # Using word boundary and looking for proper nouns before/after
        pattern = rf'(?:^|[\s,])((?:[A-Z][a-z]+\s+)*(?:{keyword})(?:\s+[A-Z][a-z]+)*)(?:[\s,.]|$)'
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            venue = match.group(1).strip()
            # Filter out stop words from beginning
            words = venue.split()
            while words and words[0].lower() in stop_words:
                words.pop(0)
            if words:
                return _title_case(" ".join(words))

    return ""


def _title_case(text: str) -> str:
    """Convert text to title case, handling special cases."""
    words = text.split()
    result = []
    for word in words:
        if word.upper() in ['YMCA', 'YWCA']:
            result.append(word.upper())
        else:
            result.append(word.capitalize())
    return " ".join(result)


def _parse_address_string(address: str) -> dict:
    """Parse a single string address into components."""
    result = {}

    # Try to extract city, state, ZIP from end
    city_state_zip = re.search(
        r',?\s*([A-Za-z][A-Za-z\s]+?),\s*([A-Z]{2})\s*(\d{5}(?:-\d{4})?)?$',
        address
    )
    if city_state_zip:
        result["city"] = city_state_zip.group(1).strip()
        result["state"] = city_state_zip.group(2)
        if city_state_zip.group(3):
            result["postal_code"] = city_state_zip.group(3)

    return result


def _normalize_state(state: str) -> str:
    """Normalize state to 2-letter abbreviation."""
    if not state:
        return ""

    state = state.strip()

    # Already 2 letters
    if len(state) == 2:
        return state.upper()

    # Look up full name
    state_lower = state.lower()
    if state_lower in STATE_ABBREVIATIONS:
        return STATE_ABBREVIATIONS[state_lower]

    return state.upper()


def build_venue_key(normalized: dict) -> tuple:
    """
    Build deterministic venue deduplication key.

    Key format: (slug, city_lower, state_upper, postal_code)

    Args:
        normalized: Normalized venue data dict

    Returns:
        Tuple of (slug, city, state, postal_code) for deduplication
    """
    venue_name = normalized.get("venue_name", "")
    city = normalized.get("city", "")
    state = normalized.get("state", "")
    postal_code = normalized.get("postal_code", "")

    return (
        slugify(venue_name),
        city.lower(),
        state.upper(),
        postal_code
    )


def get_or_create_venue(normalized: dict, source_domain: str) -> tuple[Optional[Venue], bool]:
    """
    Get existing venue or create new one based on deduplication key.

    Args:
        normalized: Normalized venue data dict
        source_domain: Domain where venue was discovered

    Returns:
        Tuple of (Venue instance or None, created boolean)
    """
    if not normalized or not normalized.get("venue_name") or not normalized.get("city"):
        return None, False

    key = build_venue_key(normalized)
    slug, city_lower, state_upper, postal_code = key

    # Use update_or_create to handle race conditions atomically
    venue, created = Venue.objects.update_or_create(
        slug=slug,
        city__iexact=city_lower,
        state__iexact=state_upper,
        postal_code=postal_code,
        defaults={
            "name": normalized.get("venue_name", ""),
            "street_address": normalized.get("street_address", ""),
            "city": normalized.get("city", ""),
            "state": state_upper or normalized.get("state", ""),
            "country": normalized.get("country") or "US",
            "source_domain": source_domain,
            "raw_schema": normalized.get("raw_schema"),
        }
    )

    # Update coordinates if venue is missing them but collector provided them
    if venue.latitude is None and normalized.get("latitude"):
        venue.latitude = _to_decimal(normalized.get("latitude"))
        venue.longitude = _to_decimal(normalized.get("longitude"))
        venue.save(update_fields=["latitude", "longitude"])

    return venue, created


def _to_decimal(value) -> Optional[Decimal]:
    """Convert value to Decimal or None."""
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (ValueError, TypeError):
        return None
