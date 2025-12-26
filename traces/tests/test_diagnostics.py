"""
Tests for the diagnostics engine.
"""

from django.test import TestCase

from traces.diagnostics import compute_diagnostics, format_diagnostics_html


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
