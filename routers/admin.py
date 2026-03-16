from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import List, Optional
import os
import tempfile
from fpdf import FPDF
from datetime import datetime, date, timedelta
from pydantic import BaseModel

from db.init import get_db
from models.user import Customer, Admin
from models.property import Property
from models.subscription import SubscriptionPlan, SubscriptionLog, Invoice, Payment, PaymentMethod, OneTimeOrder
from utils.deps import get_current_user

# Simple in-memory cache for stats
_stats_cache = {"count": 0, "expires": datetime.min}

router = APIRouter(prefix="", tags=["admin"])

class CustomerListItem(BaseModel):
    id: int
    name: str
    email: str
    phone: str
    plan: str
    planType: str
    status: str
    amount: float
    lastPayment: str
    address: str
    city: str
    zip: str
    propertyCount: int



class PlanDistributionItem(BaseModel):
    name: str
    value: int
    color: str

class GrowthItem(BaseModel):
    date: str
    customers: int
    revenue: float

class AnalyticsResponse(BaseModel):
    mrr: float
    active_subscribers: int
    total_customers: int
    total_revenue: float
    plan_distribution: List[PlanDistributionItem]
    revenue_distribution: List[PlanDistributionItem]
    growth_history: List[GrowthItem]

@router.get("/stats")
def get_admin_stats(
    db: Session = Depends(get_db),
    current_user: Admin = Depends(get_current_user)
):
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Forbidden")
    
    from utils.square_client import get_subscriptions
    subs_res = get_subscriptions(status="ACTIVE")
    active_subs = subs_res.get("subscriptions", [])
    
    return {
        "active_subscribers": len(active_subs)
    }

@router.get("/recent-invoices")
def get_recent_invoices(
    db: Session = Depends(get_db),
    current_user: Admin = Depends(get_current_user)
):
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Forbidden")
    
    from utils.square_client import list_recent_invoices
    res = list_recent_invoices(limit=5)
    if not res.get("success"):
        raise HTTPException(status_code=500, detail=res.get("error"))
    
    sq_invoices = res.get("invoices", [])
    enriched = []
    
    # Pre-fetch customers to avoid N+1
    customer_ids = list(set([inv.get("customer_id") for inv in sq_invoices if inv.get("customer_id")]))
    customers_map = {c.square_customer_id: f"{c.first_name} {c.last_name}" for c in db.query(Customer).filter(Customer.square_customer_id.in_(customer_ids)).all()}

    for inv in sq_invoices:
        i = inv.copy()
        sq_cid = i.get("customer_id")
        i["customer_name"] = customers_map.get(sq_cid, "Unknown Customer")
        
        # Calculate amount
        amount = 0
        if i.get("payment_requests"):
            amount = int(i["payment_requests"][0].get("computed_amount_money", {}).get("amount", 0)) / 100.0
        elif i.get("next_payment_amount_money"):
             amount = int(i["next_payment_amount_money"].get("amount", 0)) / 100.0
        
        i["amount"] = amount
        i["description"] = i.get("title") or i.get("description") or "Subscription Payment"
        enriched.append(i)
        
    return {"success": True, "invoices": enriched}

@router.get("/all-invoices")
def get_all_invoices(
    db: Session = Depends(get_db),
    current_user: Admin = Depends(get_current_user)
):
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Forbidden")
    
    from utils.square_client import list_recent_invoices
    # Fetch 100 recent invoices (Square)
    res = list_recent_invoices(limit=100)
    sq_invoices = res.get("invoices", []) if res.get("success") else []
    
    # Fetch all One-Time Orders (Local)
    one_time_orders = db.query(OneTimeOrder).all()
    
    # Pre-fetch customers
    customers = db.query(Customer).all()
    customers_map = {c.square_customer_id: f"{c.first_name} {c.last_name}" for c in customers if c.square_customer_id}
    local_customers_map = {c.id: f"{c.first_name} {c.last_name}" for c in customers}

    enriched_history = []
    
    # Process Square Invoices
    for inv in sq_invoices:
        i = inv.copy()
        amount = 0
        if "payment_requests" in i and i["payment_requests"]:
            for req in i["payment_requests"]:
                 amount += int(req.get("computed_amount_money", {}).get("amount", 0))
        elif i.get("next_payment_amount_money"):
             amount = int(i["next_payment_amount_money"].get("amount", 0))
        
        enriched_history.append({
            "id": i.get("id"),
            "amount": amount / 100.0,
            "status": i.get("status"),
            "created_at": i.get("invoice_date") or i.get("scheduled_at") or i.get("created_at"),
            "description": i.get("title") or i.get("description") or "Subscription Payment",
            "customer_name": customers_map.get(i.get("customer_id"), "Unknown Customer"),
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
            "customer_name": local_customers_map.get(order.customer_id, "Unknown Customer"),
            "type": "ONE_TIME"
        })
        
    # Sort by date descending
    enriched_history.sort(key=lambda x: x["created_at"], reverse=True)
    
    return {"success": True, "invoices": enriched_history}

