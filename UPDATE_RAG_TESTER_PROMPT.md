# Update RAG Tester with Location Resolution

## Context

The RAG tester at `/admin/rag-tester/` needs to be updated to show the new location resolution system. Currently it shows "Location extracted" from regex, but now we have deterministic location resolution that converts city names to coordinates.

## Current Files
- `config/admin.py` - `rag_tester_view()` function (lines 24-76)
- `templates/admin/rag_tester.html` - UI template

## Changes Needed

### 1. Update `config/admin.py` - `rag_tester_view()`

Add location resolution info to the context. After calling `get_context_events()`, also call the location resolver directly to show debug info:

```python
from locations.services import resolve_location

# After getting results, resolve location for debug display
resolved_location = None
if location or location_hints:
    location_query = location or (location_hints[0] if location_hints else None)
    if location_query:
        try:
            resolved_location = resolve_location(location_query)
        except Exception:
            pass

context['resolved_location'] = {
    'query': location_query,
    'matched': str(resolved_location.matched_location) if resolved_location and resolved_location.matched_location else None,
    'lat': float(resolved_location.latitude) if resolved_location and resolved_location.latitude else None,
    'lng': float(resolved_location.longitude) if resolved_location and resolved_location.longitude else None,
    'confidence': resolved_location.confidence if resolved_location else None,
    'is_ambiguous': resolved_location.is_ambiguous if resolved_location else None,
    'alternatives': [str(alt) for alt in resolved_location.alternatives] if resolved_location and resolved_location.alternatives else [],
} if resolved_location else None
```

### 2. Add Radius Field to Form

Add a `radius` input field to the form in the view and pass it to `get_context_events()`:

```python
radius = request.POST.get('radius', '').strip()
radius_miles = float(radius) if radius else None

results = rag_service.get_context_events(
    user_message=query,
    max_events=limit,
    similarity_threshold=threshold,
    time_filter_days=time_filter,
    location=location,
    max_distance_miles=radius_miles,  # NEW
)
```

### 3. Update Template - Add Radius Field

In `templates/admin/rag_tester.html`, add a radius selector in the inline-fields section:

```html
<div>
    <label for="radius">Search Radius:</label>
    <select id="radius" name="radius">
        <option value="" {% if not radius %}selected{% endif %}>Auto (10 mi default)</option>
        <option value="5" {% if radius == '5' %}selected{% endif %}>5 miles</option>
        <option value="10" {% if radius == '10' %}selected{% endif %}>10 miles</option>
        <option value="15" {% if radius == '15' %}selected{% endif %}>15 miles</option>
        <option value="25" {% if radius == '25' %}selected{% endif %}>25 miles</option>
        <option value="50" {% if radius == '50' %}selected{% endif %}>50 miles</option>
    </select>
</div>
```

### 4. Update Template - Show Resolved Location in Debug Section

Replace the simple "Location extracted" line with detailed resolution info:

```html
<p><strong>Location query:</strong> {{ location_extracted|default:"None" }}</p>
{% if resolved_location %}
<div style="background: #d4edda; padding: 10px; border-radius: 4px; margin: 10px 0;">
    <strong>✓ Location Resolved:</strong> {{ resolved_location.matched }}<br>
    <strong>Coordinates:</strong> {{ resolved_location.lat|floatformat:4 }}, {{ resolved_location.lng|floatformat:4 }}<br>
    <strong>Confidence:</strong> {{ resolved_location.confidence|floatformat:2 }}
    {% if resolved_location.is_ambiguous %}
        <span style="color: #856404;">(ambiguous - alternatives: {{ resolved_location.alternatives|join:", " }})</span>
    {% endif %}
</div>
{% elif location_extracted %}
<div style="background: #fff3cd; padding: 10px; border-radius: 4px; margin: 10px 0;">
    <strong>⚠ Location not resolved:</strong> "{{ location_extracted }}" - using text-based venue filter
</div>
{% endif %}
```

### 5. Add Stat Box for Filter Type

Add a stat box showing whether geo-filter or text-filter was used:

```html
<div class="stat-box">
    <div class="number">{% if resolved_location.matched %}🎯{% else %}📝{% endif %}</div>
    <div class="label">{% if resolved_location.matched %}Geo Filter{% else %}Text Filter{% endif %}</div>
</div>
```

## Expected Result

After these changes, the RAG tester will show:
- **Location Resolved**: "Newton, MA" with coordinates (42.3378, -71.2092)
- **Confidence**: 1.0 (for exact matches) or lower for ambiguous
- **Alternatives**: If ambiguous, shows other possible matches
- **Filter Type**: Whether using geo-distance or text matching
- **Radius**: Configurable search radius (5-50 miles)

## Testing

1. Query: "events in Newton" - should resolve to Newton, MA with high confidence
2. Query: "events in Springfield" - should show ambiguous with MA preferred, alternatives listed
3. Query: "events in Xyzzytown" - should show "not resolved, using text filter"
4. Try different radius values and verify result counts change
