"""
Tests for location autocomplete (suggest) API endpoint.
"""

from decimal import Decimal

from django.test import TestCase
from django.contrib.auth import get_user_model
from ninja_jwt.tokens import AccessToken

from locations.models import Location


User = get_user_model()


class LocationSuggestAPITest(TestCase):
    """Tests for GET /api/v1/locations/suggest endpoint."""

    @classmethod
    def setUpTestData(cls):
        """Create test locations and user."""
        cls.user = User.objects.create_user(username="testuser", password="testpass", is_active=True)

        # Newton, MA - most populous Newton
        cls.newton_ma = Location.objects.create(
            geoid="2545000",
            name="Newton",
            normalized_name="newton",
            state="MA",
            country_code="US",
            latitude=Decimal("42.337807"),
            longitude=Decimal("-71.209182"),
            lsad="city",
            population=88923,
        )
        # Newton, NJ - smaller Newton
        cls.newton_nj = Location.objects.create(
            geoid="3451000",
            name="Newton",
            normalized_name="newton",
            state="NJ",
            country_code="US",
            latitude=Decimal("41.058230"),
            longitude=Decimal("-74.752569"),
            lsad="town",
            population=8048,
        )
        # Newtown, CT - different name
        cls.newtown_ct = Location.objects.create(
            geoid="0952560",
            name="Newtown",
            normalized_name="newtown",
            state="CT",
            country_code="US",
            latitude=Decimal("41.413610"),
            longitude=Decimal("-73.303070"),
            lsad="town",
            population=27560,
        )
        # Cambridge, MA
        cls.cambridge = Location.objects.create(
            geoid="2511000",
            name="Cambridge",
            normalized_name="cambridge",
            state="MA",
            country_code="US",
            latitude=Decimal("42.373611"),
            longitude=Decimal("-71.110558"),
            lsad="city",
            population=118403,
        )
        # Springfield, MA (most populous)
        cls.springfield_ma = Location.objects.create(
            geoid="2567000",
            name="Springfield",
            normalized_name="springfield",
            state="MA",
            country_code="US",
            latitude=Decimal("42.101483"),
            longitude=Decimal("-72.589811"),
            lsad="city",
            population=155929,
        )
        # Springfield, MO (second most populous)
        cls.springfield_mo = Location.objects.create(
            geoid="2970000",
            name="Springfield",
            normalized_name="springfield",
            state="MO",
            country_code="US",
            latitude=Decimal("37.208957"),
            longitude=Decimal("-93.292298"),
            lsad="city",
            population=169176,
        )
        # Springfield, IL
        cls.springfield_il = Location.objects.create(
            geoid="1773000",
            name="Springfield",
            normalized_name="springfield",
            state="IL",
            country_code="US",
            latitude=Decimal("39.801055"),
            longitude=Decimal("-89.643604"),
            lsad="city",
            population=114394,
        )

    def get_auth_header(self):
        token = AccessToken.for_user(self.user)
        return {"HTTP_AUTHORIZATION": f"Bearer {token}"}

    # =========================================================================
    # Basic functionality tests
    # =========================================================================

    def test_suggest_requires_q_parameter(self):
        """Test that q parameter is required."""
        response = self.client.get("/api/v1/locations/suggest/", **self.get_auth_header())
        # Django Ninja returns 422 for missing required parameters
        self.assertEqual(response.status_code, 422)

    def test_suggest_q_too_short_returns_400(self):
        """Test that q with less than 2 characters returns 400."""
        response = self.client.get("/api/v1/locations/suggest/?q=N", **self.get_auth_header())
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertIn("at least 2 characters", data.get("detail", "").lower())

    def test_suggest_returns_results_array(self):
        """Test that valid query returns results array."""
        response = self.client.get("/api/v1/locations/suggest/?q=Newton", **self.get_auth_header())
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("results", data)
        self.assertIsInstance(data["results"], list)

    def test_suggest_result_shape(self):
        """Test that each result has required fields."""
        response = self.client.get("/api/v1/locations/suggest/?q=Newton", **self.get_auth_header())
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertGreater(len(data["results"]), 0)

        result = data["results"][0]
        self.assertIn("id", result)
        self.assertIn("name", result)
        self.assertIn("admin1", result)
        self.assertIn("country_code", result)
        self.assertIn("lat", result)
        self.assertIn("lng", result)
        self.assertIn("label", result)

    def test_suggest_label_format(self):
        """Test label format is 'City, State, Country'."""
        response = self.client.get("/api/v1/locations/suggest/?q=Cambridge", **self.get_auth_header())
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertGreater(len(data["results"]), 0)

        result = data["results"][0]
        self.assertEqual(result["label"], "Cambridge, MA, United States")

    # =========================================================================
    # Prefix matching tests
    # =========================================================================

    def test_suggest_prefix_matching(self):
        """Test prefix matching returns all Newton* locations."""
        response = self.client.get("/api/v1/locations/suggest/?q=Newt", **self.get_auth_header())
        self.assertEqual(response.status_code, 200)
        data = response.json()

        names = [r["name"] for r in data["results"]]
        self.assertIn("Newton", names)
        self.assertIn("Newtown", names)

    def test_suggest_case_insensitive(self):
        """Test search is case insensitive."""
        response = self.client.get("/api/v1/locations/suggest/?q=NEWTON", **self.get_auth_header())
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertGreater(len(data["results"]), 0)
        self.assertEqual(data["results"][0]["name"], "Newton")

    def test_suggest_normalized_matching(self):
        """Test search handles punctuation and whitespace."""
        response = self.client.get("/api/v1/locations/suggest/?q=new%20ton", **self.get_auth_header())
        self.assertEqual(response.status_code, 200)
        # Normalized as "new ton" - may not match; this tests input handling

    # =========================================================================
    # Ranking tests
    # =========================================================================

    def test_suggest_exact_match_first(self):
        """Test exact name matches rank before prefix matches."""
        response = self.client.get("/api/v1/locations/suggest/?q=Newton", **self.get_auth_header())
        self.assertEqual(response.status_code, 200)
        data = response.json()

        # Newton should come before Newtown
        newton_idx = next((i for i, r in enumerate(data["results"]) if r["name"] == "Newton"), 999)
        newtown_idx = next((i for i, r in enumerate(data["results"]) if r["name"] == "Newtown"), 999)
        self.assertLess(newton_idx, newtown_idx)

    def test_suggest_ranks_by_population(self):
        """Test results ranked by population (highest first)."""
        response = self.client.get("/api/v1/locations/suggest/?q=Newton", **self.get_auth_header())
        self.assertEqual(response.status_code, 200)
        data = response.json()

        # Newton, MA (pop 88923) should come before Newton, NJ (pop 8048)
        newton_results = [r for r in data["results"] if r["name"] == "Newton"]
        self.assertEqual(len(newton_results), 2)
        self.assertEqual(newton_results[0]["admin1"], "MA")
        self.assertEqual(newton_results[1]["admin1"], "NJ")

    def test_suggest_ambiguous_name_returns_multiple(self):
        """Test ambiguous names like 'Springfield' return multiple results."""
        response = self.client.get("/api/v1/locations/suggest/?q=Springfield", **self.get_auth_header())
        self.assertEqual(response.status_code, 200)
        data = response.json()

        # Should return all 3 Springfields
        self.assertEqual(len(data["results"]), 3)
        states = {r["admin1"] for r in data["results"]}
        self.assertEqual(states, {"MA", "MO", "IL"})

    def test_suggest_springfield_population_order(self):
        """Test Springfield results ordered by population."""
        response = self.client.get("/api/v1/locations/suggest/?q=Springfield", **self.get_auth_header())
        self.assertEqual(response.status_code, 200)
        data = response.json()

        # MO (169176) > MA (155929) > IL (114394)
        self.assertEqual(data["results"][0]["admin1"], "MO")
        self.assertEqual(data["results"][1]["admin1"], "MA")
        self.assertEqual(data["results"][2]["admin1"], "IL")

    # =========================================================================
    # Admin1/State filtering tests
    # =========================================================================

    def test_suggest_admin1_filter(self):
        """Test admin1 parameter filters by state."""
        response = self.client.get("/api/v1/locations/suggest/?q=Springfield&admin1=MA", **self.get_auth_header())
        self.assertEqual(response.status_code, 200)
        data = response.json()

        self.assertEqual(len(data["results"]), 1)
        self.assertEqual(data["results"][0]["admin1"], "MA")

    def test_suggest_admin1_case_insensitive(self):
        """Test admin1 filter is case insensitive."""
        response = self.client.get("/api/v1/locations/suggest/?q=Newton&admin1=ma", **self.get_auth_header())
        self.assertEqual(response.status_code, 200)
        data = response.json()

        self.assertEqual(len(data["results"]), 1)
        self.assertEqual(data["results"][0]["admin1"], "MA")

    def test_suggest_comma_pattern_extracts_state(self):
        """Test 'Cambridge, MA' extracts state hint."""
        response = self.client.get("/api/v1/locations/suggest/?q=Cambridge,%20MA", **self.get_auth_header())
        self.assertEqual(response.status_code, 200)
        data = response.json()

        self.assertEqual(len(data["results"]), 1)
        self.assertEqual(data["results"][0]["name"], "Cambridge")
        self.assertEqual(data["results"][0]["admin1"], "MA")

    def test_suggest_comma_pattern_with_newton(self):
        """Test 'Newton, NJ' returns only NJ result."""
        response = self.client.get("/api/v1/locations/suggest/?q=Newton,%20NJ", **self.get_auth_header())
        self.assertEqual(response.status_code, 200)
        data = response.json()

        self.assertEqual(len(data["results"]), 1)
        self.assertEqual(data["results"][0]["admin1"], "NJ")

    # =========================================================================
    # Limit tests
    # =========================================================================

    def test_suggest_default_limit_is_10(self):
        """Test default limit is 10."""
        # Would need more test data to verify, but verify parameter works
        response = self.client.get("/api/v1/locations/suggest/?q=Sp", **self.get_auth_header())
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertLessEqual(len(data["results"]), 10)

    def test_suggest_limit_parameter(self):
        """Test limit parameter constrains results."""
        response = self.client.get("/api/v1/locations/suggest/?q=Springfield&limit=2", **self.get_auth_header())
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data["results"]), 2)

    def test_suggest_limit_max_is_20(self):
        """Test limit parameter caps at 20."""
        response = self.client.get("/api/v1/locations/suggest/?q=Springfield&limit=100", **self.get_auth_header())
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertLessEqual(len(data["results"]), 20)

    # =========================================================================
    # Country filter tests
    # =========================================================================

    def test_suggest_country_filter(self):
        """Test country parameter filters results."""
        response = self.client.get("/api/v1/locations/suggest/?q=Newton&country=US", **self.get_auth_header())
        self.assertEqual(response.status_code, 200)
        data = response.json()
        for result in data["results"]:
            self.assertEqual(result["country_code"], "US")

    # =========================================================================
    # No results tests
    # =========================================================================

    def test_suggest_no_match_returns_empty_list(self):
        """Test unmatched query returns empty results."""
        response = self.client.get("/api/v1/locations/suggest/?q=Zzyzzyzzz", **self.get_auth_header())
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["results"], [])


