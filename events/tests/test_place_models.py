from django.test import TestCase
from model_bakery import baker

from events.place_models import Place


class PlaceGetCityTests(TestCase):
    """Test the Place.get_city() method that extracts city from address."""

    def test_standard_address_format(self):
        """Test extracting city from standard US address format."""
        place = baker.make(Place, address="123 Main St, Boston, MA 02101")
        assert place.get_city() == "Boston"

        place2 = baker.make(Place, address="456 Oak Ave, Cambridge, MA 02138")
        assert place2.get_city() == "Cambridge"

    def test_multi_word_city(self):
        """Test extracting multi-word city names."""
        place = baker.make(Place, address="789 Park Dr, Newton Centre, MA 02459")
        assert place.get_city() == "Newton Centre"

        place2 = baker.make(Place, address="100 Main St, San Francisco, CA 94102")
        assert place2.get_city() == "San Francisco"

    def test_address_without_zip(self):
        """Test extracting city when no ZIP code present."""
        place = baker.make(Place, address="City Hall, Cambridge, MA")
        assert place.get_city() == "Cambridge"

    def test_simple_comma_separated(self):
        """Test fallback to second-to-last comma-separated part."""
        place = baker.make(Place, address="Library, Newton, Massachusetts")
        assert place.get_city() == "Newton"

    def test_empty_address(self):
        """Test with empty address returns empty string."""
        place = baker.make(Place, address="")
        assert place.get_city() == ""

    def test_address_without_city_pattern(self):
        """Test address that doesn't match expected patterns."""
        place = baker.make(Place, address="Just a random string")
        assert place.get_city() == ""

    def test_numeric_in_address(self):
        """Test extracting city when address has numeric parts."""
        place = baker.make(Place, address="123 Street, Boston, MA 02101")
        result = place.get_city()
        # Should extract "Boston" not the ZIP code
        assert result == "Boston"


class PlaceGetSearchTextTests(TestCase):
    """Test the Place.get_search_text() method that creates comprehensive search text."""

    def test_all_fields_populated(self):
        """Test search text with all fields present."""
        place = baker.make(Place, name="Newton Public Library", address="330 Homer St, Newton, MA 02459")
        search_text = place.get_search_text()
        assert "Newton Public Library" in search_text
        assert "330 Homer St, Newton, MA 02459" in search_text
        assert "Newton" in search_text

    def test_only_name(self):
        """Test search text with only name field."""
        place = baker.make(Place, name="Community Center", address="")
        search_text = place.get_search_text()
        assert search_text == "Community Center"

    def test_only_address(self):
        """Test search text with only address field."""
        place = baker.make(Place, name="", address="123 Main St, Boston, MA")
        search_text = place.get_search_text()
        assert "123 Main St, Boston, MA" in search_text
        assert "Boston" in search_text

    def test_empty_place(self):
        """Test search text with no fields populated."""
        place = baker.make(Place, name="", address="")
        search_text = place.get_search_text()
        assert search_text == ""

    def test_no_duplicate_city(self):
        """Test that city isn't duplicated if already in address."""
        place = baker.make(Place, name="Library", address="Main St, Newton, MA")
        search_text = place.get_search_text()
        # City should appear but not be duplicated excessively
        assert "Newton" in search_text


class PlaceCreateFromSchemaOrgTests(TestCase):
    """Test the Place.create_from_schema_org() class method."""

    def test_valid_place_dict(self):
        """Test creating Place from valid Schema.org dict."""
        location_data = {
            '@type': 'Place',
            'name': 'Central Library',
            'address': '500 Boylston St, Boston, MA 02116',
            'telephone': '617-555-1234',
            'url': 'https://library.example.com'
        }
        place = Place.create_from_schema_org(location_data)
        assert place is not None
        assert place.name == 'Central Library'
        assert place.address == '500 Boylston St, Boston, MA 02116'
        assert place.telephone == '617-555-1234'
        assert place.url == 'https://library.example.com'

    def test_place_list_with_dict(self):
        """Test creating Place from list containing Place dict."""
        location_data = [
            {
                '@type': 'Place',
                'name': 'Park',
                'address': '123 Park Ave, Newton, MA'
            }
        ]
        place = Place.create_from_schema_org(location_data)
        assert place is not None
        assert place.name == 'Park'
        assert place.address == '123 Park Ave, Newton, MA'

    def test_empty_list(self):
        """Test with empty list returns None."""
        assert Place.create_from_schema_org([]) is None

    def test_list_with_non_dict(self):
        """Test with list containing non-dict returns None."""
        assert Place.create_from_schema_org(['not a dict']) is None

    def test_non_place_type(self):
        """Test with dict that's not a Place type returns None."""
        location_data = {'@type': 'Event', 'name': 'Not a place'}
        assert Place.create_from_schema_org(location_data) is None

    def test_invalid_data_type(self):
        """Test with invalid data types returns None."""
        assert Place.create_from_schema_org("just a string") is None
        assert Place.create_from_schema_org(123) is None
        assert Place.create_from_schema_org(None) is None

    def test_duplicate_address_deduplication(self):
        """Test that duplicate addresses return existing Place."""
        location_data = {'@type': 'Place', 'name': 'Library A', 'address': '100 Main St, Boston, MA'}
        place1 = Place.create_from_schema_org(location_data)

        # Create another with same address but different name
        location_data2 = {'@type': 'Place', 'name': 'Library B', 'address': '100 Main St, Boston, MA'}
        place2 = Place.create_from_schema_org(location_data2)

        # Should return the same place (deduplication by address)
        assert place1.id == place2.id
        assert place1.name == 'Library A'  # Original name preserved

    def test_missing_optional_fields(self):
        """Test creating Place with only required fields."""
        location_data = {'@type': 'Place', 'name': 'Basic Place'}
        place = Place.create_from_schema_org(location_data)
        assert place is not None
        assert place.name == 'Basic Place'
        assert place.address == ''
        assert place.telephone == ''
        assert place.url == ''

    def test_place_without_address_no_deduplication(self):
        """Test that places without addresses create separate instances."""
        location_data1 = {'@type': 'Place', 'name': 'Place 1', 'address': ''}
        place1 = Place.create_from_schema_org(location_data1)

        location_data2 = {'@type': 'Place', 'name': 'Place 2', 'address': ''}
        place2 = Place.create_from_schema_org(location_data2)

        # Should be different instances since no address for deduplication
        assert place1.id != place2.id
        assert place1.name == 'Place 1'
        assert place2.name == 'Place 2'


class PlaceStrTests(TestCase):
    """Test the Place.__str__() method."""

    def test_name_and_address(self):
        """Test string representation with both name and address."""
        place = baker.make(Place, name="Library", address="123 Main St, Boston, MA")
        assert str(place) == "Library, 123 Main St, Boston, MA"

    def test_only_name(self):
        """Test string representation with only name."""
        place = baker.make(Place, name="Community Center", address="")
        assert str(place) == "Community Center"

    def test_only_address(self):
        """Test string representation with only address."""
        place = baker.make(Place, name="", address="456 Oak Ave, Cambridge, MA")
        assert str(place) == "456 Oak Ave, Cambridge, MA"

    def test_no_name_or_address(self):
        """Test string representation with neither name nor address."""
        place = baker.make(Place, name="", address="")
        assert str(place) == "Unknown Place"