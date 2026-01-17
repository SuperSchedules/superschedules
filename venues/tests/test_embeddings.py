"""Tests for venue embedding generation and RAG search."""

from django.test import TestCase
from unittest.mock import patch, MagicMock
import numpy as np

from venues.models import Venue
from api.rag_service import EventRAGService, RankedVenue, DualRAGResult


class VenueEmbeddingTextCreationTest(TestCase):
    """Test _create_venue_text method."""

    def setUp(self):
        self.rag_service = EventRAGService()
        self.venue = Venue.objects.create(
            name="Newton Free Library",
            city="Newton",
            state="MA",
            venue_kind="library",
            description="A welcoming community library with programs for all ages.",
            kids_summary="Great children's room with weekly storytime and STEM activities.",
            audience_tags=["families", "stroller_friendly"],
            audience_age_groups=["infant", "toddler", "child"],
            audience_primary="families",
        )

    def test_includes_name(self):
        text = self.rag_service._create_venue_text(self.venue)
        self.assertIn("Newton Free Library", text)

    def test_includes_description(self):
        text = self.rag_service._create_venue_text(self.venue)
        self.assertIn("welcoming community library", text)

    def test_includes_kids_summary(self):
        text = self.rag_service._create_venue_text(self.venue)
        self.assertIn("children's room", text)

    def test_includes_venue_kind_expansion(self):
        text = self.rag_service._create_venue_text(self.venue)
        self.assertIn("library", text)
        self.assertIn("reading", text)  # From expansion

    def test_includes_audience_tags(self):
        text = self.rag_service._create_venue_text(self.venue)
        self.assertIn("families", text)
        self.assertIn("stroller_friendly", text)

    def test_includes_age_group_expansions(self):
        text = self.rag_service._create_venue_text(self.venue)
        self.assertIn("toddlers", text)  # From expansion

    def test_includes_location(self):
        text = self.rag_service._create_venue_text(self.venue)
        self.assertIn("Newton", text)
        self.assertIn("MA", text)

    def test_skips_unknown_venue_kind(self):
        self.venue.venue_kind = "unknown"
        self.venue.save()
        text = self.rag_service._create_venue_text(self.venue)
        self.assertNotIn("unknown", text)

    def test_skips_general_audience_primary(self):
        self.venue.audience_primary = "general"
        self.venue.save()
        text = self.rag_service._create_venue_text(self.venue)
        self.assertNotIn("primarily for general", text)


class VenueEmbeddingGenerationTest(TestCase):
    """Test venue embedding generation."""

    def test_update_venue_embeddings_creates_embeddings(self):
        """Test that update_venue_embeddings creates embeddings for venues."""
        venue = Venue.objects.create(
            name="Test Library",
            city="Boston",
            state="MA",
            venue_kind="library",
        )
        self.assertIsNone(venue.embedding)

        # Mock the embedding client
        mock_client = MagicMock()
        mock_client.encode.return_value = np.random.rand(1, 384)

        rag_service = EventRAGService(embedding_client=mock_client)
        rag_service.update_venue_embeddings(venue_ids=[venue.id])

        venue.refresh_from_db()
        self.assertIsNotNone(venue.embedding)
        self.assertEqual(len(venue.embedding), 384)

    def test_update_venue_embeddings_skips_if_has_embedding(self):
        """Test that venues with embeddings are skipped when not forced."""
        embedding = [0.1] * 384
        venue = Venue.objects.create(
            name="Test Museum",
            city="Cambridge",
            state="MA",
            venue_kind="museum",
            embedding=embedding,
        )

        mock_client = MagicMock()
        rag_service = EventRAGService(embedding_client=mock_client)

        # Without venue_ids specified, should only update venues without embeddings
        rag_service.update_venue_embeddings()

        # encode should not be called since venue already has embedding
        mock_client.encode.assert_not_called()


class VenueToDictTest(TestCase):
    """Test _venue_to_dict method."""

    def setUp(self):
        self.rag_service = EventRAGService()
        self.venue = Venue.objects.create(
            name="Needham Library",
            city="Needham",
            state="MA",
            street_address="1139 Highland Ave",
            venue_kind="library",
            description="Public library serving the Needham community.",
            kids_summary="Active children's section with story hours.",
            audience_tags=["families"],
            audience_age_groups=["child"],
            audience_primary="families",
            website_url="https://needhamlibrary.org",
            latitude=42.2834,
            longitude=-71.2345,
        )

    def test_includes_basic_fields(self):
        result = self.rag_service._venue_to_dict(self.venue, 0.85)
        self.assertEqual(result['id'], self.venue.id)
        self.assertEqual(result['name'], "Needham Library")
        self.assertEqual(result['city'], "Needham")
        self.assertEqual(result['state'], "MA")

    def test_includes_descriptions(self):
        result = self.rag_service._venue_to_dict(self.venue, 0.85)
        self.assertIn("Public library", result['description'])
        self.assertIn("story hours", result['kids_summary'])

    def test_includes_coordinates(self):
        result = self.rag_service._venue_to_dict(self.venue, 0.85)
        self.assertAlmostEqual(result['latitude'], 42.2834, places=3)
        self.assertAlmostEqual(result['longitude'], -71.2345, places=3)

    def test_includes_similarity_score(self):
        result = self.rag_service._venue_to_dict(self.venue, 0.85)
        self.assertEqual(result['similarity_score'], 0.85)


class RankedVenueTest(TestCase):
    """Test RankedVenue dataclass."""

    def test_to_dict(self):
        venue_data = {'id': 1, 'name': 'Test Venue', 'city': 'Boston'}
        ranked = RankedVenue(venue_data=venue_data, final_score=0.756, tier='recommended')

        result = ranked.to_dict()
        self.assertEqual(result['id'], 1)
        self.assertEqual(result['name'], 'Test Venue')
        self.assertEqual(result['final_score'], 0.756)
        self.assertEqual(result['tier'], 'recommended')


class DualRAGResultTest(TestCase):
    """Test DualRAGResult dataclass."""

    def test_all_venue_ids(self):
        from api.rag_service import RankedEvent, RankingFactors

        v1 = RankedVenue({'id': 1, 'name': 'V1'}, 0.8, 'recommended')
        v2 = RankedVenue({'id': 2, 'name': 'V2'}, 0.7, 'additional')

        result = DualRAGResult(
            recommended_venues=[v1],
            additional_venues=[v2],
        )

        self.assertEqual(result.all_venue_ids, [1, 2])

    def test_all_event_ids(self):
        from api.rag_service import RankedEvent, RankingFactors

        factors = RankingFactors()
        e1 = RankedEvent({'id': 10, 'title': 'E1'}, 0.9, factors, 'recommended')
        e2 = RankedEvent({'id': 20, 'title': 'E2'}, 0.8, factors, 'additional')
        e3 = RankedEvent({'id': 30, 'title': 'E3'}, 0.7, factors, 'context')

        result = DualRAGResult(
            recommended_events=[e1],
            additional_events=[e2],
            context_events=[e3],
        )

        self.assertEqual(result.all_event_ids, [10, 20, 30])
