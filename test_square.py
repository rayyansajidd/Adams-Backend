import os
import requests
import json
from dotenv import load_dotenv

load_dotenv()

SQUARE_ACCESS_TOKEN = os.getenv("SQUARE_ACCESS_TOKEN")
SQUARE_LOCATION_ID = os.getenv("SQUARE_LOCATION_ID")
SQUARE_ENVIRONMENT = os.getenv("SQUARE_ENVIRONMENT", "production") # Default to production
SQUARE_API_BASE_URL = "https://connect.squareupsandbox.com" if SQUARE_ENVIRONMENT == "sandbox" else "https://connect.squareup.com"

url = f"{SQUARE_API_BASE_URL}/v2/subscriptions/search"
headers = {
    "Square-Version": "2024-01-18",
    "Authorization": f"Bearer {SQUARE_ACCESS_TOKEN}",
    "Content-Type": "application/json"
}

print(f"Token present: {'Yes' if SQUARE_ACCESS_TOKEN else 'No'}, Starts with: {SQUARE_ACCESS_TOKEN[:5] if SQUARE_ACCESS_TOKEN else 'N/A'}")
print(f"Location ID: {SQUARE_LOCATION_ID}")

payload = {
    "query": {
        "filter": {
            "location_ids": [SQUARE_LOCATION_ID],
            "statuses": ["ACTIVE"]
        }
    }
}

print(f"URL: {url}")
print(f"Payload: {json.dumps(payload)}")
res = requests.post(url, headers=headers, json=payload)
print(f"Status Code: {res.status_code}")
try:
    print(json.dumps(res.json(), indent=2))
except:
    print(res.text)