@router.get("/analytics", response_model=AnalyticsResponse)
def get_admin_analytics(
    db: Session = Depends(get_db),
    current_user: Admin = Depends(get_current_user)
):
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Forbidden")
    
    from utils.square_client import get_subscriptions, get_subscription_plans
    
    subs_res = get_subscriptions(status="ACTIVE")
    print(f"DEBUG ADMIN ANALYTICS subs_res: {subs_res}")
    
    active_subs = subs_res.get("subscriptions", [])
    active_sub_count = len(active_subs)
    
    plans_res = get_subscription_plans()
    plans = plans_res.get("plans", [])
    
    variation_map = {}
    for p in plans:
        p_name = p.get("name", "Unknown Plan")
        for v in p.get("variations", []):
            var_id = v.get("id")
            phases = v.get("phases", [])
            price = 0.0
            if phases:
                amount_money = phases[0].get("recurring_price_money", {})
                price = float(amount_money.get("amount", 0)) / 100.0
            
            variation_map[var_id] = {"name": p_name, "price": price}

    mrr = 0.0
    plan_counts = {}
    plan_revenue = {}
    
    for sub in active_subs:
        var_id = sub.get("plan_variation_id")
        if var_id and var_id in variation_map:
            details = variation_map[var_id]
            price = details["price"]
            p_name = details["name"]
            
            mrr += price
            plan_counts[p_name] = plan_counts.get(p_name, 0) + 1
            plan_revenue[p_name] = plan_revenue.get(p_name, 0) + price
        else:
            p_name = "Unknown Plan"
            plan_counts[p_name] = plan_counts.get(p_name, 0) + 1
            plan_revenue[p_name] = plan_revenue.get(p_name, 0) + 0.0

    colors = ["#21568F", "#2D6A4F", "#f59e0b", "#ef4444", "#8b5cf6"]
    plan_dist = []
    rev_dist = []
    
    for i, name in enumerate(plan_counts.keys()):
        color = colors[i % len(colors)]
        
        plan_dist.append(PlanDistributionItem(
            name=name,
            value=plan_counts[name],
            color=color
        ))
        
        rev_dist.append(PlanDistributionItem(
            name=name,
            value=int(plan_revenue.get(name, 0)),
            color=color
        ))
        
    total_customers = db.query(Customer).count()
    
    # Calculate total revenue from Payments + OneTimeOrders
    total_payment_revenue = db.query(func.coalesce(func.sum(Payment.amount), 0)).filter(
        Payment.status == "PAID"
    ).scalar() or 0.0
    
    total_onetime_revenue = db.query(func.coalesce(func.sum(OneTimeOrder.total_cost), 0)).filter(
        OneTimeOrder.payment_status == "COMPLETED"
    ).scalar() or 0.0
    
    total_revenue = float(total_payment_revenue) + float(total_onetime_revenue)
    
    thirty_days_ago = datetime.now() - timedelta(days=30)
    
    daily_growth = db.query(
        func.date(Customer.created_at).label('date'),
        func.count(Customer.id)
    ).filter(Customer.created_at >= thirty_days_ago)\
     .group_by(func.date(Customer.created_at))\
     .order_by(func.date(Customer.created_at))\
     .all()
     
    growth_map = {str(d): c for d, c in daily_growth}

    # Fetch daily revenue from Payments table (reliably populated)
    daily_payment_revenue = db.query(
        func.date(Payment.created_at).label('date'),
        func.sum(Payment.amount).label('total')
    ).filter(Payment.created_at >= thirty_days_ago, Payment.status == 'PAID')\
     .group_by(func.date(Payment.created_at))\
     .all()
    
    revenue_map = {str(d): float(t) for d, t in daily_payment_revenue}
    
    # Also add one-time order revenue
    daily_onetime_revenue = db.query(
        func.date(OneTimeOrder.created_at).label('date'),
        func.sum(OneTimeOrder.total_cost).label('total')
    ).filter(OneTimeOrder.created_at >= thirty_days_ago, OneTimeOrder.payment_status == 'COMPLETED')\
     .group_by(func.date(OneTimeOrder.created_at))\
     .all()
    
    for d, t in daily_onetime_revenue:
        d_str = str(d)
        revenue_map[d_str] = revenue_map.get(d_str, 0.0) + float(t)
    
    growth_history = []
    count_before = db.query(Customer).filter(Customer.created_at < thirty_days_ago).count()
    current_total = count_before
    
    for i in range(31):
        d = thirty_days_ago + timedelta(days=i)
        d_str = d.strftime("%Y-%m-%d")
        daily_new = growth_map.get(d_str, 0)
        daily_rev = revenue_map.get(d_str, 0.0)
        current_total += daily_new
        growth_history.append(GrowthItem(date=d_str, customers=current_total, revenue=daily_rev))

    return AnalyticsResponse(
        mrr=mrr,
        active_subscribers=active_sub_count,
        total_customers=total_customers,
        total_revenue=total_revenue,
        plan_distribution=plan_dist,
        revenue_distribution=rev_dist,
        growth_history=growth_history
    )

