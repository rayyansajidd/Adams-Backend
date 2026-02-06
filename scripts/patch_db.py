import sys
import os

# Add the project root to the python path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from db.init import engine
from sqlalchemy import text

def patch():
    with engine.connect() as conn:
        print("Patching database tables...")
        # Add property_id to historical tables
        conn.execute(text('ALTER TABLE payments ADD COLUMN IF NOT EXISTS property_id INTEGER REFERENCES properties(id)'))
        conn.execute(text('ALTER TABLE subscription_logs ADD COLUMN IF NOT EXISTS property_id INTEGER REFERENCES properties(id)'))
        conn.execute(text('ALTER TABLE invoices ADD COLUMN IF NOT EXISTS property_id INTEGER REFERENCES properties(id)'))
        conn.commit()
        print("Database patched successfully.")

if __name__ == "__main__":
    patch()
