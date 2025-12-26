"""
Diagnostics engine for analyzing trace events.

Computes "what's messing it up" signals:
- Large context blocks that may be distracting the model
- Duplicate or near-duplicate content
- Low retrieval scores
- Missing or weak location/date alignment
"""

from typing import Dict, List, Any


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
