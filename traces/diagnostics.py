"""
Diagnostics engine for analyzing trace events.

Computes "what's messing it up" signals:
- Large context blocks that may be distracting the model
- Duplicate or near-duplicate content
- Low retrieval scores
- Missing or weak location/date alignment
"""

import re
from datetime import datetime
from typing import Dict, List, Any, Optional


def compute_diagnostics(events: List[Dict]) -> Dict[str, Any]:
    """
    Analyze trace events for potential issues.

    Args:
        events: List of trace event dicts from TraceRecorder

    Returns:
        Dict with:
        - context_blocks: List of block summaries with sizes and percentages
        - warnings: List of warning dicts
        - retrieval_quality: Retrieval quality metrics
        - timing: Timing breakdown
    """
    diagnostics = {
        'context_blocks': [],
        'warnings': [],
        'retrieval_quality': {},
        'timing': {},
    }

    # Analyze context blocks
    context_blocks = []
    total_chars = 0

    for event in events:
        if event.get('stage') == 'context_block':
            data = event.get('data', {})
            block_info = {
                'type': data.get('block_type', 'unknown'),
                'chars': data.get('chars', 0),
                'tokens_est': data.get('tokens_est', 0),
            }
            total_chars += block_info['chars']
            context_blocks.append(block_info)

    # Compute percentages and detect large blocks
    for block in context_blocks:
        if total_chars > 0:
            block['percent_of_total'] = round(100 * block['chars'] / total_chars, 1)
        else:
            block['percent_of_total'] = 0

        # Flag large blocks (>40% of context)
        if block['percent_of_total'] > 40:
            diagnostics['warnings'].append({
                'type': 'large_block',
                'severity': 'medium',
                'block': block['type'],
                'message': f"'{block['type']}' block is {block['percent_of_total']}% of total context",
            })

        # Flag very large blocks (>60%)
        if block['percent_of_total'] > 60:
            diagnostics['warnings'][-1]['severity'] = 'high'

    diagnostics['context_blocks'] = context_blocks
    diagnostics['total_context_chars'] = total_chars
    diagnostics['total_context_tokens_est'] = total_chars // 4

    # Analyze retrieval quality
    retrieval_event = next((e for e in events if e.get('stage') == 'retrieval'), None)
    if retrieval_event:
        data = retrieval_event.get('data', {})
        candidates = data.get('candidates', [])

        if candidates:
            scores = [c.get('similarity_score', 0) for c in candidates]
            above_threshold = [c for c in candidates if c.get('above_threshold', False)]

            diagnostics['retrieval_quality'] = {
                'total_candidates': len(candidates),
                'above_threshold': len(above_threshold),
                'top_score': round(max(scores), 3) if scores else 0,
                'bottom_score': round(min(scores), 3) if scores else 0,
                'score_spread': round(max(scores) - min(scores), 3) if scores else 0,
                'avg_score': round(sum(scores) / len(scores), 3) if scores else 0,
                'geo_filter_used': data.get('geo_filter_used', False),
                'text_filter_used': data.get('text_filter_used', False),
            }

            # Warning if top score is low
            if max(scores) < 0.35:
                diagnostics['warnings'].append({
                    'type': 'low_retrieval_score',
                    'severity': 'high',
                    'message': f"Top retrieval score is only {max(scores):.3f} - events may not match query well",
                })
            elif max(scores) < 0.45:
                diagnostics['warnings'].append({
                    'type': 'low_retrieval_score',
                    'severity': 'medium',
                    'message': f"Top retrieval score is {max(scores):.3f} - moderate match quality",
                })

            # Warning if very few results above threshold
            if len(above_threshold) < 3 and len(candidates) >= 3:
                diagnostics['warnings'].append({
                    'type': 'few_matches',
                    'severity': 'medium',
                    'message': f"Only {len(above_threshold)} of {len(candidates)} candidates above threshold",
                })

            # Warning if no geo filter was used but location was extracted
            input_event = next((e for e in events if e.get('stage') == 'input'), None)
            if input_event:
                location_hints = input_event.get('data', {}).get('location_hints_extracted', [])
                if location_hints and not data.get('geo_filter_used'):
                    diagnostics['warnings'].append({
                        'type': 'location_not_resolved',
                        'severity': 'low',
                        'message': f"Location '{location_hints[0]}' extracted but geo-filter not applied",
                    })

    # Check for duplicate content in context blocks
    block_texts = []
    for event in events:
        if event.get('stage') == 'context_block':
            text = event.get('data', {}).get('text', '')
            if text:
                # Use first 200 chars for duplicate detection
                block_texts.append((event['data'].get('block_type', 'unknown'), text[:200]))

    seen_prefixes = {}
    for block_type, text_prefix in block_texts:
        if text_prefix in seen_prefixes:
            diagnostics['warnings'].append({
                'type': 'duplicate_content',
                'severity': 'medium',
                'message': f"Duplicate content detected between '{seen_prefixes[text_prefix]}' and '{block_type}' blocks",
            })
        else:
            seen_prefixes[text_prefix] = block_type

    # Analyze timing
    timing = {}
    for event in events:
        stage = event.get('stage')
        latency = event.get('latency_ms')
        if latency is not None:
            if stage in timing:
                timing[stage] += latency
            else:
                timing[stage] = latency

    diagnostics['timing'] = timing

    # Calculate total traced time
    total_traced_ms = sum(timing.values())
    diagnostics['total_traced_ms'] = total_traced_ms

    # Warning if LLM is very slow
    llm_time = timing.get('llm_response', 0)
    if llm_time > 30000:  # 30 seconds
        diagnostics['warnings'].append({
            'type': 'slow_llm',
            'severity': 'high',
            'message': f"LLM response took {llm_time/1000:.1f}s - consider a faster model",
        })
    elif llm_time > 15000:  # 15 seconds
        diagnostics['warnings'].append({
            'type': 'slow_llm',
            'severity': 'medium',
            'message': f"LLM response took {llm_time/1000:.1f}s",
        })

    # Sort warnings by severity
    severity_order = {'high': 0, 'medium': 1, 'low': 2}
    diagnostics['warnings'].sort(key=lambda w: severity_order.get(w.get('severity', 'low'), 3))

    return diagnostics


