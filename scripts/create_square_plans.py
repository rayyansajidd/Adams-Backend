import os
import requests
import json
from dotenv import load_dotenv

# Load environment variables from the parent directory's .env file
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

SQUARE_ACCESS_TOKEN = os.getenv("SQUARE_ACCESS_TOKEN")
SQUARE_VERSION = "2025-10-16"

# Configuration
START_PRICE = 600
END_PRICE = 2000
STEP = 50

def create_square_subscription_plan(price):
    url = "https://connect.squareup.com/v2/catalog/object"
    headers = {
        "Square-Version": SQUARE_VERSION,
        "Authorization": f"Bearer {SQUARE_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    
    plan_name = f"Adams Property Monthly {price}"
    variation_name = f"Adams Property Monthly ${price}"
    # Square amount is in cents
    amount_cents = int(price * 100)
    
    payload = {
        "idempotency_key": f"adams-monthly-{price}-002", # Incremented key for safety
        "object": {
            "type": "SUBSCRIPTION_PLAN",
            "id": f"#adams_monthly_{price}_plan",
            "subscription_plan_data": {
                "name": plan_name,
                "subscription_plan_variations": [
                    {
                        "type": "SUBSCRIPTION_PLAN_VARIATION",
                        "id": f"#adams_monthly_{price}_variation",
                        "subscription_plan_variation_data": {
                            "name": variation_name,
                            "phases": [
                                {
                                    "ordinal": 0,
                                    "cadence": "MONTHLY",
                                    "pricing": {
                                        "type": "STATIC",
                                        "price": {
                                            "amount": amount_cents,
                                            "currency": "USD"
                                        }
                                    }
                                }
                            ]
                        }
                    }
                ]
            }
        }
    }
    
    response = requests.post(url, headers=headers, json=payload)
    return response

import argparse

def main():
    parser = argparse.ArgumentParser(description='Create Square subscription plans.')
    parser.add_argument('--test', action='store_true', help='Create only one plan for testing')
    parser.add_argument('--start', type=int, default=START_PRICE, help='Starting price')
    parser.add_argument('--end', type=int, default=END_PRICE, help='Ending price')
    args = parser.parse_args()

    if not SQUARE_ACCESS_TOKEN:
        print("Error: SQUARE_ACCESS_TOKEN not found in .env")
        return

    start = args.start
    end = args.end if not args.test else args.start
    
    print(f"Starting creation of plans from ${start} to ${end}...")
    
    results = []
    
    # Use range with step, but if test, only do the first one
    prices = range(start, end + 1, STEP) if not args.test else [start]
    
    for price in prices:
        print(f"Creating plan for ${price}...", end=" ", flush=True)
        resp = create_square_subscription_plan(price)
        
        if resp.status_code == 200:
            data = resp.json()
            plan_id = ""
            var_id = ""
            for mapping in data.get("id_mappings", []):
                if "_plan" in mapping["client_object_id"]:
                    plan_id = mapping["object_id"]
                if "_variation" in mapping["client_object_id"]:
                    var_id = mapping["object_id"]
            
            print(f"Success! Plan ID: {plan_id}, Variation ID: {var_id}")
            results.append({
                "price": float(price),
                "plan_id": plan_id,
                "variation_id": var_id,
                "name": f"Adams Property Monthly {price}"
            })
        elif resp.status_code == 409:
             print(f"Conflict (already exists). Skipping...")
        else:
            print(f"Failed! Status: {resp.status_code}, Error: {resp.text}")
            
    # Save results to a file for later use (like seeding DB)
    output_file = os.path.join(os.path.dirname(__file__), '..', 'created_plans.json')
    
    # If file exists, load it and append (to avoid losing previous runs)
    existing_results = []
    if os.path.exists(output_file):
        try:
            with open(output_file, "r") as f:
                existing_results = json.load(f)
        except:
            pass
    
    # Merge new results, avoiding duplicates by price
    existing_prices = {r['price'] for r in existing_results}
    for r in results:
        if r['price'] not in existing_prices:
            existing_results.append(r)
            
    with open(output_file, "w") as f:
        json.dump(existing_results, f, indent=4)
    
    print(f"\nProcessed {len(results)} plans. Total unique plans in record: {len(existing_results)}")
    print(f"Details saved/updated in {output_file}")

if __name__ == "__main__":
    main()
