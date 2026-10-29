import json
from datetime import date
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field, ConfigDict, EmailStr, field_validator
from pydantic_extra_types.currency_code import Currency


InvoiceCurrency = Literal["USD", "EUR", "GBP", "CAD", "AUD"]


class ProductItem(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    quantity: int = Field(1, ge=1)
    unit_amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)


class InvoiceDraft(BaseModel):
    client_name: str = Field(min_length=1, max_length=100)
    client_email: EmailStr
    currency_code: Currency
    items: list[ProductItem] = Field(min_items=1)
    deposit_percent: Decimal = Field(0, ge=0, le=100)
    due_in_days: int = Field(14, ge=1, le=365)
    note: str | None = Field(None, max_length=500)

    @property
    def total(self) -> Decimal:
        return sum((i.unit_amount * i.quantity for i in self.items), Decimal("0.00"))


class ReminderIn(BaseModel):
    subject: str = Field(min_length=1, max_length=200)
    note: str = Field(min_length=1, max_length=700)

class InvoiceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    paypal_invoice_id: str
    client_name: str
    client_email: EmailStr
    currency_code: str
    amount: Decimal
    due_date: date
    status: str
    payment_link: str | None
    is_overdue: bool

class ParseIn(BaseModel):
    text: str = Field(min_length=3, max_length=2000)


class DraftSuggestion(BaseModel):
    client_name: str | None = None
    client_email: EmailStr | None = None
    currency_code: Currency | None = None
    items: list[ProductItem] = []
    deposit_percent: int = Field(0, ge=0, le=100)
    due_in_days: int = Field(14, ge=1, le=365)
    model_config = ConfigDict(extra="forbid")
    note: str | None = None

class ParseOut(BaseModel):
    draft: DraftSuggestion
    missing: list[str]


class GridAssistantIn(BaseModel):
    query: str = Field(min_length=2, max_length=500)
    current_state: dict[str, Any]
    grid_schema: dict[str, Any]

    @field_validator("current_state")
    @classmethod
    def limit_current_state(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(json.dumps(value)) > 12000:
            raise ValueError("current_state is too large")
        return value

    @field_validator("grid_schema")
    @classmethod
    def validate_grid_schema(cls, value: dict[str, Any]) -> dict[str, Any]:
        if value.get("type") != "object" or not isinstance(value.get("properties"), dict):
            raise ValueError("grid_schema must be an object schema")
        if len(json.dumps(value)) > 24000:
            raise ValueError("grid_schema is too large")
        return value