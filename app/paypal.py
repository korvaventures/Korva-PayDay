import time
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

import httpx

from .config import settings
from .schemas import InvoiceDraft

CENT = Decimal("0.01")


class PayPalError(Exception):
    def __init__(self, status: int, body: str):
        super().__init__(f"PayPal {status}: {body}")
        self.status = status
        self.body = body


def _money(currency: str, value: Decimal) -> dict:
    # PayPal wants amounts as strings; floats would reintroduce rounding drift.
    return {"currency_code": currency, "value": str(value.quantize(CENT, ROUND_HALF_UP))}


def build_invoice_payload(draft: InvoiceDraft, invoice_number: str) -> tuple[dict, date]:
    today = date.today()
    due = today + timedelta(days=draft.due_in_days)
    cur = draft.currency_code

    detail: dict = {
        "invoice_number": invoice_number,
        "invoice_date": today.isoformat(),
        "currency_code": cur,
        "payment_term": {"term_type": "DUE_ON_DATE_SPECIFIED", "due_date": due.isoformat()},
    }
    if draft.note:
        detail["note"] = draft.note

    payload: dict = {
        "detail": detail,
        "primary_recipients": [
            {"billing_info": {"business_name": draft.client_name, "email_address": str(draft.client_email)}}
        ],
        "items": [
            {"name": i.name, "quantity": str(i.quantity), "unit_amount": _money(cur, i.unit_amount)}
            for i in draft.items
        ],
    }
    if draft.deposit_percent:
        # Native partial payment, so PayPal enforces the deposit rather than us faking it.
        deposit = draft.total * Decimal(draft.deposit_percent) / Decimal(100)
        payload["configuration"] = {
            "partial_payment": {
                "allow_partial_payment": True,
                "minimum_amount_due": _money(cur, deposit),
            }
        }
    return payload, due


class PayPalClient:
    def __init__(self) -> None:
        self._http = httpx.Client(base_url=settings.paypal_base_url, timeout=20)
        self._token: str | None = None
        self._expires_at = 0.0

    def _bearer(self) -> str:
        # Refresh a minute early so a token never expires mid-request.
        if self._token and time.time() < self._expires_at - 60:
            return self._token
        res = self._http.post(
            "/v1/oauth2/token",
            data={"grant_type": "client_credentials"},
            auth=(settings.paypal_client_id, settings.paypal_secret),
        )
        if res.status_code >= 400:
            raise PayPalError(res.status_code, res.text)
        data = res.json()
        self._token = data["access_token"]
        self._expires_at = time.time() + int(data["expires_in"])
        return self._token

    def _call(self, method: str, path: str, *, json: dict | None = None,
              request_id: str | None = None, representation: bool = False) -> dict:
        headers = {"Authorization": f"Bearer {self._bearer()}"}
        headers["Content-Type"] = "application/json"
        if request_id:
            headers["PayPal-Request-Id"] = request_id  # idempotency: retries don't double-create
        if representation:
            headers["Prefer"] = "return=representation"
        res = self._http.request(method, path, json=json, headers=headers)
        if res.status_code >= 400:
            raise PayPalError(res.status_code, res.text)
        return res.json() if res.content else {}

    def next_invoice_number(self) -> str:
        return self._call("POST", "/v2/invoicing/generate-next-invoice-number")["invoice_number"]

    def create_invoice(self, payload: dict, request_id: str) -> dict:
        return self._call("POST", "/v2/invoicing/invoices", json=payload,
                          request_id=request_id, representation=True)

    def send_invoice(self, invoice_id: str, note: str | None = None) -> dict:
        body: dict = {"send_to_recipient": True, "send_to_invoicer": False}
        if note:
            body["note"] = note
        return self._call("POST", f"/v2/invoicing/invoices/{invoice_id}/send", json=body)

    def remind_invoice(self, invoice_id: str, subject: str, note: str) -> dict:
        return self._call("POST", f"/v2/invoicing/invoices/{invoice_id}/remind",
                          json={"subject": subject, "note": note, "send_to_invoicer": False})

    def get_invoice(self, invoice_id: str) -> dict:
        return self._call("GET", f"/v2/invoicing/invoices/{invoice_id}")

    def verify_webhook(self, headers: dict[str, str], event: dict) -> bool:
        needed = ("paypal-auth-algo", "paypal-cert-url", "paypal-transmission-id",
                  "paypal-transmission-sig", "paypal-transmission-time")
        if not settings.paypal_webhook_id or any(k not in headers for k in needed):
            return False
        res = self._call("POST", "/v1/notifications/verify-webhook-signature", json={
            "auth_algo": headers["paypal-auth-algo"],
            "cert_url": headers["paypal-cert-url"],
            "transmission_id": headers["paypal-transmission-id"],
            "transmission_sig": headers["paypal-transmission-sig"],
            "transmission_time": headers["paypal-transmission-time"],
            "webhook_id": settings.paypal_webhook_id,
            "webhook_event": event,
        })
        return res.get("verification_status") == "SUCCESS"


paypal = PayPalClient()
