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


class BuildInfoAdminSite(admin.AdminSite):
    """Custom admin site that shows build info in the header."""

    site_header = f"EventZombie Admin | Built: {BUILD_TIME} ({GIT_COMMIT[:7] if GIT_COMMIT != 'unknown' else 'unknown'})"
    site_title = "EventZombie Admin"
    index_title = "Welcome to EventZombie Administration"


# Replace the default admin site
admin.site = BuildInfoAdminSite()
admin.sites.site = admin.site
