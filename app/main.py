import json
import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings

from .db import OPEN_STATUSES, Base, Invoice, SessionLocal, WebhookEvent, engine, get_db
from .paypal import PayPalError, build_invoice_payload, paypal
from .llm import LLMParseError, interpret_grid_request, parse_invoice_text
from .schemas import GridAssistantIn, InvoiceDraft, InvoiceOut, ParseIn, ParseOut, ReminderIn

log = logging.getLogger("payday")


@asynccontextmanager
async def lifespan(_: FastAPI):
    # create_all instead of Alembic: one schema, one week. Add migrations if it outlives that.
    Base.metadata.create_all(engine)
    yield


app = FastAPI(title="PayDay API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",")],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(PayPalError)
async def paypal_error_handler(_: Request, exc: PayPalError) -> JSONResponse:
    # Pass PayPal's body through: its debug_id and field paths are the fastest way to fix a bad payload.
    return JSONResponse(status_code=502, content={"error": "paypal_error", "status": exc.status, "detail": exc.body})

@app.exception_handler(LLMParseError)
async def llm_error_handler(_: Request, exc: LLMParseError) -> JSONResponse:
    return JSONResponse(status_code=502, content={"error": "llm_parse_error", "detail": str(exc)})

@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.post("/api/invoices", response_model=InvoiceOut, status_code=201)
def create_and_send(
    draft: InvoiceDraft,
    db: Session = Depends(get_db),
    idempotency_key: str | None = Header(default=None),
):
    # The human confirmation happens in the UI before this call. Nothing upstream touches PayPal.
    number = paypal.next_invoice_number()
    payload, due = build_invoice_payload(draft, number)
    created = paypal.create_invoice(payload, request_id=idempotency_key or str(uuid.uuid4()))
    pp_id = created["id"]

    # Known gap: if send fails, the draft stays in PayPal. Retrying with the same
    # Idempotency-Key reuses it rather than creating a duplicate.
    paypal.send_invoice(pp_id, note=draft.note)

    detail = paypal.get_invoice(pp_id)  # re-read: only a sent invoice has the payer link
    link = detail.get("detail", {}).get("metadata", {}).get("recipient_view_url")

    invoice = Invoice(
        paypal_invoice_id=pp_id,
        client_name=draft.client_name,
        client_email=str(draft.client_email),
        currency_code=draft.currency_code,
        amount=draft.total,
        due_date=due,
        status=detail.get("status", "SENT"),
        payment_link=link,
    )
    db.add(invoice)
    db.commit()
    return invoice


@app.get("/api/invoices", response_model=list[InvoiceOut])
def list_invoices(db: Session = Depends(get_db)):
    return db.scalars(select(Invoice).order_by(Invoice.created_at.desc())).all()


@app.post("/api/invoices/{invoice_id}/refresh", response_model=InvoiceOut)
def refresh(invoice_id: int, db: Session = Depends(get_db)):
    # Reconciliation path for when a webhook was missed (or isn't wired up yet).
    inv = db.get(Invoice, invoice_id)
    if not inv:
        raise HTTPException(404, "invoice not found")
    inv.status = paypal.get_invoice(inv.paypal_invoice_id)["status"]
    db.commit()
    return inv


@app.post("/api/invoices/{invoice_id}/remind", status_code=204)
def send_reminder(invoice_id: int, body: ReminderIn, db: Session = Depends(get_db)):
    # This endpoint is the approval gate: the drafted text only reaches the client when called.
    inv = db.get(Invoice, invoice_id)
    if not inv:
        raise HTTPException(404, "invoice not found")
    if inv.status not in OPEN_STATUSES:
        raise HTTPException(409, f"invoice is {inv.status}; nothing to remind")
    paypal.remind_invoice(inv.paypal_invoice_id, body.subject, body.note)
    return Response(status_code=204)


HANDLED_EVENTS = {
    "INVOICING.INVOICE.PAID",
    "INVOICING.INVOICE.CANCELLED",
    "INVOICING.INVOICE.REFUNDED",
    "INVOICING.INVOICE.UPDATED",
}


def _handle_event(event: dict) -> None:
    with SessionLocal() as db:
        if db.get(WebhookEvent, event["id"]):
            return  # PayPal retries failed deliveries; make processing idempotent
        db.add(WebhookEvent(id=event["id"], event_type=event["event_type"]))

        if event["event_type"] in HANDLED_EVENTS:
            resource = event.get("resource", {})
            pp_id = (resource.get("invoice") or resource).get("id")
            inv = db.scalar(select(Invoice).where(Invoice.paypal_invoice_id == pp_id)) if pp_id else None
            if inv:
                # The event says something happened; the invoice says what state it is now in.
                # PAID also fires for partial and pending payments, so never infer "paid" from the name.
                # If this raises we return 500 before commit and PayPal retries, which is what we want.
                inv.status = paypal.get_invoice(pp_id)["status"]
            else:
                log.warning("webhook %s for unknown invoice %s", event["event_type"], pp_id)
        db.commit()


@app.post("/webhooks/paypal")
async def paypal_webhook(request: Request):
    raw = await request.body()
    try:
        event = json.loads(raw)
    except ValueError:
        raise HTTPException(400, "invalid json")

    headers = {k.lower(): v for k, v in request.headers.items()}
    if not await run_in_threadpool(paypal.verify_webhook, headers, event):
        raise HTTPException(400, "signature verification failed")

    await run_in_threadpool(_handle_event, event)
    return {"ok": True}

@app.post("/api/parse", response_model=ParseOut)
def parse(body: ParseIn):
    draft = parse_invoice_text(body.text)
    required = {
        "client_name": draft.client_name,
        "client_email": draft.client_email,
        "currency_code": draft.currency_code,
        "items": draft.items,
    }
    return ParseOut(draft=draft, missing=[k for k, v in required.items() if not v])


@app.post("/api/grid-assistant")
async def grid_assistant(body: GridAssistantIn) -> dict:
    return await run_in_threadpool(interpret_grid_request, body)