"""
Custom Django Admin configuration to display build info in header and add custom admin views.
"""
import time
from django.contrib import admin
from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import render

# Import build info (will be generated during Docker build)
try:
    from build_info import BUILD_TIME, GIT_COMMIT
except ImportError:
    BUILD_TIME = "unknown"
    GIT_COMMIT = "unknown"


# Modify the existing admin site's properties (don't replace it)
commit_short = GIT_COMMIT[:7] if GIT_COMMIT != 'unknown' else 'unknown'
admin.site.site_header = f"EventZombie Admin | Built: {BUILD_TIME} ({commit_short})"
admin.site.site_title = "EventZombie Admin"
admin.site.index_title = "Welcome to EventZombie Administration"


@staff_member_required
def rag_tester_view(request):
    """Admin view to test RAG queries and see full retrieval results."""
    from api.rag_service import get_rag_service
    from events.models import Event
    from locations.services import resolve_location

    context = {
        'title': 'RAG Tester',
        'query': '',
        'results': [],
        'total_events': Event.objects.exclude(embedding__isnull=True).count(),
    }

    if request.method == 'POST':
        query = request.POST.get('query', '').strip()
        limit = int(request.POST.get('limit', 10))
        threshold = float(request.POST.get('threshold', 0.3))
        time_filter = int(request.POST.get('time_filter', 30))
        location = request.POST.get('location', '').strip() or None
        radius = request.POST.get('radius', '').strip()
        radius_miles = float(radius) if radius else None

        context['query'] = query
        context['limit'] = limit
        context['threshold'] = threshold
        context['time_filter'] = time_filter
        context['location'] = location
        context['radius'] = radius

        if query:
            start_time = time.time()
            rag_service = get_rag_service()

            # Extract location hints for debug display
            location_hints = rag_service._extract_location_hints(query)
            location_query = location or (location_hints[0] if location_hints else None)
            context['location_extracted'] = ', '.join(location_hints) if location_hints else None

            # Resolve location for debug display
            resolved_location = None
            if location_query:
                try:
                    resolved_location = resolve_location(location_query)
                except Exception:
                    pass

            if resolved_location and resolved_location.matched_location:
                context['resolved_location'] = {
                    'query': location_query,
                    'matched': str(resolved_location.matched_location),
                    'lat': float(resolved_location.latitude),
                    'lng': float(resolved_location.longitude),
                    'confidence': resolved_location.confidence,
                    'is_ambiguous': resolved_location.is_ambiguous,
                    'alternatives': [str(alt) for alt in resolved_location.alternatives] if resolved_location.alternatives else [],
                }
            else:
                context['resolved_location'] = None

            # Get context events with all the debug info
            results = rag_service.get_context_events(
                user_message=query,
                max_events=limit,
                similarity_threshold=threshold,
                time_filter_days=time_filter,
                location=location,
                max_distance_miles=radius_miles,
            )

            search_time_ms = (time.time() - start_time) * 1000
            context['search_time_ms'] = search_time_ms
            context['results'] = results

            # Show what text would be embedded for the first result
            if results:
                first_event = Event.objects.get(id=results[0]['id'])
                context['embedding_text'] = rag_service._create_event_text(first_event)

    return render(request, 'admin/rag_tester.html', context)
