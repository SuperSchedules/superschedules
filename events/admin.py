from django.contrib import admin
from .models import Source, Event

@admin.register(Source)
class SourceAdmin(admin.ModelAdmin):
    list_display = ('name', 'base_url', 'last_crawl')

@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ('title', 'source', 'start_time', 'location')
    search_fields = ('title', 'description')
