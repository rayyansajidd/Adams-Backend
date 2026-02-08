from fastapi import APIRouter, Depends, HTTPException, status, Header
from sqlalchemy.orm import Session
from typing import Optional, List, Dict, Any
from db.init import get_db
from models.user import Customer
from models.property import Property
from models.subscription import SubscriptionPlan, Payment, PaymentMethod, SubscriptionLog, OneTimeOrder
from utils.deps import get_current_user, get_db_user
from utils.square_client import (
    get_subscription_plans,
    create_square_customer,
    create_card_on_file,
    get_customer_cards,
    disable_card,
    create_subscription,
    get_subscriptions,
    cancel_subscription,
    update_subscription,
    update_subscription_card,
    pause_subscription,
    resume_subscription,
    get_customer_invoices,
    process_payment
)
from pydantic import BaseModel
import os
import uuid
from datetime import date, datetime
import logging

logger = logging.getLogger(__name__)

class MockInvoice:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)

router = APIRouter()

# --- Pydantic Models ---

class ValidateCardRequest(BaseModel):
    source_id: str
    customer_id: Optional[int] = None # Local DB ID
    # If customer_id is not provided, we might need these to create one
    given_name: Optional[str] = None
    family_name: Optional[str] = None
    email: Optional[str] = None
    phone_number: Optional[str] = None

class ActivateSubscriptionRequest(BaseModel):
    plan_variation_id: str
    customer_id: Optional[int] = None # Local DB ID
    property_id: Optional[int] = None # Local DB ID
    card_id: str
    location_id: Optional[str] = None
    idempotency_key: Optional[str] = None
    start_date: Optional[str] = None

class CreatePropertyRequest(BaseModel):
    nickname: str
    address: str
    city: str
    state: str
    zip_code: str
    plan_variation_id: str

class ChangePlanRequest(BaseModel):
    new_plan_variation_id: str

class SaveCardRequest(BaseModel):
    source_id: str

class OneTimePaymentRequest(BaseModel):
    source_id: str
    customer_info: Dict[str, Any]
    plan_details: Dict[str, Any]
    total_amount: float # Total in dollars
    customer_id: Optional[int] = None
    property_id: Optional[int] = None
    location_id: Optional[str] = None
    idempotency_key: Optional[str] = None

# --- Endpoints ---

@router.get("/square-config")
def get_square_config():
    return {
        "application_id": os.getenv("SQUARE_APPLICATION_ID", ""),
        "location_id": os.getenv("SQUARE_LOCATION_ID", "")
    }

@router.post("/one-time-payment")
def one_time_payment(request: OneTimePaymentRequest, db: Session = Depends(get_db)):
    """Process a one-time payment and record the order."""
    from models.subscription import OneTimeOrder, Payment
    
    # 1. Create/Get Square Customer
    sq_customer_id = None
    customer_info = request.customer_info
    customer_id = request.customer_id
    
    # Check if a customer with this email already exists OR use request.customer_id
    from models.user import Customer
    existing_user = None
    if customer_id:
        existing_user = db.query(Customer).get(customer_id)
    
    if not existing_user:
        existing_user = db.query(Customer).filter(Customer.email == customer_info.get("email")).first()
    
    if existing_user:
        sq_customer_id = existing_user.square_customer_id
        customer_id = existing_user.id
    
    if not sq_customer_id:
        # Generate basic name if split fails
        full_name = customer_info.get("name", "One Time Customer")
        name_parts = full_name.split(" ", 1)
        given_name = name_parts[0]
        family_name = name_parts[1] if len(name_parts) > 1 else ""
        
        sq_res = create_square_customer(
            given_name=given_name,
            family_name=family_name,
            email=customer_info.get("email")
        )
        if sq_res.get("success"):
            sq_customer_id = sq_res.get("customer_id")
            
            if existing_user:
                existing_user.square_customer_id = sq_customer_id
                db.commit()

    # 2. Process Payment in Square
    idempotency_key = request.idempotency_key or f"otp-{uuid.uuid4().hex}"
    
    logger.info(f"Attempting one-time payment: Amount=${request.total_amount}, Source={request.source_id[:15]}...")
    
    # payment_res = process_payment(
    #     source_id=request.source_id,
    #     amount=request.total_amount,
    #     idempotency_key=idempotency_key,
    #     location_id=request.location_id
    # )
    
    # if "errors" in payment_res:
    #     error_msg = payment_res['errors'][0].get('detail', 'Unknown error')
    #     logger.error(f"Square payment fail. Full Response: {payment_res}")
    #     raise HTTPException(status_code=400, detail=f"Payment failed: {error_msg}")

    # payment_data = payment_res.get("payment", {})
    # square_payment_id = payment_data.get("id")

    # MOCK PAYMENT FOR TESTING
    square_payment_id = f"mock_payment_{uuid.uuid4()}"
    payment_data = {"id": square_payment_id, "amount_money": {"amount": int(request.total_amount * 100), "currency": "USD"}, "status": "COMPLETED"}
    
    # 3. Create OneTimeOrder record
    new_order = OneTimeOrder(
        customer_id=existing_user.id if existing_user else None,
        property_id=request.property_id,
        plan_name=request.plan_details.get("name"),
        plan_cost=request.plan_details.get("price"),
        custom_description=customer_info.get("custom_description"),
        total_cost=request.total_amount,
        square_payment_id=square_payment_id,
        payment_status="COMPLETED"
    )
    db.add(new_order)
    
    # 4. Also record in the global Payments table for unified billing
    new_payment = Payment(
        customer_id=existing_user.id if existing_user else None,
        property_id=request.property_id,
        amount=request.total_amount,
        status="PAID",
        square_transaction_id=square_payment_id
    )
    db.add(new_payment)
    
    db.commit()
    db.refresh(new_order)
    
    return {
        "success": True,
        "order_id": new_order.id,
        "payment": payment_data
    }

