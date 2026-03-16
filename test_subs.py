from dotenv import load_dotenv
load_dotenv()

from utils.square_client import get_subscriptions
import json

res = get_subscriptions(status="ACTIVE")
print(json.dumps(res, indent=2))
