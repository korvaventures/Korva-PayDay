# Copilot instructions for Korva PayDay

## Project overview

This repository is a Python 3.12 FastAPI service that turns invoice requests into PayPal invoicing workflows and uses Gemini for invoice text extraction. The app is intentionally small and service-oriented: the API layer in `app/main.py` delegates business logic to `app/paypal.py`, `app/llm.py`, and `app/db.py`.

## Commands

Use the project environment via `uv` rather than bare `python` when possible.

- Install project dependencies: `uv sync`
- Run the focused test file: `uv run python -m pytest tests/test_llm_coerce.py -q`
- Run a single test by name: `uv run python -m pytest tests/test_llm_coerce.py -k "dollar_sign_maps_to_usd_even_if_model_omits_it" -q`
- Run the full suite: `uv run python -m pytest -q`

No dedicated lint or formatting command is configured in this repo right now; there is no `ruff`, `black`, or `mypy` setup in `pyproject.toml`.

## Architecture

- `app/config.py`: loads environment settings from `.env` with `pydantic-settings`. This is the central place for PayPal credentials, Gemini API key, DB URL, and CORS origins.
- `app/main.py`: FastAPI app, startup lifecycle, REST endpoints, PayPal webhook verification, and request-level error handling. This file is the best place to understand the user-facing API behavior.
- `app/db.py`: SQLAlchemy models and database session management. The default database is SQLite (`sqlite:///./payday.db`), and the app creates tables on startup via `Base.metadata.create_all(engine)`.
- `app/paypal.py`: PayPal REST client wrapper. It handles OAuth, idempotent invoice creation (`PayPal-Request-Id`), partial-payment configuration, invoice status updates, reminder sending, and webhook signature verification.
- `app/llm.py`: Gemini structured-output path. It uses Google GenAI function calling (`fill_invoice_form`) to extract invoice details from free-form text and then normalizes them with `_coerce()` before returning a `DraftSuggestion`.
- `app/schemas.py`: Pydantic models for invoice drafts, parsed output, and API contracts. This is the shared contract between the LLM extraction and the HTTP layer.
- `tests/test_llm_coerce.py`: deterministic regression tests for currency detection and coercion edge cases. These tests document the “evidence beats model guess” behavior.

## Key conventions

- Prefer explicit data-source precedence over model guesswork: invoice parsing prefers evidence in the user text (currency symbols/codes in the text) before trusting the model payload.
- Keep PayPal and LLM logic out of route handlers. The API routes should orchestrate flow and error handling, while the dedicated clients handle low-level integration work.
- Treat inbound webhooks as idempotent and retry-safe. `WebhookEvent` stores processed PayPal event IDs, and unknown invoice events are logged rather than assumed valid.
- Use the project’s `uv` workflow for Python commands; the repo is configured as a Python package with `pyproject.toml`, not a Node-driven app.
- Keep environment configuration in `.env` for local development; new settings should be added to `app/config.py` and remain optional where appropriate.
- Do not rely on a migration system here: startup creates tables directly, which is appropriate for the current small single-service footprint. If schema evolution grows, add migrations intentionally.

## Notes from project docs

The architectural decision record (`docs/adr/0001-fastapi-backend.md`) confirms that the project intentionally uses a FastAPI backend for PayPal and LLM work, with the frontend treated as a separate AG Studio UI that calls these APIs.
