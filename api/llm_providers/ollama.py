"""
Ollama LLM provider implementation.
Connects to a local Ollama instance for model inference.
"""

import asyncio
import logging
from typing import List, Optional, AsyncGenerator, Dict, Any
from datetime import datetime

import ollama
from django.conf import settings

from .base import BaseLLMProvider, ModelResponse


logger = logging.getLogger(__name__)


class OllamaProvider(BaseLLMProvider):
    """Provider for interacting with Ollama LLM models."""

    def __init__(self):
        self.client = ollama.AsyncClient()
        # Use Django settings for model configuration
        self._primary_model = getattr(settings, 'LLM_PRIMARY_MODEL', 'deepseek-llm:7b')
        self._backup_model = getattr(settings, 'LLM_BACKUP_MODEL', 'llama3.2:3b')

    @property
    def primary_model(self) -> str:
        return self._primary_model

    @property
    def backup_model(self) -> Optional[str]:
        return self._backup_model

    async def get_available_models(self) -> List[str]:
        """Get list of available Ollama models."""
        try:
            models = await self.client.list()
            return [model.get('name', model.get('model', 'unknown')) for model in models.get('models', [])]
        except Exception as e:
            logger.error(f"Failed to get available models: {e}")
            return []

    async def generate_response(
        self,
        model: str,
        prompt: str,
        system_prompt: Optional[str] = None,
        timeout_seconds: int = 30,
        stream: bool = False
    ) -> ModelResponse:
        """Generate response from a single model."""
        start_time = datetime.now()

        try:
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})

            response = await asyncio.wait_for(
                self.client.chat(
                    model=model,
                    messages=messages,
                    stream=False
                ),
                timeout=timeout_seconds
            )

            end_time = datetime.now()
            response_time = int((end_time - start_time).total_seconds() * 1000)

            return ModelResponse(
                model_name=model,
                response=response['message']['content'].strip(),
                response_time_ms=response_time,
                success=True
            )

        except asyncio.TimeoutError:
            response_time = timeout_seconds * 1000
            return ModelResponse(
                model_name=model,
                response="",
                response_time_ms=response_time,
                success=False,
                error=f"Timeout after {timeout_seconds}s"
            )
        except Exception as e:
            end_time = datetime.now()
            response_time = int((end_time - start_time).total_seconds() * 1000)

            return ModelResponse(
                model_name=model,
                response="",
                response_time_ms=response_time,
                success=False,
                error=str(e)
            )

    async def generate_streaming_response(
        self,
        model: str,
        prompt: str,
        system_prompt: Optional[str] = None,
        timeout_seconds: int = 60
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Generate streaming response from a single model with improved error handling."""
        start_time = datetime.now()
        full_response = ""
        last_chunk_time = start_time

        try:
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})

            logger.info(f"Starting stream for model {model}, timeout: {timeout_seconds}s")

            # Add timeout wrapper for the entire streaming operation
            stream_generator = await asyncio.wait_for(
                self.client.chat(
                    model=model,
                    messages=messages,
                    stream=True
                ),
                timeout=timeout_seconds
            )

            chunk_count = 0

            # Stream response from Ollama with chunk timeout monitoring
            async for chunk in stream_generator:
                current_time = datetime.now()
                chunk_count += 1

                # Check if we've been silent too long (potential hang)
                time_since_last_chunk = (current_time - last_chunk_time).total_seconds()
                if time_since_last_chunk > 30:  # 30 second silence threshold
                    logger.warning(f"Long pause detected: {time_since_last_chunk:.1f}s since last chunk for {model}")

                last_chunk_time = current_time

                try:
                    token = chunk['message']['content']
                    full_response += token

                    # Yield each token
                    yield {
                        'token': token,
                        'done': False,
                        'model_name': model,
                        'response_time_ms': int((current_time - start_time).total_seconds() * 1000),
                        'chunk_number': chunk_count
                    }

                except KeyError as e:
                    logger.error(f"Malformed chunk from {model}: {chunk}, error: {e}")
                    # Continue processing - don't stop the stream for one bad chunk
                    continue

            # Final response with complete metadata
            end_time = datetime.now()
            response_time = int((end_time - start_time).total_seconds() * 1000)

            logger.info(f"Stream completed for {model}: {chunk_count} chunks, {response_time}ms, {len(full_response)} chars")

            yield {
                'token': '',
                'done': True,
                'model_name': model,
                'response_time_ms': response_time,
                'full_response': full_response.strip(),
                'success': True,
                'total_chunks': chunk_count
            }

        except asyncio.TimeoutError:
            end_time = datetime.now()
            response_time = int((end_time - start_time).total_seconds() * 1000)
            error_msg = f"Stream timeout after {timeout_seconds}s for {model}"
            logger.error(f"{error_msg}, partial response: {len(full_response)} chars")

            yield {
                'token': '',
                'done': True,
                'model_name': model,
                'response_time_ms': response_time,
                'success': False,
                'error': error_msg,
                'partial_response': full_response if full_response else None
            }

        except Exception as e:
            end_time = datetime.now()
            response_time = int((end_time - start_time).total_seconds() * 1000)
            error_msg = f"Stream error for {model}: {type(e).__name__}: {str(e)}"
            logger.error(f"{error_msg}, partial response: {len(full_response)} chars")

            yield {
                'token': '',
                'done': True,
                'model_name': model,
                'response_time_ms': response_time,
                'success': False,
                'error': error_msg,
                'partial_response': full_response if full_response else None
            }
