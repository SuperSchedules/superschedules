"""
Venue admin configuration with Grappelli styling.
"""

from django.contrib import admin
from django.db.models import Count
from django.urls import reverse
from django.utils.html import format_html

from venues.models import Venue, VenueHours
from venues.geocoding import geocode_venue
from venues.extraction import _clean_street_address


def geolocate_venues(modeladmin, request, queryset):
    """Trigger geocoding for selected venues."""
    success = 0
    skipped = 0
    failed = 0

    for venue in queryset:
        if venue.latitude and venue.longitude:
            skipped += 1
            continue
        if geocode_venue(venue.id):
            success += 1
        else:
            failed += 1

    modeladmin.message_user(request, f"Geocoded: {success}, Skipped (already has coords): {skipped}, Failed: {failed}")
geolocate_venues.short_description = "Geolocate selected venues"


def force_geolocate_venues(modeladmin, request, queryset):
    """Force re-geocoding for selected venues (clears existing coords first)."""
    success = 0
    failed = 0

    for venue in queryset:
        # Clear existing coordinates so geocode_venue will run
        venue.latitude = None
        venue.longitude = None
        venue.save(update_fields=['latitude', 'longitude'])

        if geocode_venue(venue.id):
            success += 1
        else:
            failed += 1

    modeladmin.message_user(request, f"Re-geocoded: {success}, Failed: {failed}")
force_geolocate_venues.short_description = "Force re-geolocate (overwrite existing)"


def cleanup_addresses(modeladmin, request, queryset):
    """Clean up street_address fields that contain full addresses."""
    cleaned = 0
    skipped = 0

    for venue in queryset:
        cleaned_street, extracted_postal = _clean_street_address(
            venue.street_address or "",
            venue.city or "",
            venue.state or "",
            venue.postal_code or ""
        )

        # Check if anything changed
        changed = False
        if cleaned_street != (venue.street_address or ""):
            venue.street_address = cleaned_street
            changed = True
        if extracted_postal and not venue.postal_code:
            venue.postal_code = extracted_postal
            changed = True

        if changed:
            venue.save(update_fields=['street_address', 'postal_code'])
            cleaned += 1
        else:
            skipped += 1

    modeladmin.message_user(request, f"Cleaned: {cleaned}, Already clean: {skipped}")
cleanup_addresses.short_description = "Clean up addresses (remove duplicated city/state/zip)"


def rerun_enrichment(modeladmin, request, queryset):
    """
    Mark selected venues for re-enrichment.

    Clears enrichment data so the collector service will pick them up
    on the next enrichment batch run via /api/v1/venues/needing-enrichment.
    """
    count = queryset.update(
        website_url=None,
        website_url_confidence=None,
        description='',
        kids_summary='',
        enrichment_status='none',
        last_enriched_at=None,
    )
    modeladmin.message_user(request, f"Marked {count} venue(s) for re-enrichment. They will be picked up by the next enrichment batch.")
rerun_enrichment.short_description = "Re-run enrichment (clear and re-queue)"


def rerun_enrichment_keep_website(modeladmin, request, queryset):
    """
    Mark selected venues for re-enrichment, keeping discovered website URL.

    Only clears description/kids_summary so they get regenerated.
    Useful when website URL is correct but descriptions need updating.
    """
    count = queryset.update(
        description='',
        kids_summary='',
        enrichment_status='partial',
        last_enriched_at=None,
    )
    modeladmin.message_user(request, f"Marked {count} venue(s) for description re-generation (keeping website URL).")
rerun_enrichment_keep_website.short_description = "Re-run enrichment (keep website, regenerate descriptions)"


class VenueHoursInline(admin.TabularInline):
    """Inline display of venue hours."""
    model = VenueHours
    extra = 0
    fields = ['day_of_week', 'open_time', 'close_time', 'is_closed', 'notes']
    ordering = ['day_of_week']


class HasGeoFilter(admin.SimpleListFilter):
    """Filter venues by whether they have geocoding."""
    title = 'Has Geocoding'
    parameter_name = 'has_geo'

    def lookups(self, request, model_admin):
        return [('yes', 'Has coordinates'), ('no', 'Missing coordinates')]

    def queryset(self, request, queryset):
        if self.value() == 'yes':
            return queryset.filter(latitude__isnull=False, longitude__isnull=False)
        if self.value() == 'no':
            return queryset.filter(latitude__isnull=True)
        return queryset


class HasWebsiteFilter(admin.SimpleListFilter):
    """Filter venues by whether they have a website."""
    title = 'Has Website'
    parameter_name = 'has_website'

    def lookups(self, request, model_admin):
        return [('yes', 'Has website'), ('no', 'No website')]

    def queryset(self, request, queryset):
        if self.value() == 'yes':
            return queryset.exclude(website_url__isnull=True).exclude(website_url='')
        if self.value() == 'no':
            return queryset.filter(website_url__isnull=True) | queryset.filter(website_url='')
        return queryset


class HasDescriptionFilter(admin.SimpleListFilter):
    """Filter venues by whether they have a description."""
    title = 'Has Description'
    parameter_name = 'has_desc'

    def lookups(self, request, model_admin):
        return [('yes', 'Has description'), ('no', 'No description')]

    def queryset(self, request, queryset):
        if self.value() == 'yes':
            return queryset.exclude(description='')
        if self.value() == 'no':
            return queryset.filter(description='')
        return queryset


