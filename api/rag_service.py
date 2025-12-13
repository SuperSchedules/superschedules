"""
RAG (Retrieval-Augmented Generation) service for semantic event search.
Uses sentence transformers for embeddings and cosine similarity for retrieval.
"""

import json
import logging
import html
from typing import List, Dict, Any, Tuple
from datetime import datetime, timedelta

import numpy as np
from sentence_transformers import SentenceTransformer
from django.conf import settings
from django.utils import timezone
from django.db.models import Q
from pgvector.django import CosineDistance

from events.models import Event

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
        self._load_model()
    
    def _load_model(self):
        """Lazy load the sentence transformer model."""
        if self.model is None:
            logger.info(f"Loading sentence transformer model: {self.model_name}")
            self.model = SentenceTransformer(self.model_name)
    
    def _create_event_text(self, event: Event) -> str:
        """Create searchable text representation of an event."""
        parts = [
            clean_html_content(event.title),
            clean_html_content(event.description or ""),
            clean_html_content(event.get_location_search_text()),  # Use rich location data
        ]
        
        # Add temporal context
        if event.start_time:
            # Add day of week and time info for better matching
            day_name = event.start_time.strftime("%A")
            time_desc = event.start_time.strftime("%I:%M %p")
            parts.append(f"{day_name} {time_desc}")
            
            # Add seasonal/monthly context
            month_name = event.start_time.strftime("%B")
            parts.append(month_name)
        
        return " ".join(filter(None, parts))
    
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
        only_future_events: bool = True
    ) -> List[Tuple[Event, float]]:
        """
        Perform semantic search for events using PostgreSQL vector operations.
        
        Args:
            query: Natural language search query
            top_k: Number of results to return
            time_filter_days: Only include events within this many days from now
            location_filter: Optional location filter
            only_future_events: Only include events that haven't started yet
            
        Returns:
            List of (Event, similarity_score) tuples
        """
        self._load_model()
        
        # Compute query embedding
        query_embedding = self.model.encode([query], convert_to_numpy=True)[0]
        
        # Build the base query with time filtering
        queryset = Event.objects.exclude(embedding__isnull=True)
        
        # Filter out past events by default
        if only_future_events:
            queryset = queryset.filter(start_time__gt=timezone.now())
        
        # Apply time window filter
        if time_filter_days and time_filter_days > 0:
            end_date = timezone.now() + timedelta(days=time_filter_days)
            queryset = queryset.filter(start_time__lte=end_date)
        
        # Apply location filter
        if location_filter:
            queryset = queryset.filter(location__icontains=location_filter)
        
        # Perform vector similarity search using raw SQL (Django ORM CosineDistance has issues)
        from django.db import connection
        
        # Build WHERE conditions for the filters
        where_conditions = ["embedding IS NOT NULL"]
        params = []
        
        if only_future_events:
            where_conditions.append("start_time > %s")
            params.append(timezone.now())
            
        if time_filter_days and time_filter_days > 0:
            end_date = timezone.now() + timedelta(days=time_filter_days)
            where_conditions.append("start_time <= %s")
            params.append(end_date)
            
        if location_filter:
            where_conditions.append("location ILIKE %s")
            params.append(f"%{location_filter}%")
        
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
        max_events: int = 10,
        similarity_threshold: float = 0.3,
        time_filter_days: int = 30
    ) -> List[Dict[str, Any]]:
        """
        Get relevant future events for LLM context based on user message.
        
        Args:
            user_message: User's natural language query
            max_events: Maximum events to return
            similarity_threshold: Minimum similarity score to include
            time_filter_days: Only include events within this many days
            
        Returns:
            List of event dictionaries for LLM context
        """
        try:
            # Extract location from message for filtering
            location_hints = self._extract_location_hints(user_message)
            location_filter = location_hints[0] if location_hints else None
            
            # Perform semantic search with time filtering
            results = self.semantic_search(
                query=user_message,
                top_k=max_events * 2,  # Get more results to filter
                time_filter_days=time_filter_days,
                location_filter=location_filter,
                only_future_events=True  # Only show events that haven't started
            )
            
            # Filter by similarity threshold and format for LLM
            context_events = []
            for event, score in results:
                if score >= similarity_threshold:
                    context_events.append({
                        'id': event.id,
                        'title': clean_html_content(event.title),
                        'description': clean_html_content(event.description),
                        'location': clean_html_content(event.get_location_string()),  # Display location
                        'room_name': event.room_name or '',  # Room within venue
                        'full_address': event.get_full_address(),  # Rich address data
                        'city': event.get_city(),  # Extracted city
                        'organizer': event.organizer,  # Schema.org organizer
                        'event_status': event.event_status,  # Schema.org event status
                        'start_time': event.start_time.isoformat() if event.start_time else None,
                        'end_time': event.end_time.isoformat() if event.end_time else None,
                        'url': event.url,
                        'similarity_score': float(score),
                    })
            
            logger.info(f"Returning {len(context_events)} future context events with scores >= {similarity_threshold}")
            return context_events[:max_events]
            
        except Exception as e:
            logger.error(f"Error in get_context_events: {e}")
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