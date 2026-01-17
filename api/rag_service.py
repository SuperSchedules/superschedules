"""
RAG (Retrieval-Augmented Generation) service for semantic event search.
Uses sentence transformers for embeddings and cosine similarity for retrieval.

Supports:
- Tiered event retrieval (recommended vs additional)
- Configurable scoring weights for ranking factors
- Location ID-based filtering (deterministic, no string matching)
- Multi-factor ranking with transparent scoring breakdown
"""

import json
import logging
import html
import math
from dataclasses import dataclass, field
from typing import List, Dict, Any, Tuple, Optional, TYPE_CHECKING
from datetime import datetime, timedelta

import numpy as np
from django.conf import settings
from django.utils import timezone
from django.db.models import Q
from pgvector.django import CosineDistance

from events.models import Event
from api.embedding_client import get_embedding_client, EmbeddingClient

if TYPE_CHECKING:
    from traces.recorder import TraceRecorder

logger = logging.getLogger(__name__)


# =============================================================================
# Scoring Configuration
# =============================================================================

@dataclass
class ScoringWeights:
    """
    Configurable weights for multi-factor event scoring.

    All weights are 0-1 and get combined into a weighted final score.
    Default weights emphasize semantic match but give meaningful boost to
    location and time factors.
    """
    semantic_similarity: float = 0.4   # Weight for embedding similarity (default dominant factor)
    location_match: float = 0.25       # Weight for distance-based location score
    time_relevance: float = 0.20       # Weight for how soon the event is
    category_match: float = 0.10       # Weight for tag/audience alignment
    popularity: float = 0.05           # Weight for source quality/popularity

    def __post_init__(self):
        """Validate weights sum to ~1.0"""
        total = self.semantic_similarity + self.location_match + self.time_relevance + self.category_match + self.popularity
        if abs(total - 1.0) > 0.01:
            logger.warning(f"Scoring weights sum to {total:.2f}, not 1.0. Scores may be unexpected.")

    @classmethod
    def from_dict(cls, d: Dict[str, float]) -> 'ScoringWeights':
        """Create from dictionary, using defaults for missing keys."""
        return cls(
            semantic_similarity=d.get('semantic_similarity', 0.4),
            location_match=d.get('location_match', 0.25),
            time_relevance=d.get('time_relevance', 0.20),
            category_match=d.get('category_match', 0.10),
            popularity=d.get('popularity', 0.05),
        )


@dataclass
class RankingFactors:
    """Breakdown of scoring factors for a single event."""
    semantic_similarity: float = 0.0
    location_match: float = 0.0
    time_relevance: float = 0.0
    category_match: float = 0.0
    popularity: float = 0.0
    distance_miles: Optional[float] = None  # Actual distance if location used
    days_until_event: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            'semantic_similarity': round(self.semantic_similarity, 3),
            'location_match': round(self.location_match, 3),
            'time_relevance': round(self.time_relevance, 3),
            'category_match': round(self.category_match, 3),
            'popularity': round(self.popularity, 3),
            'distance_miles': round(self.distance_miles, 1) if self.distance_miles is not None else None,
            'days_until_event': round(self.days_until_event, 1) if self.days_until_event is not None else None,
        }


@dataclass
class RankedEvent:
    """An event with its scoring breakdown and tier assignment."""
    event_data: Dict[str, Any]
    final_score: float
    ranking_factors: RankingFactors
    tier: str  # 'recommended', 'additional', or 'context'

    def to_dict(self) -> Dict[str, Any]:
        return {
            **self.event_data,
            'final_score': round(self.final_score, 3),
            'ranking_factors': self.ranking_factors.to_dict(),
            'tier': self.tier,
        }


@dataclass
class RAGResult:
    """
    Complete result from tiered RAG retrieval.

    Events are split into tiers for different use cases:
    - recommended: Top events to show prominently and pass to LLM (5-10)
    - additional: Good matches shown in secondary list (10-20)
    - context: All other matches for map display (up to 50)
    """
    recommended_events: List[RankedEvent] = field(default_factory=list)
    additional_events: List[RankedEvent] = field(default_factory=list)
    context_events: List[RankedEvent] = field(default_factory=list)
    total_considered: int = 0
    search_metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def all_events(self) -> List[RankedEvent]:
        """All events across all tiers, ordered by score."""
        return self.recommended_events + self.additional_events + self.context_events

    @property
    def recommended_ids(self) -> List[int]:
        return [e.event_data['id'] for e in self.recommended_events]

    @property
    def all_ids(self) -> List[int]:
        return [e.event_data['id'] for e in self.all_events]

    def to_legacy_format(self) -> List[Dict[str, Any]]:
        """Convert to legacy format for backward compatibility with existing code."""
        return [e.event_data for e in self.recommended_events + self.additional_events]


@dataclass
class RankedVenue:
    """A venue with its scoring breakdown and tier assignment."""
    venue_data: Dict[str, Any]
    final_score: float
    tier: str  # 'recommended' or 'additional'

    def to_dict(self) -> Dict[str, Any]:
        return {
            **self.venue_data,
            'final_score': round(self.final_score, 3),
            'tier': self.tier,
        }


@dataclass
class DualRAGResult:
    """
    Complete result from dual venue + event RAG retrieval.

    Provides separate sections for LLM context:
    - recommended_venues: Top venues matching the query
    - recommended_events: Top events matching the query
    """
    recommended_venues: List[RankedVenue] = field(default_factory=list)
    recommended_events: List[RankedEvent] = field(default_factory=list)
    additional_venues: List[RankedVenue] = field(default_factory=list)
    additional_events: List[RankedEvent] = field(default_factory=list)
    context_events: List[RankedEvent] = field(default_factory=list)
    total_venues_considered: int = 0
    total_events_considered: int = 0
    search_metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def all_venue_ids(self) -> List[int]:
        return [v.venue_data['id'] for v in self.recommended_venues + self.additional_venues]

    @property
    def all_event_ids(self) -> List[int]:
        return [e.event_data['id'] for e in self.recommended_events + self.additional_events + self.context_events]


def clean_html_content(content: str) -> str:
    """Clean HTML entities and tags from content."""
    if not content:
        return ''
    
    # First decode HTML entities
    content = html.unescape(content)
    
    # Remove HTML tags
    import re
    content = re.sub(r'<[^>]+>', '', content)
    
    # Clean up extra whitespace and newlines
    content = re.sub(r'\s+', ' ', content)
    content = content.replace('\\n', ' ')
    
    return content.strip()