@admin.register(Venue)
class VenueAdmin(admin.ModelAdmin):
    """Admin interface for Venue management."""

    list_display = [
        'id', 'name', 'venue_kind_display', 'name_quality_display', 'city', 'state',
        'data_quality_indicator', 'audience_display', 'event_count', 'enrichment_status'
    ]
    list_filter = [
        'venue_name_quality', 'venue_kind', 'enrichment_status', 'audience_primary',
        HasGeoFilter, HasWebsiteFilter, HasDescriptionFilter,
        'state', 'city'
    ]
    search_fields = ['name', 'city', 'state', 'street_address', 'source_domain', 'description']
    readonly_fields = ['slug', 'created_at', 'updated_at', 'last_enriched_at', 'data_quality_indicator']
    ordering = ['-created_at']
    actions = [geolocate_venues, force_geolocate_venues, cleanup_addresses, rerun_enrichment, rerun_enrichment_keep_website]
    inlines = [VenueHoursInline]
    list_per_page = 50

    fieldsets = (
        ('Venue Identity', {
            'fields': ('name', 'slug', 'website_url', 'canonical_url')
        }),
        ('Classification', {
            'fields': ('venue_kind', 'venue_kind_confidence', 'venue_name_quality'),
        }),
        ('Address', {
            'fields': ('street_address', 'city', 'state', 'postal_code', 'country')
        }),
        ('Geocoding', {
            'fields': ('latitude', 'longitude'),
        }),
        ('Audience', {
            'fields': ('audience_age_groups', 'audience_tags', 'audience_min_age', 'audience_primary'),
        }),
        ('Content', {
            'fields': ('description', 'kids_summary'),
        }),
        ('Enrichment Metadata', {
            'fields': ('enrichment_status', 'last_enriched_at', 'website_url_confidence'),
            'classes': ('collapse',)
        }),
        ('Source Tracking', {
            'fields': ('source_domain', 'raw_schema'),
            'classes': ('collapse',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

    def get_queryset(self, request):
        """Annotate queryset with event count."""
        queryset = super().get_queryset(request)
        return queryset.annotate(_event_count=Count('events'))

    def event_count(self, obj):
        """Display number of events at this venue as a link to the events list."""
        count = getattr(obj, '_event_count', obj.events.count())
        if count == 0:
            return '0'
        url = reverse('admin:events_event_changelist') + f'?venue__id__exact={obj.id}'
        return format_html('<a href="{}">{}</a>', url, count)
    event_count.short_description = 'Events'
    event_count.admin_order_field = '_event_count'

    def venue_kind_display(self, obj):
        """Display venue kind with icon."""
        if not obj.venue_kind or obj.venue_kind == 'unknown':
            return '-'
        icons = {
            'library': '📚', 'museum': '🏛️', 'park': '🌳', 'playground': '🎪',
            'beach': '🏖️', 'skating_rink': '⛸️', 'dog_park': '🐕', 'pool': '🏊',
            'school': '🏫', 'community_center': '🏢', 'church': '⛪', 'theater': '🎭',
            'restaurant': '🍽️', 'senior_center': '👴', 'ymca': '🏋️', 'sports_facility': '⚽',
            'nature_center': '🦋', 'zoo': '🦁', 'aquarium': '🐠',
        }
        icon = icons.get(obj.venue_kind, '📍')
        return f"{icon} {obj.get_venue_kind_display()}"
    venue_kind_display.short_description = 'Kind'
    venue_kind_display.admin_order_field = 'venue_kind'

    def name_quality_display(self, obj):
        """Display name quality with color coding."""
        if not obj.venue_name_quality:
            return '-'
        colors = {
            'good': '#28a745',
            'address_only': '#ffc107',
            'empty': '#dc3545',
            'placeholder': '#dc3545',
            'unknown': '#6c757d',
        }
        color = colors.get(obj.venue_name_quality, '#6c757d')
        label = obj.get_venue_name_quality_display()
        return format_html('<span style="color: {}; font-weight: bold;">{}</span>', color, label)
    name_quality_display.short_description = 'Name Quality'
    name_quality_display.admin_order_field = 'venue_name_quality'

    def data_quality_indicator(self, obj):
        """Show data quality indicators: geo, website, description."""
        indicators = []

        # Geocoding
        if obj.latitude and obj.longitude:
            indicators.append('<span title="Has coordinates" style="color: #28a745;">📍</span>')
        else:
            indicators.append('<span title="Missing coordinates" style="color: #dc3545;">📍</span>')

        # Website
        if obj.website_url:
            indicators.append('<span title="Has website" style="color: #28a745;">🌐</span>')
        else:
            indicators.append('<span title="No website" style="color: #999;">🌐</span>')

        # Description
        if obj.description:
            indicators.append('<span title="Has description" style="color: #28a745;">📝</span>')
        else:
            indicators.append('<span title="No description" style="color: #999;">📝</span>')

        # Kids summary
        if obj.kids_summary:
            indicators.append('<span title="Has kids summary" style="color: #28a745;">👶</span>')
        else:
            indicators.append('<span title="No kids summary" style="color: #999;">👶</span>')

        return format_html(' '.join(indicators))
    data_quality_indicator.short_description = 'Data'

    def audience_display(self, obj):
        """Display audience info compactly."""
        parts = []
        if obj.audience_primary and obj.audience_primary != 'general':
            parts.append(obj.get_audience_primary_display())
        if obj.audience_tags:
            # Show first 2 tags
            tags = obj.audience_tags[:2]
            parts.extend(tags)
        if parts:
            return ', '.join(parts)
        return '-'
    audience_display.short_description = 'Audience'


@admin.register(VenueHours)
class VenueHoursAdmin(admin.ModelAdmin):
    """Admin for viewing/managing venue hours."""
    list_display = ['venue', 'day_of_week', 'open_time', 'close_time', 'is_closed']
    list_filter = ['day_of_week', 'is_closed']
    search_fields = ['venue__name']
    ordering = ['venue', 'day_of_week']
