"""
Abstract base class for LLM providers.
Defines the interface that all LLM providers (Ollama, Bedrock, etc.) must implement.
"""

from abc import ABC, abstractmethod
from typing import List, Optional, AsyncGenerator, Dict, Any
from dataclasses import dataclass


@dataclass
class ModelResponse:
    """Response from an LLM model."""
    model_name: str
    response: str
    response_time_ms: int
    success: bool
    error: Optional[str] = None


class BaseLLMProvider(ABC):
    """Abstract base class for LLM providers."""

    @abstractmethod
    async def get_available_models(self) -> List[str]:
        """
        Get list of available models from this provider.

        Returns:
            List of model names/identifiers
        """
        pass

    @abstractmethod
    async def generate_response(
        self,
        model: str,
        prompt: str,
        system_prompt: Optional[str] = None,
        timeout_seconds: int = 30,
        stream: bool = False
    ) -> ModelResponse:
        """
        Generate a single response from the model (non-streaming).

        Args:
            model: Model identifier
            prompt: User prompt/message
            system_prompt: Optional system prompt for context
            timeout_seconds: Maximum time to wait for response
            stream: Whether to stream response (not used in this method, for compatibility)

        Returns:
            ModelResponse with the complete response
        """
        pass

    @abstractmethod
    async def generate_streaming_response(
        self,
        model: str,
        prompt: str,
        system_prompt: Optional[str] = None,
        timeout_seconds: int = 60
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Generate a streaming response from the model.

        Args:
            model: Model identifier
            prompt: User prompt/message
            system_prompt: Optional system prompt for context
            timeout_seconds: Maximum time to wait for complete response

        Yields:
            Dict with keys:
                - token: str - The text chunk
                - done: bool - True if this is the final chunk
                - model_name: str - Model that generated this chunk
                - response_time_ms: int - Time elapsed since start
                - success: bool - (on final chunk) Whether generation succeeded
                - error: Optional[str] - (on final chunk if failed) Error message
                - full_response: Optional[str] - (on final chunk) Complete response
        """
        pass

    @property
    @abstractmethod
    def primary_model(self) -> str:
        """Get the primary/default model identifier for this provider."""
        pass

    @property
    @abstractmethod
    def backup_model(self) -> Optional[str]:
        """Get the backup/fallback model identifier for this provider."""
        pass
