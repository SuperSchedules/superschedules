"""
LLM service for integrating with Ollama models.
Supports A/B testing with multiple models running in parallel.
"""

import asyncio
import logging
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime

import ollama
from django.conf import settings


logger = logging.getLogger(__name__)


@dataclass
class ModelResponse:
    """Response from an LLM model."""
    model_name: str
    response: str
    response_time_ms: int
    success: bool
    error: Optional[str] = None


@dataclass 
class ChatComparisonResult:
    """Result from comparing multiple models on the same query."""
    query: str
    model_a: ModelResponse
    model_b: ModelResponse
    timestamp: datetime


class OllamaService:
    """Service for interacting with Ollama LLM models."""
    
    # Default models for A/B testing
    DEFAULT_MODEL_A = "llama3.2:1b"
    DEFAULT_MODEL_B = "llama3.2:3b"
    
    def __init__(self):
        self.client = ollama.AsyncClient()
    
    async def get_available_models(self) -> List[str]:
        """Get list of available Ollama models."""
        try:
            models = await self.client.list()
            return [model['name'] for model in models['models']]
        except Exception as e:
            logger.error(f"Failed to get available models: {e}")
            return []
    
    async def generate_response(
        self, 
        model: str, 
        prompt: str,
        system_prompt: Optional[str] = None,
        timeout_seconds: int = 30
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
    
    async def compare_models(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        model_a: Optional[str] = None,
        model_b: Optional[str] = None,
        timeout_seconds: int = 30
    ) -> ChatComparisonResult:
        """Generate responses from two models for A/B testing."""
        
        model_a = model_a or self.DEFAULT_MODEL_A
        model_b = model_b or self.DEFAULT_MODEL_B
        
        # Run both models in parallel
        results = await asyncio.gather(
            self.generate_response(model_a, prompt, system_prompt, timeout_seconds),
            self.generate_response(model_b, prompt, system_prompt, timeout_seconds),
            return_exceptions=True
        )
        
        # Handle any exceptions from gather
        response_a = results[0] if isinstance(results[0], ModelResponse) else ModelResponse(
            model_name=model_a,
            response="",
            response_time_ms=timeout_seconds * 1000,
            success=False,
            error=str(results[0])
        )
        
        response_b = results[1] if isinstance(results[1], ModelResponse) else ModelResponse(
            model_name=model_b,
            response="",
            response_time_ms=timeout_seconds * 1000,
            success=False,
            error=str(results[1])
        )
        
        return ChatComparisonResult(
            query=prompt,
            model_a=response_a,
            model_b=response_b,
            timestamp=datetime.now()
        )


def create_event_discovery_prompt(message: str, events: List[Dict[str, Any]], context: Dict[str, Any]) -> Tuple[str, str]:
    """Create system and user prompts for event discovery chat."""
    
    system_prompt = """You are an AI assistant helping people discover local events and activities.

Your role:
- Help users find events based on their needs (age, location, interests, timing)
- Ask clarifying questions when information is missing
- Use the provided event data to make specific recommendations

Guidelines:
- Respond briefly and avoid unnecessary commentary
- When listing events, format each as "Title – Date – Location"
- If no events match perfectly, suggest close alternatives
- Focus on why each event suits the user
"""

    user_prompt = f"""User message: "{message}"

Context:
- Current date: {context.get('current_date', 'unknown')}
- Location preference: {context.get('location', 'not specified')}
- User preferences: {context.get('preferences', {})}

Available events that might be relevant:
"""

    if events:
        for i, event in enumerate(events[:5], 1):  # Limit to 5 events
            user_prompt += f"\n{i}. {event.get('title', 'Untitled Event')}"
            if event.get('location'):
                user_prompt += f" at {event['location']}"
            if event.get('start_time'):
                user_prompt += f" on {event['start_time']}"
            if event.get('description'):
                # Truncate long descriptions
                desc = event['description'][:200] + "..." if len(event['description']) > 200 else event['description']
                user_prompt += f" - {desc}"
    else:
        user_prompt += "\n(No specific events found in database matching the criteria)"

    user_prompt += "\n\nRespond concisely. When suggesting events, list each on its own line as 'Title – Date – Location'."

    return system_prompt, user_prompt


# Global service instance
_llm_service = None


def get_llm_service() -> OllamaService:
    """Get the global LLM service instance."""
    global _llm_service
    if _llm_service is None:
        _llm_service = OllamaService()
    return _llm_service