class EventRAGService:
    """RAG service for semantic event search using sentence transformers and PostgreSQL."""

    def __init__(self, embedding_client: Optional[EmbeddingClient] = None):
        """
        Initialize the RAG service.

        Args:
            embedding_client: Optional EmbeddingClient instance. If not provided,
                              uses the global client from get_embedding_client().
        """
        self._embedding_client = embedding_client
        self._warmed_up = False

    @property
    def embedding_client(self) -> EmbeddingClient:
        """Get the embedding client, using global instance if not set."""
        if self._embedding_client is None:
            self._embedding_client = get_embedding_client()
        return self._embedding_client

    def warmup(self):
        """
        Warm up the embedding service to avoid cold-start latency.
        Call this at application startup.
        """
        import time as perf_time
        start = perf_time.perf_counter()

        if not self._warmed_up:
            self.embedding_client.warmup()
            self._warmed_up = True

        warmup_ms = (perf_time.perf_counter() - start) * 1000
        logger.info(f"[RAG] Warmup complete in {warmup_ms:.1f}ms")
    
    def _create_event_text(self, event: Event) -> str:
        """Create searchable text representation of an event for embedding."""
        parts = [
            clean_html_content(event.title),
            clean_html_content(event.description or ""),
            clean_html_content(event.get_location_search_text()),
        ]

        # Temporal context
        if event.start_time:
            day_name = event.start_time.strftime("%A")
            time_desc = event.start_time.strftime("%I:%M %p")
            month_name = event.start_time.strftime("%B")
            parts.append(f"{day_name} {time_desc} {month_name}")

        # Age range with semantic expansion for better matching
        if event.age_range:
            age_text = self._expand_age_range(event.age_range)
            parts.append(age_text)

        # Audience tags (e.g., "Children", "Families", "Seniors")
        if event.audience_tags:
            parts.append(" ".join(event.audience_tags))

        # Metadata tags (e.g., "outdoor", "music", "free")
        if event.metadata_tags:
            parts.append(" ".join(event.metadata_tags))

        # Virtual/in-person indicator
        if event.is_virtual:
            parts.append("virtual online remote")
        elif event.event_attendance_mode == "offline":
            parts.append("in-person")

        return " ".join(filter(None, parts))

    def _expand_age_range(self, age_range: str) -> str:
        """Expand age range codes into semantic text for better embedding matches."""
        expansions = {
            "0-5": "babies toddlers preschool young children ages 0-5",
            "6-9": "kids children elementary school ages 6-9",
            "10-12": "tweens preteens middle school ages 10-12",
            "13-18": "teens teenagers high school young adults ages 13-18",
            "adults": "adults grown-ups 18+",
            "all-ages": "all ages family friendly everyone",
        }
        return expansions.get(age_range, f"ages {age_range}")

    def _create_venue_text(self, venue: 'Venue') -> str:
        """Create searchable text representation of a venue for embedding."""
        parts = [clean_html_content(venue.name)]

        if venue.description:
            parts.append(clean_html_content(venue.description))

        if venue.kids_summary:
            parts.append(clean_html_content(venue.kids_summary))

        # Venue kind with semantic expansion
        if venue.venue_kind and venue.venue_kind not in ('other', 'unknown'):
            venue_kind_expansions = {
                'library': 'library books reading children storytime programs',
                'museum': 'museum exhibits art science history learning',
                'park': 'park outdoor playground nature trails',
                'playground': 'playground kids play outdoor equipment',
                'beach': 'beach swimming water sand outdoor',
                'skating_rink': 'skating rink ice roller skating',
                'dog_park': 'dog park pets off-leash outdoor',
                'pool': 'pool swimming aquatics water recreation',
                'school': 'school education learning classes',
                'community_center': 'community center classes activities programs',
                'church': 'church religious worship community',
                'theater': 'theater performance shows arts entertainment',
                'restaurant': 'restaurant dining food family',
                'senior_center': 'senior center elderly programs activities',
                'ymca': 'ymca fitness recreation sports programs',
                'sports_facility': 'sports facility athletics recreation games',
                'nature_center': 'nature center trails wildlife outdoor education',
                'zoo': 'zoo animals wildlife family children',
                'aquarium': 'aquarium fish marine animals family children',
                'town_hall': 'town hall government civic meetings',
                'city_hall': 'city hall government civic meetings',
                'government_office': 'government office civic services',
            }
            parts.append(venue_kind_expansions.get(venue.venue_kind, venue.venue_kind.replace('_', ' ')))

        # Audience tags (e.g., 'families', 'stroller_friendly', 'wheelchair_accessible')
        if venue.audience_tags:
            parts.append(" ".join(venue.audience_tags))

        # Age groups with semantic expansion
        if venue.audience_age_groups:
            age_expansions = {
                'infant': 'infants babies 0-1',
                'toddler': 'toddlers ages 1-3',
                'child': 'children kids ages 3-12',
                'teen': 'teens teenagers ages 13-18',
                'adult': 'adults grown-ups 18+',
                'senior': 'seniors elderly 65+',
            }
            for group in venue.audience_age_groups:
                parts.append(age_expansions.get(group, group))

        # Primary audience
        if venue.audience_primary and venue.audience_primary not in ('general', 'unknown'):
            parts.append(f"primarily for {venue.audience_primary}")

        # Location context
        if venue.city and venue.state:
            parts.append(f"{venue.city} {venue.state}")

        return " ".join(filter(None, parts))

    def update_event_embeddings(self, event_ids: List[int] = None):
        """Update embeddings for specified events or all events."""
        # Get events to update
        if event_ids:
            events = Event.objects.filter(id__in=event_ids)
        else:
            # Update all events that don't have embeddings
            events = Event.objects.filter(embedding__isnull=True)

        if not events.exists():
            logger.info("No events to update embeddings for")
            return

        # Create text representations and compute embeddings
        texts = []
        event_list = list(events)

        for event in event_list:
            text = self._create_event_text(event)
            texts.append(text)

        logger.info(f"Computing embeddings for {len(texts)} events...")
        new_embeddings = self.embedding_client.encode(texts, use_cache=False)

        # Update database with new embeddings
        for event, embedding in zip(event_list, new_embeddings):
            event.embedding = embedding.tolist()  # Convert numpy array to list for pgvector
            event.save(update_fields=['embedding'])

        logger.info(f"Updated embeddings for {len(event_list)} events")

    def update_venue_embeddings(self, venue_ids: List[int] = None):
        """Update embeddings for specified venues or all venues without embeddings."""
        from venues.models import Venue

        if venue_ids:
            venues = Venue.objects.filter(id__in=venue_ids)
        else:
            venues = Venue.objects.filter(embedding__isnull=True)

        if not venues.exists():
            logger.info("No venues to update embeddings for")
            return

        texts = []
        venue_list = list(venues)

        for venue in venue_list:
            text = self._create_venue_text(venue)
            texts.append(text)

        logger.info(f"Computing embeddings for {len(texts)} venues...")
        new_embeddings = self.embedding_client.encode(texts, use_cache=False)

        for venue, embedding in zip(venue_list, new_embeddings):
            venue.embedding = embedding.tolist()
            venue.save(update_fields=['embedding'])

        logger.info(f"Updated embeddings for {len(venue_list)} venues")

    def semantic_search(
        self,
        query: str,
        top_k: int = 5,
        time_filter_days: int = 30,
        location_filter: str = None,
        only_future_events: bool = True,
        date_from: datetime = None,
        date_to: datetime = None,
        is_virtual: bool = None,
        max_distance_miles: float = None,
        user_lat: float = None,
        user_lng: float = None,
    ) -> List[Tuple[Event, float]]:
        """
        Perform semantic search for events using PostgreSQL vector operations.

        Args:
            query: Natural language search query
            top_k: Number of results to return
            time_filter_days: Only include events within this many days from now (ignored if date_from/date_to set)
            location_filter: Optional location filter (city name or venue name)
            only_future_events: Only include events that haven't started yet
            date_from: Explicit start of date range (overrides time_filter_days)
            date_to: Explicit end of date range (overrides time_filter_days)
            is_virtual: Filter by virtual (True), in-person (False), or any (None)
            max_distance_miles: Maximum distance from user location
            user_lat: User's latitude for distance calculation
            user_lng: User's longitude for distance calculation

        Returns:
            List of (Event, similarity_score) tuples
        """
        import time as perf_time

        total_start = perf_time.perf_counter()

        # Get query embedding via embedding client (handles caching internally)
        embed_start = perf_time.perf_counter()
        query_embedding = self.embedding_client.encode(query)
        embed_ms = (perf_time.perf_counter() - embed_start) * 1000

        logger.info(f"[RAG PERF] embed_ms={embed_ms:.1f}")

        # Perform vector similarity search using raw SQL (Django ORM CosineDistance has issues)
        from django.db import connection

        # Build WHERE conditions for the filters
        where_conditions = ["embedding IS NOT NULL"]
        params = []

        # Always exclude cancelled and full events
        where_conditions.append("is_cancelled = FALSE")
        where_conditions.append("is_full = FALSE")

        # Filter out past events by default
        if only_future_events:
            where_conditions.append("start_time > %s")
            params.append(timezone.now())

        # Apply date range filters - explicit dates override time_filter_days
        if date_from:
            where_conditions.append("start_time >= %s")
            params.append(date_from)
        if date_to:
            where_conditions.append("start_time <= %s")
            params.append(date_to)
        elif time_filter_days and time_filter_days > 0:
            # Only use time_filter_days if no explicit date range provided
            end_date = timezone.now() + timedelta(days=time_filter_days)
            where_conditions.append("start_time <= %s")
            params.append(end_date)

        # Filter by virtual/in-person preference
        if is_virtual is not None:
            where_conditions.append("is_virtual = %s")
            params.append(is_virtual)

        # Apply location filter (search venue name, city, or room_name)
        if location_filter:
            where_conditions.append(
                "(venue_id IN (SELECT id FROM venues_venue WHERE name ILIKE %s OR city ILIKE %s) OR room_name ILIKE %s)"
            )
            params.append(f"%{location_filter}%")
            params.append(f"%{location_filter}%")
            params.append(f"%{location_filter}%")

        # Geo-distance filter using Haversine formula
        if max_distance_miles and user_lat is not None and user_lng is not None:
            # Haversine formula in SQL (returns miles)
            # Only include events with venues that have lat/lng
            where_conditions.append("""
                venue_id IN (
                    SELECT id FROM venues_venue
                    WHERE latitude IS NOT NULL AND longitude IS NOT NULL
                    AND (3959 * acos(
                        cos(radians(%s)) * cos(radians(latitude)) * cos(radians(longitude) - radians(%s)) +
                        sin(radians(%s)) * sin(radians(latitude))
                    )) <= %s
                )
            """)
            params.extend([user_lat, user_lng, user_lat, max_distance_miles])
        
        where_clause = " AND ".join(where_conditions)

        # Separate filter params from embedding/limit params
        filter_params = params.copy()

        # Timing: numpy to list conversion
        tolist_start = perf_time.perf_counter()
        query_embedding_list = query_embedding.tolist()
        tolist_ms = (perf_time.perf_counter() - tolist_start) * 1000

        # SQL query timing
        sql_start = perf_time.perf_counter()
        with connection.cursor() as cursor:
            # Build the complete SQL with correct parameter order
            sql = f'''
                SELECT id, 1 - (embedding <=> %s::vector) as similarity
                FROM events_event
                WHERE {where_clause}
                ORDER BY similarity DESC
                LIMIT %s
            '''
            # Parameters: [query_embedding, filter_params..., top_k]
            all_params = [query_embedding_list] + filter_params + [top_k]
            cursor.execute(sql, all_params)

            sql_results = cursor.fetchall()
        sql_ms = (perf_time.perf_counter() - sql_start) * 1000

        # ORM fetch timing
        orm_start = perf_time.perf_counter()
        event_ids = [row[0] for row in sql_results]
        similarity_scores = {row[0]: row[1] for row in sql_results}

        # Get Event objects in the same order
        events = Event.objects.filter(id__in=event_ids).select_related('venue')
        event_dict = {event.id: event for event in events}

        event_similarity_pairs = [
            (event_dict[event_id], similarity_scores[event_id])
            for event_id in event_ids if event_id in event_dict
        ]
        orm_ms = (perf_time.perf_counter() - orm_start) * 1000

        total_ms = (perf_time.perf_counter() - total_start) * 1000

        logger.info(
            f"[RAG PERF] query='{query[:30]}...' embed_ms={embed_ms:.1f}, "
            f"tolist_ms={tolist_ms:.1f}, sql_ms={sql_ms:.1f}, orm_ms={orm_ms:.1f}, "
            f"total_ms={total_ms:.1f}, results={len(event_similarity_pairs)}"
        )
        return event_similarity_pairs
    
    def get_context_events(
        self,
        user_message: str,
        max_events: int = 20,
        similarity_threshold: float = 0.2,
        time_filter_days: int = 30,
        date_from: datetime = None,
        date_to: datetime = None,
        location: str = None,
        is_virtual: bool = None,
        max_distance_miles: float = None,
        user_lat: float = None,
        user_lng: float = None,
        default_state: str = None,
        trace: Optional['TraceRecorder'] = None,
    ) -> List[Dict[str, Any]]:
        """
        Get relevant future events for LLM context based on user message.

        Args:
            user_message: User's natural language query
            max_events: Maximum events to return
            similarity_threshold: Minimum similarity score to include
            time_filter_days: Only include events within this many days (ignored if date_from/date_to set)
            date_from: Explicit start of date range
            date_to: Explicit end of date range
            location: Explicit location filter (overrides message extraction)
            is_virtual: Filter by virtual (True), in-person (False), or any (None)
            max_distance_miles: Maximum distance from user location
            user_lat: User's latitude for distance calculation
            user_lng: User's longitude for distance calculation
            default_state: Default state for location disambiguation (e.g., 'MA')
            trace: Optional TraceRecorder for debugging

        Returns:
            List of event dictionaries for LLM context
        """
        import time
        retrieval_start = time.time()

        try:
            # Step 1: Determine location query string
            location_hints = self._extract_location_hints(user_message)
            if location:
                location_query = location
            else:
                location_query = location_hints[0] if location_hints else None

            # Step 1b: Extract dates from natural language query (if no explicit dates provided)
            date_extraction_result = None
            logger.info(f"Date extraction check: date_from={date_from}, date_to={date_to}")
            if date_from is None and date_to is None:
                from api.date_extraction import extract_dates_from_query
                date_extraction_result = extract_dates_from_query(user_message, timezone.localtime(timezone.now()))
                logger.info(f"Date extraction result: {date_extraction_result}")
                if date_extraction_result.date_from and date_extraction_result.confidence >= 0.5:
                    # Handle both naive and aware datetimes from dateparser
                    if timezone.is_naive(date_extraction_result.date_from):
                        date_from = timezone.make_aware(date_extraction_result.date_from)
                    else:
                        date_from = date_extraction_result.date_from
                    if timezone.is_naive(date_extraction_result.date_to):
                        date_to = timezone.make_aware(date_extraction_result.date_to)
                    else:
                        date_to = date_extraction_result.date_to
                    # When we extract dates, don't use the default time_filter_days
                    time_filter_days = None
                    logger.info(
                        f"Date extracted from query: '{user_message[:50]}' -> "
                        f"{date_from.strftime('%Y-%m-%d')} to {date_to.strftime('%Y-%m-%d')} "
                        f"(phrases: {date_extraction_result.extracted_phrases}, confidence: {date_extraction_result.confidence})"
                    )

            # Record input event with all filters
            if trace:
                trace.event('input', {
                    'message': user_message,
                    'location_hints_extracted': location_hints,
                    'date_extraction': {
                        'extracted_phrases': date_extraction_result.extracted_phrases if date_extraction_result else [],
                        'confidence': date_extraction_result.confidence if date_extraction_result else 0,
                        'date_from': date_from.isoformat() if date_from else None,
                        'date_to': date_to.isoformat() if date_to else None,
                    } if date_extraction_result else None,
                    'filters': {
                        'max_events': max_events,
                        'similarity_threshold': similarity_threshold,
                        'time_filter_days': time_filter_days,
                        'date_from': date_from.isoformat() if date_from else None,
                        'date_to': date_to.isoformat() if date_to else None,
                        'location': location,
                        'is_virtual': is_virtual,
                        'max_distance_miles': max_distance_miles,
                        'user_lat': user_lat,
                        'user_lng': user_lng,
                        'default_state': default_state,
                    }
                })

            # Step 2: Try to resolve location to coordinates (if not already provided)
            resolved_location = None
            effective_lat = user_lat
            effective_lng = user_lng
            location_filter = None  # Text-based fallback

            if location_query and (effective_lat is None or effective_lng is None):
                location_resolve_start = time.time()
                try:
                    from locations.services import resolve_location
                    result = resolve_location(location_query, default_state=default_state)
                    location_resolve_ms = int((time.time() - location_resolve_start) * 1000)

                    if result.matched_location:
                        resolved_location = result
                        effective_lat = float(result.latitude)
                        effective_lng = float(result.longitude)
                        # Use 10 mile default radius if not specified
                        if max_distance_miles is None:
                            max_distance_miles = 10.0
                        logger.info(
                            f"Location resolved: '{location_query}' -> {result.display_name} "
                            f"(lat={effective_lat:.4f}, lng={effective_lng:.4f}, confidence={result.confidence})"
                        )

                        if trace:
                            trace.event('location_resolution', {
                                'query': location_query,
                                'matched': result.display_name,
                                'latitude': effective_lat,
                                'longitude': effective_lng,
                                'confidence': result.confidence,
                                'is_ambiguous': result.is_ambiguous,
                                'alternatives': [str(alt) for alt in result.alternatives] if result.alternatives else [],
                                'resolution_type': 'geo',
                            }, latency_ms=location_resolve_ms)
                    else:
                        # Location not found in database, fall back to text filter
                        location_filter = location_query
                        logger.info(f"Location not resolved: '{location_query}', falling back to text filter")

                        if trace:
                            trace.event('location_resolution', {
                                'query': location_query,
                                'matched': None,
                                'resolution_type': 'text_fallback',
                                'reason': 'not_found_in_database',
                            }, latency_ms=location_resolve_ms)
                except Exception as e:
                    logger.warning(f"Location resolution failed for '{location_query}': {e}, using text filter")
                    location_filter = location_query

                    if trace:
                        trace.event('location_resolution', {
                            'query': location_query,
                            'matched': None,
                            'resolution_type': 'text_fallback',
                            'reason': f'error: {str(e)}',
                        }, latency_ms=int((time.time() - location_resolve_start) * 1000))

            # Step 3: Perform semantic search with geo-filter or text filter
            search_start = time.time()
            results = self.semantic_search(
                query=user_message,
                top_k=max_events * 2,  # Get more results to filter
                time_filter_days=time_filter_days,
                location_filter=location_filter,  # Only used if geo resolution failed
                only_future_events=True,
                date_from=date_from,
                date_to=date_to,
                is_virtual=is_virtual,
                max_distance_miles=max_distance_miles if effective_lat else None,
                user_lat=effective_lat,
                user_lng=effective_lng,
            )
            search_ms = int((time.time() - search_start) * 1000)

            # Filter by similarity threshold and format for LLM
            context_events = []
            for event, score in results:
                if score >= similarity_threshold:
                    context_events.append({
                        'id': event.id,
                        'title': clean_html_content(event.title),
                        'description': clean_html_content(event.description),
                        'location': clean_html_content(event.get_location_string()),
                        'room_name': event.room_name or '',
                        'full_address': event.get_full_address(),
                        'city': event.get_city(),
                        'organizer': event.organizer,
                        'event_status': event.event_status,
                        # New fields for richer context
                        'age_range': event.age_range or '',
                        'audience_tags': event.audience_tags or [],
                        'is_virtual': event.is_virtual,
                        'requires_registration': event.requires_registration,
                        'start_time': event.start_time.isoformat() if event.start_time else None,
                        'end_time': event.end_time.isoformat() if event.end_time else None,
                        'url': event.url,
                        'similarity_score': float(score),
                    })

            # Record retrieval event with candidates
            if trace:
                # Build candidate list with truncated snippets for display
                candidates_for_trace = []
                for event, score in results:
                    event_text = self._create_event_text(event)
                    candidates_for_trace.append({
                        'id': event.id,
                        'title': clean_html_content(event.title),
                        'venue': event.venue.name if event.venue else None,
                        'city': event.get_city(),
                        'similarity_score': float(score),
                        'start_time': event.start_time.isoformat() if event.start_time else None,
                        'above_threshold': score >= similarity_threshold,
                        'context_snippet': event_text[:500] + ('...' if len(event_text) > 500 else ''),
                    })

                trace.event('retrieval', {
                    'query_text': user_message,
                    'total_candidates': len(results),
                    'above_threshold': len(context_events),
                    'threshold': similarity_threshold,
                    'geo_filter_used': effective_lat is not None,
                    'text_filter_used': location_filter is not None,
                    'candidates': candidates_for_trace,
                }, latency_ms=search_ms)

            logger.info(f"Returning {len(context_events)} future context events with scores >= {similarity_threshold}")
            return context_events[:max_events]

        except Exception as e:
            logger.error(f"Error in get_context_events: {e}")

            if trace:
                import traceback
                trace.event('error', {
                    'stage': 'retrieval',
                    'message': str(e),
                    'stack': traceback.format_exc(),
                })

            # Fallback to basic query if RAG fails
            return self._fallback_event_search(user_message, max_events)
    
    def _extract_location_hints(self, message: str) -> List[str]:
        """Extract potential location mentions from user message."""
        import re
        
        # Common location patterns
        patterns = [
            r'(?:in|at|near|around)\s+([A-Z][a-zA-Z\s,]+?)(?:\s|$|,)',
            r'([A-Z][a-zA-Z]+\s*,?\s*[A-Z][A-Z])',  # City, State
            r'([A-Z][a-zA-Z]+\s+[A-Z][a-zA-Z]+)',   # Two-word locations
        ]
        
        locations = []
        for pattern in patterns:
            matches = re.findall(pattern, message)
            for match in matches:
                clean_location = match.strip(' ,')
                if len(clean_location) > 2:  # Filter out very short matches
                    locations.append(clean_location)
        
        return locations[:3]  # Return top 3 location hints
    
    def _fallback_event_search(self, message: str, max_events: int) -> List[Dict[str, Any]]:
        """Fallback to basic keyword search if semantic search fails."""
        # Simple keyword-based search as fallback - only future events
        events = Event.objects.filter(
            start_time__gt=timezone.now()
        ).order_by('start_time')[:max_events]

        return [
            {
                'id': event.id,
                'title': clean_html_content(event.title),
                'description': clean_html_content(event.description),
                'location': clean_html_content(event.get_location_string()),
                'room_name': event.room_name or '',
                'full_address': event.get_full_address(),
                'city': event.get_city(),
                'organizer': event.organizer,
                'event_status': event.event_status,
                'start_time': event.start_time.isoformat() if event.start_time else None,
                'end_time': event.end_time.isoformat() if event.end_time else None,
                'url': event.url,
                'similarity_score': 0.0,  # No semantic score for fallback
            }
            for event in events
        ]

    # =========================================================================
    # Enhanced Tiered Retrieval with Multi-Factor Scoring
    # =========================================================================

    def get_context_events_tiered(
        self,
        user_message: str,
        # Tier sizes
        max_recommended: int = 10,
        max_additional: int = 15,
        max_context: int = 50,
        # Scoring configuration
        similarity_threshold: float = 0.15,
        scoring_weights: Optional[ScoringWeights] = None,
        # Location parameters - prefer location_id over string
        location_id: Optional[int] = None,
        location: Optional[str] = None,  # Fallback if no location_id
        max_distance_miles: Optional[float] = None,
        user_lat: Optional[float] = None,
        user_lng: Optional[float] = None,
        default_state: Optional[str] = None,
        # Date parameters
        time_filter_days: Optional[int] = 30,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
        # Other filters
        is_virtual: Optional[bool] = None,
        # Search restriction
        candidate_ids: Optional[List[int]] = None,
        # Debugging
        trace: Optional['TraceRecorder'] = None,
    ) -> RAGResult:
        """
        Get tiered event results with multi-factor scoring.

        This is the enhanced version of get_context_events that returns:
        - recommended_events: Top matches for LLM context (high confidence)
        - additional_events: Good matches for secondary display
        - context_events: All matches for map display

        Each event includes a scoring breakdown showing contribution of each factor.

        Args:
            user_message: User's natural language query
            max_recommended: Max events in recommended tier (default 10)
            max_additional: Max events in additional tier (default 15)
            max_context: Max events in context tier (default 50)
            similarity_threshold: Minimum semantic similarity to include
            scoring_weights: Custom weights for scoring factors (default balanced)
            location_id: Location table ID for deterministic geo-filtering
            location: Location string (fallback if no location_id)
            max_distance_miles: Max distance filter
            user_lat/user_lng: User coordinates (overrides location resolution)
            default_state: State for ambiguous location resolution
            time_filter_days: Days ahead to search (ignored if date_from/to set)
            date_from/date_to: Explicit date range
            is_virtual: Filter by virtual/in-person
            candidate_ids: Restrict search to these event IDs
            trace: TraceRecorder for debugging

        Returns:
            RAGResult with tiered events and metadata
        """
        import time
        retrieval_start = time.time()

        weights = scoring_weights or ScoringWeights()

        try:
            # Step 1: Resolve location from location_id (preferred) or string
            effective_lat = user_lat
            effective_lng = user_lng
            location_filter = None
            resolved_location = None
            location_resolve_ms = 0

            # If location_id provided, use it directly (deterministic)
            if location_id is not None and (effective_lat is None or effective_lng is None):
                location_resolve_start = time.time()
                try:
                    from locations.models import Location
                    loc = Location.objects.get(id=location_id)
                    effective_lat = float(loc.latitude)
                    effective_lng = float(loc.longitude)
                    if max_distance_miles is None:
                        max_distance_miles = 10.0
                    resolved_location = {'id': loc.id, 'name': str(loc), 'source': 'location_id'}
                    location_resolve_ms = int((time.time() - location_resolve_start) * 1000)
                    logger.info(f"Location resolved from ID {location_id}: {loc}")

                    if trace:
                        trace.event('location_resolution', {
                            'source': 'location_id',
                            'location_id': location_id,
                            'matched': str(loc),
                            'latitude': effective_lat,
                            'longitude': effective_lng,
                            'resolution_type': 'id_lookup',
                        }, latency_ms=location_resolve_ms)

                except Exception as e:
                    logger.warning(f"Location ID {location_id} lookup failed: {e}")
                    if trace:
                        trace.event('location_resolution', {
                            'source': 'location_id',
                            'location_id': location_id,
                            'matched': None,
                            'resolution_type': 'error',
                            'error': str(e),
                        }, latency_ms=int((time.time() - location_resolve_start) * 1000))

            # Fallback to string-based resolution if no location_id
            if effective_lat is None and location:
                location_resolve_start = time.time()
                location_hints = self._extract_location_hints(user_message)
                location_query = location or (location_hints[0] if location_hints else None)

                if location_query:
                    try:
                        from locations.services import resolve_location
                        result = resolve_location(location_query, default_state=default_state)
                        location_resolve_ms = int((time.time() - location_resolve_start) * 1000)

                        if result.matched_location:
                            effective_lat = float(result.latitude)
                            effective_lng = float(result.longitude)
                            if max_distance_miles is None:
                                max_distance_miles = 10.0
                            resolved_location = {
                                'id': result.matched_location.id,
                                'name': result.display_name,
                                'source': 'string_resolution',
                                'confidence': result.confidence,
                            }
                            logger.info(f"Location resolved from string: '{location_query}' -> {result.display_name}")

                            if trace:
                                trace.event('location_resolution', {
                                    'source': 'string',
                                    'query': location_query,
                                    'matched': result.display_name,
                                    'matched_id': result.matched_location.id,
                                    'latitude': effective_lat,
                                    'longitude': effective_lng,
                                    'confidence': result.confidence,
                                    'is_ambiguous': result.is_ambiguous,
                                    'resolution_type': 'geo',
                                }, latency_ms=location_resolve_ms)
                        else:
                            location_filter = location_query
                            if trace:
                                trace.event('location_resolution', {
                                    'source': 'string',
                                    'query': location_query,
                                    'matched': None,
                                    'resolution_type': 'text_fallback',
                                }, latency_ms=location_resolve_ms)
                    except Exception as e:
                        logger.warning(f"Location resolution failed: {e}")
                        location_filter = location_query

            # Step 2: Extract dates from query if not provided
            if date_from is None and date_to is None:
                from api.date_extraction import extract_dates_from_query
                date_result = extract_dates_from_query(user_message, timezone.localtime(timezone.now()))
                if date_result.date_from and date_result.confidence >= 0.5:
                    if timezone.is_naive(date_result.date_from):
                        date_from = timezone.make_aware(date_result.date_from)
                    else:
                        date_from = date_result.date_from
                    if timezone.is_naive(date_result.date_to):
                        date_to = timezone.make_aware(date_result.date_to)
                    else:
                        date_to = date_result.date_to
                    time_filter_days = None

            # Step 3: Perform semantic search
            search_start = time.time()
            total_to_fetch = max_recommended + max_additional + max_context

            results = self.semantic_search(
                query=user_message,
                top_k=total_to_fetch * 2,  # Fetch extra for filtering
                time_filter_days=time_filter_days,
                location_filter=location_filter,
                only_future_events=True,
                date_from=date_from,
                date_to=date_to,
                is_virtual=is_virtual,
                max_distance_miles=max_distance_miles if effective_lat else None,
                user_lat=effective_lat,
                user_lng=effective_lng,
            )
            search_ms = int((time.time() - search_start) * 1000)

            # If candidate_ids provided, filter to those
            if candidate_ids:
                candidate_set = set(candidate_ids)
                results = [(e, s) for e, s in results if e.id in candidate_set]

            # Step 4: Compute multi-factor scores for each event
            now = timezone.now()
            scored_events: List[Tuple[Event, float, float, RankingFactors]] = []

            for event, semantic_score in results:
                if semantic_score < similarity_threshold:
                    continue

                factors = self._compute_ranking_factors(
                    event=event,
                    semantic_score=semantic_score,
                    center_lat=effective_lat,
                    center_lng=effective_lng,
                    now=now,
                    user_message=user_message,
                )

                # Compute weighted final score
                final_score = (
                    weights.semantic_similarity * factors.semantic_similarity +
                    weights.location_match * factors.location_match +
                    weights.time_relevance * factors.time_relevance +
                    weights.category_match * factors.category_match +
                    weights.popularity * factors.popularity
                )

                scored_events.append((event, semantic_score, final_score, factors))

            # Sort by final score descending
            scored_events.sort(key=lambda x: x[2], reverse=True)

            # Step 5: Assign to tiers
            recommended: List[RankedEvent] = []
            additional: List[RankedEvent] = []
            context: List[RankedEvent] = []

            for i, (event, semantic_score, final_score, factors) in enumerate(scored_events):
                event_data = self._event_to_dict(event, semantic_score)

                if i < max_recommended:
                    tier = 'recommended'
                    recommended.append(RankedEvent(event_data, final_score, factors, tier))
                elif i < max_recommended + max_additional:
                    tier = 'additional'
                    additional.append(RankedEvent(event_data, final_score, factors, tier))
                elif i < max_recommended + max_additional + max_context:
                    tier = 'context'
                    context.append(RankedEvent(event_data, final_score, factors, tier))
                else:
                    break

            # Build result
            result = RAGResult(
                recommended_events=recommended,
                additional_events=additional,
                context_events=context,
                total_considered=len(results),
                search_metadata={
                    'query': user_message,
                    'weights': {
                        'semantic_similarity': weights.semantic_similarity,
                        'location_match': weights.location_match,
                        'time_relevance': weights.time_relevance,
                        'category_match': weights.category_match,
                        'popularity': weights.popularity,
                    },
                    'location_used': resolved_location,
                    'similarity_threshold': similarity_threshold,
                    'search_time_ms': search_ms,
                    'location_resolve_time_ms': location_resolve_ms,
                },
            )

            # Record trace
            if trace:
                trace.event('retrieval', {
                    'query_text': user_message,
                    'total_candidates': len(results),
                    'above_threshold': len(scored_events),
                    'threshold': similarity_threshold,
                    'tiers': {
                        'recommended': len(recommended),
                        'additional': len(additional),
                        'context': len(context),
                    },
                    'weights': result.search_metadata['weights'],
                    'geo_filter_used': effective_lat is not None,
                    'text_filter_used': location_filter is not None,
                    'candidates': [
                        {
                            'id': e.event_data['id'],
                            'title': e.event_data['title'],
                            'venue': e.event_data.get('location', ''),
                            'city': e.event_data.get('city', ''),
                            'start_time': e.event_data.get('start_time'),
                            'tier': e.tier,
                            'final_score': round(e.final_score, 3),
                            'similarity_score': e.ranking_factors.semantic_similarity,
                            'above_threshold': True,  # All scored events are above threshold
                            'factors': e.ranking_factors.to_dict(),
                        }
                        for e in result.all_events[:30]  # Top 30 for trace
                    ],
                }, latency_ms=search_ms)

            logger.info(
                f"Tiered retrieval: {len(recommended)} recommended, "
                f"{len(additional)} additional, {len(context)} context "
                f"(from {len(results)} candidates)"
            )

            return result

        except Exception as e:
            logger.error(f"Error in get_context_events_tiered: {e}")
            import traceback
            if trace:
                trace.event('error', {
                    'stage': 'retrieval',
                    'message': str(e),
                    'stack': traceback.format_exc(),
                })
            return RAGResult(total_considered=0, search_metadata={'error': str(e)})

    def _compute_ranking_factors(
        self,
        event: Event,
        semantic_score: float,
        center_lat: Optional[float],
        center_lng: Optional[float],
        now: datetime,
        user_message: str,
    ) -> RankingFactors:
        """Compute individual ranking factors for an event."""
        factors = RankingFactors()

        # Semantic similarity (already 0-1)
        factors.semantic_similarity = max(0.0, min(1.0, semantic_score))

        # Location match: inverse of distance (closer = higher)
        if center_lat is not None and center_lng is not None and event.venue:
            venue = event.venue
            if venue.latitude and venue.longitude:
                from locations.services import haversine_distance
                distance = haversine_distance(
                    center_lat, center_lng,
                    float(venue.latitude), float(venue.longitude)
                )
                factors.distance_miles = distance
                # Score decays with distance: 1.0 at 0 miles, ~0.5 at 5 miles, ~0.1 at 20 miles
                factors.location_match = max(0.0, 1.0 / (1.0 + distance / 5.0))
        else:
            # No location available - neutral score
            factors.location_match = 0.5

        # Time relevance: sooner events score higher
        if event.start_time:
            days_until = (event.start_time - now).total_seconds() / 86400
            factors.days_until_event = max(0, days_until)
            # Score decays with time: 1.0 today, ~0.7 at 3 days, ~0.5 at 7 days, ~0.2 at 30 days
            factors.time_relevance = max(0.0, 1.0 / (1.0 + days_until / 7.0))
        else:
            factors.time_relevance = 0.5

        # Category match: check for tag overlap with query keywords
        query_words = set(user_message.lower().split())
        event_tags = set()
        if event.audience_tags:
            event_tags.update(tag.lower() for tag in event.audience_tags)
        if event.metadata_tags:
            event_tags.update(tag.lower() for tag in event.metadata_tags)

        if event_tags and query_words:
            overlap = len(query_words & event_tags)
            factors.category_match = min(1.0, overlap / 3.0)  # Cap at 3 matches
        else:
            factors.category_match = 0.5

        # Popularity: based on source quality (placeholder - expand later)
        # For now, use a neutral 0.5 unless we have better signals
        factors.popularity = 0.5

        return factors

    def _event_to_dict(self, event: Event, similarity_score: float) -> Dict[str, Any]:
        """Convert event to dictionary format for API response."""
        return {
            'id': event.id,
            'title': clean_html_content(event.title),
            'description': clean_html_content(event.description),
            'location': clean_html_content(event.get_location_string()),
            'room_name': event.room_name or '',
            'full_address': event.get_full_address(),
            'city': event.get_city(),
            'organizer': event.organizer,
            'event_status': event.event_status,
            'age_range': event.age_range or '',
            'audience_tags': event.audience_tags or [],
            'is_virtual': event.is_virtual,
            'requires_registration': event.requires_registration,
            'start_time': event.start_time.isoformat() if event.start_time else None,
            'end_time': event.end_time.isoformat() if event.end_time else None,
            'url': event.url,
            'similarity_score': float(similarity_score),
        }

    def _venue_to_dict(self, venue: 'Venue', similarity_score: float) -> Dict[str, Any]:
        """Convert venue to dictionary format for API response."""
        return {
            'id': venue.id,
            'name': clean_html_content(venue.name),
            'description': clean_html_content(venue.description) if venue.description else '',
            'kids_summary': clean_html_content(venue.kids_summary) if venue.kids_summary else '',
            'venue_kind': venue.venue_kind or '',
            'audience_tags': venue.audience_tags or [],
            'audience_age_groups': venue.audience_age_groups or [],
            'audience_primary': venue.audience_primary,
            'city': venue.city,
            'state': venue.state,
            'full_address': venue.get_full_address(),
            'latitude': float(venue.latitude) if venue.latitude else None,
            'longitude': float(venue.longitude) if venue.longitude else None,
            'website_url': venue.website_url or '',
            'similarity_score': float(similarity_score),
        }

    def venue_semantic_search(
        self,
        query: str,
        top_k: int = 10,
        max_distance_miles: float = None,
        user_lat: float = None,
        user_lng: float = None,
        venue_kinds: List[str] = None,
        family_friendly_only: bool = False,
    ) -> List[Tuple['Venue', float]]:
        """
        Perform semantic search for venues using PostgreSQL vector operations.

        Args:
            query: Natural language search query
            top_k: Number of results to return
            max_distance_miles: Max distance from user location
            user_lat/user_lng: User coordinates
            venue_kinds: Filter to specific venue types
            family_friendly_only: Only include family-friendly venues

        Returns:
            List of (Venue, similarity_score) tuples
        """
        import time as perf_time
        from venues.models import Venue
        from django.db import connection

        total_start = perf_time.perf_counter()

        embed_start = perf_time.perf_counter()
        query_embedding = self.embedding_client.encode(query)
        embed_ms = (perf_time.perf_counter() - embed_start) * 1000

        where_conditions = ["embedding IS NOT NULL"]
        params = []

        # Geo-distance filter
        if max_distance_miles and user_lat is not None and user_lng is not None:
            where_conditions.append("""
                latitude IS NOT NULL AND longitude IS NOT NULL
                AND (3959 * acos(
                    cos(radians(%s)) * cos(radians(latitude)) * cos(radians(longitude) - radians(%s)) +
                    sin(radians(%s)) * sin(radians(latitude))
                )) <= %s
            """)
            params.extend([user_lat, user_lng, user_lat, max_distance_miles])

        # Venue kind filter
        if venue_kinds:
            placeholders = ', '.join(['%s'] * len(venue_kinds))
            where_conditions.append(f"venue_kind IN ({placeholders})")
            params.extend(venue_kinds)

        # Family-friendly filter
        if family_friendly_only:
            where_conditions.append("""
                (audience_primary IN ('families', 'children')
                 OR audience_age_groups::text LIKE '%%infant%%'
                 OR audience_age_groups::text LIKE '%%toddler%%'
                 OR audience_age_groups::text LIKE '%%child%%'
                 OR audience_tags::text LIKE '%%family_friendly%%')
            """)

        where_clause = " AND ".join(where_conditions)
        query_embedding_list = query_embedding.tolist()

        sql_start = perf_time.perf_counter()
        with connection.cursor() as cursor:
            sql = f'''
                SELECT id, 1 - (embedding <=> %s::vector) as similarity
                FROM venues_venue
                WHERE {where_clause}
                ORDER BY similarity DESC
                LIMIT %s
            '''
            all_params = [query_embedding_list] + params + [top_k]
            cursor.execute(sql, all_params)
            sql_results = cursor.fetchall()
        sql_ms = (perf_time.perf_counter() - sql_start) * 1000

        venue_ids = [row[0] for row in sql_results]
        similarity_scores = {row[0]: row[1] for row in sql_results}

        venues = Venue.objects.filter(id__in=venue_ids)
        venue_dict = {venue.id: venue for venue in venues}

        results = [
            (venue_dict[venue_id], similarity_scores[venue_id])
            for venue_id in venue_ids if venue_id in venue_dict
        ]

        total_ms = (perf_time.perf_counter() - total_start) * 1000
        logger.info(f"[VENUE RAG PERF] query='{query[:30]}...' embed_ms={embed_ms:.1f}, sql_ms={sql_ms:.1f}, total_ms={total_ms:.1f}, results={len(results)}")

        return results

    def get_context_dual(
        self,
        user_message: str,
        max_venues: int = 5,
        max_events: int = 10,
        max_additional_events: int = 15,
        max_context_events: int = 50,
        venue_similarity_threshold: float = 0.2,
        event_similarity_threshold: float = 0.15,
        scoring_weights: Optional[ScoringWeights] = None,
        location_id: Optional[int] = None,
        location: Optional[str] = None,
        max_distance_miles: Optional[float] = None,
        user_lat: Optional[float] = None,
        user_lng: Optional[float] = None,
        default_state: Optional[str] = None,
        time_filter_days: Optional[int] = 30,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
        is_virtual: Optional[bool] = None,
        trace: Optional['TraceRecorder'] = None,
    ) -> DualRAGResult:
        """
        Get both relevant venues and events for LLM context.

        Returns separate venue and event sections for clearer LLM prompting.
        """
        import time
        retrieval_start = time.time()

        weights = scoring_weights or ScoringWeights()

        # Resolve location
        effective_lat = user_lat
        effective_lng = user_lng
        resolved_location = None

        if location_id is not None and (effective_lat is None or effective_lng is None):
            try:
                from locations.models import Location
                loc = Location.objects.get(id=location_id)
                effective_lat = float(loc.latitude)
                effective_lng = float(loc.longitude)
                if max_distance_miles is None:
                    max_distance_miles = 10.0
                resolved_location = {'id': loc.id, 'name': str(loc), 'source': 'location_id'}
            except Exception as e:
                logger.warning(f"Location ID {location_id} lookup failed: {e}")

        if effective_lat is None and location:
            try:
                from locations.services import resolve_location
                result = resolve_location(location, default_state=default_state)
                if result.matched_location:
                    effective_lat = float(result.latitude)
                    effective_lng = float(result.longitude)
                    if max_distance_miles is None:
                        max_distance_miles = 10.0
                    resolved_location = {'id': result.matched_location.id, 'name': result.display_name, 'source': 'string_resolution'}
            except Exception as e:
                logger.warning(f"Location resolution failed: {e}")

        # Search venues
        venue_start = time.time()
        venue_results = self.venue_semantic_search(
            query=user_message,
            top_k=max_venues * 2,
            max_distance_miles=max_distance_miles,
            user_lat=effective_lat,
            user_lng=effective_lng,
        )
        venue_ms = int((time.time() - venue_start) * 1000)

        # Search events (use existing tiered method)
        event_result = self.get_context_events_tiered(
            user_message=user_message,
            max_recommended=max_events,
            max_additional=max_additional_events,
            max_context=max_context_events,
            similarity_threshold=event_similarity_threshold,
            scoring_weights=weights,
            location_id=location_id,
            location=location,
            max_distance_miles=max_distance_miles,
            user_lat=effective_lat,
            user_lng=effective_lng,
            default_state=default_state,
            time_filter_days=time_filter_days,
            date_from=date_from,
            date_to=date_to,
            is_virtual=is_virtual,
            trace=trace,
        )

        # Score and rank venues
        ranked_venues: List[RankedVenue] = []
        for venue, similarity_score in venue_results:
            if similarity_score >= venue_similarity_threshold:
                venue_data = self._venue_to_dict(venue, similarity_score)
                tier = 'recommended' if len(ranked_venues) < max_venues else 'additional'
                ranked_venues.append(RankedVenue(venue_data, similarity_score, tier))

        recommended_venues = [v for v in ranked_venues if v.tier == 'recommended']
        additional_venues = [v for v in ranked_venues if v.tier == 'additional']

        total_ms = int((time.time() - retrieval_start) * 1000)

        result = DualRAGResult(
            recommended_venues=recommended_venues,
            recommended_events=event_result.recommended_events,
            additional_venues=additional_venues,
            additional_events=event_result.additional_events,
            context_events=event_result.context_events,
            total_venues_considered=len(venue_results),
            total_events_considered=event_result.total_considered,
            search_metadata={
                'query': user_message,
                'location_used': resolved_location,
                'venue_search_ms': venue_ms,
                'total_search_ms': total_ms,
                'venue_threshold': venue_similarity_threshold,
                'event_threshold': event_similarity_threshold,
            },
        )

        if trace:
            trace.event('dual_retrieval', {
                'query': user_message,
                'venues_found': len(ranked_venues),
                'events_found': len(event_result.all_events),
                'recommended_venues': len(recommended_venues),
                'recommended_events': len(event_result.recommended_events),
            }, latency_ms=total_ms)

        logger.info(f"Dual retrieval: {len(recommended_venues)} venues, {len(event_result.recommended_events)} events")

        return result


# Global RAG service instance
_rag_service = None
_rag_service_warmed_up = False


def get_rag_service(warmup: bool = False) -> EventRAGService:
    """
    Get the global RAG service instance.

    Args:
        warmup: If True, also warm up the model (call this at app startup)
    """
    global _rag_service, _rag_service_warmed_up
    if _rag_service is None:
        _rag_service = EventRAGService()
    if warmup and not _rag_service_warmed_up:
        _rag_service.warmup()
        _rag_service_warmed_up = True
    return _rag_service


def warmup_rag_service():
    """
    Warm up the RAG service by loading the model and running initial inference.
    Call this at application startup to avoid cold-start latency on first request.

    Usage in Django:
        # In apps.py ready() method or a startup signal
        from api.rag_service import warmup_rag_service
        warmup_rag_service()

    Usage in FastAPI:
        # In app startup event
        @app.on_event("startup")
        async def startup():
            warmup_rag_service()
    """
    get_rag_service(warmup=True)