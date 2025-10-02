"""
Amazon Bedrock LLM provider implementation.
Connects to AWS Bedrock for Claude model inference.
"""

import json
import logging
from typing import List, Optional, AsyncGenerator, Dict, Any
from datetime import datetime

import boto3
from botocore.exceptions import ClientError
from django.conf import settings

from .base import BaseLLMProvider, ModelResponse


logger = logging.getLogger(__name__)


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
        stream: bool = False
    ) -> ModelResponse:
        """Generate response from Bedrock Claude model."""
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

            response = self.client.invoke_model(
                modelId=model,
                contentType="application/json",
                accept="application/json",
                body=json.dumps(body)
            )

            response_body = json.loads(response['body'].read())
            content = response_body.get('content', [{}])[0].get('text', '')

            end_time = datetime.now()
            response_time = int((end_time - start_time).total_seconds() * 1000)

            return ModelResponse(
                model_name=model,
                response=content.strip(),
                response_time_ms=response_time,
                success=True
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
        timeout_seconds: int = 60
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Generate streaming response from Bedrock Claude model."""
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

            logger.info(f"Starting Bedrock stream for model {model}")

            response = self.client.invoke_model_with_response_stream(
                modelId=model,
                contentType="application/json",
                accept="application/json",
                body=json.dumps(body)
            )

            # Stream response from Bedrock
            for event in response['body']:
                current_time = datetime.now()
                chunk_count += 1

                if 'chunk' in event:
                    chunk_data = json.loads(event['chunk']['bytes'].decode())

                    # Claude streaming format
                    if chunk_data.get('type') == 'content_block_delta':
                        delta = chunk_data.get('delta', {})
                        if delta.get('type') == 'text_delta':
                            token = delta.get('text', '')
                            full_response += token

                            yield {
                                'token': token,
                                'done': False,
                                'model_name': model,
                                'response_time_ms': int((current_time - start_time).total_seconds() * 1000),
                                'chunk_number': chunk_count
                            }

                    elif chunk_data.get('type') == 'message_stop':
                        # Final chunk - streaming complete
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
