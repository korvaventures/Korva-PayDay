from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, ConfigDict, EmailStr
from pydantic_extra_types.currency_code import Currency

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
