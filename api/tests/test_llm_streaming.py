"""
Tests for LLM streaming functionality and model management.
"""

import asyncio
from unittest.mock import patch, AsyncMock, MagicMock
from django.test import TestCase

from api.llm_service import OllamaService, ModelResponse


class LLMStreamingTests(TestCase):
    """Test LLM streaming and model management functionality."""
    
    def setUp(self):
        self.service = OllamaService()
    
    @patch('ollama.AsyncClient')
    async def test_get_available_models_success(self, mock_client_class):
        """Test successful retrieval of available models."""
        mock_client = AsyncMock()
        mock_client_class.return_value = mock_client
        
        # Mock successful response
        mock_client.list.return_value = {
            'models': [
                {'name': 'llama3.2:3b'},
                {'name': 'deepseek-llm:7b'},
                {'model': 'mistral:7b'}  # Test alternative key
            ]
        }
        
        service = OllamaService()
        models = await service.get_available_models()
        
        self.assertEqual(models, ['llama3.2:3b', 'deepseek-llm:7b', 'mistral:7b'])
        mock_client.list.assert_called_once()
    
    @patch('ollama.AsyncClient')
    async def test_get_available_models_error(self, mock_client_class):
        """Test error handling when getting models fails."""
        mock_client = AsyncMock()
        mock_client_class.return_value = mock_client
        
        # Mock exception
        mock_client.list.side_effect = Exception("Connection failed")
        
        service = OllamaService()
        models = await service.get_available_models()
        
        self.assertEqual(models, [])
        mock_client.list.assert_called_once()
    
    @patch('ollama.AsyncClient')
    async def test_streaming_response_success(self, mock_client_class):
        """Test successful streaming response generation."""
        mock_client = AsyncMock()
        mock_client_class.return_value = mock_client
        
        # Mock streaming chunks
        async def mock_stream():
            yield {'message': {'content': 'Hello'}, 'done': False}
            yield {'message': {'content': ' there'}, 'done': False}
            # The final chunk from Ollama indicates completion
            # Note: Our implementation processes this and adds its own final chunk
        
        mock_client.chat.return_value = mock_stream()
        
        service = OllamaService()
        chunks = []
        async for chunk in service.generate_streaming_response(
            model="test-model",
            prompt="Hello",
            system_prompt="You are helpful"
        ):
            chunks.append(chunk)
        
        # Should have 3 chunks: 2 content + 1 final
        # (The empty content chunk from mock gets processed + final completion chunk)
        self.assertEqual(len(chunks), 3)
        
        # Check content chunks
        self.assertEqual(chunks[0]['token'], 'Hello')
        self.assertFalse(chunks[0]['done'])
        self.assertEqual(chunks[1]['token'], ' there')
        self.assertFalse(chunks[1]['done'])
        
        # Check final chunk (added by our implementation)
        final_chunk = chunks[-1]
        self.assertEqual(final_chunk['token'], '')
        self.assertTrue(final_chunk['done'])
        self.assertTrue(final_chunk['success'])
        self.assertIn('response_time_ms', final_chunk)
        self.assertIn('full_response', final_chunk)
    
    @patch('ollama.AsyncClient')
    async def test_streaming_response_error(self, mock_client_class):
        """Test error handling in streaming response."""
        mock_client = AsyncMock()
        mock_client_class.return_value = mock_client
        
        # Mock exception during streaming
        mock_client.chat.side_effect = Exception("Model not found")
        
        service = OllamaService()
        chunks = []
        async for chunk in service.generate_streaming_response(
            model="nonexistent-model",
            prompt="Hello"
        ):
            chunks.append(chunk)
        
        # Should have 1 error chunk
        self.assertEqual(len(chunks), 1)
        self.assertTrue(chunks[0]['done'])
        self.assertFalse(chunks[0]['success'])
        self.assertIn('Model not found', chunks[0]['error'])
    
    def test_get_available_models_sync_wrapper(self):
        """Test the sync wrapper for get_available_models."""
        async def run_test():
            return await self.test_get_available_models_success()
        
        # Run async test in sync context
        asyncio.run(run_test())
    
    def test_streaming_response_sync_wrapper(self):
        """Test the sync wrapper for streaming response."""
        async def run_test():
            return await self.test_streaming_response_success()
        
        # Run async test in sync context
        asyncio.run(run_test())