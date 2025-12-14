"""
Management command to set up Celery Beat periodic tasks.

Run this after migrations to configure scheduled tasks.
"""

from django.core.management.base import BaseCommand
from django_celery_beat.models import PeriodicTask, IntervalSchedule, CrontabSchedule
import json


class Command(BaseCommand):
    help = 'Set up Celery Beat periodic tasks for the application'

    def handle(self, *args, **options):
        self.stdout.write('Setting up Celery Beat periodic tasks...')

        # Create schedules
        hourly, _ = IntervalSchedule.objects.get_or_create(every=1, period=IntervalSchedule.HOURS)
        six_hours, _ = IntervalSchedule.objects.get_or_create(every=6, period=IntervalSchedule.HOURS)

        midnight, _ = CrontabSchedule.objects.get_or_create(minute='0', hour='0', day_of_week='*', day_of_month='*', month_of_year='*')
        sunday_2am, _ = CrontabSchedule.objects.get_or_create(minute='0', hour='2', day_of_week='0', day_of_month='*', month_of_year='*')
        daily_3am, _ = CrontabSchedule.objects.get_or_create(minute='0', hour='3', day_of_week='*', day_of_month='*', month_of_year='*')

        # Define tasks
        tasks = [
            {
                'name': 'Generate daily stats',
                'task': 'events.tasks.generate_daily_stats',
                'crontab': midnight,
                'description': 'Generate daily system statistics at midnight',
            },
            {
                'name': 'Cleanup old events',
                'task': 'events.tasks.cleanup_old_events',
                'crontab': sunday_2am,
                'kwargs': json.dumps({'days': 90}),
                'description': 'Delete events older than 90 days every Sunday at 2 AM',
            },
            {
                'name': 'Cleanup old scraping jobs',
                'task': 'events.tasks.cleanup_old_scraping_jobs',
                'crontab': daily_3am,
                'kwargs': json.dumps({'days': 30}),
                'description': 'Delete completed/failed jobs older than 30 days at 3 AM',
            },
            {
                'name': 'Bulk generate missing embeddings',
                'task': 'events.tasks.bulk_generate_embeddings',
                'interval': six_hours,
                'description': 'Generate embeddings for events missing them every 6 hours',
            },
            {
                'name': 'Bulk geocode venues',
                'task': 'venues.tasks.bulk_geocode_venues',
                'interval': hourly,
                'kwargs': json.dumps({'limit': 100}),
                'description': 'Geocode venues missing coordinates every hour',
            },
        ]

        created_count = 0
        updated_count = 0

        for task_config in tasks:
            name = task_config['name']
            defaults = {
                'task': task_config['task'],
                'enabled': True,
                'description': task_config.get('description', ''),
                'kwargs': task_config.get('kwargs', '{}'),
            }

            if 'crontab' in task_config:
                defaults['crontab'] = task_config['crontab']
                defaults['interval'] = None
            elif 'interval' in task_config:
                defaults['interval'] = task_config['interval']
                defaults['crontab'] = None

            task, created = PeriodicTask.objects.update_or_create(name=name, defaults=defaults)

            if created:
                created_count += 1
                self.stdout.write(self.style.SUCCESS(f'  Created: {name}'))
            else:
                updated_count += 1
                self.stdout.write(f'  Updated: {name}')

        self.stdout.write(self.style.SUCCESS(f'\nDone! Created {created_count}, updated {updated_count} periodic tasks.'))
        self.stdout.write('\nConfigured tasks:')
        for task in PeriodicTask.objects.filter(enabled=True):
            schedule = task.crontab or task.interval
            self.stdout.write(f'  - {task.name}: {task.task} ({schedule})')
