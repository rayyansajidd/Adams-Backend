from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from dotenv import load_dotenv
import os

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    # Use default based on user request if env not set
    DATABASE_URL = "postgresql://postgres:postgres@localhost:5432/adams"

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_recycle=3600,
    connect_args={"connect_timeout": 5} # Prevent silent hangs
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def init_db():
    # Import models here to register them with Base
    from models import user, subscription, property
    try:
        print("Initializing database tables...")
        Base.metadata.create_all(bind=engine)
        print("Database tables initialized successfully.")
    except Exception as e:
        print(f"Note: Database initialization skipped or encountered an error: {e}")
        # We don't want to crash the whole app if tables already exist
    
    seed_db()

def seed_db():
    from models.subscription import SubscriptionPlan
    from models.user import Admin
    from utils.security import hash_password
    db = SessionLocal()
    try:
        # Check if plans exist
        if not db.query(SubscriptionPlan).first():
            plans = [
                SubscriptionPlan(
                    plan_name="Basic Care",
                    plan_cost=149.0,
                    plan_variation_id="basic_plan_id",
                    plan_description="Essential property maintenance"
                ),
                SubscriptionPlan(
                    plan_name="Standard Care",
                    plan_cost=299.0,
                    plan_variation_id="standard_plan_id",
                    plan_description="Comprehensive property maintenance"
                ),
                SubscriptionPlan(
                    plan_name="Premium Care",
                    plan_cost=499.0,
                    plan_variation_id="premium_plan_id",
                    plan_description="Full-service property management"
                )
            ]
            db.add_all(plans)
        
        # Database seeding with admins for Adams
        admin_data = [
            {"name": "Adams Admin", "email": "admin@adamspropertycare.com", "password": "admin123", "phone": "9105235762"},
        ]
        
        for data in admin_data:
            if not db.query(Admin).filter(Admin.email == data["email"]).first():
                new_admin = Admin(
                    name=data["name"],
                    email=data["email"],
                    password_hash=hash_password(data["password"]),
                    phone_number=data["phone"]
                )
                db.add(new_admin)

        # Database seeding with customers for Adams
        from models.user import Customer
        customer_data = [
            {
                "firstName": "Adams",
                "lastName": "Customer",
                "email": "customer@adamspropertycare.com",
                "password": "admin123",
                "phone": "9105235761",
                "address": "123 Maintenance Lane",
                "city": "Adamsville",
                "zip": "12345"
            }
        ]

        for data in customer_data:
            if not db.query(Customer).filter(Customer.email == data["email"]).first():
                new_customer = Customer(
                    first_name=data["firstName"],
                    last_name=data["lastName"],
                    email=data["email"],
                    password_hash=hash_password(data["password"]),
                    phone_number=data["phone"],
                    address=data["address"],
                    city=data["city"],
                    zip_code=data["zip"],
                    plan_id="1",
                    plan_variation_id="basic_plan_id",
                    subscription_active=True,
                    subscription_status="ACTIVE"
                )
                db.add(new_customer)
            
        db.commit()
    finally:
        db.close()
