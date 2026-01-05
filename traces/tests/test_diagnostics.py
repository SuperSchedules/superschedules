"""
Tests for the diagnostics engine.
"""

from django.test import TestCase

from traces.diagnostics import (
    compute_diagnostics, format_diagnostics_html,
    analyze_response_quality, find_event_mention, detect_hallucinated_events,
    compare_run_results, compute_event_diff,
)


class TestComputeDiagnostics(TestCase):
    """Tests for compute_diagnostics function."""

    def test_empty_events(self):
        diagnostics = compute_diagnostics([])

        self.assertEqual(diagnostics['context_blocks'], [])
        self.assertEqual(diagnostics['warnings'], [])
        self.assertEqual(diagnostics['retrieval_quality'], {})

    def test_context_block_percentages(self):
        events = [
            {'stage': 'context_block', 'data': {'block_type': 'system', 'chars': 1000, 'tokens_est': 250}},
            {'stage': 'context_block', 'data': {'block_type': 'events', 'chars': 500, 'tokens_est': 125}},
            {'stage': 'context_block', 'data': {'block_type': 'history', 'chars': 500, 'tokens_est': 125}},
        ]

        diagnostics = compute_diagnostics(events)

        self.assertEqual(len(diagnostics['context_blocks']), 3)
        self.assertEqual(diagnostics['context_blocks'][0]['percent_of_total'], 50.0)
        self.assertEqual(diagnostics['context_blocks'][1]['percent_of_total'], 25.0)
        self.assertEqual(diagnostics['context_blocks'][2]['percent_of_total'], 25.0)
        self.assertEqual(diagnostics['total_context_chars'], 2000)

    def test_large_block_warning(self):
        events = [
            {'stage': 'context_block', 'data': {'block_type': 'system', 'chars': 800, 'tokens_est': 200}},
            {'stage': 'context_block', 'data': {'block_type': 'events', 'chars': 200, 'tokens_est': 50}},
        ]

        diagnostics = compute_diagnostics(events)

        # system block is 80% of total, should trigger warning
        warnings = [w for w in diagnostics['warnings'] if w['type'] == 'large_block']
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0]['severity'], 'high')  # >60% is high severity
        self.assertIn('80.0%', warnings[0]['message'])

    def test_medium_large_block_warning(self):
        events = [
            {'stage': 'context_block', 'data': {'block_type': 'system', 'chars': 500, 'tokens_est': 125}},
            {'stage': 'context_block', 'data': {'block_type': 'events', 'chars': 500, 'tokens_est': 125}},
        ]

        diagnostics = compute_diagnostics(events)

        # Both blocks are 50%, which is > 40% threshold but < 60%
        warnings = [w for w in diagnostics['warnings'] if w['type'] == 'large_block']
        self.assertEqual(len(warnings), 2)
        self.assertEqual(warnings[0]['severity'], 'medium')

    def test_retrieval_quality_metrics(self):
        events = [
            {
                'stage': 'retrieval',
                'data': {
                    'candidates': [
                        {'similarity_score': 0.8, 'above_threshold': True},
                        {'similarity_score': 0.6, 'above_threshold': True},
                        {'similarity_score': 0.4, 'above_threshold': True},
                        {'similarity_score': 0.2, 'above_threshold': False},
                    ],
                    'geo_filter_used': True,
                    'text_filter_used': False,
                }
            }
        ]

        diagnostics = compute_diagnostics(events)

        rq = diagnostics['retrieval_quality']
        self.assertEqual(rq['total_candidates'], 4)
        self.assertEqual(rq['above_threshold'], 3)
        self.assertEqual(rq['top_score'], 0.8)
        self.assertEqual(rq['bottom_score'], 0.2)
        self.assertEqual(rq['score_spread'], 0.6)
        self.assertTrue(rq['geo_filter_used'])
        self.assertFalse(rq['text_filter_used'])

    def test_low_retrieval_score_warning_high(self):
        events = [
            {
                'stage': 'retrieval',
                'data': {
                    'candidates': [
                        {'similarity_score': 0.3, 'above_threshold': True},
                        {'similarity_score': 0.25, 'above_threshold': False},
                    ],
                }
            }
        ]

        diagnostics = compute_diagnostics(events)

        warnings = [w for w in diagnostics['warnings'] if w['type'] == 'low_retrieval_score']
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0]['severity'], 'high')  # < 0.35

    def test_low_retrieval_score_warning_medium(self):
        events = [
            {
                'stage': 'retrieval',
                'data': {
                    'candidates': [
                        {'similarity_score': 0.4, 'above_threshold': True},
                    ],
                }
            }
        ]

        diagnostics = compute_diagnostics(events)

        warnings = [w for w in diagnostics['warnings'] if w['type'] == 'low_retrieval_score']
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0]['severity'], 'medium')  # < 0.45

    def test_few_matches_warning(self):
        events = [
            {
                'stage': 'retrieval',
                'data': {
                    'candidates': [
                        {'similarity_score': 0.8, 'above_threshold': True},
                        {'similarity_score': 0.6, 'above_threshold': True},
                        {'similarity_score': 0.2, 'above_threshold': False},
                        {'similarity_score': 0.15, 'above_threshold': False},
                        {'similarity_score': 0.1, 'above_threshold': False},
                    ],
                }
            }
        ]

        diagnostics = compute_diagnostics(events)

        warnings = [w for w in diagnostics['warnings'] if w['type'] == 'few_matches']
        self.assertEqual(len(warnings), 1)
        self.assertIn('2 of 5', warnings[0]['message'])

    def test_location_not_resolved_warning(self):
        events = [
            {
                'stage': 'input',
                'data': {
                    'location_hints_extracted': ['Newton'],
                }
            },
            {
                'stage': 'retrieval',
                'data': {
                    'candidates': [{'similarity_score': 0.8, 'above_threshold': True}],
                    'geo_filter_used': False,
                    'text_filter_used': True,
                }
            }
        ]

        diagnostics = compute_diagnostics(events)

        warnings = [w for w in diagnostics['warnings'] if w['type'] == 'location_not_resolved']
        self.assertEqual(len(warnings), 1)
        self.assertIn('Newton', warnings[0]['message'])

    def test_duplicate_content_detection(self):
        duplicate_text = "This is some duplicate content that appears in both blocks"
        events = [
            {'stage': 'context_block', 'data': {'block_type': 'system', 'text': duplicate_text, 'chars': 100, 'tokens_est': 25}},
            {'stage': 'context_block', 'data': {'block_type': 'events', 'text': duplicate_text, 'chars': 100, 'tokens_est': 25}},
        ]

        diagnostics = compute_diagnostics(events)

        warnings = [w for w in diagnostics['warnings'] if w['type'] == 'duplicate_content']
        self.assertEqual(len(warnings), 1)
        self.assertIn('system', warnings[0]['message'])
        self.assertIn('events', warnings[0]['message'])

    def test_timing_breakdown(self):
        events = [
            {'stage': 'retrieval', 'data': {}, 'latency_ms': 100},
            {'stage': 'context_block', 'data': {'block_type': 'system', 'chars': 100, 'tokens_est': 25}},
            {'stage': 'llm_response', 'data': {}, 'latency_ms': 5000},
        ]

        diagnostics = compute_diagnostics(events)

        self.assertEqual(diagnostics['timing']['retrieval'], 100)
        self.assertEqual(diagnostics['timing']['llm_response'], 5000)
        self.assertEqual(diagnostics['total_traced_ms'], 5100)

    def test_slow_llm_warning(self):
        events = [
            {'stage': 'llm_response', 'data': {}, 'latency_ms': 35000},  # 35 seconds
        ]

        diagnostics = compute_diagnostics(events)

        warnings = [w for w in diagnostics['warnings'] if w['type'] == 'slow_llm']
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0]['severity'], 'high')

    def test_warnings_sorted_by_severity(self):
        events = [
            {'stage': 'context_block', 'data': {'block_type': 'system', 'chars': 900, 'tokens_est': 225}},
            {'stage': 'context_block', 'data': {'block_type': 'events', 'chars': 100, 'tokens_est': 25}},
            {
                'stage': 'retrieval',
                'data': {
                    'candidates': [{'similarity_score': 0.3, 'above_threshold': True}],
                }
            }
        ]

        diagnostics = compute_diagnostics(events)

        # Should have at least 2 warnings (large block, low retrieval score)
        self.assertGreaterEqual(len(diagnostics['warnings']), 2)

        # First warning should be high severity
        self.assertEqual(diagnostics['warnings'][0]['severity'], 'high')


