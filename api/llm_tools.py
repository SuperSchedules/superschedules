"""
LLM Tool definitions for Claude tool use.

Defines tools that Claude can call during response generation,
and executors that run the actual logic when tools are invoked.
"""

import logging
from typing import Dict, Any, List, Optional, Callable
from datetime import datetime, timedelta

from django.utils import timezone

logger = logging.getLogger(__name__)


# Tool Definitions (Claude tool use format)
SEARCH_EVENTS_TOOL = {
    "name": "search_events",
    "description": (
        "Search for more events when the current results don't match what the user is looking for. "
        "Use this to find events with more specific criteria like age ranges, activity types, "
        "different locations, or different dates. Only call this if the initial results are "
        "insufficient or the user asks for something specific not covered."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Refined search query focusing on specific activity type, age group, or interest (e.g., 'outdoor toddler activities', 'teen coding classes', 'family music events')"
            },
            "location": {
                "type": "string",
                "description": "City or town to search in (e.g., 'Newton, MA', 'Brookline'). Leave empty to keep current location."
            },
            "date_filter": {
                "type": "string",
                "enum": ["today", "tomorrow", "this_weekend", "next_week", "next_weekend"],
                "description": "Time period to search. Use when user mentions specific dates."
            },
            "max_results": {
                "type": "integer",
                "description": "Number of results to return (default 10, max 20)",
                "default": 10
            }
        },
        "required": ["query"]
    }
}

# All available tools
AVAILABLE_TOOLS = [SEARCH_EVENTS_TOOL]


def parse_date_filter(date_filter: Optional[str]) -> tuple[Optional[datetime], Optional[datetime]]:
    """Convert date_filter enum to date range."""
    if not date_filter:
        return None, None

    now = timezone.localtime(timezone.now())
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    if date_filter == "today":
        return today_start, today_start + timedelta(days=1)
    elif date_filter == "tomorrow":
        tomorrow = today_start + timedelta(days=1)
        return tomorrow, tomorrow + timedelta(days=1)
    elif date_filter == "this_weekend":
        # Find next Saturday
        days_until_saturday = (5 - now.weekday()) % 7
        if days_until_saturday == 0 and now.weekday() == 5:
            saturday = today_start  # It's Saturday
        else:
            saturday = today_start + timedelta(days=days_until_saturday)
        sunday_end = saturday + timedelta(days=2)
        return saturday, sunday_end
    elif date_filter == "next_week":
        # Monday to Sunday of next week
        days_until_monday = (7 - now.weekday()) % 7
        if days_until_monday == 0:
            days_until_monday = 7
        next_monday = today_start + timedelta(days=days_until_monday)
        return next_monday, next_monday + timedelta(days=7)
    elif date_filter == "next_weekend":
        # Saturday/Sunday of next week
        days_until_saturday = (5 - now.weekday()) % 7
        if days_until_saturday == 0:
            days_until_saturday = 7  # Skip this Saturday, go to next
        next_saturday = today_start + timedelta(days=days_until_saturday + 7)
        return next_saturday, next_saturday + timedelta(days=2)

    return None, None


class ToolExecutor:
    """
    Executes tools called by the LLM.

    Takes tool calls from Claude and runs the actual logic,
    returning results to feed back to the LLM.
    """

    def __init__(self, rag_service, default_location: Optional[str] = None):
        """
        Initialize executor with dependencies.

        Args:
            rag_service: RAG service instance for event searches
            default_location: Default location if none specified in tool call
        """
        self.rag_service = rag_service
        self.default_location = default_location

    def execute(self, tool_name: str, tool_input: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute a tool and return results.

        Args:
            tool_name: Name of the tool to execute
            tool_input: Input parameters from the LLM

        Returns:
            Dict with 'success', 'result' or 'error' keys
        """
        logger.info(f"Executing tool: {tool_name} with input: {tool_input}")

        if tool_name == "search_events":
            return self._execute_search_events(tool_input)
        else:
            return {
                "success": False,
                "error": f"Unknown tool: {tool_name}"
            }

    def _execute_search_events(self, tool_input: Dict[str, Any]) -> Dict[str, Any]:
        """Execute the search_events tool."""
        try:
            query = tool_input.get("query", "")
            location = tool_input.get("location") or self.default_location
            date_filter = tool_input.get("date_filter")
            max_results = min(tool_input.get("max_results", 10), 20)

            # Parse date filter
            date_from, date_to = parse_date_filter(date_filter)

            # Execute RAG search
            events = self.rag_service.get_context_events(
                user_message=query,
                max_events=max_results,
                similarity_threshold=0.15,  # Lower threshold for tool searches
                location=location,
                date_from=date_from,
                date_to=date_to,
            )

            logger.info(f"Tool search_events found {len(events)} events for query='{query}'")

            # Format results for LLM consumption
            if not events:
                return {
                    "success": True,
                    "result": f"No additional events found for '{query}'" + (f" in {location}" if location else ""),
                    "event_count": 0
                }

            # Format events as readable text
            result_lines = [f"Found {len(events)} additional events:\n"]
            for i, event in enumerate(events, 1):
                lines = [f"{i}. **{event.get('title', 'Untitled')}**"]

                if event.get('start_time'):
                    try:
                        dt = datetime.fromisoformat(event['start_time'].replace('Z', '+00:00'))
                        lines.append(f"   When: {dt.strftime('%A, %B %d at %I:%M %p')}")
                    except:
                        pass

                if event.get('location'):
                    lines.append(f"   Where: {event['location']}")

                if event.get('description'):
                    desc = event['description'][:150] + "..." if len(event['description']) > 150 else event['description']
                    lines.append(f"   About: {desc}")

                result_lines.append("\n".join(lines))

            return {
                "success": True,
                "result": "\n\n".join(result_lines),
                "event_count": len(events),
                "events": events  # Include raw events for potential further use
            }

        except Exception as e:
            logger.error(f"Tool search_events error: {e}")
            return {
                "success": False,
                "error": f"Search failed: {str(e)}"
            }


def format_tool_result_for_claude(tool_use_id: str, result: Dict[str, Any]) -> Dict[str, Any]:
    """
    Format tool execution result for sending back to Claude.

    Args:
        tool_use_id: The ID from Claude's tool_use block
        result: Result from ToolExecutor.execute()

    Returns:
        Message content block for Claude API
    """
    if result.get("success"):
        content = result.get("result", "Tool executed successfully")
    else:
        content = f"Error: {result.get('error', 'Unknown error')}"

    return {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": content
    }
