from django.contrib import admin, messages
from django.utils import timezone
from .models import Source, Event
from .scraper import scrape_events_for_query

@admin.register(Source)
class SourceAdmin(admin.ModelAdmin):
    list_display = ('name', 'base_url', 'last_crawl')
    fields = ('name', 'base_url', 'search_query')

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if obj.search_query:
            events = scrape_events_for_query(obj.search_query)
            imported = 0
            for ev in events:
                _, created = Event.objects.get_or_create(
                    source=obj,
                    external_id=ev["uid"],
                    defaults=dict(
                        title=ev["title"],
                        description=ev["description"],
                        location=ev["location"],
                        start_time=ev["start_time"],
                        end_time=ev["end_time"],
                        url=ev["url"],
                    ),
                )
                if created:
                    imported += 1
            obj.last_crawl = timezone.now()
            obj.save()
            messages.success(request, f"Imported {imported} events")

@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ('title', 'source', 'start_time', 'location')
    search_fields = ('title', 'description')
