# Chat API Documentation

This document describes the enhanced chat API with tiered event retrieval, location ID support, and configurable scoring.

## Overview

The chat API now supports:
- **Tiered event retrieval**: Events are categorized into recommended, additional, and context tiers
- **Multi-factor scoring**: Events are scored based on semantic similarity, location, time, category, and popularity
- **Location ID**: Deterministic location filtering using database IDs instead of string matching
- **Configurable weights**: Adjust how different factors contribute to the final score

## Chat Request

### Endpoint
```
POST /api/v1/chat/stream  (FastAPI, Port 8002)
```

### Request Body

```typescript
interface ChatRequest {
  message: string;                    // User's query
  context?: {
    location?: string;                // Fallback: location string (e.g., "Newton, MA")
    date_range?: {
      from?: string;                  // ISO date string
      to?: string;
    };
    is_virtual?: boolean;
    user_location?: {
      lat: number;
      lng: number;
    };
    max_distance_miles?: number;
  };
  session_id?: number;                // For conversation continuity

  // NEW: Location ID (preferred over context.location)
  location_id?: number;               // Location table ID for deterministic filtering

  // NEW: Tiered retrieval configuration
  use_tiered_retrieval?: boolean;     // Default: true
  max_recommended?: number;           // Default: 10
  max_additional?: number;            // Default: 15
  max_context?: number;               // Default: 50

  // NEW: Custom scoring weights
  scoring_weights?: {
    semantic_similarity?: number;     // Default: 0.40
    location_match?: number;          // Default: 0.25
    time_relevance?: number;          // Default: 0.20
    category_match?: number;          // Default: 0.10
    popularity?: number;              // Default: 0.05
  };

  // Existing fields
  model_a?: string;
  model_b?: string;
  single_model_mode?: boolean;        // Default: true
  preferred_model?: string;
  debug?: boolean;                    // Enable tracing
}
```

### Example Request

```json
{
  "message": "Activities for kids in Newton this weekend",
  "location_id": 12345,
  "max_recommended": 10,
  "max_additional": 20,
  "scoring_weights": {
    "semantic_similarity": 0.3,
    "location_match": 0.4,
    "time_relevance": 0.2,
    "category_match": 0.05,
    "popularity": 0.05
  }
}
```

## Chat Response (SSE Stream)

The response is a Server-Sent Events (SSE) stream. Each chunk is a JSON object:

### Token Chunks (during streaming)

```typescript
interface StreamChunk {
  model: string;         // 'A', 'B', or 'SYSTEM'
  token: string;         // Individual token/text chunk
  done: boolean;         // true for final chunk
  error?: string;
  error_code?: string;   // NEW: Machine-readable error code
}
```

### Final Chunk (done=true)

```typescript
interface FinalChunk {
  model: 'SYSTEM';
  token: '';
  done: true;
  session_id: number;

  // Legacy (backward compatible)
  suggested_event_ids: number[];      // Top 5 events

  // NEW: Enhanced tiered response
  recommended_event_ids: number[];    // All recommended tier events
  all_event_ids: number[];            // All events across all tiers
  event_metadata: {
    [event_id: number]: {
      tier: 'recommended' | 'additional' | 'context';
      final_score: number;
      ranking_factors: {
        semantic_similarity: number;
        location_match: number;
        time_relevance: number;
        category_match: number;
        popularity: number;
        distance_miles?: number;
        days_until_event?: number;
      };
    };
  };

  debug_run_id?: string;              // If debug=true was set
}
```

### Example Final Response

```json
{
  "model": "SYSTEM",
  "token": "",
  "done": true,
  "session_id": 42,
  "suggested_event_ids": [101, 102, 103, 104, 105],
  "recommended_event_ids": [101, 102, 103, 104, 105, 106, 107, 108, 109, 110],
  "all_event_ids": [101, 102, 103, 104, 105, 106, 107, 108, 109, 110, 111, 112, 113],
  "event_metadata": {
    "101": {
      "tier": "recommended",
      "final_score": 0.823,
      "ranking_factors": {
        "semantic_similarity": 0.85,
        "location_match": 0.92,
        "time_relevance": 0.75,
        "category_match": 0.6,
        "popularity": 0.5,
        "distance_miles": 1.2,
        "days_until_event": 2.0
      }
    }
  }
}
```

