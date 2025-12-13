from django.apps import AppConfig


class VenuesConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'venues'

    def ready(self):
        from django.db.models.signals import post_save
        from venues.models import Venue
        from venues.signals import venue_post_save

        post_save.connect(venue_post_save, sender=Venue)
