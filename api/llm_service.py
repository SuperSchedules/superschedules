"""
LLM service for event discovery and chat.
Uses provider abstraction to support multiple LLM backends (Ollama, Bedrock, etc.).
"""

import logging
from typing import List, Dict, Any, Tuple
from datetime import datetime

from .llm_providers import get_llm_provider, ModelResponse


logger = logging.getLogger(__name__)


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


def get_llm_service():
    """
    Get the configured LLM provider instance.

    This function maintains backward compatibility with existing code
    while using the new provider abstraction layer.

    Returns:
        BaseLLMProvider: The configured provider (Ollama, Bedrock, etc.)
    """
    return get_llm_provider()
