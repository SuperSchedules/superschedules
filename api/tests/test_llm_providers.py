"""
Tests for LLM provider abstraction layer (factory, base, Ollama, Bedrock).
"""

import asyncio
from unittest.mock import patch, AsyncMock, MagicMock
from django.test import TestCase, override_settings

from api.llm_providers import (
    BaseLLMProvider,
    ModelResponse,
    get_llm_provider,
    reset_provider,
    OllamaProvider,
    BedrockProvider,
)


class ProviderFactoryTests(TestCase):
    """Test the provider factory selection logic."""

    def tearDown(self):
        # Reset provider singleton between tests
        reset_provider()

    @override_settings(LLM_PROVIDER='ollama')
    def test_factory_returns_ollama_provider(self):
        """Test factory returns OllamaProvider when configured."""
        provider = get_llm_provider()
        self.assertIsInstance(provider, OllamaProvider)
        self.assertEqual(provider.primary_model, 'deepseek-llm:7b')

    @override_settings(LLM_PROVIDER='bedrock')
    def test_factory_returns_bedrock_provider(self):
        """Test factory returns BedrockProvider when configured."""
        provider = get_llm_provider()
        self.assertIsInstance(provider, BedrockProvider)
        self.assertEqual(provider.primary_model, 'anthropic.claude-3-haiku-20240307-v1:0')

    @override_settings(LLM_PROVIDER='unknown')
    def test_factory_defaults_to_ollama_for_unknown(self):
        """Test factory defaults to Ollama for unknown provider types."""
        provider = get_llm_provider()
        self.assertIsInstance(provider, OllamaProvider)

    def test_factory_singleton_pattern(self):
        """Test factory returns same instance on repeated calls."""
        provider1 = get_llm_provider()
        provider2 = get_llm_provider()
        self.assertIs(provider1, provider2)

    def test_reset_provider_clears_singleton(self):
        """Test reset_provider clears the singleton instance."""
        provider1 = get_llm_provider()
        reset_provider()
        provider2 = get_llm_provider()
        self.assertIsNot(provider1, provider2)


class OllamaProviderTests(TestCase):
    """Test OllamaProvider implementation."""

    @patch('api.llm_providers.ollama.ollama.AsyncClient')
    def test_ollama_provider_initialization(self, mock_client_class):
        """Test OllamaProvider initializes correctly."""
        provider = OllamaProvider()
        self.assertEqual(provider.primary_model, 'deepseek-llm:7b')
        self.assertEqual(provider.backup_model, 'llama3.2:3b')

    @patch('api.llm_providers.ollama.ollama.AsyncClient')
    async def test_ollama_get_available_models(self, mock_client_class):
        """Test OllamaProvider.get_available_models()."""
        mock_client = AsyncMock()
        mock_client_class.return_value = mock_client
        mock_client.list.return_value = {
            'models': [
                {'name': 'model1'},
                {'name': 'model2'},
            ]
        }

        provider = OllamaProvider()
        models = await provider.get_available_models()

        self.assertEqual(models, ['model1', 'model2'])
        mock_client.list.assert_called_once()

    @patch('api.llm_providers.ollama.ollama.AsyncClient')
    async def test_ollama_generate_response_success(self, mock_client_class):
        """Test OllamaProvider.generate_response() success case."""
        mock_client = AsyncMock()
        mock_client_class.return_value = mock_client
        mock_client.chat.return_value = {
            'message': {'content': 'Test response'}
        }

        provider = OllamaProvider()
        response = await provider.generate_response(
            model='test-model',
            prompt='test prompt',
            system_prompt='test system'
        )

        self.assertIsInstance(response, ModelResponse)
        self.assertTrue(response.success)
        self.assertEqual(response.model_name, 'test-model')
        self.assertEqual(response.response, 'Test response')
        self.assertGreaterEqual(response.response_time_ms, 0)
        self.assertIsNone(response.error)

    @patch('api.llm_providers.ollama.ollama.AsyncClient')
    async def test_ollama_generate_response_timeout(self, mock_client_class):
        """Test OllamaProvider.generate_response() timeout handling."""
        mock_client = AsyncMock()
        mock_client_class.return_value = mock_client
        mock_client.chat.side_effect = asyncio.TimeoutError()

        provider = OllamaProvider()
        response = await provider.generate_response(
            model='test-model',
            prompt='test prompt',
            timeout_seconds=1
        )

        self.assertIsInstance(response, ModelResponse)
        self.assertFalse(response.success)
        self.assertEqual(response.response, '')
        self.assertIn('Timeout', response.error)

    @patch('api.llm_providers.ollama.ollama.AsyncClient')
    async def test_ollama_streaming_response(self, mock_client_class):
        """Test OllamaProvider.generate_streaming_response()."""
        mock_client = AsyncMock()
        mock_client_class.return_value = mock_client

        async def mock_stream():
            yield {'message': {'content': 'Hello'}, 'done': False}
            yield {'message': {'content': ' world'}, 'done': False}

        mock_client.chat.return_value = mock_stream()

        provider = OllamaProvider()
        chunks = []
        async for chunk in provider.generate_streaming_response(
            model='test-model',
            prompt='test prompt'
        ):
            chunks.append(chunk)

        # Should have content chunks + final completion chunk
        self.assertGreaterEqual(len(chunks), 2)
        self.assertEqual(chunks[0]['token'], 'Hello')
        self.assertFalse(chunks[0]['done'])
        self.assertTrue(chunks[-1]['done'])
        self.assertTrue(chunks[-1]['success'])

    def test_ollama_get_available_models_sync(self):
        """Sync wrapper for async test."""
        asyncio.run(self.test_ollama_get_available_models())

    def test_ollama_generate_response_sync(self):
        """Sync wrapper for async test."""
        asyncio.run(self.test_ollama_generate_response_success())

    def test_ollama_streaming_sync(self):
        """Sync wrapper for async test."""
        asyncio.run(self.test_ollama_streaming_response())