@router.get("/subscription-plans")
def get_square_plans():
    """Fetch all subscription plans directly from Square Catalog."""
    result = get_subscription_plans()
    if not result.get("success"):
        raise HTTPException(status_code=500, detail=result.get("error"))
    return result

@router.get("/subscription-plans/db")
def get_db_plans(db: Session = Depends(get_db)):
    """Fetch all subscription plans from local database."""
    plans = db.query(SubscriptionPlan).all()
    return {"success": True, "plans": plans}

@router.post("/validate-card")
def validate_card(request: ValidateCardRequest, db: Session = Depends(get_db)):
    """
    1. Create/Get Square Customer.
    2. Attach Card to Square Customer.
    3. Return card_id and customer info.
    """
    customer = None
    if request.customer_id:
        customer = db.query(Customer).get(request.customer_id)
    
    sq_customer_id = customer.square_customer_id if customer else None
    
    if not sq_customer_id:
        # Create Square Customer
        given_name = request.given_name or (customer.first_name if customer else "Guest")
        family_name = request.family_name or (customer.last_name if customer else "User")
        email = request.email or (customer.email if customer else f"guest_{uuid.uuid4().hex[:8]}@example.com")
        
        res = create_square_customer(
            given_name=given_name,
            family_name=family_name,
            email=email,
            phone_number=request.phone_number or (customer.phone_number if customer else None)
        )
        
        if not res.get("success"):
            raise HTTPException(status_code=400, detail=f"Square customer creation failed: {res.get('error')}")
        sq_customer_id = res.get("customer_id")
        
        if customer:
            customer.square_customer_id = sq_customer_id
            db.commit()

    # Attach Card
    card_res = create_card_on_file(
        source_id=request.source_id,
        customer_id=sq_customer_id
    )
    
    if not card_res.get("success"):
        raise HTTPException(status_code=400, detail=f"Card validation failed: {card_res.get('error')}")

    # Save Payment Method to DB if customer exists
    if customer:
        new_method = PaymentMethod(
            customer_id=customer.id,
            square_card_id=card_res.get("card_id"),
            last_4_digits=card_res.get("last_4"),
            card_brand=card_res.get("brand"),
            exp_month=card_res.get("exp_month"),
            exp_year=card_res.get("exp_year"),
            is_default=True
        )
        # Set others to not default
        db.query(PaymentMethod).filter(PaymentMethod.customer_id == customer.id).update({"is_default": False})
        db.add(new_method)
        db.commit()

    return {
        "success": True,
        "card_id": card_res.get("card_id"),
        "customer_id": sq_customer_id,
        "card_details": card_res
    }

