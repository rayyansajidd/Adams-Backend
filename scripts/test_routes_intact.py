"""Self-check: every endpoint the frontend calls still exists.

Regression guarded: a scripted edit to routers/admin.py anchored on two lines that
appear in BOTH /admin/customer-payments and /admin/sync-invoices. str.index found the
first, and the replacement swallowed everything up to the second - silently deleting
/admin/change-subscription, /admin/customer-details and /admin/sync-invoices, 140
lines in all. Syntax stayed valid, so nothing caught it until the live route table
was compared against the repo.

Run: python scripts/test_routes_intact.py
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite://")

from main import app

paths = set(app.openapi()["paths"].keys())

REQUIRED = [
    "/admin/customers", "/admin/customer-payments/{customer_id}",
    "/admin/customer-details/{customer_id}", "/admin/customer-cards/{customer_id}",
    "/admin/sync-invoices/{customer_id}", "/admin/invoice-pdf/{square_invoice_id}",
    "/admin/all-invoices", "/admin/recent-invoices", "/admin/stats",
    "/admin/change-subscription/{customer_id}/{property_id}",
    "/admin/cancel-subscription/{customer_id}/{property_id}",
    "/admin/save-card/{customer_id}", "/admin/remove-card/{customer_id}/{card_id}",
    "/payments/billing-history", "/payments/my-subscriptions", "/payments/properties",
    "/payments/create-property", "/payments/change-plan/{property_id}",
    "/payments/save-card", "/payments/square-config", "/payments/subscription-plans",
    "/payments/one-time-payment", "/payments/validate-card",
    "/auth/login", "/auth/signup", "/auth/admin/login",
    "/webhooks/square",
]

missing = [p for p in REQUIRED if p not in paths]
assert not missing, "routes disappeared:\n  " + "\n  ".join(missing)
print(f"1. All {len(REQUIRED)} required routes present ({len(paths)} total)          OK")

# The one endpoint this work rewired must still be a POST that reaches the shared sync.
import inspect, routers.admin as admin
src = inspect.getsource(admin.sync_customer_invoices)
assert "sync_invoices_for_customer" in src, "sync-invoices no longer uses the shared helper"
print("2. sync-invoices routes through utils.invoice_sync                      OK")

# customer-payments is a GET and must NOT have been turned into a writer.
src = inspect.getsource(admin.get_customer_payments)
assert "sync_invoices_for_customer" not in src, "customer-payments (a GET) now writes"
print("3. customer-payments left read-only                                     OK")

print("\nall checks passed")
