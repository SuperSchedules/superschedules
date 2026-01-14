from datetime import datetime, timedelta
from django.test import TestCase
from django.utils import timezone
from model_bakery import baker

from api import views as api_views
from events.models import Event
from venues.models import Venue


class ChatParsersTests(TestCase):
    def test_parse_ages_from_message(self):
        assert api_views._parse_ages_from_message("for 5 year old") == [5]
        assert api_views._parse_ages_from_message("for 4-6 years old") == [4, 6]
        assert api_views._parse_ages_from_message("ages 3 to 5 years old") == [3, 5]
        assert api_views._parse_ages_from_message("no ages here") is None

    def test_parse_ages_edge_cases(self):
        assert api_views._parse_ages_from_message("15 and 17 year olds") == [15, 17]
        assert api_views._parse_ages_from_message("3-5 year olds") == [3, 5]
        assert api_views._parse_ages_from_message("3 - 5 years old") == [3, 5]
        # Regex requires "old" after "years" - won't match "for 8 years" alone
        assert api_views._parse_ages_from_message("for 8 years old") == [8]
        assert api_views._parse_ages_from_message("for 8 years") is None

    def test_parse_location_from_message(self):
        Ctx = api_views.ChatContextSchema
        assert api_views._parse_location_from_message("events in Boston", Ctx()) == "boston"
        assert api_views._parse_location_from_message("meet at San Jose, please", Ctx()) == "san jose"
        assert api_views._parse_location_from_message("no location", Ctx(location="seattle")) == "seattle"

    def test_parse_location_edge_cases(self):
        Ctx = api_views.ChatContextSchema
        assert api_views._parse_location_from_message("activities in Newton.", Ctx()) == "newton"
        assert api_views._parse_location_from_message("things near Cambridge!", Ctx()) == "cambridge"
        # Parser stops at punctuation - "Boston, MA" captures only "boston"
        result = api_views._parse_location_from_message("events in Boston, MA", Ctx())
        assert result == "boston"
        assert api_views._parse_location_from_message("just some text", Ctx()) is None

    def test_parse_timeframe_from_message(self):
        assert api_views._parse_timeframe_from_message("today") == "today"
        assert api_views._parse_timeframe_from_message("tomorrow") == "tomorrow"
        assert api_views._parse_timeframe_from_message("this weekend") == "this weekend"
        assert api_views._parse_timeframe_from_message("something else") == "upcoming"

    def test_parse_timeframe_variations(self):
        assert api_views._parse_timeframe_from_message("this week") == "this week"
        assert api_views._parse_timeframe_from_message("this month") == "this month"
        assert api_views._parse_timeframe_from_message("next 3 hours") == "next 3 hours"
        assert api_views._parse_timeframe_from_message("next 2 days") == "next 2 days"
        assert api_views._parse_timeframe_from_message("next week") == "next week"
        assert api_views._parse_timeframe_from_message("next month") == "next month"
        assert api_views._parse_timeframe_from_message("sometime later") == "upcoming"
        assert api_views._parse_timeframe_from_message("") == "upcoming"

    def test_detect_topic_change(self):
        assert api_views._detect_topic_change("actually let's do something different") is True
        assert api_views._detect_topic_change("keep going") is False

    def test_detect_topic_change_all_keywords(self):
        assert api_views._detect_topic_change("actually I want something else") is True
        assert api_views._detect_topic_change("instead show me parks") is True
        assert api_views._detect_topic_change("nevermind, let's try museums") is True
        assert api_views._detect_topic_change("I want something different") is True
        assert api_views._detect_topic_change("let's change to sports") is True
        assert api_views._detect_topic_change("Actually, I prefer indoors") is True
        assert api_views._detect_topic_change("INSTEAD show outdoor events") is True
        assert api_views._detect_topic_change("tell me more") is False
        assert api_views._detect_topic_change("that sounds great") is False
        assert api_views._detect_topic_change("show me the details") is False

    def test_extract_follow_up_questions(self):
        text = "Do you like indoor events? Any age range? Great!"
        questions = api_views._extract_follow_up_questions(text)
        assert questions == ["Do you like indoor events?", "Any age range?"]

    def test_extract_follow_up_questions_edge_cases(self):
        text = "Here are some events. They look great."
        assert api_views._extract_follow_up_questions(text) == []

        text = "Would you like more options?"
        questions = api_views._extract_follow_up_questions(text)
        assert len(questions) == 1
        assert questions[0] == "Would you like more options?"

        # Limited to 3 questions max
        text = "Question 1? Question 2? Question 3? Question 4? Question 5?"
        questions = api_views._extract_follow_up_questions(text)
        assert len(questions) == 3
        assert questions == ["Question 1?", "Question 2?", "Question 3?"]

        text = "Would you like indoor or outdoor events?"
        questions = api_views._extract_follow_up_questions(text)
        assert len(questions) == 1
        assert "indoor or outdoor" in questions[0]


