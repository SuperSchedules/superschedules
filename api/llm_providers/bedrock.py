"""
Amazon Bedrock LLM provider implementation.
Connects to AWS Bedrock for Claude model inference.
Supports tool use for agentic RAG workflows.
"""

import json
import logging
from typing import List, Optional, AsyncGenerator, Dict, Any, Callable, TYPE_CHECKING
from datetime import datetime

import boto3
from botocore.exceptions import ClientError
from django.conf import settings

from .base import BaseLLMProvider, ModelResponse

if TYPE_CHECKING:
    from api.llm_tools import ToolExecutor


logger = logging.getLogger(__name__)

# Maximum tool call iterations to prevent infinite loops
MAX_TOOL_ITERATIONS = 3


class BedrockProvider(BaseLLMProvider):
    """Provider for interacting with Amazon Bedrock Claude models."""

    def __init__(self):
        # Initialize Bedrock client
        self.region = getattr(settings, 'AWS_BEDROCK_REGION', 'us-east-1')
        self.client = boto3.client('bedrock-runtime', region_name=self.region)

        # Model configuration from settings
        self._primary_model = getattr(
            settings,
            'AWS_BEDROCK_MODEL_ID',
            'anthropic.claude-3-haiku-20240307-v1:0'
        )
        # Bedrock typically uses one model, but we can have a backup
        self._backup_model = getattr(
            settings,
            'AWS_BEDROCK_BACKUP_MODEL_ID',
            'anthropic.claude-3-sonnet-20240229-v1:0'
        )

    @property
    def primary_model(self) -> str:
        return self._primary_model

    @property
    def backup_model(self) -> Optional[str]:
        return self._backup_model

    async def get_available_models(self) -> List[str]:
        """Get list of available Bedrock models."""
        try:
            # Use regular boto3 client for this sync operation
            bedrock_client = boto3.client('bedrock', region_name=self.region)
            response = bedrock_client.list_foundation_models()
            models = response.get('modelSummaries', [])
            # Filter for Claude models
            return [m['modelId'] for m in models if 'claude' in m['modelId'].lower()]
        except Exception as e:
            logger.error(f"Failed to get available Bedrock models: {e}")
            return [self._primary_model, self._backup_model]

    def _format_messages_for_bedrock(
        self,
        prompt: str,
        system_prompt: Optional[str] = None
    ) -> tuple[str, list]:
        """Format messages for Bedrock Claude API."""
        messages = [{"role": "user", "content": prompt}]
        return system_prompt or "", messages

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
        """
        Generate response from Bedrock Claude model.

        Args:
            model: Model ID to use
            prompt: User prompt
            system_prompt: System instructions
            timeout_seconds: Request timeout
            stream: Whether to stream (not used here, use generate_streaming_response)
            tools: List of tool definitions for Claude to use
            tool_executor: Executor to run tools if Claude calls them
        """
        start_time = datetime.now()

        try:
            system, messages = self._format_messages_for_bedrock(prompt, system_prompt)

            body = {
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 4096,
                "messages": messages,
            }
            if system:
                body["system"] = system
            if tools:
                body["tools"] = tools

            # Tool use loop - Claude may call tools multiple times
            iteration = 0
            while iteration < MAX_TOOL_ITERATIONS:
                iteration += 1

                response = self.client.invoke_model(
                    modelId=model,
                    contentType="application/json",
                    accept="application/json",
                    body=json.dumps(body)
                )

                response_body = json.loads(response['body'].read())
                stop_reason = response_body.get('stop_reason', 'end_turn')
                content_blocks = response_body.get('content', [])

                # Check if Claude wants to use a tool
                if stop_reason == 'tool_use' and tool_executor:
                    # Find tool_use blocks and execute them
                    tool_results = []
                    text_content = ""

                    for block in content_blocks:
                        if block.get('type') == 'text':
                            text_content += block.get('text', '')
                        elif block.get('type') == 'tool_use':
                            tool_name = block.get('name')
                            tool_input = block.get('input', {})
                            tool_use_id = block.get('id')

                            logger.info(f"Claude calling tool: {tool_name} with {tool_input}")

                            # Execute the tool
                            result = tool_executor.execute(tool_name, tool_input)

                            # Format result for Claude
                            from api.llm_tools import format_tool_result_for_claude
                            tool_results.append(format_tool_result_for_claude(tool_use_id, result))

                    # Add assistant's response (with tool_use) and tool results to messages
                    body["messages"].append({"role": "assistant", "content": content_blocks})
                    body["messages"].append({"role": "user", "content": tool_results})

                    # Continue loop to get Claude's response with tool results
                    continue

                # No more tool calls - extract final text response
                final_text = ""
                for block in content_blocks:
                    if block.get('type') == 'text':
                        final_text += block.get('text', '')

                end_time = datetime.now()
                response_time = int((end_time - start_time).total_seconds() * 1000)

                return ModelResponse(
                    model_name=model,
                    response=final_text.strip(),
                    response_time_ms=response_time,
                    success=True
                )

            # If we hit max iterations, return what we have
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

        except ClientError as e:
            end_time = datetime.now()
            response_time = int((end_time - start_time).total_seconds() * 1000)
            error_msg = f"Bedrock API error: {e.response['Error']['Code']} - {e.response['Error']['Message']}"
            logger.error(error_msg)

            return ModelResponse(
                model_name=model,
                response="",
                response_time_ms=response_time,
                success=False,
                error=error_msg
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
        """
        Generate streaming response from Bedrock Claude model.

        Supports tool use - if Claude calls a tool, we pause streaming,
        execute the tool, and continue with a new stream.
        """
        start_time = datetime.now()
        full_response = ""
        chunk_count = 0

        try:
            system, messages = self._format_messages_for_bedrock(prompt, system_prompt)

            body = {
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 4096,
                "messages": messages,
            }
            if system:
                body["system"] = system
            if tools:
                body["tools"] = tools

            iteration = 0
            while iteration < MAX_TOOL_ITERATIONS:
                iteration += 1

                logger.info(f"Starting Bedrock stream for model {model} (iteration {iteration})")

                response = self.client.invoke_model_with_response_stream(
                    modelId=model,
                    contentType="application/json",
                    accept="application/json",
                    body=json.dumps(body)
                )

                # Track tool use during streaming
                current_tool_use = None
                tool_use_blocks = []
                content_blocks = []
                stop_reason = None

                # Stream response from Bedrock
                for event in response['body']:
                    current_time = datetime.now()
                    chunk_count += 1

                    if 'chunk' in event:
                        chunk_data = json.loads(event['chunk']['bytes'].decode())
                        chunk_type = chunk_data.get('type')

                        if chunk_type == 'content_block_start':
                            block = chunk_data.get('content_block', {})
                            if block.get('type') == 'tool_use':
                                current_tool_use = {
                                    'type': 'tool_use',
                                    'id': block.get('id'),
                                    'name': block.get('name'),
                                    'input': ''
                                }
                            elif block.get('type') == 'text':
                                content_blocks.append({'type': 'text', 'text': ''})

                        elif chunk_type == 'content_block_delta':
                            delta = chunk_data.get('delta', {})
                            delta_type = delta.get('type')

                            if delta_type == 'text_delta':
                                token = delta.get('text', '')
                                full_response += token
                                if content_blocks and content_blocks[-1].get('type') == 'text':
                                    content_blocks[-1]['text'] += token

                                yield {
                                    'token': token,
                                    'done': False,
                                    'model_name': model,
                                    'response_time_ms': int((current_time - start_time).total_seconds() * 1000),
                                    'chunk_number': chunk_count
                                }

                            elif delta_type == 'input_json_delta' and current_tool_use:
                                # Accumulate tool input JSON
                                current_tool_use['input'] += delta.get('partial_json', '')

                        elif chunk_type == 'content_block_stop':
                            if current_tool_use:
                                # Parse the accumulated JSON input
                                try:
                                    current_tool_use['input'] = json.loads(current_tool_use['input'])
                                except json.JSONDecodeError:
                                    current_tool_use['input'] = {}
                                tool_use_blocks.append(current_tool_use)
                                content_blocks.append(current_tool_use)
                                current_tool_use = None

                        elif chunk_type == 'message_delta':
                            stop_reason = chunk_data.get('delta', {}).get('stop_reason')

                        elif chunk_type == 'message_stop':
                            break

                # Check if we need to execute tools
                if stop_reason == 'tool_use' and tool_use_blocks and tool_executor:
                    logger.info(f"Claude requested {len(tool_use_blocks)} tool call(s)")

                    # Notify client that we're executing tools
                    yield {
                        'token': '\n\n*Searching for more events...*\n\n',
                        'done': False,
                        'model_name': model,
                        'tool_use': True,
                        'response_time_ms': int((datetime.now() - start_time).total_seconds() * 1000),
                    }

                    # Execute tools and collect results
                    tool_results = []
                    for tool_block in tool_use_blocks:
                        tool_name = tool_block.get('name')
                        tool_input = tool_block.get('input', {})
                        tool_use_id = tool_block.get('id')

                        logger.info(f"Executing tool: {tool_name} with {tool_input}")
                        result = tool_executor.execute(tool_name, tool_input)

                        from api.llm_tools import format_tool_result_for_claude
                        tool_results.append(format_tool_result_for_claude(tool_use_id, result))

                    # Add to messages for next iteration
                    body["messages"].append({"role": "assistant", "content": content_blocks})
                    body["messages"].append({"role": "user", "content": tool_results})

                    # Continue to next iteration to get final response
                    continue

                # No more tool calls - we're done
                break

            # Final response with complete metadata
            end_time = datetime.now()
            response_time = int((end_time - start_time).total_seconds() * 1000)

            logger.info(f"Bedrock stream completed for {model}: {chunk_count} chunks, {response_time}ms, {len(full_response)} chars")

            yield {
                'token': '',
                'done': True,
                'model_name': model,
                'response_time_ms': response_time,
                'full_response': full_response.strip(),
                'success': True,
                'total_chunks': chunk_count
            }

        except ClientError as e:
            end_time = datetime.now()
            response_time = int((end_time - start_time).total_seconds() * 1000)
            error_msg = f"Bedrock API error: {e.response['Error']['Code']} - {e.response['Error']['Message']}"
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
            error_msg = f"Bedrock stream error for {model}: {type(e).__name__}: {str(e)}"
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
