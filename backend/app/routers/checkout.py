"""
Checkout router.

POST /api/checkout/{session_id}:
  1. Load cart from DB (never trusts client-sent totals).
  2. Compute amount server-side in paise.
  3. Create Razorpay order (with retry + graceful error state).
  4. Return order params needed for Razorpay checkout.js.

Cart is NEVER destroyed on checkout failure — the user can retry.
"""
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database import get_db
from app.models.cart import CartItem, CartSession
from app.models.proposal import AuditLog
from app.schemas.checkout import CheckoutErrorOut, CheckoutOut
from app.services.razorpay_service import RazorpayServiceError, create_order

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/checkout", tags=["checkout"])


async def _write_audit(
    db: AsyncSession, session_id: uuid.UUID, event_type: str, payload: dict
) -> None:
    import structlog
    ctx = structlog.contextvars.get_contextvars()
    db.add(AuditLog(
        session_id=session_id,
        event_type=event_type,
        payload=payload,
        request_id=ctx.get("request_id", ""),
    ))


@router.post(
    "/{session_id}",
    response_model=CheckoutOut,
    responses={
        402: {"model": CheckoutErrorOut, "description": "Razorpay order creation failed"},
        422: {"description": "Cart is empty"},
    },
)
async def create_checkout(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> CheckoutOut:
    """
    Create a Razorpay order for the cart.

    The total is always computed server-side from DB prices.
    Any client-sent total is completely ignored.
    """
    # Load session with items
    result = await db.execute(
        select(CartSession)
        .options(selectinload(CartSession.items).selectinload(CartItem.product))
        .where(CartSession.id == session_id)
    )
    session = result.scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cart session not found")

    if not session.items:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Cannot checkout an empty cart",
        )

    # Compute total server-side — never trust client
    subtotal = sum(item.unit_price * item.quantity for item in session.items)
    amount_paise = int(subtotal * 100)  # Razorpay requires smallest unit

    logger.info(
        "Creating checkout order",
        extra={
            "session_id": str(session_id),
            "subtotal": float(subtotal),
            "amount_paise": amount_paise,
            "mock_mode": settings.mock_checkout,
        },
    )

    receipt = f"trustcart_{str(session_id)[:8]}"

    try:
        order = await create_order(
            amount_paise=amount_paise,
            currency="INR",
            receipt=receipt,
        )
    except RazorpayServiceError as exc:
        logger.error(
            "Checkout failed — Razorpay error after retry",
            extra={"session_id": str(session_id), "error": str(exc)},
        )
        await _write_audit(db, session_id, "checkout.failed", {
            "error": str(exc),
            "amount_paise": amount_paise,
            "cart_preserved": True,
        })
        await db.commit()
        # Raise 402 with structured body — cart is preserved
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail={
                "error": "Payment provider unavailable. Please try again.",
                "session_id": str(session_id),
                "cart_preserved": True,
            },
        )

    await _write_audit(db, session_id, "checkout.created", {
        "order_id": order["id"],
        "amount_paise": amount_paise,
        "mock": order.get("mock", False),
    })
    await db.commit()

    return CheckoutOut(
        order_id=order["id"],
        amount_paise=amount_paise,
        currency="INR",
        razorpay_key_id=settings.RAZORPAY_KEY_ID or "mock",
        session_id=session_id,
        mock_mode=order.get("mock", False),
    )
