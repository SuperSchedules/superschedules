from django.contrib import admin
from django.contrib import messages
from django.conf import settings
import requests
import logging
from .models import Source, Event, ServiceToken, SiteStrategy, ScrapingJob, ScrapeBatch

logger = logging.getLogger(__name__)


def process_sources_via_collector(modeladmin, request, queryset):
    """Admin action to process selected sources via collector API."""
    collector_url = getattr(settings, 'COLLECTOR_URL', 'http://localhost:8001')
    
    results = []
    for source in queryset:
        try:
            # Call collector API to extract events
            response = requests.post(
                f"{collector_url}/extract",
                json={
                    "url": source.base_url,
                    "extraction_hints": {
                        "content_selectors": source.site_strategy.best_selectors if source.site_strategy else None,
                        "additional_hints": {}
                    }
                },
                timeout=180  # Allow time for iframe + calendar pagination
            )
            
            if response.status_code == 200:
                data = response.json()
                if data.get('success') and data.get('events'):
                    event_count = len(data['events'])
                    # Process and save events (reusing logic from api/views.py)
                    from events.models import Event
                    
                    for event_data in data['events']:
                        try:
                            # Use Schema.org aware event creation
                            Event.create_with_schema_org_data(event_data, source)
                        except Exception as e:
                            logger.error("Failed to save event: %s", e)
                    
                    # Update source status and last run time
                    from django.utils import timezone
                    source.status = 'active'
                    source.last_run_at = timezone.now()
                    source.save()
                    
                    results.append(f"✅ {source.name}: {event_count} events collected")
                else:
                    results.append(f"⚠️ {source.name}: No events found")
            else:
                results.append(f"❌ {source.name}: API error ({response.status_code})")
                
        except requests.exceptions.RequestException as e:
            results.append(f"❌ {source.name}: Connection failed - {str(e)}")
        except Exception as e:
            results.append(f"❌ {source.name}: Processing failed - {str(e)}")
    
    # Display results to admin user
    if results:
        messages.success(request, "Processing complete:\n" + "\n".join(results))
    else:
        messages.warning(request, "No sources were processed.")

process_sources_via_collector.short_description = "Process selected sources via collector API"


@admin.register(Source)
class SourceAdmin(admin.ModelAdmin):
    list_display = (
        'name',
        'base_url',
        'site_strategy',
        'status',
        'last_run_at',
        'date_added',
    )
    fields = (
        'user',
        'name',
        'base_url',
        'site_strategy',
        'search_method',
        'status',
        'event',
        'last_run_at',
        'date_added',
    )
    readonly_fields = ('last_run_at', 'date_added')
    actions = [process_sources_via_collector]
    list_filter = ('status', 'site_strategy', 'search_method')
    search_fields = ('name', 'base_url')


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ('title', 'source', 'start_time', 'get_location_display', 'organizer', 'event_status')
    search_fields = ('title', 'description', 'organizer')
    list_filter = ('source', 'event_status', 'event_attendance_mode', 'start_time')
    readonly_fields = ('external_id', 'created_at', 'updated_at')
    
    def get_location_display(self, obj):
        """Display location with Place info if available."""
        if obj.place:
            return f"{obj.location} ({obj.place.name})" if obj.location != obj.place.name else obj.place.name
        return obj.location
    get_location_display.short_description = 'Location'


@admin.register(ServiceToken)
class ServiceTokenAdmin(admin.ModelAdmin):
    list_display = ("name", "token", "created_at")
    readonly_fields = ("token", "created_at")


@admin.register(SiteStrategy)
class SiteStrategyAdmin(admin.ModelAdmin):
    list_display = ("domain", "success_rate", "updated_at")
    search_fields = ("domain",)


@admin.register(ScrapingJob)
class ScrapingJobAdmin(admin.ModelAdmin):
    list_display = ("url", "status", "events_found", "created_at")
    search_fields = ("url", "domain")
    list_filter = ("status",)


@admin.register(ScrapeBatch)
class ScrapeBatchAdmin(admin.ModelAdmin):
    list_display = ("id", "submitted_by", "created_at")