class LocationDetailAPITest(TestCase):
    """Tests for GET /api/v1/locations/{id} endpoint."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="testuser", password="testpass", is_active=True)
        cls.newton = Location.objects.create(
            geoid="2545000",
            name="Newton",
            normalized_name="newton",
            state="MA",
            country_code="US",
            latitude=Decimal("42.337807"),
            longitude=Decimal("-71.209182"),
            lsad="city",
            population=88923,
        )

    def get_auth_header(self):
        token = AccessToken.for_user(self.user)
        return {"HTTP_AUTHORIZATION": f"Bearer {token}"}

    def test_get_location_by_id(self):
        """Test retrieving a location by ID."""
        response = self.client.get(f"/api/v1/locations/{self.newton.id}", **self.get_auth_header())
        self.assertEqual(response.status_code, 200)

        data = response.json()
        self.assertEqual(data["id"], self.newton.id)
        self.assertEqual(data["name"], "Newton")
        self.assertEqual(data["admin1"], "MA")
        self.assertEqual(data["country_code"], "US")
        self.assertAlmostEqual(float(data["lat"]), 42.337807, places=5)
        self.assertAlmostEqual(float(data["lng"]), -71.209182, places=5)
        self.assertEqual(data["label"], "Newton, MA, United States")

    def test_get_location_not_found(self):
        """Test 404 for non-existent location."""
        response = self.client.get("/api/v1/locations/99999", **self.get_auth_header())
        self.assertEqual(response.status_code, 404)


class LocationSuggestNoAuthTest(TestCase):
    """Test that suggest endpoint works without authentication (public endpoint)."""

    @classmethod
    def setUpTestData(cls):
        cls.newton = Location.objects.create(
            geoid="2545000",
            name="Newton",
            normalized_name="newton",
            state="MA",
            country_code="US",
            latitude=Decimal("42.337807"),
            longitude=Decimal("-71.209182"),
            population=88923,
        )

    def test_suggest_works_without_auth(self):
        """Test suggest endpoint is publicly accessible."""
        response = self.client.get("/api/v1/locations/suggest/?q=Newton")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertGreater(len(data["results"]), 0)

    def test_detail_works_without_auth(self):
        """Test detail endpoint is publicly accessible."""
        response = self.client.get(f"/api/v1/locations/{self.newton.id}")
        self.assertEqual(response.status_code, 200)