class TestFormatDiagnosticsHtml(TestCase):
    """Tests for format_diagnostics_html function."""

    def test_formats_warnings(self):
        diagnostics = {
            'warnings': [
                {'type': 'test', 'severity': 'high', 'message': 'High priority issue'},
                {'type': 'test', 'severity': 'low', 'message': 'Low priority issue'},
            ],
            'context_blocks': [],
            'retrieval_quality': {},
            'timing': {},
        }

        html = format_diagnostics_html(diagnostics)

        self.assertIn('High priority issue', html)
        self.assertIn('Low priority issue', html)
        self.assertIn('#dc3545', html)  # High severity color
        self.assertIn('#17a2b8', html)  # Low severity color

    def test_formats_context_blocks(self):
        diagnostics = {
            'warnings': [],
            'context_blocks': [
                {'type': 'system', 'chars': 1000, 'tokens_est': 250, 'percent_of_total': 50},
            ],
            'total_context_chars': 2000,
            'total_context_tokens_est': 500,
            'retrieval_quality': {},
            'timing': {},
        }

        html = format_diagnostics_html(diagnostics)

        self.assertIn('system', html)
        self.assertIn('1,000', html)
        self.assertIn('250', html)
        self.assertIn('50%', html)


class TestFindEventMention(TestCase):
    """Tests for find_event_mention function."""

    def test_exact_title_match(self):
        response = "I found Baby Storytime at the library which is perfect for toddlers."
        event = {'title': 'Baby Storytime', 'id': 1}

        result = find_event_mention(response, event)

        self.assertTrue(result['mentioned'])
        self.assertEqual(result['match_type'], 'exact_title')

    def test_exact_title_match_case_insensitive(self):
        response = "Check out BABY STORYTIME for your little one."
        event = {'title': 'Baby Storytime', 'id': 1}

        result = find_event_mention(response, event)

        self.assertTrue(result['mentioned'])
        self.assertEqual(result['match_type'], 'exact_title')

    def test_partial_title_match_three_words(self):
        response = "The Creative Arts Workshop for Kids is great."
        event = {'title': 'Creative Arts Workshop for Children', 'id': 1}

        result = find_event_mention(response, event)

        self.assertTrue(result['mentioned'])
        self.assertEqual(result['match_type'], 'partial_title')

    def test_no_match_different_event(self):
        response = "There's a great yoga class on Tuesday."
        event = {'title': 'Baby Storytime', 'id': 1}

        result = find_event_mention(response, event)

        self.assertFalse(result['mentioned'])
        self.assertIsNone(result['match_type'])

    def test_venue_time_match(self):
        response = "Head to Newton Library at 10:00 AM for activities."
        event = {'title': 'Unknown Event', 'venue': 'Newton Library', 'start_time': '2025-01-15T10:00:00', 'id': 1}

        result = find_event_mention(response, event)

        self.assertTrue(result['mentioned'])
        self.assertEqual(result['match_type'], 'venue_time')


