import os
import json
import sys

# Add parent directory to path to import models and db
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from db.init import SessionLocal
from models.subscription import SubscriptionPlan

def sync_plans():
    json_path = os.path.join(os.path.dirname(__file__), '..', 'created_plans.json')
    if not os.path.exists(json_path):
        print(f"Error: {json_path} not found. Run create_square_plans.py first.")
        return

    with open(json_path, 'r') as f:
        plans_data = json.load(f)

    db = SessionLocal()
    try:
        count = 0
        for plan in plans_data:
            # Check if plan already exists in DB
            existing = db.query(SubscriptionPlan).filter(
                SubscriptionPlan.plan_variation_id == plan['variation_id']
            ).first()
            
            if not existing:
                new_plan = SubscriptionPlan(
                    plan_name=plan['name'],
                    plan_cost=plan['price'],
                    plan_variation_id=plan['variation_id'],
                    plan_description=f"Monthly property care at ${plan['price']}"
                )
                db.add(new_plan)
                count += 1
        
        db.commit()
        print(f"Successfully synced {count} new plans to the database.")
    except Exception as e:
        print(f"Error syncing plans: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    sync_plans()
