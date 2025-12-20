# Location Data Deployment - Terraform IAC Update

## Overview

The `superschedules` backend now includes a `locations/` app that provides deterministic location resolution for queries like "events near Newton". This requires seeding the database with US Census Gazetteer data (~31K US cities/towns with coordinates and population).

## What Needs to Happen

On first deployment (or when the locations table is empty), the system needs to:

1. Download the Census Gazetteer data file from S3
2. Run `python manage.py import_locations` to populate the `locations_location` table
3. This only needs to run once per environment (the import is idempotent)

## Data Files

### Source Data
- **Census Gazetteer**: https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2023_Gazetteer/2023_Gaz_place_national.zip
  - ~31K US places with GEOID, name, state, lat/lng coordinates
  - File size: ~1.5MB compressed

- **Census Population** (optional but recommended): https://www2.census.gov/programs-surveys/popest/datasets/2020-2023/cities/totals/sub-est2023.csv
  - Population estimates for disambiguation ranking
  - File size: ~2MB

### Recommended S3 Storage
Store these files in an S3 bucket accessible by the Django container:

```
s3://superschedules-data/census/
├── 2023_Gaz_place_national.zip
└── sub-est2023.csv
```

## Management Command

```bash
# Full import with population data
python manage.py import_locations --source=census \
    --file=/tmp/2023_Gaz_place_national.zip \
    --population-file=/tmp/sub-est2023.csv

# Without population data (simpler, still works)
python manage.py import_locations --source=census \
    --file=/tmp/2023_Gaz_place_national.zip

# Download from Census directly (requires internet, slower)
python manage.py import_locations --source=census
```

## Implementation Options

### Option A: User Data Script (Recommended)

Add to `user_data.sh.tftpl` after migrations:

```bash
# Seed location data (only if table is empty)
LOCATION_COUNT=$(python manage.py shell -c "from locations.models import Location; print(Location.objects.count())")
if [ "$LOCATION_COUNT" -eq "0" ]; then
    echo "Seeding location data from S3..."
    aws s3 cp s3://${data_bucket}/census/2023_Gaz_place_national.zip /tmp/
    aws s3 cp s3://${data_bucket}/census/sub-est2023.csv /tmp/
    python manage.py import_locations --source=census \
        --file=/tmp/2023_Gaz_place_national.zip \
        --population-file=/tmp/sub-est2023.csv
    rm /tmp/2023_Gaz_place_national.zip /tmp/sub-est2023.csv
    echo "Location data seeded: $(python manage.py shell -c 'from locations.models import Location; print(Location.objects.count())') locations"
fi
```

### Option B: Dockerfile/Entrypoint

Add an entrypoint script that checks and seeds:

```bash
#!/bin/bash
# entrypoint.sh

# Run migrations
python manage.py migrate --noinput

# Seed locations if empty
python manage.py shell -c "
from locations.models import Location
if Location.objects.count() == 0:
    print('SEED_LOCATIONS=true')
" | grep -q "SEED_LOCATIONS=true" && {
    echo "Seeding location data..."
    # Download from S3 or use bundled file
    python manage.py import_locations --source=census
}

# Start the application
exec "$@"
```

### Option C: Separate Seed Task

Create a one-time ECS task or Lambda that runs after deployment:

```bash
aws ecs run-task \
    --cluster superschedules \
    --task-definition superschedules-seed-locations \
    --overrides '{"containerOverrides":[{"name":"django","command":["python","manage.py","import_locations","--source=census"]}]}'
```

## Terraform Resources Needed

1. **S3 Bucket** (if not existing): Store Census data files
2. **IAM Policy**: Allow Django container to read from S3 bucket
3. **User Data Update**: Add seed script to EC2/ECS startup

### Example IAM Policy Addition

```hcl
resource "aws_iam_role_policy" "census_data_access" {
  name = "census-data-access"
  role = aws_iam_role.django_task_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject"
        ]
        Resource = [
          "arn:aws:s3:::${var.data_bucket}/census/*"
        ]
      }
    ]
  })
}
```

## Verification

After deployment, verify the data was seeded:

```bash
# Check location count (should be ~31,000)
python manage.py shell -c "from locations.models import Location; print(f'Locations: {Location.objects.count()}')"

# Test resolution
python manage.py shell -c "
from locations.services import resolve_location
result = resolve_location('Newton, MA')
print(f'Resolved: {result.display_name} ({result.latitude}, {result.longitude})')
"
```

## Notes

- The import is **idempotent** - running it multiple times won't create duplicates (uses `update_or_create` by GEOID)
- Import takes ~30-60 seconds for full dataset
- Population data is optional but improves disambiguation for common city names (e.g., "Springfield")
- The system falls back to text-based venue matching if location resolution fails, so missing data won't break the app
