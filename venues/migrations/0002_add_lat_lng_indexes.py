"""Add latitude/longitude indexes for bounding box geo-queries.

These indexes enable fast initial filtering before applying
the more expensive Haversine distance calculation.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('venues', '0001_initial'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='venue',
            index=models.Index(fields=['latitude', 'longitude'], name='venue_lat_lng_idx'),
        ),
        migrations.AddIndex(
            model_name='venue',
            index=models.Index(fields=['latitude'], name='venue_lat_idx'),
        ),
        migrations.AddIndex(
            model_name='venue',
            index=models.Index(fields=['longitude'], name='venue_lng_idx'),
        ),
    ]
