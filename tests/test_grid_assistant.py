from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app import llm
from app.schemas import GridAssistantIn


def test_grid_schema_inlines_local_definitions_and_drops_unsupported_keywords():
    schema = {
        "type": "object",
        "$defs": {
            "columnId": {"type": "string", "enum": ["client_name", "amount"]},
        },
        "properties": {
            "sort": {
                "type": "array",
                "items": {"$ref": "#/$defs/columnId"},
            },
        },
    }

    normalized = llm._normalize_genai_schema(schema)

    assert "$defs" not in normalized
    assert normalized["properties"]["sort"]["items"] == {
        "type": "string",
        "enum": ["client_name", "amount"],
    }


def test_grid_schema_inlines_nested_column_definitions():
    schema = {
        "type": "object",
        "properties": {
            "sort": {
                "$defs": {
                    "allColumnIds": {
                        "type": "string",
                        "enum": ["client_name", "amount"],
                    },
                },
                "type": "array",
                "items": {"$ref": "#/$defs/allColumnIds"},
            },
        },
    }

    normalized = llm._normalize_genai_schema(schema)

    assert normalized["properties"]["sort"]["items"] == {
        "type": "string",
        "enum": ["client_name", "amount"],
    }


def test_grid_schema_rejects_external_references():
    with pytest.raises(llm.LLMParseError, match="unsupported reference"):
        llm._normalize_genai_schema({"$ref": "https://example.com/schema.json"})


def test_grid_assistant_requires_an_object_schema():
    with pytest.raises(ValidationError):
        GridAssistantIn(
            query="Show overdue invoices",
            current_state={},
            grid_schema={"type": "string"},
        )


def test_grid_assistant_normalizes_nullable_grid_schema(monkeypatch):
    expected = {
        "gridState": {"sort": {"sortModel": [{"colId": "amount", "sort": "desc"}]}},
        "propertiesToIgnore": ["filter"],
        "explanation": "Showing the largest invoices first.",
    }

    class Models:
        def generate_content(self, *, model, contents, config):
            assert model
            assert "Show largest invoices" in contents
            schema = config.tools[0].function_declarations[0].parameters.model_dump(
                by_alias=True,
                exclude_none=True,
            )
            assert "$ref" not in repr(schema)
            assert "$defs" not in repr(schema)
            grid_state_schema = schema["properties"]["gridState"]
            normalized = grid_state_schema["properties"]["filter"]["properties"]["filterType"]
            assert normalized["type"].value == "STRING"
            assert normalized["nullable"] is True
            return SimpleNamespace(
                function_calls=[SimpleNamespace(name="apply_invoice_grid_state", args=expected)]
            )

    monkeypatch.setattr(llm, "_client", lambda: SimpleNamespace(models=Models()))
    request = GridAssistantIn(
        query="Show largest invoices",
        current_state={},
        grid_schema={
            "type": "object",
            "$defs": {"filterType": {"type": ["string", "null"]}},
            "properties": {
                "filter": {
                    "type": "object",
                    "properties": {"filterType": {"$ref": "#/$defs/filterType"}},
                }
            },
        },
    )

    assert llm.interpret_grid_request(request) == expected


def test_grid_assistant_rejects_unexpected_function_response(monkeypatch):
    monkeypatch.setattr(
        llm,
        "_client",
        lambda: SimpleNamespace(
            models=SimpleNamespace(
                generate_content=lambda **_: SimpleNamespace(
                    function_calls=[SimpleNamespace(name="other", args={})]
                )
            )
        ),
    )
    request = GridAssistantIn(
        query="Show overdue invoices",
        current_state={},
        grid_schema={"type": "object", "properties": {}},
    )

    with pytest.raises(llm.LLMParseError, match="valid grid update"):
        llm.interpret_grid_request(request)


def test_llm_retries_with_fallback_model(monkeypatch):
    models_tried = []

    class Models:
        def generate_content(self, *, model, contents, config):
            models_tried.append(model)
            if model == "primary":
                raise RuntimeError("temporary model failure")
            return SimpleNamespace(function_calls=[])

    monkeypatch.setattr(llm, "_client", lambda: SimpleNamespace(models=Models()))
    monkeypatch.setattr(llm.settings, "llm_model", "primary")
    monkeypatch.setattr(llm.settings, "llm_fallback_model", "fallback")

    llm._generate_content(contents="prompt", config=llm.types.GenerateContentConfig())

    assert models_tried == ["primary", "fallback"]