@router.get("/my-cards")
def get_my_cards(user: Customer = Depends(get_db_user), db: Session = Depends(get_db)):
    """Fetch saved payment methods for the authenticated customer."""
    if not user.square_customer_id:
        return {"success": True, "cards": []}
    
    # 1. Fetch from local DB
    db_methods = db.query(PaymentMethod).filter(PaymentMethod.customer_id == user.id).all()
    db_card_map = {pm.square_card_id: pm for pm in db_methods}
    
    # 2. Fetch from Square to ensure sync
    sq_res = get_customer_cards(user.square_customer_id)
    sq_cards = sq_res.get("cards", []) if sq_res.get("success") else []
    
    # 3. Merge: Start with Square cards and enrich with DB info if available
    final_cards = []
    sq_card_ids_in_list = set()
    
    for sq_c in sq_cards:
        card_id = sq_c.get("id")
        sq_card_ids_in_list.add(card_id)
        
        db_pm = db_card_map.get(card_id)
        
        final_cards.append({
            "id": card_id,
            "last_4": sq_c.get("last_4") or (db_pm.last_4_digits if db_pm else ""),
            "brand": sq_c.get("card_brand") or (db_pm.card_brand if db_pm else "Unknown"),
            "exp_month": sq_c.get("exp_month") or (db_pm.exp_month if db_pm else 0),
            "exp_year": sq_c.get("exp_year") or (db_pm.exp_year if db_pm else 0),
            "is_default": db_pm.is_default if db_pm else False,
            "is_active_in_square": True
        })
    
    # Also add any cards from DB that might not have been in Square response (though unlikely if sq sync is on)
    for card_id, pm in db_card_map.items():
        if card_id not in sq_card_ids_in_list:
            final_cards.append({
                "id": pm.square_card_id,
                "last_4": pm.last_4_digits,
                "brand": pm.card_brand,
                "exp_month": pm.exp_month,
                "exp_year": pm.exp_year,
                "is_default": pm.is_default,
                "is_active_in_square": False
            })
    
    return {
        "success": True,
        "cards": final_cards
    }

@router.get("/properties")
def get_properties(user: Customer = Depends(get_db_user), db: Session = Depends(get_db)):
    """Fetch all properties for the authenticated customer."""
    properties = db.query(Property).filter(Property.customer_id == user.id).all()
    return {"success": True, "properties": properties}

@router.post("/create-property")
def create_property(request: CreatePropertyRequest, user: Customer = Depends(get_db_user), db: Session = Depends(get_db)):
    """Create a new property for the customer (mini-signup flow)."""
    new_property = Property(
        customer_id=user.id,
        nickname=request.nickname,
        address=request.address,
        city=request.city,
        state=request.state,
        zip_code=request.zip_code,
        plan_variation_id=request.plan_variation_id
    )
    db.add(new_property)
    db.commit()
    db.refresh(new_property)
    return {"success": True, "property": new_property}

@router.post("/save-card")
def save_card(request: SaveCardRequest, user: Customer = Depends(get_db_user), db: Session = Depends(get_db)):
    """
    Save a new payment method for the logged-in customer.
    If they have an active subscription, update it to use this new card.
    """
    if not user.square_customer_id:
        # Should ideally have one by now if they reached dashboard, but let's be safe
        res = create_square_customer(
            given_name=user.first_name,
            family_name=user.last_name,
            email=user.email,
            phone_number=user.phone_number
        )
        if not res.get("success"):
            raise HTTPException(status_code=400, detail=f"Failed to create Square customer: {res.get('error')}")
        user.square_customer_id = res.get("customer_id")
        db.commit()

    # 1. Create Card in Square
    card_res = create_card_on_file(
        source_id=request.source_id,
        customer_id=user.square_customer_id
    )
    
    if not card_res.get("success"):
        raise HTTPException(status_code=400, detail=f"Failed to save card: {card_res.get('error')}")
        
    card_id = card_res.get("card_id")
    
    # 2. Save to Local DB
    # Disable previous default
    db.query(PaymentMethod).filter(PaymentMethod.customer_id == user.id).update({"is_default": False})
    
    new_method = PaymentMethod(
        customer_id=user.id,
        square_card_id=card_id,
        last_4_digits=card_res.get("last_4"),
        card_brand=card_res.get("brand"),
        exp_month=card_res.get("exp_month"),
        exp_year=card_res.get("exp_year"),
        is_default=True
    )
    db.add(new_method)
    
    # 3. Update active subscription if exists
    if user.square_subscription_id and user.subscription_active:
        logger.info(f"Updating subscription {user.square_subscription_id} to use new card {card_id}")
        update_subscription_card(user.square_subscription_id, card_id)
    
    db.commit()
    
    return {
        "success": True,
        "message": "Payment method saved successfully",
        "card_id": card_id
    }

