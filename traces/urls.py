"""
URL configuration for traces app.

These URLs are mounted under /admin/traces/ for staff-only access.
"""

from django.urls import path
from . import views

app_name = 'traces'

urlpatterns = [
    path('runs/recent/', views.get_recent_runs, name='recent_runs'),
    path('run/create/', views.create_debug_run, name='create_run'),
    path('run/<uuid:run_id>/stream/', views.stream_debug_run, name='stream_run'),
    path('run/<uuid:run_id>/events/', views.get_run_events, name='get_events'),
    # Comparison endpoints
    path('run/compare/', views.compare_runs_view, name='compare_runs'),
    path('run/create-variant/', views.create_variant_run, name='create_variant_run'),
]
