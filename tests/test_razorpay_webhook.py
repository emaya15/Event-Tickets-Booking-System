import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json
import hmac
import hashlib
import httpx

from app.config import settings

# Use the order ID returned by your Razorpay order API
order_id = input("Enter Razorpay Order ID: ").strip()

# Create a sample failed-payment webhook
payload = {
    "event": "payment.failed",
    "payload": {
        "payment": {
            "entity": {
                "id": "pay_test_failed_001",
                "order_id": order_id
            }
        }
    }
}

# Convert payload into exact JSON bytes
raw_body = json.dumps(payload, separators=(",", ":")).encode()

# Generate valid webhook signature
signature = hmac.new(
    settings.RAZORPAY_WEBHOOK_SECRET.encode(),
    raw_body,
    hashlib.sha256
).hexdigest()

# Send webhook to your public URL
url = "https://knight-observer-geo-obtain.trycloudflare.com/webhooks/razorpay"

headers = {
    "Content-Type": "application/json",
    "x-razorpay-signature": signature,
    "x-razorpay-event-id": "test_event_failed_001"
}

response = httpx.post(url, content=raw_body, headers=headers)

print("Status:", response.status_code)
print("Response:", response.text)
