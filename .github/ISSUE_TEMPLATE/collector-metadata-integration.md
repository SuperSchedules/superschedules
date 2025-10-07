# Integrate Enhanced Collector Metadata

## Summary
The collector API now returns comprehensive extraction metadata that the Django backend should consume and store to improve extraction intelligence, track source quality, and identify unparseable sites.

## Background
The collector has been enhanced (see `superschedules_collector` branch `feature/enhanced-extraction-visibility`) to return detailed metadata about each extraction attempt, including:
- Extraction method used (JSON-LD, LLM, or failed)
- Performance timing for each stage
- Confidence scores from LLM validation
- Failure snapshots and error details

## Current State
The Django backend currently receives:
```python
{
  "success": bool,
  "events": [...],
  "metadata": {
    "extraction_method": str,
    "page_title": str,
    "total_found": int
  },
  "processing_time_seconds": float
}
```

## New Collector Response Format
```python
{
  "success": bool,
  "events": [...],
  "metadata": {
    # Basic info
    "extraction_method": str,  # "jsonld" | "llm" | "failed"
    "page_title": str,
    "total_found": int,

    # Extraction intelligence (NEW)
    "parseable": bool,  # False if extraction_method == "failed"
    "confidence_score": float,  # 0.0-1.0 average from validation

    # Method-specific details (NEW)
    "jsonld_found": bool,
    "jsonld_event_count": int,
    "llm_attempted": bool,
    "llm_event_count": int,

    # Performance metrics (NEW)
    "jsonld_time_seconds": float | null,
    "llm_time_seconds": float | null,
    "validation_time_seconds": float | null,

    # Future optimization (NEW)
    "recommended_hints": dict | null,  # CSS selectors that worked
    "iframe_url": str | null,

    # Failure details (NEW)
    "failure_reason": str | null,
    "snapshot_path": str | null  # Local path on collector server
  },
  "processing_time_seconds": float
}
```

## Required Changes

### 1. Update SiteStrategy Model
**File:** `events/models.py`

Add fields to track extraction intelligence:
```python
class SiteStrategy(models.Model):
    domain = models.CharField(max_length=255, unique=True)

    # Extraction strategy (learned from collector)
    best_method = models.CharField(
        max_length=20,
        choices=[('jsonld', 'JSON-LD'), ('llm', 'LLM'), ('unknown', 'Unknown')],
        default='unknown'
    )
    best_selectors = models.JSONField(null=True, blank=True)
    iframe_url_pattern = models.CharField(max_length=255, null=True, blank=True)

    # Success tracking (NEW)
    parseable = models.BooleanField(default=True)
    last_success = models.DateTimeField(null=True, blank=True)
    last_failure = models.DateTimeField(null=True, blank=True)
    success_rate = models.FloatField(default=1.0)  # Rolling average
    avg_confidence = models.FloatField(default=0.0)

    # Performance stats (NEW)
    avg_extraction_time = models.FloatField(default=0.0)  # seconds
    total_extractions = models.IntegerField(default=0)
    total_events_found = models.IntegerField(default=0)

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def update_from_extraction(self, metadata: dict) -> None:
        """Update strategy based on extraction metadata."""
        # Implementation details below
        pass
```

### 2. Create ExtractionAttempt Model (Optional but Recommended)
**File:** `events/models.py`

Track individual extraction attempts for analytics:
```python
class ExtractionAttempt(models.Model):
    """Historical record of each extraction attempt for analytics."""
    source = models.ForeignKey('Source', on_delete=models.CASCADE, related_name='extraction_attempts')

    # Results
    success = models.BooleanField()
    extraction_method = models.CharField(max_length=20)  # jsonld, llm, failed
    parseable = models.BooleanField()
    event_count = models.IntegerField(default=0)
    confidence_score = models.FloatField(default=0.0)

    # Timing
    total_time_seconds = models.FloatField()
    jsonld_time_seconds = models.FloatField(null=True, blank=True)
    llm_time_seconds = models.FloatField(null=True, blank=True)
    validation_time_seconds = models.FloatField(null=True, blank=True)

    # Failure tracking
    failure_reason = models.TextField(null=True, blank=True)
    snapshot_available = models.BooleanField(default=False)

    # Metadata
    attempted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-attempted_at']
        indexes = [
            models.Index(fields=['source', '-attempted_at']),
            models.Index(fields=['parseable', '-attempted_at']),
        ]
```

### 3. Update Source Model
**File:** `events/models.py`

Add method to mark sources as unparseable:
```python
class Source(models.Model):
    # ... existing fields ...

    def mark_unparseable(self, reason: str) -> None:
        """Mark this source as unparseable based on collector failure."""
        strategy, created = SiteStrategy.objects.get_or_create(
            domain=urlparse(self.base_url).netloc
        )
        strategy.parseable = False
        strategy.last_failure = timezone.now()
        strategy.save()

        logger.warning(f"Source {self.name} marked unparseable: {reason}")
```

### 4. Update Collector API Integration
**File:** `events/admin.py` or `api/views.py` (wherever collector is called)