@router.delete("/remove-card/{card_id}")
def remove_card(card_id: str, user: Customer = Depends(get_db_user), db: Session = Depends(get_db)):
    """Disable a card in Square and remove from local DB."""
    # 1. Disable in Square
    disable_card(card_id)
    
    # 2. Remove from Local DB (or mark as inactive)
    method = db.query(PaymentMethod).filter(
        PaymentMethod.customer_id == user.id,
        PaymentMethod.square_card_id == card_id
    ).first()
    
    if method:
        db.delete(method)
        db.commit()
        
    return {"success": True, "message": "Card removed successfully"}

def dummy_create_subscription(customer_id: str, location_id: str, plan_variation_id: str, card_id: str, **kwargs) -> Dict[str, Any]:
    """Helper for testing to skip real Square call, matching Skeeter project logic."""
    return {
        "success": True,
        "subscription_id": f"dummy_sub_{uuid.uuid4().hex[:12]}",
        "subscription": {"status": "ACTIVE", "id": f"dummy_sub_{uuid.uuid4().hex[:12]}"}
    }

@router.post("/activate-subscription")
def activate_sub(request: ActivateSubscriptionRequest, db: Session = Depends(get_db), auth_header: Optional[str] = Header(None)):
    customer = None
    if request.customer_id:
        customer = db.query(Customer).get(request.customer_id)
    
    # Fallback to token if customer_id not provided (e.g. from Dashboard)
    if not customer and auth_header:
        try:
            from jose import jwt
            from utils.deps import SECRET_KEY, ALGORITHM
            token = auth_header.replace("Bearer ", "")
            payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
            user_id = payload.get("id")
            if user_id:
                customer = db.query(Customer).get(user_id)
        except Exception as e:
            logger.error(f"Failed to identify customer from token: {e}")

    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    sq_customer_id = customer.square_customer_id
    if not sq_customer_id:
        raise HTTPException(status_code=400, detail="Square customer ID missing")

    # Find the property
    property_obj = None
    if request.property_id:
        property_obj = db.query(Property).filter(Property.id == request.property_id, Property.customer_id == customer.id).first()
    else:
        # Fallback to the first property if none specified (Legacy or initial signup)
        property_obj = db.query(Property).filter(Property.customer_id == customer.id).first()

    if not property_obj:
         # If no property found, create one from customer legacy details
         property_obj = Property(
            customer_id=customer.id,
            nickname="Primary Property",
            address=customer.address,
            city=customer.city,
            state=customer.state,
            zip_code=customer.zip_code
         )
         db.add(property_obj)
         db.flush()

    location_id = request.location_id or os.getenv("SQUARE_LOCATION_ID")
    
    # Create subscription
    res = dummy_create_subscription(
        customer_id=sq_customer_id,
        location_id=location_id,
        plan_variation_id=request.plan_variation_id,
        card_id=request.card_id,
        idempotency_key=request.idempotency_key
    )
    
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=f"Subscription failed: {res.get('error')}")

    subscription_id = res.get("subscription_id")
    
    # Update Property
    property_obj.square_subscription_id = subscription_id
    property_obj.subscription_active = True
    property_obj.subscription_status = "ACTIVE"
    property_obj.plan_variation_id = request.plan_variation_id
    
    # Update Customer Legacy Fields for backward compatibility (optional but safer for now)
    customer.square_subscription_id = subscription_id
    customer.subscription_active = True
    customer.subscription_status = "ACTIVE"
    customer.plan_variation_id = request.plan_variation_id
    
    # Log payment locally
    plan = db.query(SubscriptionPlan).filter(SubscriptionPlan.plan_variation_id == request.plan_variation_id).first()
    if plan:
        new_payment = Payment(
            customer_id=customer.id,
            property_id=property_obj.id,
            amount=plan.plan_cost,
            status="PAID",
            square_transaction_id=subscription_id
        )
        db.add(new_payment)

    # Log action
    log = SubscriptionLog(
        customer_id=customer.id,
        property_id=property_obj.id,
        subscription_id=subscription_id,
        action="ACTIVATE",
        effective_date=date.today()
    )
    db.add(log)
    db.commit()

    return res

