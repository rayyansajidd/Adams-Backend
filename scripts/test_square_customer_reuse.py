"""Self-check: a person who already exists in this Square account must never get a
second customer record when they sign up on another brand's site.

Adams and Skeeterman share one Square account. A customer of the other brand signing
up here used to get a brand-new Square customer record, splitting one person across
two ids and leaving the other backend orphaned when one was later deleted.

Run: python scripts/test_square_customer_reuse.py
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite://")

import utils.square_client as sq

created = []


def stub(existing_email=None, existing_id=None):
    created.clear()

    def by_email(email):
        if existing_email and email == existing_email:
            return {"success": True, "customer_id": existing_id,
                    "customer": {"id": existing_id, "email_address": email}}
        return {"success": False, "error": "Customer not found"}

    def create(given_name, family_name, email, phone_number=None, address=None):
        created.append(email)
        return {"success": True, "customer_id": "NEWLY_CREATED", "customer": {"id": "NEWLY_CREATED"}}

    sq.get_square_customer_by_email = by_email
    sq.create_square_customer = create


stub(existing_email="shared@example.com", existing_id="C19T02WWWXM5B0KP8FHYABY7RR")
r = sq.get_or_create_square_customer(email="shared@example.com", given_name="Shared", family_name="Person")
assert r["success"] and r["customer_id"] == "C19T02WWWXM5B0KP8FHYABY7RR", r
assert r["reused"] is True and created == [], f"created a duplicate: {created}"
print("1. Existing customer at another brand -> record reused, no duplicate      OK")

stub()
r = sq.get_or_create_square_customer(email="brand-new@example.com", given_name="New", family_name="Person")
assert r["success"] and r["reused"] is False and created == ["brand-new@example.com"], (r, created)
print("2. Brand new person -> created once                                       OK")

stub()
searched = []
sq.get_square_customer_by_email = lambda e: searched.append(e) or {"success": False}
r = sq.get_or_create_square_customer(email=None, given_name="Guest", family_name="User")
assert r["success"] and searched == [], f"searched Square for a null email: {searched}"
print("3. Missing email -> no lookup on None, creates directly                   OK")

print("\nall checks passed")
