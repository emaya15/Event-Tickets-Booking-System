import hmac
import hashlib
import json
import os
from dotenv import load_dotenv

load_dotenv()

secret = os.getenv("RAZORPAY_WEBHOOK_SECRET")
print("Secret loaded:", bool(secret))
print("Secret length:", len(secret) if secret else 0)

payload = {
    "event": "payment.failed",
    "payload": {
        "payment": {
            "entity": {
                "id": "pay_test123",
                "order_id": "order_test123"
            }
        }
    }
}

raw_body = json.dumps(payload, separators=(",", ":")).encode()

signature = hmac.new(
    secret.encode(),
    raw_body,
    hashlib.sha256
).hexdigest()

print("JSON body:", raw_body.decode())
print("Signature:", signature)