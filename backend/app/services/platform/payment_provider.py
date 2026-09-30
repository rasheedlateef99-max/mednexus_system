"""
Payment Provider Abstraction
---------------------------------
Defines the interface every payment provider must implement.

This exists so subscription_service.py never imports Paystack directly.
Adding Stripe, Flutterwave, or any other provider later means writing
one new class that implements this interface — subscription logic
itself never changes.
"""

from abc import ABC, abstractmethod


class PaymentProvider(ABC):
    """Abstract base — all payment providers implement this contract."""

    @abstractmethod
    async def initialize_payment(
        self,
        email: str,
        amount: float,
        currency: str,
        reference: str,
        callback_url: str | None = None,
        metadata: dict | None = None,
    ) -> dict:
        """
        Starts a payment session.
        Returns a dict with at minimum: authorization_url, access_code, reference.
        """
        ...

    @abstractmethod
    async def verify_transaction(self, reference: str) -> dict:
        """
        Server-to-server verification of a transaction by reference.
        Returns the provider's raw transaction data.
        Used as a fallback / manual reconciliation, separate from webhooks.
        """
        ...

    @abstractmethod
    def verify_webhook_signature(self, payload_body: bytes, signature_header: str) -> bool:
        """
        Cryptographically verifies that a webhook payload genuinely came
        from the provider and was not forged or tampered with in transit.
        """
        ...