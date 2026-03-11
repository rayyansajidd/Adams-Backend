from db.init import SessionLocal
from models.property import Property
from models.user import Customer
from models.subscription import PaymentMethod

def test_cards():
    db = SessionLocal()
    customers = db.query(Customer).all()
    for c in customers:
        print(f"\nUser: {c.email}, square_id: {c.square_customer_id}")
        if c.square_customer_id:
            db_cards = db.query(PaymentMethod).filter(PaymentMethod.customer_id == c.id).all()
            print(f"  DB Cards: {[ (card.square_card_id, card.is_default) for card in db_cards]}")
        props = db.query(Property).filter(Property.customer_id == c.id).all()
        print(f"  Properties: {[(p.id, p.address, p.city, p.zip_code) for p in props]}")
        print(f"  User record address: {(c.address, c.city, c.zip_code)}")
    db.close()

if __name__ == "__main__":
    test_cards()

if __name__ == "__main__":
    test_cards()
