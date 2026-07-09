"""Pydantic DTOs and all LLM input/output schemas.

Every AI job validates its output against one of these schemas. If the LLM returns
something that does not validate, the provider retries (bounded) and then the job
falls back to a safe default. The engine only ever consumes validated objects.
"""
