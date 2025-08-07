from django.contrib import admin
from .models import Source, Event, ServiceToken


@admin.register(Source)
class SourceAdmin(admin.ModelAdmin):
    list_display = ('name', 'base_url', 'status', 'last_crawl')
    fields = ('user', 'name', 'base_url', 'search_method', 'status', 'event', 'last_crawl')
    readonly_fields = ('last_crawl',)


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ('title', 'source', 'start_time', 'location')
    search_fields = ('title', 'description')


@admin.register(ServiceToken)
class ServiceTokenAdmin(admin.ModelAdmin):
    list_display = ("name", "token", "created_at")
    readonly_fields = ("token", "created_at")

