"""Self-check: every model is registered when anything imports Customer.

Regression guarded: Customer.properties = relationship("Property"), and
models/__init__.py did not import models.property. Inside the app that was masked,
because init_db and three routers import Property. A standalone script does not, so
the first db.query(Customer) died with:

    InvalidRequestError: When initializing mapper Mapper[Customer(customers)],
    expression 'Property' failed to locate a name ('Property').

which is exactly how fix_square_customer_ids.py failed on the server.

Run: python scripts/test_model_registry.py
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite://")

# Import the way a standalone script does - Customer only, no routers, no init_db.
from models.user import Customer
from sqlalchemy.orm import configure_mappers

configure_mappers()   # raised InvalidRequestError before models/__init__ imported Property
print("1. Mappers configure from a bare 'from models.user import Customer'    OK")

assert Customer.properties is not None
print("2. Customer.properties relationship resolves                           OK")

# The scripts that run outside the app must all survive this import path.
import importlib
for mod in ("utils.invoice_sync",):
    importlib.import_module(mod)
print("3. utils.invoice_sync imports cleanly outside the app                   OK")

print("\nall checks passed")