@router.get("/customers", response_model=List[CustomerListItem])
def list_customers(
    db: Session = Depends(get_db),
    current_user: Admin = Depends(get_current_user)
):
    if current_user.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins can access this resource"
        )
    
    customers = db.query(Customer).all()
    all_plans = {p.id: p for p in db.query(SubscriptionPlan).all()}
    
    last_payments = db.query(
        Payment.customer_id,
        func.max(Payment.created_at)
    ).group_by(Payment.customer_id).all()
    last_payment_map = {cid: dt for cid, dt in last_payments}

    # Pre-fetch which customers have one-time orders
    one_time_customer_ids = set(
        cid for (cid,) in db.query(OneTimeOrder.customer_id).distinct().all() if cid
    )

    result = []
    for c in customers:
        plan_name = "No Plan"
        plan_cost = 0.0
        
        try:
            pid = int(c.plan_id) if c.plan_id else None
            if pid and pid in all_plans:
                plan_name = all_plans[pid].plan_name
                plan_cost = all_plans[pid].plan_cost
        except (ValueError, TypeError):
            pass

        # Determine plan type: Recurring (has active Square subscription) vs One-Time
        has_active_subscription = any(
            p.subscription_active for p in c.properties
        ) or c.subscription_active
        has_one_time = c.id in one_time_customer_ids

        if has_active_subscription:
            plan_type = "Recurring"
        elif has_one_time:
            plan_type = "One-Time"
        elif plan_name != "No Plan":
            plan_type = "Recurring"
        else:
            plan_type = "N/A"

        last_payment_date = last_payment_map.get(c.id)
        last_payment_str = last_payment_date.strftime("%Y-%m-%d") if last_payment_date else "N/A"

        result.append(CustomerListItem(
            id=c.id,
            name=f"{c.first_name} {c.last_name}",
            email=c.email,
            phone=c.phone_number or "",
            plan=plan_name,
            planType=plan_type,
            status="Active" if c.subscription_active else "Inactive",
            amount=plan_cost,
            lastPayment=last_payment_str,
            address=c.address or "",
            city=c.city or "",
            zip=c.zip_code or "",
            propertyCount=len(c.properties),
        ))
    
    return result

@router.post("/cancel-subscription/{customer_id}/{property_id}")
def cancel_customer_subscription(
    customer_id: int,
    property_id: int,
    db: Session = Depends(get_db),
    current_user: Admin = Depends(get_current_user)
):
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Forbidden")
    
    property_obj = db.query(Property).filter(Property.id == property_id, Property.customer_id == customer_id).first()
    if not property_obj or not property_obj.square_subscription_id:
        raise HTTPException(status_code=404, detail="Active subscription not found for this property")
    
    from utils.square_client import cancel_subscription
    res = cancel_subscription(property_obj.square_subscription_id)
    
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=f"Square error: {res.get('error', 'Failed to cancel')}")
    
    property_obj.subscription_active = False
    property_obj.subscription_status = "CANCELED"
    
    # Also update customer legacy fields if this was their last active property
    other_active = db.query(Property).filter(
        Property.customer_id == customer_id,
        Property.id != property_id,
        Property.subscription_active == True
    ).count()
    
    if other_active == 0:
        customer = db.query(Customer).get(customer_id)
        if customer:
            customer.subscription_active = False
            customer.subscription_status = "CANCELED"
    
    # Log action
    log = SubscriptionLog(
        customer_id=customer_id,
        property_id=property_id,
        subscription_id=property_obj.square_subscription_id,
        action="CANCEL",
        effective_date=date.today()
    )
    db.add(log)
    db.commit()
    
    return {"success": True, "message": "Subscription canceled"}

