"""
Ollama LLM provider implementation.
Connects to a local Ollama instance for model inference.
Supports tool use with compatible models (qwen2.5, llama3.1, etc).
"""

import asyncio
import json
import logging
from typing import List, Optional, AsyncGenerator, Dict, Any, TYPE_CHECKING
from datetime import datetime

import ollama
from django.conf import settings

from .base import BaseLLMProvider, ModelResponse

if TYPE_CHECKING:
    from api.llm_tools import ToolExecutor


logger = logging.getLogger(__name__)

# Maximum tool call iterations to prevent infinite loops
MAX_TOOL_ITERATIONS = 3

# Models known to support tool use
TOOL_CAPABLE_MODELS = ['qwen2.5', 'qwen2', 'llama3.1', 'llama3.2', 'mistral', 'mixtral']


def model_supports_tools(model_name: str) -> bool:
    """Check if a model supports tool use."""
    model_lower = model_name.lower()
    return any(capable in model_lower for capable in TOOL_CAPABLE_MODELS)


def convert_tools_to_ollama_format(tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Convert our tool format to Ollama's expected format."""
    ollama_tools = []
    for tool in tools:
        ollama_tool = {
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool["description"],
                "parameters": tool["input_schema"]
            }
        }
        ollama_tools.append(ollama_tool)
    return ollama_tools


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
        stream: bool = False,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_executor: Optional['ToolExecutor'] = None,
    ) -> ModelResponse:
        """Generate response from a single model with optional tool use."""
        start_time = datetime.now()

        try:
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})

            # Prepare tools if model supports them
            ollama_tools = None
            use_tools = tools and tool_executor and model_supports_tools(model)
            if use_tools:
                ollama_tools = convert_tools_to_ollama_format(tools)
                logger.info(f"Enabling tool use for {model} with {len(ollama_tools)} tools")

            # Tool use loop
            iteration = 0
            while iteration < MAX_TOOL_ITERATIONS:
                iteration += 1

                chat_kwargs = {"model": model, "messages": messages, "stream": False}
                if ollama_tools:
                    chat_kwargs["tools"] = ollama_tools

                response = await asyncio.wait_for(
                    self.client.chat(**chat_kwargs),
                    timeout=timeout_seconds
                )

                message = response.get('message', {})
                tool_calls = message.get('tool_calls', [])

                # Check if model wants to call tools
                if tool_calls and tool_executor:
                    logger.info(f"Model requested {len(tool_calls)} tool call(s)")

                    # Add assistant message with tool calls
                    messages.append(message)

                    # Execute each tool and add results
                    for tool_call in tool_calls:
                        func = tool_call.get('function', {})
                        tool_name = func.get('name')
                        tool_args = func.get('arguments', {})

                        # Arguments might be a string that needs parsing
                        if isinstance(tool_args, str):
                            try:
                                tool_args = json.loads(tool_args)
                            except json.JSONDecodeError:
                                tool_args = {}

                        logger.info(f"Executing tool: {tool_name} with {tool_args}")
                        result = tool_executor.execute(tool_name, tool_args)

                        # Add tool result message
                        messages.append({
                            "role": "tool",
                            "content": result.get('result', str(result)) if result.get('success') else f"Error: {result.get('error')}"
                        })

                    # Continue loop to get response with tool results
                    continue

                # No tool calls - we have final response
                content = message.get('content', '').strip()

                end_time = datetime.now()
                response_time = int((end_time - start_time).total_seconds() * 1000)

                return ModelResponse(
                    model_name=model,
                    response=content,
                    response_time_ms=response_time,
                    success=True
                )

            # Hit max iterations
            logger.warning(f"Hit max tool iterations ({MAX_TOOL_ITERATIONS})")
            end_time = datetime.now()
            response_time = int((end_time - start_time).total_seconds() * 1000)

            return ModelResponse(
                model_name=model,
                response="I encountered an issue processing your request. Please try again.",
                response_time_ms=response_time,
                success=False,
                error="Max tool iterations reached"
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
        timeout_seconds: int = 60,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_executor: Optional['ToolExecutor'] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Generate streaming response with optional tool use."""
        start_time = datetime.now()
        full_response = ""
        last_chunk_time = start_time

        try:
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})

            # Prepare tools if model supports them
            ollama_tools = None
            use_tools = tools and tool_executor and model_supports_tools(model)
            if use_tools:
                ollama_tools = convert_tools_to_ollama_format(tools)
                logger.info(f"Enabling tool use for streaming with {model}")

            logger.info(f"Starting stream for model {model}, timeout: {timeout_seconds}s")

            iteration = 0
            while iteration < MAX_TOOL_ITERATIONS:
                iteration += 1

                chat_kwargs = {"model": model, "messages": messages, "stream": True}
                if ollama_tools:
                    chat_kwargs["tools"] = ollama_tools

                stream_generator = await asyncio.wait_for(
                    self.client.chat(**chat_kwargs),
                    timeout=timeout_seconds
                )

                chunk_count = 0
                current_message_content = ""
                tool_calls = []

                # Stream response from Ollama
                async for chunk in stream_generator:
                    current_time = datetime.now()
                    chunk_count += 1

                    # Check for long pauses
                    time_since_last_chunk = (current_time - last_chunk_time).total_seconds()
                    if time_since_last_chunk > 30:
                        logger.warning(f"Long pause: {time_since_last_chunk:.1f}s since last chunk")

                    last_chunk_time = current_time

                    message = chunk.get('message', {})

                    # Check for tool calls in chunk
                    if message.get('tool_calls'):
                        tool_calls = message['tool_calls']

                    # Stream text content
                    content = message.get('content', '')
                    if content:
                        current_message_content += content
                        full_response += content

                        yield {
                            'token': content,
                            'done': False,
                            'model_name': model,
                            'response_time_ms': int((current_time - start_time).total_seconds() * 1000),
                            'chunk_number': chunk_count
                        }

                # After streaming, check if we need to handle tool calls
                if tool_calls and tool_executor:
                    logger.info(f"Model requested {len(tool_calls)} tool call(s)")

                    yield {
                        'token': '\n\n*Searching for more events...*\n\n',
                        'done': False,
                        'model_name': model,
                        'tool_use': True,
                        'response_time_ms': int((datetime.now() - start_time).total_seconds() * 1000),
                    }

                    # Add assistant message with tool calls
                    messages.append({
                        "role": "assistant",
                        "content": current_message_content,
                        "tool_calls": tool_calls
                    })

                    # Execute tools
                    for tool_call in tool_calls:
                        func = tool_call.get('function', {})
                        tool_name = func.get('name')
                        tool_args = func.get('arguments', {})

                        if isinstance(tool_args, str):
                            try:
                                tool_args = json.loads(tool_args)
                            except json.JSONDecodeError:
                                tool_args = {}

                        logger.info(f"Executing tool: {tool_name}")
                        result = tool_executor.execute(tool_name, tool_args)

                        messages.append({
                            "role": "tool",
                            "content": result.get('result', str(result)) if result.get('success') else f"Error: {result.get('error')}"
                        })

                    # Continue to next iteration
                    continue

                # No tool calls - we're done
                break

            # Final response
            end_time = datetime.now()
            response_time = int((end_time - start_time).total_seconds() * 1000)

            logger.info(f"Stream completed for {model}: {response_time}ms, {len(full_response)} chars")

            yield {
                'token': '',
                'done': True,
                'model_name': model,
                'response_time_ms': response_time,
                'full_response': full_response.strip(),
                'success': True,
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