class TestAnalyzeResponseQuality(TestCase):
    """Tests for analyze_response_quality function."""

    def test_empty_inputs(self):
        result = analyze_response_quality('', [])

        self.assertEqual(result['coverage']['total'], 0)
        self.assertEqual(result['events_analyzed'], [])

    def test_all_events_mentioned(self):
        response = "Check out Baby Storytime and Art Workshop today!"
        events = [
            {'id': 1, 'title': 'Baby Storytime', 'similarity_score': 0.8},
            {'id': 2, 'title': 'Art Workshop', 'similarity_score': 0.7},
        ]

        result = analyze_response_quality(response, events)

        self.assertEqual(result['coverage']['total'], 2)
        self.assertEqual(result['coverage']['mentioned'], 2)
        self.assertEqual(result['coverage']['coverage_rate'], 1.0)

    def test_some_events_ignored(self):
        response = "Check out Baby Storytime today!"
        events = [
            {'id': 1, 'title': 'Baby Storytime', 'similarity_score': 0.8},
            {'id': 2, 'title': 'Art Workshop', 'similarity_score': 0.7},
            {'id': 3, 'title': 'Yoga Class', 'similarity_score': 0.5},
        ]

        result = analyze_response_quality(response, events)

        self.assertEqual(result['coverage']['total'], 3)
        self.assertEqual(result['coverage']['mentioned'], 1)
        self.assertEqual(result['coverage']['ignored'], 2)

    def test_high_relevance_coverage(self):
        response = "Baby Storytime is great!"
        events = [
            {'id': 1, 'title': 'Baby Storytime', 'similarity_score': 0.8},  # high relevance, mentioned
            {'id': 2, 'title': 'Art Workshop', 'similarity_score': 0.7},    # high relevance, ignored
            {'id': 3, 'title': 'Yoga Class', 'similarity_score': 0.4},      # low relevance, ignored
        ]

        result = analyze_response_quality(response, events)

        self.assertEqual(result['coverage']['high_relevance_total'], 2)
        self.assertEqual(result['coverage']['high_relevance_mentioned'], 1)
        self.assertEqual(result['coverage']['high_relevance_coverage'], 0.5)