@router.delete("/customer/{customer_id}")
def delete_customer(
    customer_id: int,
    db: Session = Depends(get_db),
    current_user: Admin = Depends(get_current_user)
):
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Forbidden")
    
    customer = db.query(Customer).get(customer_id)
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    
    # 1. Cancel all active Square subscriptions on customer's properties
    properties = db.query(Property).filter(Property.customer_id == customer_id).all()
    for prop in properties:
        if prop.square_subscription_id and prop.subscription_active:
            try:
                from utils.square_client import cancel_subscription
                cancel_subscription(prop.square_subscription_id)
            except Exception:
                pass  # Best effort — don't block deletion if Square fails
    
    # 2. Also cancel the legacy customer-level subscription if present
    if customer.square_subscription_id and customer.subscription_active:
        try:
            from utils.square_client import cancel_subscription
            cancel_subscription(customer.square_subscription_id)
        except Exception:
            pass
    
    # 3. Delete the customer from Square's customer directory
    if customer.square_customer_id:
        try:
            from utils.square_client import delete_square_customer
            delete_square_customer(customer.square_customer_id)
        except Exception:
            pass  # Best effort — don't block deletion if Square fails
    
    # 4. Delete all related records (order matters for FK constraints)
    property_ids = [p.id for p in properties]
    
    if property_ids:
        db.query(SubscriptionLog).filter(SubscriptionLog.property_id.in_(property_ids)).delete(synchronize_session=False)
        db.query(Payment).filter(Payment.property_id.in_(property_ids)).delete(synchronize_session=False)
        db.query(Invoice).filter(Invoice.property_id.in_(property_ids)).delete(synchronize_session=False)
    
    # Delete records linked by customer_id (catches any without a property_id)
    db.query(SubscriptionLog).filter(SubscriptionLog.customer_id == customer_id).delete(synchronize_session=False)
    db.query(Payment).filter(Payment.customer_id == customer_id).delete(synchronize_session=False)
    db.query(Invoice).filter(Invoice.customer_id == customer_id).delete(synchronize_session=False)
    db.query(OneTimeOrder).filter(OneTimeOrder.customer_id == customer_id).delete(synchronize_session=False)
    db.query(PaymentMethod).filter(PaymentMethod.customer_id == customer_id).delete(synchronize_session=False)
    db.query(Property).filter(Property.customer_id == customer_id).delete(synchronize_session=False)
    
    # 5. Delete the customer
    db.delete(customer)
    db.commit()
    
    return {"success": True, "message": f"Customer '{customer.first_name} {customer.last_name}' and all related data deleted successfully"}

@router.delete("/property/{customer_id}/{property_id}")
def delete_customer_property(
    customer_id: int,
    property_id: int,
    db: Session = Depends(get_db),
    current_user: Admin = Depends(get_current_user)
):
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Forbidden")
    
    property_obj = db.query(Property).filter(Property.id == property_id, Property.customer_id == customer_id).first()
    if not property_obj:
        raise HTTPException(status_code=404, detail="Property not found")
    
    # If there's an active Square subscription, try to cancel it but don't block deletion if it fails
    if property_obj.square_subscription_id and property_obj.subscription_active:
        try:
            from utils.square_client import cancel_subscription
            cancel_subscription(property_obj.square_subscription_id)
        except:
            pass
            
    # Cleanup history to satisfy FK constraints
    db.query(SubscriptionLog).filter(SubscriptionLog.property_id == property_id).delete()
    db.query(Payment).filter(Payment.property_id == property_id).delete()
    db.query(Invoice).filter(Invoice.property_id == property_id).delete()
    
    db.delete(property_obj)
    db.commit()
    
    # Also update customer legacy fields if this was their last active property
    other_active = db.query(Property).filter(
        Property.customer_id == customer_id,
        Property.subscription_active == True
    ).count()
    
    if other_active == 0:
        customer = db.query(Customer).get(customer_id)
        if customer:
            customer.subscription_active = False
            customer.subscription_status = "CANCELED"
            db.commit()
    
    return {"success": True, "message": "Property and history removed successfully"}

