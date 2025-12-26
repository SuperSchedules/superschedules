"""
Traces app for chat debugging and pipeline inspection.

Provides a Pinecone-like interface in Django admin to:
- Run debug chat queries with parameter tweaking
- Inspect retrieval results, context assembly, and prompts
- Diagnose LLM "flailing" with visibility into the full pipeline
"""

default_app_config = 'traces.apps.TracesConfig'
