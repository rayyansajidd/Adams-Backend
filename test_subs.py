import os
from dotenv import load_dotenv

load_dotenv()

# Now import square_client, which will read os.getenv
from utils.square_client import get_subscriptions
import json

if __name__ == "__main__":
    res = get_subscriptions(status="ACTIVE")
    print(json.dumps(res, indent=2))