## Location API

### Location Suggest Endpoint

Use this for location autocomplete in the frontend.

```
GET /api/v1/locations/suggest?q=Newton&limit=10
```

**Response:**
```json
{
  "locations": [
    {
      "id": 12345,
      "name": "Newton",
      "state": "MA",
      "display_name": "Newton, MA",
      "latitude": 42.337807,
      "longitude": -71.209182,
      "population": 88923
    }
  ]
}
```

### Frontend Flow

1. User types location in autocomplete
2. Frontend calls `/api/v1/locations/suggest?q=<query>`
3. User selects a location, frontend stores `location_id`
4. Chat requests include `location_id` for deterministic filtering

## Scoring Weights

The scoring weights control how different factors contribute to the final score:

| Factor | Default | Description |
|--------|---------|-------------|
| `semantic_similarity` | 0.40 | How well the event matches the query semantically |
| `location_match` | 0.25 | Inverse of distance (closer = higher score) |
| `time_relevance` | 0.20 | Sooner events score higher |
| `category_match` | 0.10 | Tag/audience overlap with query keywords |
| `popularity` | 0.05 | Source quality signals |

**Weights should sum to 1.0** for predictable scoring.

### Scoring Formulas

**Location Match:**
```
location_score = 1.0 / (1.0 + distance_miles / 5.0)
```
- At 0 miles: 1.0
- At 5 miles: ~0.5
- At 20 miles: ~0.2

**Time Relevance:**
```
time_score = 1.0 / (1.0 + days_until_event / 7.0)
```
- Today: 1.0
- In 3 days: ~0.7
- In 7 days: ~0.5
- In 30 days: ~0.2

**Category Match:**
```
category_score = min(1.0, overlap_count / 3.0)
```
Where `overlap_count` is the number of query words matching event tags.

## Debug Runner

The debug runner at `/admin/traces/chatdebugrun/debug-runner/` now includes:

### Scoring Weight Controls

A collapsible "Scoring Weights (Advanced)" section with sliders for each weight factor. The total is displayed and highlighted green when weights sum to 1.0.

### Tier Configuration

- **Max Recommended**: Number of events in the recommended tier (5, 10, 15)
- **Max Additional**: Number of events in the additional tier (10, 15, 25)
- **Max Context**: Number of events in the context tier (25, 50, 100)

### Enhanced Retrieval Table

The retrieval tab now shows:
- **Tier badge**: Color-coded (green=Recommended, blue=Additional, gray=Context)
- **Final Score**: Combined multi-factor score
- **Factors**: Compact display of S(emantic), L(ocation), T(ime) scores
- **Admin link**: Quick link to edit the event in Django admin

## Migration Notes

### For Frontend Developers

1. **Location handling**: Switch from `context.location` string to `location_id` integer
2. **Event display**: Use `recommended_event_ids` for the main list, `all_event_ids` for the map
3. **Scoring visibility**: `event_metadata` provides transparency into ranking decisions

### Backward Compatibility

- `suggested_event_ids` is still populated (first 5 recommended events)
- `context.location` string is still supported as fallback
- `use_tiered_retrieval: false` uses legacy retrieval

## Error Codes

The `error_code` field provides machine-readable error types:

| Code | Meaning | Client Action |
|------|---------|---------------|
| `token_expired` | JWT has expired | Refresh token, reconnect |
| `token_invalid` | JWT is malformed | Re-authenticate |
| `rate_limited` | Too many requests | Wait and retry |
| `llm_unavailable` | LLM service down | Show error, retry later |
| `rag_error` | RAG retrieval failed | Show error, retry |
| `server_error` | Internal error | Show error, retry |

## Future: Elasticsearch Integration

The RAG service is prepared for Elasticsearch integration:

```python
# RAG can accept candidate IDs from external search
result = rag.get_context_events_tiered(
    user_message="kids activities",
    candidate_ids=[101, 102, 103, ...],  # From ES search
    # ... other params
)
```

See `ISSUES_BACKLOG.md` for the full Elasticsearch integration plan.
