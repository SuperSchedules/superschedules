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

# Address suffix normalization map (abbreviated -> full form)
ADDRESS_SUFFIX_MAP = {
    'st': 'street', 'ave': 'avenue', 'rd': 'road', 'blvd': 'boulevard',
    'dr': 'drive', 'ln': 'lane', 'ct': 'court', 'cir': 'circle',
    'pl': 'place', 'ter': 'terrace', 'way': 'way', 'pkwy': 'parkway',
    'hwy': 'highway', 'sq': 'square', 'trl': 'trail',
}

# Directional abbreviation map
DIRECTION_MAP = {
    'n': 'north', 's': 'south', 'e': 'east', 'w': 'west',
    'ne': 'northeast', 'nw': 'northwest', 'se': 'southeast', 'sw': 'southwest',
}

# Room indicators for detecting room-like venue names
ROOM_INDICATORS = {'room', 'hall', 'suite', 'wing', 'floor', 'level', 'space',
                   'makerspace', 'display case', 'gallery', 'auditorium', 'studio'}

# Venue indicators for detecting proper venue names
# Note: "hall" is NOT included because it's ambiguous - "Lecture Hall" is a room, but "City Hall" is a venue
# We detect venues like "City Hall" via "city" keyword, not "hall"
VENUE_INDICATORS = {'library', 'museum', 'center', 'centre', 'school', 'church', 'ymca',
                    'ywca', 'park', 'recreation', 'community', 'city', 'town'}


def normalize_street_address(address: Optional[str]) -> str:
    """
    Normalize a street address for comparison/deduplication.

    Handles:
    - Case normalization (lowercase)
    - Suffix expansion: "St" → "street", "Ave" → "avenue"
    - Direction expansion: "W." → "west", "N" → "north"
    - "St." followed by a name → "saint" (e.g., "St. James" → "saint james")
    - Punctuation removal
    - Whitespace normalization

    Args:
        address: Raw street address string

    Returns:
        Normalized address string for comparison
    """
    if not address:
        return ""

    addr = address.strip()
    if not addr:
        return ""

    # Handle "St." as "Saint" when followed by a capitalized word (before removing periods)
    # e.g., "St. James" → "Saint James", but "Main St." → "Main St"
    addr = re.sub(r'\bSt\.\s+([A-Z])', r'Saint \1', addr)

    # Remove remaining periods (e.g., "W." -> "W", "Ave." -> "Ave")
    addr = addr.replace('.', '')

    # Lowercase for comparison
    addr = addr.lower()

    # Normalize whitespace
    addr = ' '.join(addr.split())

    # Expand directional abbreviations and suffixes
    words = addr.split()
    normalized_words = []
    for i, word in enumerate(words):
        # Check for direction at start of word or standalone
        if word in DIRECTION_MAP:
            normalized_words.append(DIRECTION_MAP[word])
        elif word in ADDRESS_SUFFIX_MAP:
            normalized_words.append(ADDRESS_SUFFIX_MAP[word])
        else:
            normalized_words.append(word)

    return ' '.join(normalized_words)


def _is_room_like_name(name: str) -> bool:
    """
    Detect if a name looks like a room/space rather than a proper venue.

    Args:
        name: Venue name to check

    Returns:
        True if name appears to be a room/space within a venue
    """
    if not name:
        return False

    name_lower = name.lower()

    # First check for venue keywords - these override room indicators
    # e.g., "City Hall" has "hall" (room) but also "city" (venue)
    has_venue_keyword = any(kw in name_lower for kw in VENUE_INDICATORS)
    if has_venue_keyword:
        return False

    # Check for room indicators
    for indicator in ROOM_INDICATORS:
        if indicator in name_lower:
            return True

    # Short names without venue keywords are likely rooms
    words = name_lower.split()
    if len(words) <= 2:
        return True

    return False


