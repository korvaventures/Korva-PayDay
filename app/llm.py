import json
import re
from typing import Any

from google import genai
from google.genai import types

from .config import settings
from .schemas import DraftSuggestion, GridAssistantIn


class LLMParseError(Exception):
    pass


_client_instance: genai.Client | None = None
_SUPPORTED_CURRENCIES = {"USD", "EUR", "GBP", "CAD", "AUD"}
_JSON_SCHEMA_KEYWORDS = {
    "type",
    "description",
    "enum",
    "format",
    "properties",
    "required",
    "items",
    "additionalProperties",
    "minimum",
    "maximum",
    "minItems",
    "maxItems",
    "minLength",
    "maxLength",
    "nullable",
}


def _client() -> genai.Client:
    global _client_instance
    if _client_instance is None:
        _client_instance = genai.Client(api_key=settings.gemini_api_key)
    return _client_instance


def _generate_content(*, contents: str, config: types.GenerateContentConfig) -> Any:
    models = dict.fromkeys((settings.llm_model, settings.llm_fallback_model))
    last_error: Exception | None = None
    for model in models:
        try:
            return _client().models.generate_content(model=model, contents=contents, config=config)
        except Exception as exc:
            last_error = exc
    if last_error is None:
        raise LLMParseError("No LLM model is configured.")
    raise last_error


def _coerce(payload: dict[str, Any], source_text: str) -> DraftSuggestion:
    data = dict(payload)
    explicit_currency = re.search(r"\b(USD|EUR|GBP|CAD|AUD|KES)\b", source_text, re.IGNORECASE)
    if explicit_currency:
        currency = explicit_currency.group(1).upper()
        data["currency_code"] = currency if currency in _SUPPORTED_CURRENCIES else None
    elif "€" in source_text:
        data["currency_code"] = "EUR"
    elif "£" in source_text:
        data["currency_code"] = "GBP"
    elif re.search(r"\b(?:C\$|CA\$)", source_text, re.IGNORECASE):
        data["currency_code"] = "CAD"
    elif re.search(r"\b(?:A\$|AU\$)", source_text, re.IGNORECASE):
        data["currency_code"] = "AUD"
    elif "$" in source_text:
        data["currency_code"] = "USD"
    elif data.get("currency_code") not in _SUPPORTED_CURRENCIES:
        data["currency_code"] = None

    if data.get("currency_code") not in _SUPPORTED_CURRENCIES:
        data["currency_code"] = None
    return DraftSuggestion.model_validate(data)


def _parse_function_result(response: Any, function_name: str) -> dict[str, Any]:
    calls = getattr(response, "function_calls", None) or []
    for call in calls:
        if getattr(call, "name", None) == function_name and isinstance(call.args, dict):
            return call.args
    raise LLMParseError("The model did not return a valid grid update or invoice form.")


def parse_invoice_text(text: str) -> DraftSuggestion:
    declaration = types.FunctionDeclaration(
        name="fill_invoice_form",
        description="Extract the invoice details that are explicitly present in the user's text.",
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "client_name": types.Schema(type=types.Type.STRING, nullable=True),
                "client_email": types.Schema(type=types.Type.STRING, nullable=True),
                "currency_code": types.Schema(
                    type=types.Type.STRING,
                    enum=sorted(_SUPPORTED_CURRENCIES),
                    nullable=True,
                ),
                "items": types.Schema(
                    type=types.Type.ARRAY,
                    items=types.Schema(
                        type=types.Type.OBJECT,
                        properties={
                            "name": types.Schema(type=types.Type.STRING),
                            "quantity": types.Schema(type=types.Type.INTEGER),
                            "unit_amount": types.Schema(type=types.Type.NUMBER),
                        },
                        required=["name", "quantity", "unit_amount"],
                    ),
                    nullable=True,
                ),
                "deposit_percent": types.Schema(type=types.Type.INTEGER, nullable=True),
                "due_in_days": types.Schema(type=types.Type.INTEGER, nullable=True),
                "note": types.Schema(type=types.Type.STRING, nullable=True),
            },
        ),
    )
    try:
        response = _generate_content(
            contents=f"Extract invoice details from this text. Do not invent missing information.\n\n{text}",
            config=types.GenerateContentConfig(
                tools=[types.Tool(function_declarations=[declaration])],
            ),
        )
        return _coerce(_parse_function_result(response, "fill_invoice_form"), text)
    except LLMParseError:
        raise
    except Exception as exc:
        raise LLMParseError(f"Unable to parse invoice text: {exc}") from exc


