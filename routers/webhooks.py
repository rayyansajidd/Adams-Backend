from fastapi import APIRouter, Request, HTTPException, Depends
from sqlalchemy.orm import Session
from db.init import get_db
from models.user import Customer
from models.subscription import Invoice, SubscriptionLog
from datetime import date
import base64
import hashlib
import hmac
import json
import logging
import os
from typing import Dict, Any

router = APIRouter()
logger = logging.getLogger(__name__)


def verify_square_signature(signature: str, body: bytes) -> bool:
    """Square signs HMAC-SHA256 over (notification_url + raw body), base64 encoded.

    Fails closed: without both env vars set, no webhook is accepted.
    """
    key = os.getenv("SQUARE_WEBHOOK_SIGNATURE_KEY")
    url = os.getenv("SQUARE_WEBHOOK_URL")
    if not key or not url:
        logger.error("SQUARE_WEBHOOK_SIGNATURE_KEY / SQUARE_WEBHOOK_URL not set - rejecting webhook")
        return False
    expected = base64.b64encode(
        hmac.new(key.encode(), url.encode() + body, hashlib.sha256).digest()
    ).decode()
    return hmac.compare_digest(expected, signature or "")

@router.post("/square")
async def square_webhook(request: Request, db: Session = Depends(get_db)):
    """
    Handle Square Webhooks for Invoice Payment Failures and Successes
    """
    body_bytes = await request.body()
    if not verify_square_signature(request.headers.get("x-square-hmacsha256-signature"), body_bytes):
        raise HTTPException(status_code=403, detail="Invalid webhook signature")

    try:
        data = json.loads(body_bytes)
        
        # Determine event type
        event_type = data.get("type")
        event_id = data.get("event_id")
        
        logger.info(f"Received Square Webhook: {event_type} - {event_id}")
        
        if event_type == "invoice.scheduled_charge_failed":
            # Square's real name for a failed subscription charge; "invoice.payment_failed"
            # is not in GET /v2/webhooks/event-types and never fired.
            await handle_payment_failed(data, db)
        elif event_type == "invoice.payment_made":
             await handle_payment_success(data, db)
        elif event_type == "subscription.updated":
             await handle_subscription_updated(data, db)
        
        return {"status": "success"}

    except Exception as e:
        logger.error(f"Error processing webhook: {str(e)}")
        # Return 200 to prevent Square from retrying indefinitely in case of logic error, but log it.
        # Check Square docs: usually 2xx is ack.
        return {"status": "error", "message": str(e)}

async def handle_payment_failed(data: Dict[str, Any], db: Session):
    try:
        object_data = data.get("data", {}).get("object", {}).get("invoice", {})
        square_customer_id = object_data.get("primary_recipient", {}).get("customer_id")
        
        if not square_customer_id:
            logger.warning("No customer ID found in payment_failed webhook")
            return

        customer = db.query(Customer).filter(Customer.square_customer_id == square_customer_id).first()
        if not customer:
            logger.warning(f"Customer not found for Square ID: {square_customer_id}")
            return

        # Increment failure count
        current_attempts = customer.failed_payment_attempts or 0
        customer.failed_payment_attempts = current_attempts + 1
        
        logger.info(f"Customer {customer.id} payment failed. Attempts: {customer.failed_payment_attempts}")

        # Suspension Rule: 3 failures
        if customer.failed_payment_attempts >= 3 and customer.subscription_status != "SUSPENDED":
            customer.subscription_status = "SUSPENDED"
            customer.subscription_active = False
            
            # Log suspension
            log = SubscriptionLog(
                customer_id=customer.id,
                subscription_id=customer.square_subscription_id,
                action="SUSPEND",
                effective_date=date.today()
            )
            db.add(log)
            logger.warning(f"Customer {customer.id} SUSPENDED due to payment failures.")

        db.commit()
    except Exception as e:
        logger.error(f"Error in handle_payment_failed: {e}")
        db.rollback()

async def handle_payment_success(data: Dict[str, Any], db: Session):
    try:
        object_data = data.get("data", {}).get("object", {}).get("invoice", {})
        square_customer_id = object_data.get("primary_recipient", {}).get("customer_id")
        
        if not square_customer_id:
             # Try getting it from payment object if invoice structure is different
             # Square structure varies by event.
             # fallback for "payment.created" or similar if needed.
             pass

        if not square_customer_id:
             logger.warning("No customer ID found in payment_made webhook")
             return

        customer = db.query(Customer).filter(Customer.square_customer_id == square_customer_id).first()
        if not customer:
            logger.warning(f"Customer not found for Square ID: {square_customer_id}")
            return

        # Reset failures on success
        if customer.failed_payment_attempts > 0:
            customer.failed_payment_attempts = 0
            logger.info(f"Customer {customer.id} payment success. Reset failures to 0.")

        # Reactivate if suspended
        if customer.subscription_status == "SUSPENDED":
            customer.subscription_status = "ACTIVE"
            customer.subscription_active = True
            
            # Log reactivation
            log = SubscriptionLog(
                customer_id=customer.id,
                subscription_id=customer.square_subscription_id,
                action="REACTIVATE",
                effective_date=date.today()
            )
            db.add(log)
            logger.info(f"Customer {customer.id} REACTIVATED after payment success.")

        db.commit()

        # Record the invoice itself. Nothing else does this automatically: the invoices
        # table was only ever filled by an admin triggering sync for one customer, which
        # is why admin invoice-pdf answers "Invoice not found. Please sync first."
        from utils.invoice_sync import sync_invoices_for_customer
        sync_res = sync_invoices_for_customer(db, customer)
        if not sync_res.get("success"):
            logger.error(f"Invoice sync failed for customer {customer.id}: {sync_res.get('error')}")
    except Exception as e:
        logger.error(f"Error in handle_payment_success: {e}")
        db.rollback()


async def handle_subscription_updated(data: Dict[str, Any], db: Session):
    """Keep local status honest when a subscription changes anywhere other than this
    backend (the Square dashboard, or another brand's tooling)."""
    try:
        sub = data.get("data", {}).get("object", {}).get("subscription", {})
        sub_id, status = sub.get("id"), sub.get("status")
        if not sub_id or not status:
            return

        customer = db.query(Customer).filter(Customer.square_subscription_id == sub_id).first()
        if not customer or customer.subscription_status == status:
            return

        logger.info(f"Customer {customer.id}: subscription {sub_id} is {status} in Square, "
                    f"was {customer.subscription_status}")
        customer.subscription_status = status
        customer.subscription_active = status == "ACTIVE"
        db.add(SubscriptionLog(
            customer_id=customer.id,
            subscription_id=sub_id,
            action=f"SQUARE_{status}",
            effective_date=date.today()
        ))
        db.commit()
    except Exception as e:
        logger.error(f"Error in handle_subscription_updated: {e}")
        db.rollback()
