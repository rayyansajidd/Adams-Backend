import sys
import os

# Add the project root to the python path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from db.init import SessionLocal
from models.user import Customer
from models.property import Property
from models.subscription import Payment, SubscriptionLog, Invoice

def migrate():
    db = SessionLocal()
    try:
        customers = db.query(Customer).all()
        print(f"Found {len(customers)} customers to migrate.")
        
        for customer in customers:
            # Check if property already exists for this customer (to avoid double migration)
            existing_property = db.query(Property).filter(Property.customer_id == customer.id).first()
            if existing_property:
                print(f"Customer {customer.email} already has a property. Skipping.")
                continue
            
            # Create a Property from Customer's address/subscription info
            new_property = Property(
                customer_id=customer.id,
                nickname="Primary Property",
                address=customer.address,
                city=customer.city,
                state=customer.state,
                zip_code=customer.zip_code,
                square_subscription_id=customer.square_subscription_id,
                subscription_active=customer.subscription_active,
                subscription_status=customer.subscription_status,
                plan_id=customer.plan_id,
                plan_variation_id=customer.plan_variation_id
            )
            db.add(new_property)
            db.flush() # Get the new_property.id
            
            # Update history tables to point to this property
            db.query(Payment).filter(Payment.customer_id == customer.id).update({"property_id": new_property.id})
            db.query(SubscriptionLog).filter(SubscriptionLog.customer_id == customer.id).update({"property_id": new_property.id})
            db.query(Invoice).filter(Invoice.customer_id == customer.id).update({"property_id": new_property.id})
            
            print(f"Migrated customer {customer.email} to property ID {new_property.id}")
        
        db.commit()
        print("Migration completed successfully.")
    except Exception as e:
        print(f"Error during migration: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    migrate()
