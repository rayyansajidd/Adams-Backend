from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from db.init import get_db
from models.user import Customer, Admin
from models.subscription import SubscriptionPlan
from utils.security import hash_password, verify_password, create_access_token
from typing import Optional, List
from pydantic import BaseModel, EmailStr

router = APIRouter()

class PropertyRequest(BaseModel):
    address: str
    city: str
    zip: str
    plan: Optional[str] = None
    planVariationId: Optional[str] = None

class SignupRequest(BaseModel):
    firstName: str
    lastName: str
    email: EmailStr
    phone: str
    password: str
    properties: List[PropertyRequest]


class LoginRequest(BaseModel):
    email: EmailStr
    password: str

@router.post("/signup")
def signup(request: SignupRequest, db: Session = Depends(get_db)):
    # Check if user exists
    existing_user = db.query(Customer).filter(Customer.email == request.email).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="Email already registered")
    
    # Use the hash_password utility from security.py
    hashed_password = hash_password(request.password) 
    
    # Use the first property as the primary address for the customer record
    primary_property = request.properties[0] if request.properties else None
    
    new_user = Customer(
        first_name=request.firstName,
        last_name=request.lastName,
        email=request.email,
        phone_number=request.phone,
        password_hash=hashed_password,
        address=primary_property.address if primary_property else "",
        city=primary_property.city if primary_property else "",
        zip_code=primary_property.zip if primary_property else "",
        # These fields on Customer are legacy/primary, we'll set them based on first property
        plan_id=primary_property.plan if primary_property else None,
        plan_variation_id=primary_property.planVariationId if primary_property else None,

        subscription_active=False,
        subscription_status="PENDING"
    )
    
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    # Create Property records
    created_properties = []
    for i, prop_data in enumerate(request.properties):
        nickname = "Primary Property" if i == 0 else f"Property {i+1}"
        
        new_property = Property(
            customer_id=new_user.id,
            nickname=nickname,
            address=prop_data.address,
            city=prop_data.city,
            state="", # Frontend doesn't send state yet, optional
            zip_code=prop_data.zip,
            plan_id=prop_data.plan,
            plan_variation_id=prop_data.planVariationId,
            subscription_active=False,
            subscription_status="PENDING"
        )
        db.add(new_property)
        db.commit()
        db.refresh(new_property)
        created_properties.append(new_property)
    
    # Create simple access token
    access_token = f"token_{new_user.id}"
    
    # Get plan details safely for the primary property (for backward compat in response)
    plan_obj = None
    if new_user.plan_id and str(new_user.plan_id).isdigit():
        plan_obj = db.query(SubscriptionPlan).filter(SubscriptionPlan.id == int(new_user.plan_id)).first()

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user_id": new_user.id,
        "user": {
            "id": new_user.id,
            "email": new_user.email,
            "firstName": new_user.first_name,
            "lastName": new_user.last_name,
            "role": "customer",
            "plan_id": new_user.plan_id,
            "plan_name": plan_obj.plan_name if plan_obj else "Active Plan",
            "plan_cost": plan_obj.plan_cost if plan_obj else 0,
            "subscription_status": new_user.subscription_status
        },
        "properties": [
            {
                "id": p.id,
                "address": p.address,
                "plan_variation_id": p.plan_variation_id
            } for p in created_properties
        ]
    }

@router.post("/login")
def login(request: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(Customer).filter(Customer.email == request.email).first()
    if not user or not verify_password(request.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    
    access_token = create_access_token(data={"sub": user.email, "id": user.id})
    
    plan_obj = None
    if user.plan_id and str(user.plan_id).isdigit():
        plan_obj = db.query(SubscriptionPlan).filter(SubscriptionPlan.id == int(user.plan_id)).first()

    return {"access_token": access_token, "token_type": "bearer", "user": {
        "id": user.id,
        "email": user.email,
        "firstName": user.first_name,
        "lastName": user.last_name,
        "role": "customer",
        "plan_id": user.plan_id,
        "plan_name": plan_obj.plan_name if plan_obj else "Active Plan",
        "plan_cost": plan_obj.plan_cost if plan_obj else 0,
        "subscription_status": user.subscription_status
    }}

@router.post("/admin/login")
def admin_login(request: LoginRequest, db: Session = Depends(get_db)):
    import logging
    logger = logging.getLogger(__name__)
    
    logger.info(f"Admin login attempt for email: {request.email}")
    
    admin = db.query(Admin).filter(Admin.email == request.email).first()
    
    if not admin:
        logger.warning(f"Admin not found for email: {request.email}")
        raise HTTPException(status_code=401, detail="Invalid email or password")
    
    logger.info(f"Admin found: {admin.email}, verifying password...")
    password_valid = verify_password(request.password, admin.password_hash)
    
    if not password_valid:
        logger.warning(f"Password verification failed for email: {request.email}")
        raise HTTPException(status_code=401, detail="Invalid email or password")
    
    logger.info(f"Login successful for admin: {admin.email}")
    access_token = create_access_token(data={"sub": admin.email, "id": admin.id, "role": "admin"})
    return {"access_token": access_token, "token_type": "bearer", "user": {
        "id": admin.id,
        "email": admin.email,
        "name": admin.name,
        "role": "admin"
    }}
