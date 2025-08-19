from django.contrib import admin
from .models import Source, Event, ServiceToken, SiteStrategy, ScrapingJob, ScrapeBatch


@admin.register(Source)
class SourceAdmin(admin.ModelAdmin):
    list_display = (
        'name',
        'base_url',
        'status',
        'last_run_at',
        'date_added',
    )
    fields = (
        'user',
        'name',
        'base_url',
        'search_method',
        'status',
        'event',
        'last_run_at',
        'date_added',
    )
    readonly_fields = ('last_run_at', 'date_added')


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ('title', 'source', 'start_time', 'location')
    search_fields = ('title', 'description')


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

