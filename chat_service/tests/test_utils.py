from __future__ import annotations

from django.test import TestCase

from chat_service.fastapi_app import extract_follow_up_questions


class ExtractFollowUpQuestionsTests(TestCase):
    def test_extracts_up_to_three_questions(self):
        text = "What time is it? Here are details. Do you prefer indoors? Great! Anything else?"
        qs = extract_follow_up_questions(text)
        assert qs == [
            "What time is it?",
            "Do you prefer indoors?",
            "Anything else?",
        ]