@router.get("/customer-cards/{customer_id}")
def get_customer_cards(
    customer_id: int,
    db: Session = Depends(get_db),
    current_user: Admin = Depends(get_current_user)
):
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Forbidden")
    
    customer = db.query(Customer).get(customer_id)
    if not customer or not customer.square_customer_id:
        raise HTTPException(status_code=404, detail="Square customer not found")
    
    from utils.square_client import get_customer_cards
    res = get_customer_cards(customer.square_customer_id)
    
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=f"Square error: {res.get('error')}")
    
    return res

@router.post("/remove-card/{customer_id}/{card_id}")
def remove_customer_card(
    customer_id: int,
    card_id: str,
    db: Session = Depends(get_db),
    current_user: Admin = Depends(get_current_user)
):
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Forbidden")
    
    from utils.square_client import disable_card
    res = disable_card(card_id)
    
    if "errors" in res:
        raise HTTPException(status_code=400, detail="Square error")
    
    return {"success": True, "message": "Card removed"}

class SaveCardRequest(BaseModel):
    source_id: str

@router.post("/save-card/{customer_id}")
def admin_save_customer_card(
    customer_id: int,
    request: SaveCardRequest,
    db: Session = Depends(get_db),
    current_user: Admin = Depends(get_current_user)
):
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Forbidden")
    
    customer = db.query(Customer).get(customer_id)
    if not customer or not customer.square_customer_id:
        raise HTTPException(status_code=404, detail="Square customer not found")
    
    from utils.square_client import create_card_on_file
    res = create_card_on_file(request.source_id, customer.square_customer_id)
    
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=f"Square error: {res.get('error')}")
    
    return {"success": True, "message": "Card saved successfully", "card": res.get("card")}

class UpdateCustomerRequest(BaseModel):
    first_name: str
    last_name: str
    email: str
    phone_number: str
    address: str
    city: str
    zip_code: str

@router.put("/customer-details/{customer_id}")
def update_customer_details(
    customer_id: int,
    request: UpdateCustomerRequest,
    db: Session = Depends(get_db),
    current_user: Admin = Depends(get_current_user)
):
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Forbidden")
    
    customer = db.query(Customer).get(customer_id)
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    
    if customer.square_customer_id:
        from utils.square_client import update_square_customer
        sq_res = update_square_customer(
            customer.square_customer_id,
            given_name=request.first_name,
            family_name=request.last_name,
            email_address=request.email,
            phone_number=request.phone_number,
            address={
                "address_line_1": request.address,
                "locality": request.city,
                "postal_code": request.zip_code
            }
        )
        if not sq_res.get("success"):
            raise HTTPException(status_code=400, detail=f"Square sync error: {sq_res.get('error')}")

    customer.first_name = request.first_name
    customer.last_name = request.last_name
    customer.email = request.email
    customer.phone_number = request.phone_number
    customer.address = request.address
    customer.city = request.city
    customer.zip_code = request.zip_code
    
    db.commit()
    return {"success": True, "message": "Customer details updated"}

@router.get("/customer-payments/{customer_id}")
def get_customer_payments(
    customer_id: int,
    db: Session = Depends(get_db),
    current_user: Admin = Depends(get_current_user)
):
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Forbidden")
    
    customer = db.query(Customer).get(customer_id)
    if not customer or not customer.square_customer_id:
        raise HTTPException(status_code=404, detail="Square customer not found")
    
    from utils.square_client import get_customer_invoices
    res = get_customer_invoices(customer.square_customer_id)
    
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=f"Square error: {res.get('error')}")
    
    return res

class ChangeSubscriptionRequest(BaseModel):
    new_plan_variation_id: str