@router.get("/my-subscriptions")
def get_my_subs(user: Customer = Depends(get_db_user)):
    if not user.square_customer_id:
        return {"success": True, "subscriptions": []}
    
    # Fetch user's subscriptions
    subs_res = get_subscriptions(customer_id=user.square_customer_id)
    if not subs_res.get("success"):
        return subs_res
        
    subscriptions = subs_res.get("subscriptions", [])
    
    # Fetch all plans to map names and amounts
    plans_res = get_subscription_plans()
    plans_map = {}
    if plans_res.get("success"):
        for p in plans_res.get("plans", []):
            for v in p.get("variations", []):
                # Try to get price from the first phase
                price = 0
                if v.get("phases") and len(v["phases"]) > 0:
                    price = int(v["phases"][0].get("recurring_price_money", {}).get("amount", 0))
                
                plans_map[v["id"]] = {
                    "name": f"{p['name']} - {v['name']}",
                    "amount": price
                }
    
    # Enrich subscriptions
    enriched_subs = []
    for sub in subscriptions:
        # Create a copy to modify
        s = sub.copy()
        var_id = s.get("plan_variation_id")
        
        # Map fields
        if var_id in plans_map:
            s["plan_name"] = plans_map[var_id]["name"]
            s["amount"] = plans_map[var_id]["amount"]
        else:
            s["plan_name"] = "Unknown Plan"
            s["amount"] = 0
            
        # Map next_billing_date from charged_through_date
        s["next_billing_date"] = s.get("charged_through_date")
        
        enriched_subs.append(s)
        
    return {"success": True, "subscriptions": enriched_subs}

@router.post("/pause-subscription/{property_id}")
def pause_sub(property_id: int, user: Customer = Depends(get_db_user), db: Session = Depends(get_db)):
    property_obj = db.query(Property).filter(Property.id == property_id, Property.customer_id == user.id).first()
    if not property_obj or not property_obj.square_subscription_id:
        raise HTTPException(status_code=404, detail="Active subscription not found for this property")
    
    res = pause_subscription(property_obj.square_subscription_id)
    if "errors" in res:
        raise HTTPException(status_code=400, detail=str(res["errors"]))
    
    property_obj.subscription_status = "PAUSED"
    log = SubscriptionLog(
        customer_id=user.id,
        property_id=property_obj.id,
        subscription_id=property_obj.square_subscription_id,
        action="PAUSE",
        effective_date=date.today()
    )
    db.add(log)
    db.commit()
    return res

@router.post("/resume-subscription/{property_id}")
def resume_sub(property_id: int, user: Customer = Depends(get_db_user), db: Session = Depends(get_db)):
    property_obj = db.query(Property).filter(Property.id == property_id, Property.customer_id == user.id).first()
    if not property_obj or not property_obj.square_subscription_id:
        raise HTTPException(status_code=404, detail="No subscription found to resume")
    
    res = resume_subscription(property_obj.square_subscription_id)
    if "errors" in res:
        raise HTTPException(status_code=400, detail=str(res["errors"]))
    
    property_obj.subscription_status = "ACTIVE"
    log = SubscriptionLog(
        customer_id=user.id,
        property_id=property_obj.id,
        subscription_id=property_obj.square_subscription_id,
        action="RESUME",
        effective_date=date.today()
    )
    db.add(log)
    db.commit()
    return res

@router.post("/cancel-subscription/{property_id}")
def cancel_sub(property_id: int, user: Customer = Depends(get_db_user), db: Session = Depends(get_db)):
    property_obj = db.query(Property).filter(Property.id == property_id, Property.customer_id == user.id).first()
    if not property_obj or not property_obj.square_subscription_id:
        raise HTTPException(status_code=404, detail="No subscription found to cancel")
    
    res = cancel_subscription(property_obj.square_subscription_id)
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=res.get("error"))
    
    property_obj.subscription_active = False
    property_obj.subscription_status = "CANCELED"
    log = SubscriptionLog(
        customer_id=user.id,
        property_id=property_obj.id,
        subscription_id=property_obj.square_subscription_id,
        action="CANCEL",
        effective_date=date.today()
    )
    db.add(log)
    db.commit()
    return res

@router.post("/change-plan/{property_id}")
def change_plan(property_id: int, request: ChangePlanRequest, user: Customer = Depends(get_db_user), db: Session = Depends(get_db)):
    property_obj = db.query(Property).filter(Property.id == property_id, Property.customer_id == user.id).first()
    if not property_obj or not property_obj.square_subscription_id:
        raise HTTPException(status_code=404, detail="No active subscription found for this property")
    
    res = update_subscription(property_obj.square_subscription_id, request.new_plan_variation_id)
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=res.get("error"))
    
    # Update local state
    property_obj.plan_variation_id = request.new_plan_variation_id
    db.commit()
    
    return res

