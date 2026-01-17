"""
LLM service for event discovery and chat.
Uses provider abstraction to support multiple LLM backends (Ollama, Bedrock, etc.).
"""

import logging
from typing import List, Dict, Any, Tuple, Optional, TYPE_CHECKING
from datetime import datetime

from .llm_providers import get_llm_provider, ModelResponse

if TYPE_CHECKING:
    from traces.recorder import TraceRecorder


logger = logging.getLogger(__name__)


def create_event_discovery_prompt(
    message: str,
    events: List[Dict[str, Any]],
    context: Dict[str, Any],
    conversation_history: List[Dict[str, str]] = None,
    user_preferences: Dict[str, Any] = None,
    venues: List[Dict[str, Any]] = None,
    trace: Optional['TraceRecorder'] = None,
) -> Tuple[str, str]:
    """Create system and user prompts for event discovery chat with conversation context."""

    system_prompt = """You are a friendly, trustworthy local events assistant — like a well-informed neighbor who genuinely wants to help someone find something fun to do nearby.

Your job is to help the user discover events and venues from the provided lists, explain why they might be a good fit, and guide the conversation when choices are limited.

CORE BEHAVIOR (VERY IMPORTANT)
- You must ONLY reference events from the "EVENTS YOU CAN RECOMMEND" section and venues from the "RELEVANT VENUES" section.
- Never invent, guess, or imply the existence of events or venues not in the lists.
- If something is unclear, ask a short follow-up question instead of assuming.
- If there are no matching events, say so clearly and help the user refine their search.
- When relevant venues are provided, you can mention them as places worth exploring even if they don't have specific events listed.

DATE MATCHING (CRITICAL)
- Pay attention to TODAY'S DATE shown in the context to interpret relative dates correctly.
- If the user mentions a date in their message ("tomorrow", "this weekend", "next Friday"), that takes PRIORITY over any date filter.
- Interpret date phrases relative to today: "tomorrow" = today + 1 day, "this weekend" = upcoming Saturday/Sunday.
- If the user doesn't mention dates but a DATE FILTER is shown, respect that filter.
- ONLY recommend events that fall within the applicable date range.
- If no events match the exact date request, say "I don't see any events for [date]" rather than suggesting other dates.

PERSONALITY & TONE
- Warm, conversational, and natural — not salesy, robotic, or overly verbose
- Helpful and optimistic, but honest when options are limited
- Sounds like a local who knows the area, not a marketing bot

Avoid:
- Over-enthusiasm
- Emoji
- Long introductions or summaries
- Repeating the same phrasing across answers

HOW TO RESPOND

1. Start With a Helpful Framing
   Briefly acknowledge what they're looking for:
   "For kids this weekend, a couple things stand out…"
   "I found a few family-friendly options nearby…"
   Do not repeat the user's question verbatim.

2. Recommend Events Thoughtfully
   When listing events:
   - Use bullet points
   - Include what, when, where
   - Explain why each event might be a good fit (age, energy level, cost, timing)

   Example structure:
   - Event name – one-sentence description
   - Why it works for this user
   - Key logistics (time, location, registration if required)

3. Match to the User
   Actively use any info you're given:
   - Kids' ages
   - Budget preferences
   - Time of day
   - Interests (music, outdoors, quiet vs active)

   If info is missing, ask one focused follow-up question at the end.

4. When Options Are Limited
   If there are only 1–2 relevant events:
   - Say so plainly
   - Still present them confidently
   - Offer one way to expand the search (dates, nearby towns, activity type)

   If no events exist:
   - Say that directly
   - Ask 2–3 short clarifying questions (location, dates, interests)
   - Do not apologize excessively.

5. Formatting Rules
   - Bullets for multiple events
   - Short paragraphs
   - Scannable layout
   - No markdown headers beyond simple emphasis
   - No links unless they are provided in the event data

FAILURE MODES TO AVOID (CRITICAL)
- Inventing events
- Suggesting generic activities ("you could go to a park") unless explicitly allowed
- Listing events without context
- Acting as if more data exists than provided
- Asking many questions at once

FINAL CHECK BEFORE RESPONDING
Before answering, confirm internally:
- Every event mentioned exists in the list
- Dates, times, and locations match exactly
- Tone is friendly but grounded
- The response helps the user decide, not just browse

Now respond to the user using only the information provided below."""

    # Build user prompt with context
    user_prompt_parts = []

    # User profile section
    profile_lines = []
    if user_preferences:
        if user_preferences.get('familySize') and user_preferences['familySize'] > 1:
            profile_lines.append(f"Has a family of {user_preferences['familySize']}")
        if user_preferences.get('age'):
            profile_lines.append(f"User is {user_preferences['age']} years old")
        if user_preferences.get('interests'):
            profile_lines.append(f"Interested in: {', '.join(user_preferences['interests'])}")
        if user_preferences.get('accessibility'):
            profile_lines.append(f"Needs: {', '.join(user_preferences['accessibility'])}")
        if user_preferences.get('preferredTimes') and user_preferences['preferredTimes'] != 'any':
            profile_lines.append(f"Prefers {user_preferences['preferredTimes']} activities")

    # max_price is now at context level, not in preferences
    max_price = context.get('max_price')
    if max_price is not None and max_price > 0:
        if max_price <= 25:
            profile_lines.append("Budget preference: low-cost events (under $25)")
        elif max_price <= 75:
            profile_lines.append(f"Budget preference: up to ${max_price}")
        else:
            profile_lines.append("Budget preference: flexible")

    if profile_lines:
        user_prompt_parts.append("ABOUT THIS USER:\n" + "\n".join(profile_lines))

    # Conversation history
    if conversation_history and len(conversation_history) > 0:
        history_lines = []
        for msg in conversation_history[-10:]:
            role = "User" if msg['role'] == 'user' else "You"
            content = msg['content'][:300] + "..." if len(msg['content']) > 300 else msg['content']
            history_lines.append(f"{role}: {content}")
        user_prompt_parts.append("\nCONVERSATION SO FAR:\n" + "\n".join(history_lines))

    # Current context
    user_prompt_parts.append(f"\nTODAY'S DATE: {context.get('current_date', 'unknown')}")

    # Date filter from user's filter settings
    date_range = context.get('date_range')
    if date_range:
        from_date = date_range.get('from', '')
        to_date = date_range.get('to', '')
        if from_date and to_date:
            user_prompt_parts.append(f"DATE FILTER: {from_date} to {to_date}")
        elif from_date:
            user_prompt_parts.append(f"DATE FILTER: from {from_date}")
        elif to_date:
            user_prompt_parts.append(f"DATE FILTER: until {to_date}")

    location = context.get('location')
    if location:
        user_prompt_parts.append(f"LOCATION: {location}")
    else:
        user_prompt_parts.append("LOCATION: Not specified (you might want to ask!)")

    # Current message
    user_prompt_parts.append(f'\nUSER SAYS: "{message}"')

    # Relevant venues section (if provided)
    if venues:
        user_prompt_parts.append("\n" + "="*50)
        user_prompt_parts.append("RELEVANT VENUES:")
        user_prompt_parts.append("="*50)
        user_prompt_parts.append("[These are places that match the search - they may have multiple events or programs]")

        for i, venue in enumerate(venues[:5], 1):
            venue_lines = [f"{i}. **{venue.get('name', 'Unknown Venue')}**"]

            if venue.get('venue_kind') and venue['venue_kind'] not in ('other', 'unknown'):
                venue_lines.append(f"   Type: {venue['venue_kind'].replace('_', ' ').title()}")

            if venue.get('description'):
                desc = venue['description'][:150] + "..." if len(venue['description']) > 150 else venue['description']
                venue_lines.append(f"   About: {desc}")

            if venue.get('kids_summary'):
                kids = venue['kids_summary'][:100] + "..." if len(venue['kids_summary']) > 100 else venue['kids_summary']
                venue_lines.append(f"   For Kids: {kids}")

            if venue.get('audience_tags'):
                venue_lines.append(f"   Good for: {', '.join(venue['audience_tags'][:4])}")

            if venue.get('city'):
                venue_lines.append(f"   Location: {venue['city']}, {venue.get('state', '')}")

            if venue.get('website_url'):
                venue_lines.append(f"   Website: {venue['website_url']}")

            user_prompt_parts.append("\n".join(venue_lines) + "\n")

    # Available events - formatted for easy scanning
    user_prompt_parts.append("\n" + "="*50)
    user_prompt_parts.append("EVENTS YOU CAN RECOMMEND:")
    user_prompt_parts.append("="*50)

    if events:
        # Extract themes from events to help the LLM
        themes = set()
        for event in events:
            if event.get('audience_tags'):
                themes.update(event['audience_tags'])
            if event.get('age_range'):
                themes.add(f"ages {event['age_range']}")

        if themes:
            user_prompt_parts.append(f"[Event themes available: {', '.join(list(themes)[:8])}]\n")

        for i, event in enumerate(events[:10], 1):
            event_lines = [f"{i}. **{event.get('title', 'Untitled Event')}**"]

            # When
            if event.get('start_time'):
                try:
                    dt = datetime.fromisoformat(event['start_time'].replace('Z', '+00:00'))
                    time_str = f"   When: {dt.strftime('%A, %B %d')} at {dt.strftime('%I:%M %p')}"
                    if event.get('end_time'):
                        try:
                            dt_end = datetime.fromisoformat(event['end_time'].replace('Z', '+00:00'))
                            time_str += f" - {dt_end.strftime('%I:%M %p')}"
                        except:
                            pass
                    event_lines.append(time_str)
                except:
                    event_lines.append(f"   When: {event['start_time']}")

            # Where
            if event.get('location'):
                event_lines.append(f"   Where: {event['location']}")

            # Who it's for
            audience_parts = []
            if event.get('age_range'):
                audience_parts.append(f"ages {event['age_range']}")
            if event.get('audience_tags'):
                audience_parts.extend(event['audience_tags'][:3])
            if audience_parts:
                event_lines.append(f"   Good for: {', '.join(audience_parts)}")

            # Important notes
            notes = []
            if event.get('is_virtual'):
                notes.append("VIRTUAL/ONLINE")
            if event.get('requires_registration'):
                notes.append("Registration required")
            if notes:
                event_lines.append(f"   Note: {' | '.join(notes)}")

            # Description
            if event.get('description'):
                desc = event['description'][:200] + "..." if len(event['description']) > 200 else event['description']
                event_lines.append(f"   About: {desc}")

            # Link
            if event.get('url'):
                event_lines.append(f"   Link: {event['url']}")

            user_prompt_parts.append("\n".join(event_lines) + "\n")
    else:
        user_prompt_parts.append("\n[No events found matching this search]")
        user_prompt_parts.append("\nSince there are no matches, ask the user questions to help find something:")
        user_prompt_parts.append("- What area/town are they in?")
        user_prompt_parts.append("- What dates work for them?")
        user_prompt_parts.append("- What kind of activity are they hoping for?")

    user_prompt_parts.append("\n" + "="*50)
    user_prompt_parts.append("Now respond helpfully to the user. Remember to be conversational!")

    user_prompt = "\n".join(user_prompt_parts)

    # Record trace events for each context block
    if trace:
        # System prompt block
        trace.event('context_block', {
            'block_type': 'system_prompt',
            'text': system_prompt,
            'chars': len(system_prompt),
            'tokens_est': len(system_prompt) // 4,
        })

        # User profile block
        if profile_lines:
            profile_text = "ABOUT THIS USER:\n" + "\n".join(profile_lines)
            trace.event('context_block', {
                'block_type': 'user_profile',
                'text': profile_text,
                'chars': len(profile_text),
                'tokens_est': len(profile_text) // 4,
            })

        # Conversation history block
        if conversation_history and len(conversation_history) > 0:
            history_text = "\n".join(
                f"{'User' if msg['role'] == 'user' else 'You'}: {msg['content'][:300]}"
                for msg in conversation_history[-10:]
            )
            trace.event('context_block', {
                'block_type': 'conversation_history',
                'text': history_text,
                'chars': len(history_text),
                'tokens_est': len(history_text) // 4,
                'message_count': len(conversation_history[-10:]),
            })

        # Current context block (date, location, filters)
        context_text = f"TODAY'S DATE: {context.get('current_date', 'unknown')}"
        if context.get('location'):
            context_text += f"\nLOCATION: {context.get('location')}"
        if context.get('date_range'):
            date_range = context['date_range']
            from_date = date_range.get('from', '')
            to_date = date_range.get('to', '')
            if from_date or to_date:
                context_text += f"\nDATE FILTER: {from_date} to {to_date}"
        trace.event('context_block', {
            'block_type': 'current_context',
            'text': context_text,
            'chars': len(context_text),
            'tokens_est': len(context_text) // 4,
        })

        # User message block
        user_message_text = f'USER SAYS: "{message}"'
        trace.event('context_block', {
            'block_type': 'user_message',
            'text': user_message_text,
            'chars': len(user_message_text),
            'tokens_est': len(user_message_text) // 4,
        })

        # Venues block (if provided)
        if venues:
            venues_start = None
            for i, part in enumerate(user_prompt_parts):
                if "RELEVANT VENUES:" in part:
                    venues_start = i
                    break

            if venues_start is not None:
                # Find where venues section ends (at EVENTS YOU CAN RECOMMEND)
                venues_end = None
                for i, part in enumerate(user_prompt_parts[venues_start:], venues_start):
                    if "EVENTS YOU CAN RECOMMEND" in part:
                        venues_end = i
                        break
                if venues_end:
                    venues_text = "\n".join(user_prompt_parts[venues_start:venues_end])
                    trace.event('context_block', {
                        'block_type': 'retrieved_venues',
                        'text': venues_text,
                        'chars': len(venues_text),
                        'tokens_est': len(venues_text) // 4,
                        'venue_count': len(venues) if venues else 0,
                        'venue_ids': [v.get('id') for v in venues] if venues else [],
                    })

        # Events block - the main retrieval context
        # Find the events section in user_prompt_parts
        events_start = None
        for i, part in enumerate(user_prompt_parts):
            if "EVENTS YOU CAN RECOMMEND" in part:
                events_start = i
                break

        if events_start is not None:
            # Everything from events header to the end instructions
            events_text = "\n".join(user_prompt_parts[events_start:-2])  # Exclude final instructions
            trace.event('context_block', {
                'block_type': 'retrieved_events',
                'text': events_text,
                'chars': len(events_text),
                'tokens_est': len(events_text) // 4,
                'event_count': len(events) if events else 0,
                'event_ids': [e.get('id') for e in events] if events else [],
            })

        # Final prompt event with complete prompts
        trace.event('prompt_final', {
            'system_prompt': system_prompt,
            'user_prompt': user_prompt,
            'total_chars': len(system_prompt) + len(user_prompt),
            'total_tokens_est': (len(system_prompt) + len(user_prompt)) // 4,
        })

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
