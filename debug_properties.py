from db.init import SessionLocal
from models.user import Customer
from models.property import Property

def debug_user_properties(email):
    db = SessionLocal()
    try:
        user = db.query(Customer).filter(Customer.email == email).first()
        if not user:
            print(f"User with email {email} NOT FOUND")
            return
        
        print(f"User found: ID={user.id}, Name={user.first_name} {user.last_name}, Email={user.email}")
        
        properties = db.query(Property).filter(Property.customer_id == user.id).all()
        print(f"Found {len(properties)} properties for this user:")
        for p in properties:
            print(f" - ID={p.id}, Nickname={p.nickname}, Address={p.address}")
            
        all_properties = db.query(Property).all()
        print(f"\nTotal properties in DB: {len(all_properties)}")
        for p in all_properties:
            print(f" - P_ID={p.id}, Cust_ID={p.customer_id}, Address={p.address}")
            
    finally:
        db.close()

if __name__ == "__main__":
    debug_user_properties("tariq@gmail.com")