class TestDetectHallucinatedEvents(TestCase):
    """Tests for detect_hallucinated_events function."""

    def test_no_hallucinations_when_events_match(self):
        response = "Check out **Baby Storytime** at the library."
        known_events = [{'title': 'Baby Storytime', 'id': 1}]

        result = detect_hallucinated_events(response, known_events)

        self.assertEqual(len(result), 0)

    def test_detects_bold_event_not_in_list(self):
        response = "I recommend **Mystery Book Club** for adults."
        known_events = [{'title': 'Baby Storytime', 'id': 1}]

        result = detect_hallucinated_events(response, known_events)

        # Should not detect this as it doesn't have event indicators
        self.assertEqual(len(result), 0)

    def test_detects_bold_event_with_indicator(self):
        response = "Check out **Art Workshop** for creative fun!"
        known_events = [{'title': 'Baby Storytime', 'id': 1}]

        result = detect_hallucinated_events(response, known_events)

        self.assertEqual(len(result), 1)
        self.assertIn('Art Workshop', result[0]['text'])

    def test_ignores_common_bold_phrases(self):
        response = "**Note:** Please register in advance."
        known_events = [{'title': 'Baby Storytime', 'id': 1}]

        result = detect_hallucinated_events(response, known_events)

        self.assertEqual(len(result), 0)


