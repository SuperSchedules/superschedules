# Generated manually for scraper optimization tracking

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0022_add_scrape_history_remove_scrape_batch'),
    ]

    operations = [
        migrations.AddField(
            model_name='scrapehistory',
            name='last_successful_scraper',
            field=models.CharField(
                blank=True,
                default='',
                help_text="Last extraction method that worked (e.g., 'jsonld', 'localist', 'llm')",
                max_length=50
            ),
        ),
        migrations.AddField(
            model_name='scrapehistory',
            name='last_scraper_updated_at',
            field=models.DateTimeField(
                blank=True,
                help_text='When the last_successful_scraper was last updated',
                null=True
            ),
        ),
    ]