@router.post("/change-subscription/{customer_id}/{property_id}")
def admin_change_subscription(
    customer_id: int,
    property_id: int,
    request: ChangeSubscriptionRequest,
    db: Session = Depends(get_db),
    current_user: Admin = Depends(get_current_user)
):
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Forbidden")
    
    property_obj = db.query(Property).filter(Property.id == property_id, Property.customer_id == customer_id).first()
    if not property_obj:
        raise HTTPException(status_code=404, detail="Property not found")
        
    if not property_obj.square_subscription_id:
        raise HTTPException(status_code=400, detail="Property has no active subscription")
    
    from utils.square_client import update_subscription
    res = update_subscription(property_obj.square_subscription_id, request.new_plan_variation_id)
    
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=f"Square error: {res.get('error')}")
        
    plan = db.query(SubscriptionPlan).filter(SubscriptionPlan.plan_variation_id == request.new_plan_variation_id).first()
    if plan:
        property_obj.plan_id = str(plan.id)
        property_obj.plan_variation_id = request.new_plan_variation_id
        
        # Log action
        log = SubscriptionLog(
            customer_id=customer_id,
            property_id=property_id,
            subscription_id=property_obj.square_subscription_id,
            action="ACTIVATE",
            effective_date=date.today()
        )
        db.add(log)
        db.commit()
    
    return {"success": True, "message": "Subscription updated successfully"}

@router.get("/customer-details/{customer_id}")
def get_customer_details(
    customer_id: int,
    db: Session = Depends(get_db),
    current_user: Admin = Depends(get_current_user)
):
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Forbidden")
        
    customer = db.query(Customer).get(customer_id)
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
        
    properties = db.query(Property).filter(Property.customer_id == customer_id).all()
    
    return {
        "success": True,
        "customer": customer,
        "properties": properties
    }

@router.post("/sync-invoices/{customer_id}")
def sync_customer_invoices(
    customer_id: int,
    db: Session = Depends(get_db),
    current_user: Admin = Depends(get_current_user)
):
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Forbidden")
    
    customer = db.query(Customer).get(customer_id)
    if not customer or not customer.square_customer_id:
        raise HTTPException(status_code=404, detail="Customer not found or no Square ID")
    
    from utils.square_client import get_customer_invoices
    res = get_customer_invoices(customer.square_customer_id)
    
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=f"Square error: {res.get('error')}")
    
    sq_invoices = res.get("invoices", [])
    synced_count = 0
    
    for sq_inv in sq_invoices:
        inv_id = sq_inv.get("id")
        
        amount_data = {}
        if sq_inv.get("payment_requests"):
            amount_data = sq_inv.get("payment_requests")[0].get("computed_amount_money", {})
        if not amount_data.get("amount") and sq_inv.get("next_payment_amount_money"):
             amount_data = sq_inv.get("next_payment_amount_money")
             
        amount = float(amount_data.get("amount", 0)) / 100.0
        
        existing = db.query(Invoice).filter(Invoice.square_invoice_id == inv_id).first()
        
        due_date_str = sq_inv.get("scheduled_at") or sq_inv.get("created_at", datetime.now().isoformat())
        try:
            if "T" in due_date_str:
                due_date = datetime.fromisoformat(due_date_str.replace("Z", "+00:00")).date()
            else:
                due_date = datetime.strptime(due_date_str, "%Y-%m-%d").date()
        except:
            due_date = datetime.now().date()

        if not existing:
            # Find property_id from subscription_id if available
            prop_id = None
            if sq_inv.get("subscription_id"):
                prop = db.query(Property).filter(Property.square_subscription_id == sq_inv.get("subscription_id")).first()
                if prop:
                    prop_id = prop.id
            
            new_inv = Invoice(
                square_invoice_id=inv_id,
                customer_id=customer.id,
                property_id=prop_id,
                subscription_id=sq_inv.get("subscription_id"),
                amount=amount,
                status=sq_inv.get("status"),
                due_date=due_date,
                public_url=sq_inv.get("public_url")
            )
            db.add(new_inv)
            synced_count += 1
        else:
            existing.status = sq_inv.get("status")
            existing.public_url = sq_inv.get("public_url")
            existing.amount = amount
    
    db.commit()
    return {"success": True, "synced": synced_count}

@router.get("/invoice-pdf/{square_invoice_id}")
def download_invoice_pdf(
    square_invoice_id: str,
    db: Session = Depends(get_db),
    current_user: Admin = Depends(get_current_user)
):
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Forbidden")
        
    invoice = db.query(Invoice).filter(Invoice.square_invoice_id == square_invoice_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found. Please sync first.")
        
    customer = db.query(Customer).get(invoice.customer_id)
    
    plan_name = "Subscription Service"
    if customer.plan_id:
        try:
            plan = db.query(SubscriptionPlan).get(int(customer.plan_id))
            if plan:
                plan_name = plan.plan_name
        except:
            pass
            
    from utils.pdf_generator import generate_invoice_pdf
    return generate_invoice_pdf(invoice, customer, plan_name)