class TestCompareRunResults(TestCase):
    """Tests for compare_run_results function."""

    def test_detects_settings_diff(self):
        run_a = {'settings': {'max_events': 10, 'location': 'Newton'}, 'events': [], 'diagnostics': {}}
        run_b = {'settings': {'max_events': 20, 'location': 'Newton'}, 'events': [], 'diagnostics': {}}

        result = compare_run_results(run_a, run_b)

        self.assertIn('max_events', result['settings_diff'])
        self.assertEqual(result['settings_diff']['max_events']['a'], 10)
        self.assertEqual(result['settings_diff']['max_events']['b'], 20)
        self.assertNotIn('location', result['settings_diff'])  # Same value, no diff

    def test_computes_metrics_diff(self):
        run_a = {
            'settings': {},
            'events': [],
            'diagnostics': {'retrieval_quality': {'total_candidates': 10, 'above_threshold': 8, 'top_score': 0.8}},
            'total_latency_ms': 2000,
            'final_answer_text': 'Short response',
        }
        run_b = {
            'settings': {},
            'events': [],
            'diagnostics': {'retrieval_quality': {'total_candidates': 20, 'above_threshold': 15, 'top_score': 0.75}},
            'total_latency_ms': 3000,
            'final_answer_text': 'This is a much longer response with more content',
        }

        result = compare_run_results(run_a, run_b)

        self.assertEqual(result['metrics_diff']['total_candidates']['a'], 10)
        self.assertEqual(result['metrics_diff']['total_candidates']['b'], 20)
        self.assertEqual(result['metrics_diff']['total_candidates']['diff'], 10)
        self.assertEqual(result['metrics_diff']['total_latency_ms']['diff'], 1000)


class TestComputeEventDiff(TestCase):
    """Tests for compute_event_diff function."""

    def test_all_events_different(self):
        events_a = [{'id': 1, 'title': 'Event A', 'similarity_score': 0.8}]
        events_b = [{'id': 2, 'title': 'Event B', 'similarity_score': 0.7}]

        result = compute_event_diff(events_a, events_b)

        self.assertEqual(len(result['only_in_a']), 1)
        self.assertEqual(len(result['only_in_b']), 1)
        self.assertEqual(len(result['in_both']), 0)

    def test_all_events_same(self):
        events_a = [
            {'id': 1, 'title': 'Event A', 'similarity_score': 0.8},
            {'id': 2, 'title': 'Event B', 'similarity_score': 0.7},
        ]
        events_b = [
            {'id': 1, 'title': 'Event A', 'similarity_score': 0.8},
            {'id': 2, 'title': 'Event B', 'similarity_score': 0.7},
        ]

        result = compute_event_diff(events_a, events_b)

        self.assertEqual(len(result['only_in_a']), 0)
        self.assertEqual(len(result['only_in_b']), 0)
        self.assertEqual(len(result['in_both']), 2)

    def test_partial_overlap(self):
        events_a = [
            {'id': 1, 'title': 'Event A', 'similarity_score': 0.8},
            {'id': 2, 'title': 'Event B', 'similarity_score': 0.7},
        ]
        events_b = [
            {'id': 2, 'title': 'Event B', 'similarity_score': 0.75},
            {'id': 3, 'title': 'Event C', 'similarity_score': 0.6},
        ]

        result = compute_event_diff(events_a, events_b)

        self.assertEqual(len(result['only_in_a']), 1)
        self.assertEqual(result['only_in_a'][0]['id'], 1)
        self.assertEqual(len(result['only_in_b']), 1)
        self.assertEqual(result['only_in_b'][0]['id'], 3)
        self.assertEqual(len(result['in_both']), 1)
        self.assertEqual(result['in_both'][0]['id'], 2)

    def test_summary_counts(self):
        events_a = [{'id': 1, 'title': 'A', 'similarity_score': 0.8}, {'id': 2, 'title': 'B', 'similarity_score': 0.7}]
        events_b = [{'id': 2, 'title': 'B', 'similarity_score': 0.7}, {'id': 3, 'title': 'C', 'similarity_score': 0.6}]

        result = compute_event_diff(events_a, events_b)

        self.assertEqual(result['summary']['only_a_count'], 1)
        self.assertEqual(result['summary']['only_b_count'], 1)
        self.assertEqual(result['summary']['in_both_count'], 1)