def format_diagnostics_html(diagnostics: Dict) -> str:
    """
    Format diagnostics as HTML for admin display.

    Returns an HTML string suitable for Django admin.
    """
    parts = []

    # Warnings section
    if diagnostics.get('warnings'):
        parts.append('<div class="diagnostics-warnings">')
        parts.append('<h4>Potential Issues</h4>')
        parts.append('<ul>')
        for warning in diagnostics['warnings']:
            severity = warning.get('severity', 'low')
            color = {'high': '#dc3545', 'medium': '#ffc107', 'low': '#17a2b8'}.get(severity, '#6c757d')
            parts.append(f'<li style="color:{color}"><strong>[{severity.upper()}]</strong> {warning["message"]}</li>')
        parts.append('</ul>')
        parts.append('</div>')

    # Context blocks summary
    if diagnostics.get('context_blocks'):
        parts.append('<div class="diagnostics-context">')
        parts.append('<h4>Context Block Sizes</h4>')
        parts.append('<table style="width:100%; border-collapse:collapse;">')
        parts.append('<tr><th>Block</th><th>Chars</th><th>~Tokens</th><th>%</th></tr>')
        for block in diagnostics['context_blocks']:
            pct = block.get('percent_of_total', 0)
            color = '#dc3545' if pct > 50 else '#ffc107' if pct > 30 else '#28a745'
            parts.append(f'<tr>')
            parts.append(f'<td>{block["type"]}</td>')
            parts.append(f'<td>{block["chars"]:,}</td>')
            parts.append(f'<td>{block["tokens_est"]:,}</td>')
            parts.append(f'<td style="color:{color}">{pct}%</td>')
            parts.append('</tr>')
        parts.append('</table>')
        parts.append(f'<p>Total: {diagnostics.get("total_context_chars", 0):,} chars (~{diagnostics.get("total_context_tokens_est", 0):,} tokens)</p>')
        parts.append('</div>')

    # Retrieval quality
    if diagnostics.get('retrieval_quality'):
        rq = diagnostics['retrieval_quality']
        parts.append('<div class="diagnostics-retrieval">')
        parts.append('<h4>Retrieval Quality</h4>')
        parts.append(f'<p>Candidates: {rq.get("total_candidates", 0)} total, {rq.get("above_threshold", 0)} above threshold</p>')
        parts.append(f'<p>Scores: {rq.get("top_score", 0):.3f} (top) to {rq.get("bottom_score", 0):.3f} (bottom)</p>')
        filter_type = "geo" if rq.get('geo_filter_used') else "text" if rq.get('text_filter_used') else "none"
        parts.append(f'<p>Location filter: {filter_type}</p>')
        parts.append('</div>')

    # Timing breakdown
    if diagnostics.get('timing'):
        parts.append('<div class="diagnostics-timing">')
        parts.append('<h4>Timing Breakdown</h4>')
        parts.append('<table style="width:100%; border-collapse:collapse;">')
        for stage, ms in sorted(diagnostics['timing'].items(), key=lambda x: -x[1]):
            parts.append(f'<tr><td>{stage}</td><td>{ms}ms</td></tr>')
        parts.append('</table>')
        parts.append(f'<p>Total traced: {diagnostics.get("total_traced_ms", 0)}ms</p>')
        parts.append('</div>')

    return '\n'.join(parts)


