"""
Factory for creating LLM provider instances based on configuration.
"""

import logging
from typing import Optional

from django.conf import settings

from .base import BaseLLMProvider
from .ollama import OllamaProvider
from .bedrock import BedrockProvider


logger = logging.getLogger(__name__)


# Global provider instance (singleton pattern)
_provider_instance: Optional[BaseLLMProvider] = None


def get_llm_provider() -> BaseLLMProvider:
    """
    Get the configured LLM provider instance.

    Provider is selected based on the LLM_PROVIDER setting:
    - 'ollama': Use local Ollama instance (default for development)
    - 'bedrock': Use AWS Bedrock Claude models (recommended for production)

    Returns:
        BaseLLMProvider: The configured provider instance
    """
    global _provider_instance

    if _provider_instance is not None:
        return _provider_instance

    provider_type = getattr(settings, 'LLM_PROVIDER', 'ollama').lower()

    if provider_type == 'bedrock':
        logger.info("Initializing Bedrock LLM provider")
        _provider_instance = BedrockProvider()
    elif provider_type == 'ollama':
        logger.info("Initializing Ollama LLM provider")
        _provider_instance = OllamaProvider()
    else:
        logger.warning(f"Unknown LLM_PROVIDER '{provider_type}', defaulting to Ollama")
        _provider_instance = OllamaProvider()

    return _provider_instance


def reset_provider():
    """
    Reset the global provider instance.
    Useful for testing or when configuration changes.
    """
    global _provider_instance
    _provider_instance = None