def _is_better_venue_name(new_name: str, existing_name: str) -> bool:
    """
    Determine if new_name is a better venue name than existing_name.

    Prefers names with venue keywords (library, museum, center, etc.)
    over room-like names. When equal, prefers longer names.

    Args:
        new_name: Candidate new venue name
        existing_name: Current venue name

    Returns:
        True if new_name should replace existing_name
    """
    if not new_name or new_name == existing_name:
        return False

    new_lower = new_name.lower()
    existing_lower = existing_name.lower()

    # Check if either has venue keywords
    new_has_keywords = any(kw in new_lower for kw in VENUE_INDICATORS)
    existing_has_keywords = any(kw in existing_lower for kw in VENUE_INDICATORS)

    # If existing has venue keywords and new doesn't, keep existing
    if existing_has_keywords and not new_has_keywords:
        return False

    # If new has keywords and existing doesn't, use new
    if new_has_keywords and not existing_has_keywords:
        return True

    # If both have keywords or neither, prefer longer name
    return len(new_name) > len(existing_name)


def find_venue_by_address(street_address: str, city: str, state: str) -> Optional[Venue]:
    """
    Find an existing venue by normalized street address.

    This enables address-based deduplication where different "venue names"
    at the same physical address are recognized as the same venue.

    Args:
        street_address: Street address to search for
        city: City name
        state: State abbreviation or name

    Returns:
        Matching Venue or None if not found
    """
    if not street_address or not city:
        return None

    normalized_addr = normalize_street_address(street_address)
    if not normalized_addr:
        return None

    # Query venues in this city/state that have street addresses
    candidates = Venue.objects.filter(
        city__iexact=city,
        state__iexact=state,
    ).exclude(street_address='').exclude(street_address__isnull=True)

    # Compare normalized addresses
    for venue in candidates:
        if normalize_street_address(venue.street_address) == normalized_addr:
            return venue

    return None


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
    """
    Check if location_data has high confidence and required fields.

    Returns True if either:
    1. extraction_confidence >= 0.7 AND has venue_name OR city
    2. Has both venue_name AND city (explicit data from collector/API)
    """
    has_venue = bool(location_data.get("venue_name"))
    has_city = bool(location_data.get("city"))

    # If we have both venue_name and city, trust the data regardless of confidence
    # This handles direct API calls where confidence isn't explicitly set
    if has_venue and has_city:
        return True

    # Otherwise require explicit high confidence
    confidence = location_data.get("extraction_confidence", 0)
    if confidence >= CONFIDENCE_THRESHOLD and (has_venue or has_city):
        return True

    return False


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
    """Convert collector's location_data to normalized format, including enrichment fields."""
    city = location_data.get("city", "")
    state = _normalize_state(location_data.get("state", ""))
    postal_code = location_data.get("postal_code", "")

    # Clean street_address - remove city/state/zip if accidentally included
    raw_street = location_data.get("street_address", "")
    cleaned_street, extracted_postal = _clean_street_address(raw_street, city, state, postal_code)

    # Use extracted postal code if we didn't have one
    if not postal_code and extracted_postal:
        postal_code = extracted_postal

    result = {
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

    # Include enrichment fields if present (from collector's venue enrichment)
    enrichment_fields = [
        "venue_kind", "venue_kind_confidence", "venue_name_quality",
        "audience_age_groups", "audience_tags", "audience_min_age", "audience_primary",
        "venue_website_url", "venue_description", "venue_kids_summary", "venue_hours_available"
    ]
    for field in enrichment_fields:
        if field in location_data and location_data[field] is not None:
            result[field] = location_data[field]

    return result


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
    Get existing venue or create new one based on deduplication.

    Uses address-based matching first (same address = same venue), then falls back
    to name-based matching. Also applies enrichment fields if provided and
    confidence is higher than existing.

    When reusing an existing venue by address:
    - If incoming venue_name is "room-like", it's stored in normalized['room_name']
    - If incoming venue_name is "better", the venue name is upgraded

    Args:
        normalized: Normalized venue data dict (may include enrichment fields)
        source_domain: Domain where venue was discovered

    Returns:
        Tuple of (Venue instance or None, created boolean)
    """
    from django.db import IntegrityError
    from django.utils import timezone

    if not normalized or not normalized.get("venue_name") or not normalized.get("city"):
        return None, False

    key = build_venue_key(normalized)
    slug, city_lower, state_upper, postal_code = key

    # Truncate values to match model max_length constraints (handle None values)
    slug = (slug or "")[:200]
    postal_code = (postal_code or "")[:20]
    name = (normalized.get("venue_name") or "")[:200]
    street_address = (normalized.get("street_address") or "")[:255]
    city = (normalized.get("city") or "")[:100]
    state = (state_upper or normalized.get("state") or "")[:50]
    country = (normalized.get("country") or "US")[:2]
    source_domain_truncated = (source_domain or "")[:255]

    # STEP 1: Try address-based matching first (different names at same address = same venue)
    if street_address:
        existing_by_address = find_venue_by_address(street_address, city, state)
        if existing_by_address:
            incoming_name = normalized.get('venue_name', '')
            existing_room_name = normalized.get('room_name', '')

            # If incoming name differs from existing venue name, determine what to do
            if incoming_name and incoming_name != existing_by_address.name:
                if _is_room_like_name(incoming_name):
                    # Incoming name is room-like - store it as room_name if not already set
                    if not existing_room_name:
                        normalized['room_name'] = incoming_name
                elif _is_better_venue_name(incoming_name, existing_by_address.name):
                    # Incoming name is better - upgrade the venue name
                    existing_by_address.name = incoming_name
                    existing_by_address.slug = slugify(incoming_name)[:200]
                    existing_by_address.save(update_fields=['name', 'slug'])

            # Update coordinates if venue is missing them but collector provided them
            update_fields = []
            if existing_by_address.latitude is None and normalized.get("latitude"):
                existing_by_address.latitude = _to_decimal(normalized.get("latitude"))
                existing_by_address.longitude = _to_decimal(normalized.get("longitude"))
                update_fields.extend(["latitude", "longitude"])

            # Apply enrichment if provided
            enrichment_updates = _apply_enrichment_fields(existing_by_address, normalized)
            update_fields.extend(enrichment_updates)

            if update_fields:
                existing_by_address.save(update_fields=update_fields)

            return existing_by_address, False

    # STEP 2: Try name-based matching (same slug, city, state, postal_code)
    try:
        venue = Venue.objects.get(
            slug=slug,
            city__iexact=city_lower,
            state__iexact=state_upper,
            postal_code=postal_code
        )
        # Update coordinates if venue is missing them but collector provided them
        update_fields = []
        if venue.latitude is None and normalized.get("latitude"):
            venue.latitude = _to_decimal(normalized.get("latitude"))
            venue.longitude = _to_decimal(normalized.get("longitude"))
            update_fields.extend(["latitude", "longitude"])

        # Apply enrichment if provided
        enrichment_updates = _apply_enrichment_fields(venue, normalized)
        update_fields.extend(enrichment_updates)

        if update_fields:
            venue.save(update_fields=update_fields)

        return venue, False
    except Venue.DoesNotExist:
        pass

    # STEP 3: Create new venue, handling race condition with IntegrityError
    enrichment_kwargs = _get_enrichment_kwargs(normalized)

    try:
        venue = Venue.objects.create(
            name=name,
            slug=slug,
            street_address=street_address,
            city=city,
            state=state,
            postal_code=postal_code,
            country=country,
            latitude=_to_decimal(normalized.get("latitude")),
            longitude=_to_decimal(normalized.get("longitude")),
            source_domain=source_domain_truncated,
            raw_schema=normalized.get("raw_schema"),
            **enrichment_kwargs
        )
        return venue, True
    except IntegrityError:
        # Race condition: another process created the venue, fetch it
        venue = Venue.objects.get(
            slug=slug,
            city__iexact=city_lower,
            state__iexact=state_upper,
            postal_code=postal_code
        )
        return venue, False


def _apply_enrichment_fields(venue: Venue, normalized: dict) -> list[str]:
    """
    Apply enrichment fields to an existing venue if confidence is higher.

    Returns list of field names that were updated.
    """
    from django.utils import timezone

    updated_fields = []
    new_confidence = normalized.get("venue_kind_confidence", 0) or 0
    existing_confidence = venue.venue_kind_confidence or 0

    # Only apply enrichment if collector's confidence is higher or venue has no enrichment
    if new_confidence > existing_confidence or venue.enrichment_status == "none":
        # Classification fields
        if normalized.get("venue_kind"):
            venue.venue_kind = normalized["venue_kind"]
            updated_fields.append("venue_kind")
        if normalized.get("venue_kind_confidence"):
            venue.venue_kind_confidence = normalized["venue_kind_confidence"]
            updated_fields.append("venue_kind_confidence")
        if normalized.get("venue_name_quality"):
            venue.venue_name_quality = normalized["venue_name_quality"]
            updated_fields.append("venue_name_quality")

        # Audience fields
        if normalized.get("audience_age_groups"):
            venue.audience_age_groups = normalized["audience_age_groups"]
            updated_fields.append("audience_age_groups")
        if normalized.get("audience_tags"):
            venue.audience_tags = normalized["audience_tags"]
            updated_fields.append("audience_tags")
        if normalized.get("audience_min_age") is not None:
            venue.audience_min_age = normalized["audience_min_age"]
            updated_fields.append("audience_min_age")
        if normalized.get("audience_primary"):
            venue.audience_primary = normalized["audience_primary"]
            updated_fields.append("audience_primary")

        # Content fields
        if normalized.get("venue_website_url"):
            venue.website_url = normalized["venue_website_url"]
            updated_fields.append("website_url")
        if normalized.get("venue_description"):
            venue.description = normalized["venue_description"]
            updated_fields.append("description")
        if normalized.get("venue_kids_summary"):
            venue.kids_summary = normalized["venue_kids_summary"]
            updated_fields.append("kids_summary")

        # Update enrichment status
        if updated_fields:
            venue.enrichment_status = "partial" if len(updated_fields) < 5 else "complete"
            venue.last_enriched_at = timezone.now()
            updated_fields.extend(["enrichment_status", "last_enriched_at"])

    return updated_fields


def _get_enrichment_kwargs(normalized: dict) -> dict:
    """
    Extract enrichment fields from normalized data for venue creation.
    """
    from django.utils import timezone

    kwargs = {}

    # Classification fields
    if normalized.get("venue_kind"):
        kwargs["venue_kind"] = normalized["venue_kind"]
    if normalized.get("venue_kind_confidence"):
        kwargs["venue_kind_confidence"] = normalized["venue_kind_confidence"]
    if normalized.get("venue_name_quality"):
        kwargs["venue_name_quality"] = normalized["venue_name_quality"]

    # Audience fields
    if normalized.get("audience_age_groups"):
        kwargs["audience_age_groups"] = normalized["audience_age_groups"]
    if normalized.get("audience_tags"):
        kwargs["audience_tags"] = normalized["audience_tags"]
    if normalized.get("audience_min_age") is not None:
        kwargs["audience_min_age"] = normalized["audience_min_age"]
    if normalized.get("audience_primary"):
        kwargs["audience_primary"] = normalized["audience_primary"]

    # Content fields
    if normalized.get("venue_website_url"):
        kwargs["website_url"] = normalized["venue_website_url"]
    if normalized.get("venue_description"):
        kwargs["description"] = normalized["venue_description"]
    if normalized.get("venue_kids_summary"):
        kwargs["kids_summary"] = normalized["venue_kids_summary"]

    # Set enrichment status if any enrichment fields present
    if kwargs:
        kwargs["enrichment_status"] = "partial" if len(kwargs) < 5 else "complete"
        kwargs["last_enriched_at"] = timezone.now()

    return kwargs


def _to_decimal(value) -> Optional[Decimal]:
    """Convert value to Decimal or None."""
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (ValueError, TypeError):
        return None