# =============================================================================
# Response Quality Analysis
# =============================================================================

def analyze_response_quality(response_text: str, retrieved_events: List[Dict]) -> Dict[str, Any]:
    """
    Analyze LLM response to determine how well it used retrieved events.

    Args:
        response_text: The LLM's response text
        retrieved_events: List of event dicts from retrieval stage

    Returns:
        Dict with events_analyzed, coverage metrics, hallucinations, accuracy_issues
    """
    if not response_text or not retrieved_events:
        return {
            'events_analyzed': [],
            'coverage': {'total': 0, 'mentioned': 0, 'high_relevance_mentioned': 0, 'high_relevance_total': 0},
            'hallucinations': [],
            'accuracy_issues': [],
        }

    # Analyze each event for mentions
    events_analyzed = []
    for event in retrieved_events:
        mention_result = find_event_mention(response_text, event)
        accuracy = check_event_accuracy(response_text, event) if mention_result['mentioned'] else {}

        events_analyzed.append({
            'id': event.get('id'),
            'title': event.get('title', ''),
            'similarity_score': event.get('similarity_score', 0),
            'mention_status': 'mentioned' if mention_result['mentioned'] else 'ignored',
            'match_type': mention_result.get('match_type'),
            'accuracy': accuracy,
        })

    # Compute coverage metrics
    total = len(events_analyzed)
    mentioned = sum(1 for e in events_analyzed if e['mention_status'] == 'mentioned')
    high_relevance_total = sum(1 for e in events_analyzed if e['similarity_score'] >= 0.6)
    high_relevance_mentioned = sum(1 for e in events_analyzed if e['similarity_score'] >= 0.6 and e['mention_status'] == 'mentioned')

    coverage = {
        'total': total,
        'mentioned': mentioned,
        'ignored': total - mentioned,
        'coverage_rate': round(mentioned / total, 2) if total > 0 else 0,
        'high_relevance_total': high_relevance_total,
        'high_relevance_mentioned': high_relevance_mentioned,
        'high_relevance_coverage': round(high_relevance_mentioned / high_relevance_total, 2) if high_relevance_total > 0 else 0,
    }

    # Detect potential hallucinations
    hallucinations = detect_hallucinated_events(response_text, retrieved_events)

    # Collect accuracy issues
    accuracy_issues = []
    for event in events_analyzed:
        if event.get('accuracy'):
            acc = event['accuracy']
            if acc.get('date_issue'):
                accuracy_issues.append({
                    'event_id': event['id'],
                    'event_title': event['title'],
                    'issue_type': 'date_mismatch',
                    'message': acc['date_issue'],
                })
            if acc.get('location_issue'):
                accuracy_issues.append({
                    'event_id': event['id'],
                    'event_title': event['title'],
                    'issue_type': 'location_mismatch',
                    'message': acc['location_issue'],
                })

    return {
        'events_analyzed': events_analyzed,
        'coverage': coverage,
        'hallucinations': hallucinations,
        'accuracy_issues': accuracy_issues,
    }