def _normalize_genai_schema(schema: dict[str, Any]) -> dict[str, Any]:
    definitions: dict[str, Any] = {}
    root = schema

    def collect_definitions(value: Any) -> None:
        if isinstance(value, dict):
            for child in value.values():
                collect_definitions(child)
            for key in ("$defs", "definitions"):
                nested_definitions = value.get(key)
                if isinstance(nested_definitions, dict):
                    for name, definition in nested_definitions.items():
                        definitions.setdefault(name, definition)
        elif isinstance(value, list):
            for child in value:
                collect_definitions(child)

    collect_definitions(schema)

    def normalize(value: Any, resolving: frozenset[str] = frozenset()) -> Any:
        if isinstance(value, list):
            return [normalize(item, resolving) for item in value]
        if not isinstance(value, dict):
            return value

        reference = value.get("$ref")
        if reference is not None:
            if not isinstance(reference, str) or not reference.startswith("#/"):
                raise LLMParseError(f"unsupported reference: {reference}")
            if reference in resolving:
                raise LLMParseError(f"unsupported reference: {reference}")
            target: Any = root
            for part in reference[2:].split("/"):
                part = part.replace("~1", "/").replace("~0", "~")
                if isinstance(target, dict) and part in target:
                    target = target[part]
                else:
                    target = None
                    break
            if target is None:
                parts = reference[2:].split("/")
                if len(parts) == 2 and parts[0] in {"$defs", "definitions"}:
                    target = definitions.get(parts[1])
            if target is None:
                raise LLMParseError(f"unsupported reference: {reference}")
            resolved = normalize(target, resolving | {reference})
            extras = {key: item for key, item in value.items() if key != "$ref"}
            if extras and isinstance(resolved, dict):
                resolved = {**resolved, **normalize(extras, resolving)}
            return resolved

        result = {
            key: (
                {name: normalize(item, resolving) for name, item in child.items()}
                if key == "properties" and isinstance(child, dict)
                else normalize(child, resolving)
            )
            for key, child in value.items()
            if key in _JSON_SCHEMA_KEYWORDS
        }
        schema_type = result.get("type")
        if isinstance(schema_type, list):
            nullable = "null" in schema_type
            non_null_types = [item for item in schema_type if item != "null"]
            if len(non_null_types) != 1:
                raise LLMParseError("unsupported schema type")
            result["type"] = non_null_types[0]
            if nullable:
                result["nullable"] = True
        return result

    normalized = normalize(schema)
    if not isinstance(normalized, dict):
        raise LLMParseError("grid schema must be an object")
    return normalized


def _genai_schema(schema: dict[str, Any]) -> types.Schema:
    schema_type = schema.get("type")
    if isinstance(schema_type, str):
        schema_type = getattr(types.Type, schema_type.upper(), None)
    fields: dict[str, Any] = {
        key: value
        for key, value in schema.items()
        if key in _JSON_SCHEMA_KEYWORDS and key not in {"type", "properties", "items", "additionalProperties"}
    }
    if schema_type is not None:
        fields["type"] = schema_type
    if "properties" in schema:
        fields["properties"] = {
            key: _genai_schema(value) for key, value in schema["properties"].items()
        }
    if isinstance(schema.get("items"), dict):
        fields["items"] = _genai_schema(schema["items"])
    if isinstance(schema.get("additionalProperties"), dict):
        fields["additional_properties"] = _genai_schema(schema["additionalProperties"])
    return types.Schema(**fields)


def interpret_grid_request(request: GridAssistantIn) -> dict[str, Any]:
    grid_schema = _normalize_genai_schema(request.grid_schema)
    if grid_schema.get("type") != "object":
        raise LLMParseError("grid schema must be an object")
    response_schema = {
        "type": "object",
        "properties": {
            "gridState": grid_schema,
            "propertiesToIgnore": {"type": "array", "items": {"type": "string"}},
            "explanation": {"type": "string"},
        },
        "required": ["gridState", "propertiesToIgnore", "explanation"],
    }
    declaration = types.FunctionDeclaration(
        name="apply_invoice_grid_state",
        description="Return a valid grid state update for the user's request.",
        parameters=_genai_schema(response_schema),
    )
    prompt = (
        "Update the invoice grid state according to the user's request. "
        "Only return values conforming to the provided schema.\n"
        f"Current grid state: {json.dumps(request.current_state)}\n"
        f"User request: {request.query}"
    )
    try:
        response = _generate_content(
            contents=prompt,
            config=types.GenerateContentConfig(
                tools=[types.Tool(function_declarations=[declaration])],
            ),
        )
        return _parse_function_result(response, "apply_invoice_grid_state")
    except LLMParseError:
        raise
    except Exception as exc:
        raise LLMParseError(f"Unable to interpret grid request: {exc}") from exc