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




class OllamaService:
    """Service for interacting with Ollama LLM models."""
    
    # Default models for A/B testing - DeepSeek vs Llama comparison  
    DEFAULT_MODEL_A = "deepseek-llm:7b"
    DEFAULT_MODEL_B = "llama3.2:3b"
    
    def __init__(self):
        self.client = ollama.AsyncClient()
    
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
    ):
        """Generate streaming response from a single model."""
        try:
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})
            
            start_time = datetime.now()
            full_response = ""
            
            # Stream response from Ollama
            async for chunk in await self.client.chat(
                model=model,
                messages=messages,
                stream=True
            ):
                token = chunk['message']['content']
                full_response += token
                
                # Yield each token
                yield {
                    'token': token,
                    'done': False,
                    'model_name': model,
                    'response_time_ms': int((datetime.now() - start_time).total_seconds() * 1000)
                }
            
            # Final response with complete metadata
            end_time = datetime.now()
            response_time = int((end_time - start_time).total_seconds() * 1000)
            
            yield {
                'token': '',
                'done': True,
                'model_name': model,
                'response_time_ms': response_time,
                'full_response': full_response.strip(),
                'success': True
            }
            
        except Exception as e:
            end_time = datetime.now()
            response_time = int((end_time - start_time).total_seconds() * 1000)
            
            yield {
                'token': '',
                'done': True,
                'model_name': model,
                'response_time_ms': response_time,
                'success': False,
                'error': str(e)
            }
    


def create_event_discovery_prompt(message: str, events: List[Dict[str, Any]], context: Dict[str, Any]) -> Tuple[str, str]:
    """Create system and user prompts for event discovery chat."""
    
    system_prompt = """You are a local events assistant. You MUST use the events provided in the user prompt.

CRITICAL INSTRUCTIONS:
1. LOOK AT THE "Available upcoming events" section in the user message
2. If ANY events are listed there, you MUST recommend them
3. NEVER say "no events found" if events are listed in the prompt
4. ALWAYS start with "Here are the upcoming events I found:"
5. Format each event as: • **Event Title** on Date at Location

STRICT RULES:
- If the user prompt shows "1. Event Name" or "Available upcoming events:" followed by event listings, those events exist and you must recommend them
- ONLY say "no events" if the prompt explicitly shows "(No matching upcoming events found in database)"
- Keep responses SHORT - maximum 3 sentences plus bullet points
- DO NOT invent events not listed in the prompt"""

    user_prompt = f"""User message: "{message}"

Current date/time: {context.get('current_date', 'unknown')}
Location preference: {context.get('location', 'not specified')}

IMPORTANT: Only recommend events that are in the future. Do not recommend events that have already happened.

Available upcoming events:
"""

    if events:
        for i, event in enumerate(events[:10], 1):  # Increased to 10 events for richer context
            user_prompt += f"\n{i}. {event.get('title', 'Untitled Event')}"
            
            # Date and time on new line
            if event.get('start_time'):
                try:
                    from datetime import datetime
                    dt = datetime.fromisoformat(event['start_time'].replace('Z', '+00:00'))
                    formatted_date = dt.strftime('%A, %B %d')
                    formatted_time = dt.strftime('%I:%M %p')
                    user_prompt += f"\n{formatted_date} - {formatted_time}"
                    
                    if event.get('end_time'):
                        try:
                            dt_end = datetime.fromisoformat(event['end_time'].replace('Z', '+00:00'))
                            formatted_end = dt_end.strftime('%I:%M %p')
                            user_prompt += f" to {formatted_end}"
                        except:
                            pass
                except:
                    user_prompt += f"\n{event['start_time']}"
            
            # Location on new line
            if event.get('location'):
                user_prompt += f"\n{event['location']}"
            
            # Abbreviated description on new line
            if event.get('description'):
                desc = event['description'][:150] + "..." if len(event['description']) > 150 else event['description']
                user_prompt += f"\n{desc}"
            
            # URL on new line
            if event.get('url'):
                user_prompt += f"\n{event['url']}"
                
            user_prompt += "\n"
    else:
        user_prompt += "\n(No matching upcoming events found in database)"

    user_prompt += """

IMPORTANT: Only use the events listed above. Do not invent any events. If the list is empty, say there are no events and suggest general alternatives like libraries or community centers."""

    return system_prompt, user_prompt


# Global service instance
_llm_service = None


def get_llm_service() -> OllamaService:
    """Get the global LLM service instance."""
    global _llm_service
    if _llm_service is None:
        _llm_service = OllamaService()
    return _llm_service