def find_event_mention(response_text: str, event: Dict) -> Dict[str, Any]:
    """
    Check if an event is mentioned in the response text.

    Uses multiple matching strategies:
    1. Exact title match (case-insensitive)
    2. Partial title match (first 3+ significant words)
    3. Venue + time combination

    Returns:
        {'mentioned': bool, 'match_type': str or None}
    """
    response_lower = response_text.lower()
    title = event.get('title', '')
    title_lower = title.lower()

    # Strategy 1: Exact title match
    if title_lower and title_lower in response_lower:
        return {'mentioned': True, 'match_type': 'exact_title'}

    # Strategy 2: Partial title match (first 3+ significant words)
    if title:
        # Remove common stop words and get significant words
        stop_words = {'the', 'a', 'an', 'and', 'or', 'for', 'of', 'to', 'in', 'at', 'on', 'with'}
        words = [w for w in re.findall(r'\b\w+\b', title_lower) if w not in stop_words and len(w) > 2]

        if len(words) >= 3:
            # Check if first 3 significant words appear together (within 20 chars of each other)
            pattern = r'\b' + r'.{0,20}'.join(re.escape(w) for w in words[:3]) + r'\b'
            if re.search(pattern, response_lower, re.IGNORECASE):
                return {'mentioned': True, 'match_type': 'partial_title'}

        elif len(words) >= 2:
            # For shorter titles, require both significant words
            pattern = r'\b' + r'.{0,15}'.join(re.escape(w) for w in words[:2]) + r'\b'
            if re.search(pattern, response_lower, re.IGNORECASE):
                return {'mentioned': True, 'match_type': 'partial_title'}

    # Strategy 3: Venue + time mentioned together
    venue = event.get('venue', '') or event.get('location', '')
    start_time = event.get('start_time', '')

    if venue and start_time:
        venue_lower = venue.lower()
        # Extract venue name (first part before comma or full name)
        venue_name = venue_lower.split(',')[0].strip()

        # Check if venue name appears in response
        if venue_name and len(venue_name) > 3 and venue_name in response_lower:
            # Also check if a time-like pattern appears nearby
            time_patterns = _extract_time_mentions(response_text)
            if time_patterns:
                return {'mentioned': True, 'match_type': 'venue_time'}

    return {'mentioned': False, 'match_type': None}


def _extract_time_mentions(text: str) -> List[str]:
    """Extract time-like patterns from text."""
    patterns = [
        r'\b\d{1,2}:\d{2}\s*(?:am|pm|AM|PM)?\b',  # 10:00 AM
        r'\b\d{1,2}\s*(?:am|pm|AM|PM)\b',          # 10 AM
        r'\b(?:morning|afternoon|evening|noon)\b',  # morning, afternoon, etc.
    ]
    matches = []
    for pattern in patterns:
        matches.extend(re.findall(pattern, text, re.IGNORECASE))
    return matches


def check_event_accuracy(response_text: str, event: Dict) -> Dict[str, Any]:
    """
    Check if the event details mentioned in the response are accurate.

    Returns dict with:
    - date_ok: bool
    - location_ok: bool
    - date_issue: str or None
    - location_issue: str or None
    """
    result = {}
    response_lower = response_text.lower()
    title_lower = event.get('title', '').lower()

    # Find the section of response that mentions this event (within 200 chars of title)
    title_pos = response_lower.find(title_lower)
    if title_pos == -1:
        # Try to find by first few words
        words = title_lower.split()[:3]
        if words:
            for i, word in enumerate(words):
                pos = response_lower.find(word)
                if pos != -1:
                    title_pos = pos
                    break

    if title_pos == -1:
        return result  # Can't verify if we can't find the event mention

    # Extract context around the event mention
    context_start = max(0, title_pos - 50)
    context_end = min(len(response_lower), title_pos + 300)
    context = response_lower[context_start:context_end]

    # Check date accuracy
    start_time = event.get('start_time')
    if start_time:
        try:
            if isinstance(start_time, str):
                event_dt = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
            else:
                event_dt = start_time

            event_day = event_dt.strftime('%A').lower()  # Monday, Tuesday, etc.
            event_date = event_dt.strftime('%B %d').lower()  # January 15

            # Check if the correct day is mentioned in context
            days_mentioned = re.findall(r'\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b', context)
            if days_mentioned and event_day not in days_mentioned:
                result['date_issue'] = f"Response mentions {days_mentioned[0]} but event is on {event_day}"
                result['date_ok'] = False
            else:
                result['date_ok'] = True
        except (ValueError, TypeError):
            pass

    # Check location accuracy
    venue = event.get('venue', '') or event.get('location', '')
    city = event.get('city', '')

    if venue or city:
        venue_lower = venue.lower() if venue else ''
        city_lower = city.lower() if city else ''

        # Extract venue name
        venue_name = venue_lower.split(',')[0].strip() if venue_lower else ''

        # Check if correct venue/city appears near the event mention
        if venue_name and len(venue_name) > 3:
            if venue_name not in context and city_lower not in context:
                # Check if a different location is mentioned
                location_pattern = r'\bat\s+(?:the\s+)?([A-Za-z\s]+(?:Library|Center|Hall|Room|Park|School|Museum))'
                other_locations = re.findall(location_pattern, response_text[context_start:context_end], re.IGNORECASE)
                if other_locations:
                    result['location_issue'] = f"Response mentions '{other_locations[0]}' but event is at '{venue_name}'"
                    result['location_ok'] = False
                else:
                    result['location_ok'] = True
            else:
                result['location_ok'] = True

    return result