class GetRelevantEventIdsTests(TestCase):
    def setUp(self):
        now = timezone.now()

        # Create venues for different cities
        self.newton_venue = baker.make(Venue, name="Newton Library", city="Newton", state="MA")
        self.boston_venue = baker.make(Venue, name="Boston Center", city="Boston", state="MA")
        self.cambridge_venue = baker.make(Venue, name="Cambridge Hall", city="Cambridge", state="MA")

        self.today_newton = baker.make(Event, title="Today Newton Event", venue=self.newton_venue,
                                       start_time=now.replace(hour=14, minute=0))
        self.today_boston = baker.make(Event, title="Today Boston Event", venue=self.boston_venue,
                                       start_time=now.replace(hour=15, minute=0))

        tomorrow = now + timedelta(days=1)
        self.tomorrow_newton = baker.make(Event, title="Tomorrow Newton Event",
                                         venue=self.newton_venue, start_time=tomorrow.replace(hour=10, minute=0))

        next_week = now + timedelta(days=5)
        self.next_week_cambridge = baker.make(Event, title="Next Week Cambridge Event",
                                             venue=self.cambridge_venue, start_time=next_week.replace(hour=11, minute=0))

        far_future = now + timedelta(days=60)
        self.far_future_event = baker.make(Event, title="Far Future Event", venue=self.newton_venue,
                                          start_time=far_future.replace(hour=12, minute=0))

    def test_filter_by_location(self):
        result = api_views._get_relevant_event_ids(ages=None, location="Newton", timeframe="upcoming", user=None)
        assert len(result) <= 3
        events = Event.objects.filter(id__in=result)
        for event in events:
            assert "Newton" in event.get_location_string()

    def test_filter_by_timeframe_today(self):
        result = api_views._get_relevant_event_ids(ages=None, location=None, timeframe="today", user=None)
        events = Event.objects.filter(id__in=result)
        today = timezone.now().date()
        for event in events:
            assert event.start_time.date() == today

    def test_filter_by_timeframe_tomorrow(self):
        result = api_views._get_relevant_event_ids(ages=None, location=None, timeframe="tomorrow", user=None)
        events = Event.objects.filter(id__in=result)
        tomorrow = (timezone.now() + timedelta(days=1)).date()
        for event in events:
            assert event.start_time.date() == tomorrow

    def test_filter_by_timeframe_week(self):
        result = api_views._get_relevant_event_ids(ages=None, location=None, timeframe="this week", user=None)
        events = Event.objects.filter(id__in=result)
        max_date = timezone.now() + timedelta(days=7)
        for event in events:
            assert event.start_time <= max_date

    def test_filter_combined_location_and_time(self):
        result = api_views._get_relevant_event_ids(ages=None, location="Newton", timeframe="today", user=None)
        events = Event.objects.filter(id__in=result)
        today = timezone.now().date()
        for event in events:
            assert "Newton" in event.get_location_string()
            assert event.start_time.date() == today

    def test_limit_to_three_results(self):
        now = timezone.now()
        for i in range(5):
            baker.make(Event, title=f"Extra Event {i}", venue=self.boston_venue,
                      start_time=now.replace(hour=10+i, minute=0))

        result = api_views._get_relevant_event_ids(ages=None, location=None, timeframe="today", user=None)
        assert len(result) <= 3

    def test_empty_database(self):
        Event.objects.all().delete()
        result = api_views._get_relevant_event_ids(ages=None, location="Anywhere", timeframe="today", user=None)
        assert result == []

    def test_no_matching_events(self):
        result = api_views._get_relevant_event_ids(ages=None, location="NonexistentCity", timeframe="today", user=None)
        assert result == []

