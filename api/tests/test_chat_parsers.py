from django.test import TestCase

from api import views as api_views


class ChatParsersTests(TestCase):
    def test_parse_ages_from_message(self):
        assert api_views._parse_ages_from_message("for 5 year old") == [5]
        assert api_views._parse_ages_from_message("for 4-6 years old") == [4, 6]
        assert api_views._parse_ages_from_message("ages 3 to 5 years old") == [3, 5]
        assert api_views._parse_ages_from_message("no ages here") is None

    def test_parse_location_from_message(self):
        Ctx = api_views.ChatContextSchema
        assert api_views._parse_location_from_message("events in Boston", Ctx()) == "boston"
        assert api_views._parse_location_from_message("meet at San Jose, please", Ctx()) == "san jose"
        assert api_views._parse_location_from_message("no location", Ctx(location="seattle")) == "seattle"

    def test_parse_timeframe_from_message(self):
        assert api_views._parse_timeframe_from_message("today") == "today"
        assert api_views._parse_timeframe_from_message("tomorrow") == "tomorrow"
        assert api_views._parse_timeframe_from_message("this weekend") == "this weekend"
        assert api_views._parse_timeframe_from_message("something else") == "upcoming"

    def test_detect_topic_change(self):
        assert api_views._detect_topic_change("actually let's do something different") is True
        assert api_views._detect_topic_change("keep going") is False

    def test_extract_follow_up_questions(self):
        text = "Do you like indoor events? Any age range? Great!"
        questions = api_views._extract_follow_up_questions(text)
        assert questions == ["Do you like indoor events?", "Any age range?"]

