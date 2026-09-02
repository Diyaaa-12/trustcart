"""
Razorpay order creation service.

Features:
  - Auto mock mode when RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET are absent.
  - Retry-once on transient failure (network / 5xx).
  - Raises RazorpayServiceError with a structured message on second failure.
  - Runs synchronous Razorpay SDK in a thread pool to avoid blocking the event loop.
"""
import asyncio
import logging
import uuid
from typing import Any

logger = logging.getLogger(__name__)


class RazorpayServiceError(Exception):
    """Raised when Razorpay order creation fails after retry."""

    def __init__(self, message: str, *, retried: bool = True) -> None:
        super().__init__(message)
        self.retried = retried


def _create_order_sync(key_id: str, key_secret: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Synchronous Razorpay call â€” run in thread pool."""
    import razorpay

    client = razorpay.Client(auth=(key_id, key_secret))
    return client.order.create(data=payload)  # type: ignore[no-any-return]


async def create_order(
    amount_paise: int,
    currency: str = "INR",
    receipt: str = "",
) -> dict[str, Any]:
    """
    Create a Razorpay order.

    Args:
        amount_paise: Order amount in smallest currency unit (paise for INR).
        currency:     ISO 4217 currency code.
        receipt:      Merchant reference ID (used for idempotency).

    Returns:
        Razorpay order dict with at least {"id", "amount", "currency", "status"}.

    Raises:
        RazorpayServiceError: If both the initial attempt and one retry fail.

    Note:
        If Razorpay keys are not configured (mock_checkout=True), returns a mock
        order dict immediately without any network call.
    """
    from app.config import settings

    if settings.mock_checkout:
        mock_id = f"mock_order_{receipt or uuid.uuid4().hex[:8]}"
        logger.info("Razorpay mock mode â€” returning fake order", extra={"order_id": mock_id})
        return {
            "id": mock_id,
            "amount": amount_paise,
            "currency": currency,
            "status": "created",
            "mock": True,
        }

    payload = {
        "amount": amount_paise,
        "currency": currency,
        "receipt": receipt or str(uuid.uuid4()),
    }

    async def _attempt() -> dict[str, Any]:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, _create_order_sync,
            settings.RAZORPAY_KEY_ID,
            settings.RAZORPAY_KEY_SECRET,
            payload,
        )

    # First attempt
    try:
        order = await _attempt()
        logger.info("Razorpay order created", extra={"order_id": order.get("id")})
        return order
    except Exception as first_exc:
        logger.warning(
            "Razorpay order creation failed, retrying once",
            extra={"error": str(first_exc), "receipt": receipt},
        )

    # Retry once
    try:
        order = await _attempt()
        logger.info(
            "Razorpay order created on retry", extra={"order_id": order.get("id")}
        )
        return order
    except Exception as second_exc:
        logger.error(
            "Razorpay order creation failed after retry",
            extra={"error": str(second_exc), "receipt": receipt},
        )
        raise RazorpayServiceError(
            f"Razorpay order creation failed after retry: {second_exc}",
            retried=True,
        ) from second_exc

