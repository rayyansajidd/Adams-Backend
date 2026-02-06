from db.init import SessionLocal
from models.user import Customer
from models.property import Property

from utils.square_client import get_customer_invoices

def check():
    db = SessionLocal()
    try:
        customers = db.query(Customer).all()
        for c in customers:
            print(f"\nChecking Customer: {c.first_name} {c.last_name} ({c.square_customer_id})")
            if c.square_customer_id:
                res = get_customer_invoices(c.square_customer_id)
                if res.get("success"):
                    invs = res.get("invoices", [])
                    print(f"  Found {len(invs)} invoices.")
                    for i in invs:
                        print(f"    - ID: {i.get('id')}, Status: {i.get('status')}, Amount: {i.get('next_payment_amount_money', {}).get('amount')}")
                else:
                    print(f"  Error fetching: {res.get('error')}")
            else:
                print("  No Square ID.")
    finally:
        db.close()

if __name__ == "__main__":
    check()