Update the extraction call to process new metadata:
```python
def scrape_source(source: Source) -> dict:
    """Call collector API and process enhanced metadata."""
    response = requests.post(
        f"{COLLECTOR_URL}/extract",
        json={
            "url": source.base_url,
            "extraction_hints": {
                "content_selectors": source.site_strategy.best_selectors if source.site_strategy else None,
            }
        },
        timeout=180
    )

    data = response.json()
    metadata = data['metadata']

    # Update SiteStrategy with extraction intelligence
    domain = urlparse(source.base_url).netloc
    strategy, _ = SiteStrategy.objects.get_or_create(domain=domain)
    strategy.update_from_extraction(metadata)

    # Track extraction attempt (optional)
    ExtractionAttempt.objects.create(
        source=source,
        success=data['success'],
        extraction_method=metadata['extraction_method'],
        parseable=metadata['parseable'],
        event_count=metadata['total_found'],
        confidence_score=metadata['confidence_score'],
        total_time_seconds=data['processing_time_seconds'],
        jsonld_time_seconds=metadata.get('jsonld_time_seconds'),
        llm_time_seconds=metadata.get('llm_time_seconds'),
        validation_time_seconds=metadata.get('validation_time_seconds'),
        failure_reason=metadata.get('failure_reason'),
        snapshot_available=metadata.get('snapshot_path') is not None,
    )

    # Mark source as unparseable if extraction failed
    if not metadata['parseable']:
        source.mark_unparseable(metadata.get('failure_reason', 'Unknown error'))

    return data
```

### 5. Implement SiteStrategy.update_from_extraction()
**File:** `events/models.py`

```python
def update_from_extraction(self, metadata: dict) -> None:
    """Update strategy based on collector extraction metadata.

    Args:
        metadata: ExtractResponse.metadata dict from collector API
    """
    from django.utils import timezone

    # Update extraction method if successful
    if metadata['parseable']:
        self.best_method = metadata['extraction_method']
        self.last_success = timezone.now()

        # Store iframe URL if used
        if metadata.get('iframe_url'):
            self.iframe_url_pattern = metadata['iframe_url']

        # Store recommended hints for future extractions
        if metadata.get('recommended_hints'):
            self.best_selectors = metadata['recommended_hints']
    else:
        self.parseable = False
        self.last_failure = timezone.now()

    # Update rolling statistics
    self.total_extractions += 1
    self.total_events_found += metadata['total_found']

    # Update rolling average confidence (weighted)
    if self.avg_confidence == 0:
        self.avg_confidence = metadata['confidence_score']
    else:
        # 80% old, 20% new
        self.avg_confidence = (0.8 * self.avg_confidence) + (0.2 * metadata['confidence_score'])

    # Update success rate (last 10 attempts weighted)
    if metadata['parseable']:
        new_success = 1.0
    else:
        new_success = 0.0

    if self.success_rate == 1.0 and self.total_extractions == 1:
        self.success_rate = new_success
    else:
        # Exponential moving average
        alpha = 0.1  # Weight for new sample
        self.success_rate = (alpha * new_success) + ((1 - alpha) * self.success_rate)

    # Update average extraction time
    total_time = (
        (metadata.get('jsonld_time_seconds') or 0) +
        (metadata.get('llm_time_seconds') or 0) +
        (metadata.get('validation_time_seconds') or 0)
    )
    if self.avg_extraction_time == 0:
        self.avg_extraction_time = total_time
    else:
        self.avg_extraction_time = (0.8 * self.avg_extraction_time) + (0.2 * total_time)

    self.save()
```

### 6. Update Django Admin Interface
**File:** `events/admin.py`

Add metadata display to Source admin:
```python
@admin.register(Source)
class SourceAdmin(admin.ModelAdmin):
    list_display = ['name', 'domain', 'parseable_status', 'success_rate', 'avg_confidence', 'last_scraped']
    list_filter = ['site_strategy__parseable', 'site_strategy__best_method']

    def parseable_status(self, obj):
        if not hasattr(obj, 'site_strategy'):
            return "Unknown"

        strategy = obj.site_strategy
        if strategy.parseable:
            return format_html('<span style="color: green;">✓ Parseable</span>')
        else:
            return format_html('<span style="color: red;">✗ Unparseable</span>')

    def success_rate(self, obj):
        if not hasattr(obj, 'site_strategy'):
            return "-"
        return f"{obj.site_strategy.success_rate * 100:.1f}%"

    def avg_confidence(self, obj):
        if not hasattr(obj, 'site_strategy'):
            return "-"
        return f"{obj.site_strategy.avg_confidence:.2f}"
```

## Migration Plan

1. **Create migrations:**
   ```bash
   python manage.py makemigrations
   python manage.py migrate
   ```

2. **Update collector integration code** in `events/admin.py` or wherever collector is called

3. **Test with known sources:**
   - Test with Needham Library (should show `jsonld`, high confidence)
   - Test with a failing URL (should mark unparseable, save snapshot)

4. **Add admin filters/displays** for new metadata fields

5. **Optional: Create management command** to backfill strategies from existing sources

## Testing Checklist

- [ ] SiteStrategy model creates/updates correctly
- [ ] Source marked unparseable when `metadata.parseable == False`
- [ ] Extraction timing stats are tracked
- [ ] Confidence scores update correctly
- [ ] Admin interface shows new metadata
- [ ] Repeated extractions update rolling averages
- [ ] Failed extraction creates ExtractionAttempt record

## Future Enhancements

- **Health dashboard:** Show extraction success rates across all sources
- **Auto-retry logic:** Retry unparseable sources after N days with updated hints
- **Extraction strategy recommendations:** ML to suggest best selectors based on HTML patterns
- **Snapshot viewer:** Admin interface to view failed HTML snapshots

## Related
- Collector PR: `superschedules_collector#[PR_NUMBER]`
- Branch: `feature/enhanced-extraction-visibility`

## Acceptance Criteria
- [ ] SiteStrategy model tracks extraction metadata
- [ ] Sources automatically marked unparseable on failure
- [ ] Django admin displays extraction statistics
- [ ] All tests pass
- [ ] Documentation updated
