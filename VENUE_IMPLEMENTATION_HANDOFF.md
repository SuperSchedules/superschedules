# Venue System Implementation - Handoff Summary

## What Was Done

### New `venues/` Django App
Created a complete venue normalization system:

- **`venues/models.py`** - `Venue` model with structured address fields:
  - `name`, `slug`, `street_address`, `city`, `state`, `postal_code`, `country`
  - `latitude`, `longitude` (geocoding)
  - `source_domain`, `canonical_url`, `raw_schema`
  - Unique constraint on `(slug, city, state, postal_code)` for deduplication

- **`venues/extraction.py`** - Normalization pipeline:
  - `normalize_venue_data(location_data, raw_location, place_json, html)` - Main orchestrator
  - Trusts collector's `location_data` when `extraction_confidence >= 0.7`
  - Falls back to JSON-LD parsing, then HTML regex heuristics
  - `build_venue_key()` - Deterministic deduplication key
  - `get_or_create_venue()` - Upsert logic

- **`venues/admin.py`** - Grappelli admin with event counts

### Updated `events/models.py`
Added new fields to Event model:
```python
venue = ForeignKey('venues.Venue', ...)  # NEW - structured venue
room_name = CharField(...)                # NEW - room within venue
raw_place_json = JSONField(...)           # NEW - original Schema.org for re-parsing
raw_location_data = JSONField(...)        # NEW - full collector location_data
# Kept: place (legacy FK), location (string for display)
```

Updated methods:
- `get_location_string()` - Prefers venue, falls back to place/location
- `get_full_address()` - Uses venue.get_full_address()
- `get_city()` - Returns venue.city
- `get_location_search_text()` - Rich text for RAG (room + venue + address)
- `create_with_schema_org_data()` - Now requires `location_data` from collector

### Collector Contract
Collector must send `location_data` dict (NOT string `location`):
```python
event_data = {
    'location_data': {
        'venue_name': 'Waltham Public Library',
        'room_name': 'Waltham Room',
        'street_address': '735 Main Street',
        'city': 'Waltham',
        'state': 'MA',
        'postal_code': '02451',
        'country': 'US',
        'latitude': 42.3765,
        'longitude': -71.2356,
        'raw_location_string': 'Waltham Room',
        'raw_place_json': {...},  # Original Schema.org
        'extraction_method': 'jsonld',
        'extraction_confidence': 0.95
    },
    # ... other event fields
}
```

### Migrations Created
- `venues/migrations/0001_initial.py` - Venue model
- `events/migrations/0014_event_raw_location_data_event_raw_place_json_and_more.py` - Event fields

### Tests
- 42 venue tests (model + extraction)
- All 115 venue + events tests passing

---

## Next Step: Update Chat LLM / Event API for Frontend

The frontend needs to display the new structured venue data. Check these areas:

### 1. API Response Schemas (`api/views.py`)
Look for event serialization - ensure responses include:
- `venue` object with `name`, `city`, `state`, `street_address`, `postal_code`
- `room_name`
- Or use existing `get_location_string()`, `get_full_address()`, `get_city()` methods

### 2. Chat Service RAG Context (`chat_service/app.py`)
Check how events are formatted for the LLM context. The `get_location_search_text()` method now returns rich venue data - verify it's being used.

### 3. RAG Service (`api/rag_service.py`)
Check `_create_event_text()` and `get_rag_context()` - they should already use `get_location_search_text()` which now includes venue data.

### 4. Frontend Event Display
The React frontend may need updates to display:
- Venue name vs room name separately
- Full structured address
- City/state for filtering

### Key Files to Check
- `api/views.py` - Event endpoints and schemas
- `api/rag_service.py` - RAG context formatting
- `chat_service/app.py` - Chat streaming with event context
- Frontend repo - Event display components

### Test Commands
```bash
source .venv/bin/activate && python manage.py test venues events --settings=config.test_settings --buffer --keepdb
```

---

## Architecture Summary

```
Collector sends location_data
        ↓
Event.create_with_schema_org_data()
        ↓
normalize_venue_data() - trusts high confidence, falls back to parsing
        ↓
get_or_create_venue() - deduplication by (slug, city, state, postal_code)
        ↓
Event created with:
  - venue FK → Venue object
  - room_name
  - raw_place_json (for re-parsing)
  - raw_location_data (full collector data)
  - location string (for display)
```

The legacy `Place` model still exists for old data but new events don't create Place objects.