def detect_hallucinated_events(response_text: str, known_events: List[Dict]) -> List[Dict]:
    """
    Detect potential hallucinated events - event-like mentions not in known_events.

    Looks for patterns like:
    - "Event Name" followed by time/location
    - Bullet points with event-like content
    - Bold text that looks like event titles

    Returns list of potential hallucinations with confidence levels.
    """
    hallucinations = []

    # Pattern 1: Look for bold text (markdown **text**) that might be event titles
    bold_pattern = r'\*\*([^*]+)\*\*'
    bold_matches = re.findall(bold_pattern, response_text)

    known_titles = {e.get('title', '').lower() for e in known_events}
    known_titles.discard('')

    for match in bold_matches:
        match_lower = match.lower().strip()
        # Skip if it matches a known event
        if any(match_lower in known or known in match_lower for known in known_titles if known):
            continue

        # Skip common non-event bold text
        skip_phrases = ['note', 'important', 'tip', 'warning', 'summary', 'here are', 'i found']
        if any(phrase in match_lower for phrase in skip_phrases):
            continue

        # Check if this looks like an event title (has event-like words)
        event_indicators = ['class', 'workshop', 'session', 'storytime', 'story time', 'program', 'event', 'show', 'performance', 'concert', 'exhibit']
        if any(indicator in match_lower for indicator in event_indicators):
            hallucinations.append({
                'text': match,
                'confidence': 0.7,
                'reason': 'Bold text looks like event title but not in retrieved events',
            })

    # Pattern 2: Look for numbered list items that look like events
    numbered_pattern = r'^\s*\d+\.\s*\*?\*?([^*\n]+)'
    for match in re.finditer(numbered_pattern, response_text, re.MULTILINE):
        text = match.group(1).strip()
        text_lower = text.lower()

        # Skip if matches known event
        if any(text_lower in known or known in text_lower for known in known_titles if known):
            continue

        # Check if it has time/location indicators (suggesting it's an event recommendation)
        if re.search(r'\b(at|on|from)\s+\d', text_lower) or re.search(r'\b\d{1,2}:\d{2}', text):
            # This looks like an event with time
            if not any(text_lower in known or known in text_lower for known in known_titles if known):
                hallucinations.append({
                    'text': text[:100],
                    'confidence': 0.6,
                    'reason': 'Numbered item with time/date not matching retrieved events',
                })

    return hallucinations


# =============================================================================
# Comparative Analysis
# =============================================================================

