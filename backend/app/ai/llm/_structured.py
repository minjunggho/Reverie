"""Shared helpers for real providers: build a strict JSON tool from a Pydantic model
and validate the returned arguments. Keeps structured-output logic in one place.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ValidationError

from app.core.errors import LLMError

TOOL_NAME = "emit_result"


def tool_schema(response_model: type[BaseModel]) -> dict[str, Any]:
    return {
        "name": TOOL_NAME,
        "description": (
            "Return the structured result. You MUST call this tool exactly once "
            "with arguments that satisfy the schema. Do not include prose."
        ),
        "input_schema": response_model.model_json_schema(),
    }


def validate_arguments(response_model: type[BaseModel], arguments: dict[str, Any]):
    try:
        return response_model.model_validate(arguments)
    except ValidationError as exc:
        raise LLMError(f"invalid structured output for {response_model.__name__}: {exc}")
