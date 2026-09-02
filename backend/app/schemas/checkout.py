"""Pydantic schemas for checkout endpoints."""
import uuid

from pydantic import BaseModel


class CheckoutRequest(BaseModel):
    """
    Client only sends session_id — everything else (total, items) is
    computed server-side. Any client-sent total is ignored.
    """
    session_id: uuid.UUID


class CheckoutOut(BaseModel):
    order_id: str
    amount_paise: int
    currency: str
    razorpay_key_id: str
    session_id: uuid.UUID
    mock_mode: bool = False     # True when Razorpay keys not configured


class CheckoutErrorOut(BaseModel):
    error: str
    session_id: uuid.UUID
    cart_preserved: bool = True  # Always True — cart is never lost on checkout failure