def compare_run_results(run_a: Dict, run_b: Dict) -> Dict[str, Any]:
    """
    Compare two debug run results and compute differences.

    Args:
        run_a: First run data (from get_run_events API)
        run_b: Second run data

    Returns:
        Dict with settings_diff, metrics_diff, events_diff, response_diff
    """
    # Compare settings
    settings_a = run_a.get('settings', {})
    settings_b = run_b.get('settings', {})
    settings_diff = {}

    all_keys = set(settings_a.keys()) | set(settings_b.keys())
    for key in all_keys:
        val_a = settings_a.get(key)
        val_b = settings_b.get(key)
        if val_a != val_b:
            settings_diff[key] = {'a': val_a, 'b': val_b}

    # Compare metrics
    diag_a = run_a.get('diagnostics', {}) or {}
    diag_b = run_b.get('diagnostics', {}) or {}

    rq_a = diag_a.get('retrieval_quality', {})
    rq_b = diag_b.get('retrieval_quality', {})

    metrics_diff = {
        'total_candidates': {'a': rq_a.get('total_candidates', 0), 'b': rq_b.get('total_candidates', 0)},
        'above_threshold': {'a': rq_a.get('above_threshold', 0), 'b': rq_b.get('above_threshold', 0)},
        'top_score': {'a': rq_a.get('top_score', 0), 'b': rq_b.get('top_score', 0)},
        'total_latency_ms': {'a': run_a.get('total_latency_ms', 0), 'b': run_b.get('total_latency_ms', 0)},
        'response_length': {
            'a': len(run_a.get('final_answer_text', '')),
            'b': len(run_b.get('final_answer_text', '')),
        },
    }

    # Add diff values
    for key in metrics_diff:
        a_val = metrics_diff[key]['a'] or 0
        b_val = metrics_diff[key]['b'] or 0
        metrics_diff[key]['diff'] = b_val - a_val

    # Compare events
    events_a = _extract_retrieval_events(run_a.get('events', []))
    events_b = _extract_retrieval_events(run_b.get('events', []))
    events_diff = compute_event_diff(events_a, events_b)

    # Compare responses (simple summary)
    response_a = run_a.get('final_answer_text', '')
    response_b = run_b.get('final_answer_text', '')
    response_diff = {
        'length_a': len(response_a),
        'length_b': len(response_b),
        'length_diff': len(response_b) - len(response_a),
    }

    return {
        'settings_diff': settings_diff,
        'metrics_diff': metrics_diff,
        'events_diff': events_diff,
        'response_diff': response_diff,
    }


def _extract_retrieval_events(events: List[Dict]) -> List[Dict]:
    """Extract candidate events from trace events list."""
    for event in events:
        if event.get('stage') == 'retrieval':
            return event.get('data', {}).get('candidates', [])
    return []


def compute_event_diff(events_a: List[Dict], events_b: List[Dict]) -> Dict[str, Any]:
    """
    Compare two event lists by event ID.

    Returns:
        {
            'only_in_a': [...],
            'only_in_b': [...],
            'in_both': [...],
            'score_changes': [...]
        }
    """
    ids_a = {e.get('id') for e in events_a if e.get('id')}
    ids_b = {e.get('id') for e in events_b if e.get('id')}

    only_in_a_ids = ids_a - ids_b
    only_in_b_ids = ids_b - ids_a
    in_both_ids = ids_a & ids_b

    # Build lookup dicts
    events_a_by_id = {e.get('id'): e for e in events_a if e.get('id')}
    events_b_by_id = {e.get('id'): e for e in events_b if e.get('id')}

    only_in_a = [
        {'id': eid, 'title': events_a_by_id[eid].get('title', ''), 'score': events_a_by_id[eid].get('similarity_score', 0)}
        for eid in only_in_a_ids
    ]
    only_in_b = [
        {'id': eid, 'title': events_b_by_id[eid].get('title', ''), 'score': events_b_by_id[eid].get('similarity_score', 0)}
        for eid in only_in_b_ids
    ]
    in_both = [
        {'id': eid, 'title': events_a_by_id[eid].get('title', ''), 'score_a': events_a_by_id[eid].get('similarity_score', 0), 'score_b': events_b_by_id[eid].get('similarity_score', 0)}
        for eid in in_both_ids
    ]

    # Find significant score changes
    score_changes = [
        e for e in in_both
        if abs((e.get('score_a', 0) or 0) - (e.get('score_b', 0) or 0)) > 0.1
    ]

    return {
        'only_in_a': sorted(only_in_a, key=lambda x: x.get('score', 0), reverse=True),
        'only_in_b': sorted(only_in_b, key=lambda x: x.get('score', 0), reverse=True),
        'in_both': sorted(in_both, key=lambda x: x.get('score_a', 0), reverse=True),
        'score_changes': score_changes,
        'summary': {
            'only_a_count': len(only_in_a),
            'only_b_count': len(only_in_b),
            'in_both_count': len(in_both),
        }
    }
