"""
Import locations from US Census Gazetteer data.

Usage:
    python manage.py import_locations --source=census
    python manage.py import_locations --source=census --file=/path/to/file.txt
    python manage.py import_locations --dry-run --limit=100
    python manage.py import_locations --state=MA

Data sources:
    Census Gazetteer: https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2023_Gazetteer/2023_Gaz_place_national.zip
    Population data: https://www2.census.gov/programs-surveys/popest/datasets/2020-2023/cities/totals/sub-est2023.csv

Production deployment:
    Store the Census files on S3 and use --file to import from a downloaded copy.
    The superschedules_IAC terraform should include a step to seed the database
    after deployment by downloading from S3 and running this command.
"""

import csv
import io
import logging
import zipfile
from decimal import Decimal
from pathlib import Path
from typing import Optional
from urllib.request import urlopen, Request

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from locations.models import Location, normalize_for_matching

logger = logging.getLogger(__name__)

# Census Gazetteer URLs
CENSUS_GAZETTEER_URL = "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2023_Gazetteer/2023_Gaz_place_national.zip"
CENSUS_POPULATION_URL = "https://www2.census.gov/programs-surveys/popest/datasets/2020-2023/cities/totals/sub-est2023.csv"


class Command(BaseCommand):
    help = 'Import locations from US Census Gazetteer with optional population data'

    def add_arguments(self, parser):
        parser.add_argument(
            '--source',
            choices=['census'],
            default='census',
            help='Data source (default: census)',
        )
        parser.add_argument(
            '--file',
            type=str,
            help='Local file path for gazetteer (instead of downloading)',
        )
        parser.add_argument(
            '--population-file',
            type=str,
            help='Local file path for population data (instead of downloading)',
        )
        parser.add_argument(
            '--skip-population',
            action='store_true',
            help='Skip population data merge',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Parse file without saving to database',
        )
        parser.add_argument(
            '--limit',
            type=int,
            help='Limit number of records to import',
        )
        parser.add_argument(
            '--state',
            type=str,
            help='Only import locations for this state (e.g., MA)',
        )

    def handle(self, *args, **options):
        source = options['source']

        if source == 'census':
            self._import_census(options)
        else:
            raise CommandError(f"Source '{source}' not yet implemented")

    def _import_census(self, options):
        """Import from US Census Gazetteer with optional population data."""
        file_path = options.get('file')
        population_file = options.get('population_file')
        skip_population = options.get('skip_population', False)
        dry_run = options.get('dry_run', False)
        limit = options.get('limit')
        state_filter = options.get('state')

        # Step 1: Load gazetteer data
        if file_path:
            self.stdout.write(f"Reading gazetteer from local file: {file_path}")
            gazetteer_content = self._read_local_file(file_path)
        else:
            self.stdout.write(f"Downloading gazetteer from Census Bureau...")
            gazetteer_content = self._download_census_file(CENSUS_GAZETTEER_URL)

        # Step 2: Load population data (optional)
        population_map = {}
        if not skip_population:
            try:
                if population_file:
                    self.stdout.write(f"Reading population from local file: {population_file}")
                    pop_content = Path(population_file).read_text()
                else:
                    self.stdout.write(f"Downloading population data from Census Bureau...")
                    pop_content = self._download_population_file()
                population_map = self._parse_population_data(pop_content)
                self.stdout.write(f"Loaded population data for {len(population_map)} places")
            except Exception as e:
                self.stdout.write(self.style.WARNING(f"Could not load population data: {e}. Continuing without it."))

        # Step 3: Parse and import gazetteer records
        created = 0
        updated = 0
        skipped = 0
        errors = 0

        # Strip whitespace from header names (Census files have trailing spaces)
        lines = gazetteer_content.split('\n')
        if lines:
            lines[0] = '\t'.join(field.strip() for field in lines[0].split('\t'))
        cleaned_content = '\n'.join(lines)

        reader = csv.DictReader(io.StringIO(cleaned_content), delimiter='\t')

        locations_to_create = []
        locations_to_update = []

        for i, row in enumerate(reader):
            if limit and i >= limit:
                break

            state = row.get('USPS', '').strip()

            if state_filter and state.upper() != state_filter.upper():
                continue

            try:
                location_data = self._parse_census_row(row)
            except ValueError as e:
                logger.warning(f"Skipping row {i}: {e}")
                errors += 1
                continue

            # Look up population by GEOID
            geoid = location_data['geoid']
            if geoid in population_map:
                location_data['population'] = population_map[geoid]

            if dry_run:
                pop_display = location_data.get('population', 'N/A')
                self.stdout.write(f"  [DRY RUN] {location_data['name']}, {location_data['state']} (pop: {pop_display})")
                continue

            # Check if exists
            try:
                existing = Location.objects.get(geoid=geoid)
                # Update existing
                for key, value in location_data.items():
                    setattr(existing, key, value)
                locations_to_update.append(existing)
                updated += 1
            except Location.DoesNotExist:
                # Create new
                locations_to_create.append(Location(**location_data))
                created += 1

        if not dry_run:
            # Bulk create new locations
            if locations_to_create:
                with transaction.atomic():
                    Location.objects.bulk_create(locations_to_create, batch_size=500)

            # Bulk update existing locations
            if locations_to_update:
                with transaction.atomic():
                    Location.objects.bulk_update(
                        locations_to_update,
                        ['name', 'normalized_name', 'state', 'latitude', 'longitude', 'lsad', 'land_area_sqmi', 'population'],
                        batch_size=500
                    )

        self.stdout.write(
            self.style.SUCCESS(
                f"Import complete: {created} created, {updated} updated, {skipped} skipped, {errors} errors"
            )
        )

    def _parse_census_row(self, row: dict) -> dict:
        """Parse a row from Census Gazetteer file."""
        name = row.get('NAME', '').strip()
        if not name:
            raise ValueError("Missing NAME field")

        state = row.get('USPS', '').strip()
        if not state or len(state) != 2:
            raise ValueError(f"Invalid state: {state}")

        geoid = row.get('GEOID', '').strip()
        if not geoid:
            raise ValueError("Missing GEOID")

        # Parse coordinates
        try:
            lat_str = row.get('INTPTLAT', '').strip()
            lng_str = row.get('INTPTLONG', '').strip()
            lat = Decimal(lat_str)
            lng = Decimal(lng_str)
        except Exception as e:
            raise ValueError(f"Invalid coordinates: {e}")

        # Parse optional area
        land_area = None
        if area_str := row.get('ALAND_SQMI', '').strip():
            try:
                land_area = Decimal(area_str)
            except ValueError:
                pass

        return {
            'geoid': geoid,
            'name': name,
            'normalized_name': normalize_for_matching(name),
            'state': state,
            'country_code': 'US',
            'latitude': lat,
            'longitude': lng,
            'lsad': row.get('LSAD', '').strip(),
            'land_area_sqmi': land_area,
        }

    def _parse_population_data(self, content: str) -> dict:
        """Parse Census population estimates CSV, return {geoid: population}."""
        population_map = {}

        reader = csv.DictReader(io.StringIO(content))

        for row in reader:
            # Build GEOID from state and place FIPS
            state_fips = row.get('STATE', '').strip().zfill(2)
            place_fips = row.get('PLACE', '').strip().zfill(5)

            if not state_fips or not place_fips or place_fips == '00000':
                continue

            geoid = f"{state_fips}{place_fips}"

            # Use most recent population estimate (POPESTIMATE2023)
            pop_str = row.get('POPESTIMATE2023', '') or row.get('POPESTIMATE2022', '') or row.get('POPESTIMATE', '')
            if pop_str:
                try:
                    population_map[geoid] = int(pop_str)
                except ValueError:
                    pass

        return population_map

    def _download_census_file(self, url: str) -> str:
        """Download and extract Census Gazetteer file."""
        request = Request(url, headers={'User-Agent': 'superschedules-import/1.0'})
        with urlopen(request, timeout=60) as response:
            zip_data = response.read()

        with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
            for name in zf.namelist():
                if name.endswith('.txt'):
                    with zf.open(name) as f:
                        return f.read().decode('utf-8')

        raise CommandError("No text file found in Census ZIP")

    def _download_population_file(self) -> str:
        """Download Census population estimates CSV."""
        request = Request(CENSUS_POPULATION_URL, headers={'User-Agent': 'superschedules-import/1.0'})
        with urlopen(request, timeout=60) as response:
            return response.read().decode('latin-1')

    def _read_local_file(self, file_path: str) -> str:
        """Read from local file (zip or txt)."""
        path = Path(file_path)

        if path.suffix == '.zip':
            with zipfile.ZipFile(path) as zf:
                for name in zf.namelist():
                    if name.endswith('.txt'):
                        with zf.open(name) as f:
                            return f.read().decode('utf-8')
            raise CommandError("No text file found in ZIP")

        return path.read_text()
