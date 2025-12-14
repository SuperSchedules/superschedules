"""
Custom Django Admin configuration to display build info in header.
"""
from django.contrib import admin

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