class BedrockProviderTests(TestCase):
    """Test BedrockProvider implementation."""

    @patch('api.llm_providers.bedrock.boto3.client')
    def test_bedrock_provider_initialization(self, mock_boto_client):
        """Test BedrockProvider initializes correctly."""
        provider = BedrockProvider()
        self.assertEqual(provider.primary_model, 'anthropic.claude-3-haiku-20240307-v1:0')
        self.assertEqual(provider.backup_model, 'anthropic.claude-3-sonnet-20240229-v1:0')
        self.assertEqual(provider.region, 'us-east-1')
        mock_boto_client.assert_called_once_with('bedrock-runtime', region_name='us-east-1')

    @patch('api.llm_providers.bedrock.boto3.client')
    async def test_bedrock_get_available_models(self, mock_boto_client):
        """Test BedrockProvider.get_available_models()."""
        mock_runtime_client = MagicMock()
        mock_bedrock_client = MagicMock()
        mock_boto_client.side_effect = [mock_runtime_client, mock_bedrock_client]

        mock_bedrock_client.list_foundation_models.return_value = {
            'modelSummaries': [
                {'modelId': 'anthropic.claude-3-haiku-20240307-v1:0'},
                {'modelId': 'anthropic.claude-3-sonnet-20240229-v1:0'},
            ]
        }

        # Create provider (this will call boto3.client once)
        provider = BedrockProvider()
        # Now get_available_models will call boto3.client again
        models = await provider.get_available_models()

        self.assertEqual(len(models), 2)
        self.assertIn('anthropic.claude-3-haiku-20240307-v1:0', models)

    @patch('api.llm_providers.bedrock.boto3.client')
    async def test_bedrock_generate_response_success(self, mock_boto_client):
        """Test BedrockProvider.generate_response() success case."""
        mock_client = MagicMock()
        mock_boto_client.return_value = mock_client

        mock_response = {
            'body': MagicMock(read=lambda: b'{"content": [{"text": "Test response from Claude"}]}')
        }
        mock_client.invoke_model.return_value = mock_response

        provider = BedrockProvider()
        response = await provider.generate_response(
            model='anthropic.claude-3-haiku-20240307-v1:0',
            prompt='test prompt',
            system_prompt='test system'
        )

        self.assertIsInstance(response, ModelResponse)
        self.assertTrue(response.success)
        self.assertEqual(response.model_name, 'anthropic.claude-3-haiku-20240307-v1:0')
        self.assertEqual(response.response, 'Test response from Claude')
        self.assertGreaterEqual(response.response_time_ms, 0)
        self.assertIsNone(response.error)

    @patch('api.llm_providers.bedrock.boto3.client')
    async def test_bedrock_generate_response_error(self, mock_boto_client):
        """Test BedrockProvider.generate_response() error handling."""
        from botocore.exceptions import ClientError

        mock_client = MagicMock()
        mock_boto_client.return_value = mock_client

        mock_client.invoke_model.side_effect = ClientError(
            {'Error': {'Code': 'ModelNotFound', 'Message': 'Model not found'}},
            'InvokeModel'
        )

        provider = BedrockProvider()
        response = await provider.generate_response(
            model='nonexistent-model',
            prompt='test prompt'
        )

        self.assertIsInstance(response, ModelResponse)
        self.assertFalse(response.success)
        self.assertEqual(response.response, '')
        self.assertIn('ModelNotFound', response.error)

    @patch('api.llm_providers.bedrock.boto3.client')
    async def test_bedrock_streaming_response(self, mock_boto_client):
        """Test BedrockProvider.generate_streaming_response()."""
        import json

        mock_client = MagicMock()
        mock_boto_client.return_value = mock_client

        # Mock streaming response chunks
        mock_chunks = [
            {
                'chunk': {
                    'bytes': json.dumps({
                        'type': 'content_block_delta',
                        'delta': {'type': 'text_delta', 'text': 'Hello'}
                    }).encode()
                }
            },
            {
                'chunk': {
                    'bytes': json.dumps({
                        'type': 'content_block_delta',
                        'delta': {'type': 'text_delta', 'text': ' world'}
                    }).encode()
                }
            },
            {
                'chunk': {
                    'bytes': json.dumps({
                        'type': 'message_stop'
                    }).encode()
                }
            },
        ]

        mock_client.invoke_model_with_response_stream.return_value = {
            'body': iter(mock_chunks)
        }

        provider = BedrockProvider()
        chunks = []
        async for chunk in provider.generate_streaming_response(
            model='anthropic.claude-3-haiku-20240307-v1:0',
            prompt='test prompt'
        ):
            chunks.append(chunk)

        # Should have 2 content chunks + 1 final completion chunk
        self.assertEqual(len(chunks), 3)
        self.assertEqual(chunks[0]['token'], 'Hello')
        self.assertFalse(chunks[0]['done'])
        self.assertEqual(chunks[1]['token'], ' world')
        self.assertFalse(chunks[1]['done'])
        self.assertTrue(chunks[2]['done'])
        self.assertTrue(chunks[2]['success'])
        self.assertEqual(chunks[2]['full_response'], 'Hello world')

    def test_bedrock_get_available_models_sync(self):
        """Sync wrapper for async test."""
        asyncio.run(self.test_bedrock_get_available_models())

    def test_bedrock_generate_response_sync(self):
        """Sync wrapper for async test."""
        asyncio.run(self.test_bedrock_generate_response_success())

    def test_bedrock_streaming_sync(self):
        """Sync wrapper for async test."""
        asyncio.run(self.test_bedrock_streaming_response())


class ModelResponseTests(TestCase):
    """Test ModelResponse dataclass."""

    def test_model_response_creation(self):
        """Test ModelResponse can be created with required fields."""
        response = ModelResponse(
            model_name='test-model',
            response='test response',
            response_time_ms=100,
            success=True
        )

        self.assertEqual(response.model_name, 'test-model')
        self.assertEqual(response.response, 'test response')
        self.assertEqual(response.response_time_ms, 100)
        self.assertTrue(response.success)
        self.assertIsNone(response.error)

    def test_model_response_with_error(self):
        """Test ModelResponse with error field."""
        response = ModelResponse(
            model_name='test-model',
            response='',
            response_time_ms=50,
            success=False,
            error='Connection failed'
        )

        self.assertFalse(response.success)
        self.assertEqual(response.error, 'Connection failed')