@router.get("/billing-history")
def billing_history(user: Customer = Depends(get_db_user), db: Session = Depends(get_db)):
    if not user.square_customer_id:
        # Even if no square customer, check for local one-time orders (though unlikely)
        one_time_orders = db.query(OneTimeOrder).filter(OneTimeOrder.customer_id == user.id).all()
        history = []
        for order in one_time_orders:
             history.append({
                "id": f"OTP-{order.id}",
                "amount": order.total_cost,
                "status": order.payment_status or "PAID",
                "created_at": order.created_at.isoformat(),
                "description": f"Custom Service: {order.custom_description or order.plan_name}",
                "type": "ONE_TIME"
            })
        return {"success": True, "invoices": history}
    
    # 1. Fetch Square Invoices
    res = get_customer_invoices(user.square_customer_id)
    invoices = res.get("invoices", []) if res.get("success") else []
    
    # 2. Fetch Local One-Time Orders
    one_time_orders = db.query(OneTimeOrder).filter(OneTimeOrder.customer_id == user.id).all()
    
    enriched_history = []
    
    # Process Square Invoices
    for inv in invoices:
        i = inv.copy()
        amount = 0
        if "payment_requests" in i and i["payment_requests"]:
            for req in i["payment_requests"]:
                 amount += int(req.get("computed_amount_money", {}).get("amount", 0))
        
        enriched_history.append({
            "id": i.get("id"),
            "amount": amount / 100.0,
            "status": i.get("status"),
            "created_at": i.get("invoice_date") or i.get("scheduled_at") or i.get("created_at"),
            "description": i.get("title") or i.get("description") or "Subscription Payment",
            "type": "SUBSCRIPTION",
            "public_url": i.get("public_url")
        })
        
    # Process One-Time Orders
    for order in one_time_orders:
        enriched_history.append({
            "id": f"OTP-{order.id}",
            "amount": order.total_cost,
            "status": order.payment_status or "PAID",
            "created_at": order.created_at.isoformat(),
            "description": f"Custom Service: {order.custom_description or order.plan_name}",
            "type": "ONE_TIME"
        })
        
    # Sort by date descending
    enriched_history.sort(key=lambda x: x["created_at"], reverse=True)
    
    return {"success": True, "invoices": enriched_history}

@router.get("/my-invoice-pdf/{square_invoice_id}")
def download_my_invoice_pdf(
    square_invoice_id: str,
    db: Session = Depends(get_db),
    user: Customer = Depends(get_db_user)
):
    from models.subscription import Invoice, SubscriptionPlan
    from utils.pdf_generator import generate_invoice_pdf
    
    # Check local DB first
    invoice = db.query(Invoice).filter(Invoice.square_invoice_id == square_invoice_id).first()
    
    if not invoice:
        # If not in local DB, fetch from Square to support live testing for existing customers
        res = get_customer_invoices(user.square_customer_id)
        if not res.get("success"):
            raise HTTPException(status_code=404, detail="Invoice not found")
        
        sq_inv = next((inv for inv in res.get("invoices", []) if inv.get("id") == square_invoice_id), None)
        if not sq_inv:
            raise HTTPException(status_code=404, detail="Invoice not found in Square")
        
        # Calculate amount from payment requests
        amount = 0
        if sq_inv.get("payment_requests"):
            amount = int(sq_inv.get("payment_requests")[0].get("computed_amount_money", {}).get("amount", 0)) / 100.0
        
        # Parse dates safely
        created_at_str = sq_inv.get("created_at")
        due_date_str = sq_inv.get("scheduled_at") or created_at_str
        
        try:
            created_at = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
            due_date = datetime.fromisoformat(due_date_str.replace("Z", "+00:00")).date()
        except:
            created_at = datetime.now()
            due_date = created_at.date()

        # Create mock object for the PDF generator
        invoice = MockInvoice(
            square_invoice_id=sq_inv.get("id"),
            amount=amount,
            status=sq_inv.get("status"),
            created_at=created_at,
            due_date=due_date
        )

    # Validate ownership
    if getattr(invoice, 'customer_id', user.id) != user.id:
        raise HTTPException(status_code=403, detail="Access denied")

    # Get plan name
    plan_name = "Subscription Service"
    if user.plan_id:
        try:
            # Plan ID might be stored as string or int, handle carefully
            plan_id_int = int(user.plan_id) if str(user.plan_id).isdigit() else 0
            plan = db.query(SubscriptionPlan).filter(SubscriptionPlan.id == plan_id_int).first()
            if plan:
                plan_name = plan.plan_name
        except:
            pass
            
    return generate_invoice_pdf(invoice, user, plan_name)
