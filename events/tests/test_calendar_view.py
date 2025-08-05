from django.test import TestCase
from django.urls import reverse


class CalendarViewTests(TestCase):
    def test_calendar_page_renders(self):
        response = self.client.get(reverse("calendar"))
        self.assertEqual(response.status_code, 200)
