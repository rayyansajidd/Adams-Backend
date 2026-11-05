"""Self-check for the Square webhook: signature enforcement, correct event names,
and subscription status sync.

Note the ordering trap this guards: square_webhook wraps its body in a try/except that
returns HTTP 200 on any exception. The signature check must therefore run BEFORE that
block, or a forged request would be swallowed and acknowledged.

Run: python scripts/test_webhook_sync.py
"""
import os, sys, json, base64, hashlib, hmac, asyncio, re
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ["SQUARE_WEBHOOK_SIGNATURE_KEY"] = "test-signature-key"
os.environ["SQUARE_WEBHOOK_URL"] = "https://adams.example.com/webhooks/square"

from fastapi import HTTPException
from models.user import Customer
import routers.webhooks as wh

# Event names Square actually emits, from GET /v2/webhooks/event-types on this account
REAL_EVENTS = {"invoice.canceled", "invoice.created", "invoice.deleted", "invoice.payment_made",
               "invoice.published", "invoice.refunded", "invoice.scheduled_charge_failed",
               "invoice.updated", "subscription.created", "subscription.updated"}

SUB_ID = "dc85c038-a39c-42b1-96ba-4006a673466e"
CUST = "C19T02WWWXM5B0KP8FHYABY7RR"


class FakeQuery:
    def __init__(self, db): self.db = db
    def filter(self, *a, **k): return self
    def first(self): return self.db.customer


class FakeDB:
    def __init__(self, customer): self.customer, self.added = customer, []
    def query(self, model): return FakeQuery(self)
    def add(self, obj): self.added.append(obj)
    def commit(self): pass
    def rollback(self): pass


class FakeRequest:
    def __init__(self, body, sig): self._body, self.headers = body, {"x-square-hmacsha256-signature": sig}
    async def body(self): return self._body


def sign(body):
    return base64.b64encode(hmac.new(b"test-signature-key",
                                     os.environ["SQUARE_WEBHOOK_URL"].encode() + body,
                                     hashlib.sha256).digest()).decode()


def customer():
    c = Customer()
    c.id = 7
    c.square_customer_id = CUST
    c.square_subscription_id = SUB_ID
    c.subscription_status, c.subscription_active = "ACTIVE", True
    c.failed_payment_attempts = 0
    return c


def post(event, sig=None, cust=None):
    body = json.dumps(event).encode()
    db = FakeDB(cust or customer())
    return asyncio.run(wh.square_webhook(FakeRequest(body, sig if sig is not None else sign(body)), db=db)), db


src = open(os.path.join(os.path.dirname(__file__), "..", "routers", "webhooks.py"), encoding="utf-8").read()
assert 'event_type == "invoice.payment_failed"' not in src, "still listening for non-existent invoice.payment_failed"
for ev in re.findall(r'event_type == "([^"]+)"', src):
    assert ev in REAL_EVENTS, f"{ev} is not a Square event type"
print("1. Every event the handler matches is one Square really emits            OK")

CANCEL = {"type": "subscription.updated",
          "data": {"object": {"subscription": {"id": SUB_ID, "status": "CANCELED"}}}}

for bad in ("", "not-a-real-signature"):
    try:
        post(CANCEL, sig=bad)
        raise AssertionError("accepted a webhook with a bad signature")
    except HTTPException as e:
        assert e.status_code == 403, e.status_code
print("2. Unsigned and forged webhooks rejected with 403, not swallowed as 200   OK")

_, db = post(CANCEL)
assert db.customer.subscription_status == "CANCELED", db.customer.subscription_status
assert db.customer.subscription_active is False
assert any(getattr(l, "action", None) == "SQUARE_CANCELED" for l in db.added), db.added
print("3. Signed subscription.updated syncs status and logs it                  OK")

c = customer(); c.failed_payment_attempts = 2
FAIL = {"type": "invoice.scheduled_charge_failed",
        "data": {"object": {"invoice": {"primary_recipient": {"customer_id": CUST}}}}}
post(FAIL, cust=c)
assert c.failed_payment_attempts == 3, c.failed_payment_attempts
assert c.subscription_status == "SUSPENDED", c.subscription_status
print("4. Failed charge resolves customer via primary_recipient, suspends at 3   OK")

del os.environ["SQUARE_WEBHOOK_SIGNATURE_KEY"]
try:
    post(CANCEL, sig="anything")
    raise AssertionError("accepted a webhook with no signing key configured")
except HTTPException as e:
    assert e.status_code == 403, e.status_code
os.environ["SQUARE_WEBHOOK_SIGNATURE_KEY"] = "test-signature-key"
print("5. No signing key configured -> fails closed, not open                   OK")

print("\nall checks passed")
