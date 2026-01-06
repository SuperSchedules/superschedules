"""
RAG (Retrieval-Augmented Generation) service for semantic event search.
Uses sentence transformers for embeddings and cosine similarity for retrieval.
"""

import json
import logging
import html
from typing import List, Dict, Any, Tuple, Optional, TYPE_CHECKING
from datetime import datetime, timedelta

import numpy as np
from django.conf import settings
from django.utils import timezone
from django.db.models import Q
from pgvector.django import CosineDistance

from events.models import Event
# NOTE: sentence_transformers and dateparser are lazy-imported to reduce startup memory
# See _load_model() and get_context_events() for the lazy imports

if TYPE_CHECKING:
    from traces.recorder import TraceRecorder

logger = logging.getLogger(__name__)


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
    
    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        """Initialize with a compact, fast sentence transformer model."""
        self.model_name = model_name
        self.model = None
        # Model is lazy-loaded on first use to avoid OOM at startup
    
    def _load_model(self):
        """Lazy load the sentence transformer model."""
        if self.model is None:
            from sentence_transformers import SentenceTransformer
            logger.info(f"Loading sentence transformer model: {self.model_name}")
            self.model = SentenceTransformer(self.model_name)
    
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
    
    def update_event_embeddings(self, event_ids: List[int] = None):
        """Update embeddings for specified events or all events."""
        self._load_model()
        
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
        new_embeddings = self.model.encode(texts, convert_to_numpy=True)
        
        # Update database with new embeddings
        for event, embedding in zip(event_list, new_embeddings):
            event.embedding = embedding.tolist()  # Convert numpy array to list for pgvector
            event.save(update_fields=['embedding'])
        
        logger.info(f"Updated embeddings for {len(event_list)} events")
    
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
        self._load_model()

        # Compute query embedding
        query_embedding = self.model.encode([query], convert_to_numpy=True)[0]

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
        query_embedding_list = query_embedding.tolist()
        
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
        
        # Convert SQL results back to Event objects with similarity scores
        event_ids = [row[0] for row in sql_results]
        similarity_scores = {row[0]: row[1] for row in sql_results}
        
        # Get Event objects in the same order
        events = Event.objects.filter(id__in=event_ids)
        event_dict = {event.id: event for event in events}
        
        event_similarity_pairs = [
            (event_dict[event_id], similarity_scores[event_id]) 
            for event_id in event_ids if event_id in event_dict
        ]
        
        logger.info(f"Found {len(event_similarity_pairs)} semantic matches for query: '{query}'")
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
            if date_from is None and date_to is None:
                from api.date_extraction import extract_dates_from_query
                date_extraction_result = extract_dates_from_query(user_message, timezone.localtime(timezone.now()))
                if date_extraction_result.date_from and date_extraction_result.confidence >= 0.5:
                    date_from = timezone.make_aware(date_extraction_result.date_from)
                    date_to = timezone.make_aware(date_extraction_result.date_to)
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


# Global RAG service instance
_rag_service = None


def get_rag_service() -> EventRAGService:
    """Get the global RAG service instance."""
    global _rag_service
    if _rag_service is None:
        _rag_service = EventRAGService()
    return _rag_service