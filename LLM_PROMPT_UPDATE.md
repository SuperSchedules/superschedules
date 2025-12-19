# LLM Prompt Update for Conversation History & User Preferences

## Overview
Update `api/llm_service.py` to include conversation history and user preferences in the LLM prompt, making responses more contextual and personalized.

## Current State

The `create_event_discovery_prompt()` function currently builds:
- System prompt: Static instructions
- User prompt: Current message + events list

Missing:
- Conversation history (last 10 messages)
- User preferences (age, interests, accessibility, etc.)
- Richer event context (age_range, audience_tags, virtual status)

## Changes Required

### 1. Update function signature

```python
def create_event_discovery_prompt(
    message: str,
    events: List[Dict[str, Any]],
    context: Dict[str, Any],
    conversation_history: List[Dict[str, str]] = None,  # NEW
    user_preferences: Dict[str, Any] = None,            # NEW
) -> Tuple[str, str]:
```

### 2. Enhanced System Prompt

```python
system_prompt = """You are a local events assistant helping users find activities and events.

CRITICAL INSTRUCTIONS:
1. Use ONLY the events provided in the "Available upcoming events" section
2. If events are listed, recommend the most relevant ones based on the user's preferences and conversation
3. NEVER invent events not in the list
4. Keep responses concise - 2-3 sentences plus bullet points
5. Format events as: • **Event Title** - Date at Location

PERSONALIZATION:
- Consider the user's stated preferences (age, interests, accessibility needs)
- Reference previous conversation context when relevant
- If user has children, prioritize family-friendly and age-appropriate events
- Note registration requirements and virtual options when relevant

CONVERSATION STYLE:
- Be helpful and friendly
- Ask clarifying questions if the request is ambiguous
- Suggest alternatives if no perfect matches exist"""
```

### 3. Build User Prompt with History & Preferences

```python
def create_event_discovery_prompt(...) -> Tuple[str, str]:
    # ... system_prompt as above ...

    user_prompt_parts = []

    # Add user preferences context
    if user_preferences:
        pref_lines = []
        if user_preferences.get('age'):
            pref_lines.append(f"User age: {user_preferences['age']}")
        if user_preferences.get('familySize') and user_preferences['familySize'] > 1:
            pref_lines.append(f"Family size: {user_preferences['familySize']} people")
        if user_preferences.get('interests'):
            pref_lines.append(f"Interests: {', '.join(user_preferences['interests'])}")
        if user_preferences.get('accessibility'):
            pref_lines.append(f"Accessibility needs: {', '.join(user_preferences['accessibility'])}")
        if user_preferences.get('budgetRange'):
            pref_lines.append(f"Budget: {', '.join(user_preferences['budgetRange'])}")
        if user_preferences.get('preferredTimes') and user_preferences['preferredTimes'] != 'any':
            pref_lines.append(f"Preferred time: {user_preferences['preferredTimes']}")

        if pref_lines:
            user_prompt_parts.append("User preferences:\n" + "\n".join(pref_lines))

    # Add conversation history (last 10 messages)
    if conversation_history:
        history_text = "Previous conversation:\n"
        for msg in conversation_history[-10:]:
            role = "User" if msg['role'] == 'user' else "Assistant"
            # Truncate long messages
            content = msg['content'][:200] + "..." if len(msg['content']) > 200 else msg['content']
            history_text += f"{role}: {content}\n"
        user_prompt_parts.append(history_text)

    # Current message and context
    user_prompt_parts.append(f'Current date/time: {context.get("current_date", "unknown")}')
    user_prompt_parts.append(f'Location preference: {context.get("location", "not specified")}')
    user_prompt_parts.append(f'\nCurrent user message: "{message}"')

    # Available events with enhanced details
    user_prompt_parts.append("\nAvailable upcoming events:")

    if events:
        for i, event in enumerate(events[:10], 1):
            event_text = f"\n{i}. {event.get('title', 'Untitled Event')}"

            # Date/time
            if event.get('start_time'):
                try:
                    dt = datetime.fromisoformat(event['start_time'].replace('Z', '+00:00'))
                    event_text += f"\n   {dt.strftime('%A, %B %d')} - {dt.strftime('%I:%M %p')}"
                except:
                    pass

            # Location
            if event.get('location'):
                event_text += f"\n   📍 {event['location']}"

            # NEW: Age range and audience
            if event.get('age_range'):
                event_text += f"\n   👥 Ages: {event['age_range']}"
            if event.get('audience_tags'):
                event_text += f" ({', '.join(event['audience_tags'])})"

            # NEW: Virtual/registration info
            flags = []
            if event.get('is_virtual'):
                flags.append("🖥️ Virtual")
            if event.get('requires_registration'):
                flags.append("📝 Registration required")
            if flags:
                event_text += f"\n   {' | '.join(flags)}"

            # Description (abbreviated)
            if event.get('description'):
                desc = event['description'][:150]
                if len(event['description']) > 150:
                    desc += "..."
                event_text += f"\n   {desc}"

            # URL
            if event.get('url'):
                event_text += f"\n   🔗 {event['url']}"

            user_prompt_parts.append(event_text)
    else:
        user_prompt_parts.append("\n(No matching upcoming events found in database)")

    user_prompt_parts.append("\n\nIMPORTANT: Only recommend events from the list above. If empty, suggest general alternatives.")

    user_prompt = "\n".join(user_prompt_parts)
    return system_prompt, user_prompt
```

### 4. Update chat_service/app.py to pass history & preferences

In `stream_model_response()`:

```python
# Extract preferences from user context
preferences = user_context.get('preferences', {}) if user_context else {}

# Get conversation history from context (frontend sends this)
conversation_history = user_context.get('chat_history', []) if user_context else []

# Create prompts with full context
system_prompt, user_prompt = create_event_discovery_prompt(
    message=message,
    events=context_events,
    context={
        'current_date': current_time.strftime('%A, %B %d, %Y at %I:%M %p'),
        'location': location,
    },
    conversation_history=conversation_history,
    user_preferences=preferences,
)
```

## Files to Modify

1. **`api/llm_service.py`** - Update `create_event_discovery_prompt()` as shown above

2. **`chat_service/app.py`** - Update `stream_model_response()` to pass conversation_history and preferences

## Example Output

For a user with preferences `{age: 35, familySize: 4, interests: ["Family Events", "Outdoor"]}` and conversation history:

```
User preferences:
User age: 35
Family size: 4 people
Interests: Family Events, Outdoor

Previous conversation:
User: What's happening this weekend in Newton?
Assistant: Here are some family events I found: Story Time at Newton Library...
User: Anything outdoors?

Current date/time: Thursday, December 19, 2024 at 2:30 PM
Location preference: Newton

Current user message: "Anything outdoors?"

Available upcoming events:

1. Nature Walk for Families
   Saturday, December 21 - 10:00 AM
   📍 Newton Conservation Land
   👥 Ages: all-ages (Children, Families)
   Join us for a guided nature walk...
   🔗 https://...
```

## Testing

After updating, verify:
1. Conversation history appears in LLM prompt
2. User preferences are summarized
3. Event details include age_range, virtual status
4. LLM responses reference user's stated preferences
