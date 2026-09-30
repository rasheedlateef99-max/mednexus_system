"""
Paystack Payment Provider
------------------------------
Concrete implementation of PaymentProvider for Paystack.

Security note on verify_webhook_signature:
  Paystack signs every webhook payload with HMAC-SHA512 using your
  secret key. We recompute the same HMAC over the raw request body
  and compare it to the x-paystack-signature header using a
  constant-time comparison (hmac.compare_digest) to prevent timing
  attacks. If they don't match, the payload is rejected outright —
  we never act on an unverified webhook body, regardless of what it
  claims to contain.
"""

import hmac
import hashlib
import httpx

from app.services.platform.payment_provider import PaymentProvider
from app.config import settings

PAYSTACK_BASE_URL = "https://api.paystack.co"


class PaystackProvider(PaymentProvider):
    def __init__(self):
        self.secret_key = settings.PAYSTACK_SECRET_KEY
        self._headers = {
            "Authorization": f"Bearer {self.secret_key}",
            "Content-Type": "application/json",
        }

    async def initialize_payment(
        self,
        email: str,
        amount: float,
        currency: str,
        reference: str,
        callback_url: str | None = None,
        metadata: dict | None = None,
    ) -> dict:
        # Paystack expects amount in the smallest currency unit (kobo for NGN)
        amount_in_subunits = int(round(amount * 100))

        payload = {
            "email": email,
            "amount": amount_in_subunits,
            "currency": currency.upper(),
            "reference": reference,
        }
        if callback_url:
            payload["callback_url"] = callback_url
        if metadata:
            payload["metadata"] = metadata

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{PAYSTACK_BASE_URL}/transaction/initialize",
                json=payload,
                headers=self._headers,
                timeout=15.0,
            )
            response.raise_for_status()
            data = response.json()

        return {
            "authorization_url": data["data"]["authorization_url"],
            "access_code": data["data"]["access_code"],
            "reference": data["data"]["reference"],
        }

    async def verify_transaction(self, reference: str) -> dict:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{PAYSTACK_BASE_URL}/transaction/verify/{reference}",
                headers=self._headers,
                timeout=15.0,
            )
            response.raise_for_status()
            return response.json()

    def verify_webhook_signature(self, payload_body: bytes, signature_header: str) -> bool:
        if not signature_header:
            return False

        computed_signature = hmac.new(
            key=self.secret_key.encode("utf-8"),
            msg=payload_body,
            digestmod=hashlib.sha512,
        ).hexdigest()

        return hmac.compare_digest(computed_signature, signature_header)


def get_payment_provider() -> PaymentProvider:
    """
    Factory function — returns the active payment provider implementation.
    Switching providers later means changing this one function.
    """
    return PaystackProvider()