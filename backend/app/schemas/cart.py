"""Pydantic schemas for cart endpoints."""
import uuid
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class AddItemRequest(BaseModel):
    """Client sends only product_id + quantity. Price is always server-resolved."""

    product_id: int = Field(..., gt=0)
    quantity: int = Field(..., ge=1, le=100)


class CartItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    product_id: int
    product_name: str
    category: str
    quantity: int
    unit_price: Decimal
    line_total: Decimal


class CartOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    session_id: uuid.UUID
    items: list[CartItemOut]
    subtotal: Decimal
    discount_budget_used_pct: Decimal
    discount_budget_remaining_pct: Decimal
    item_count: int
    # Trust-adaptive autonomy fields
    trust_score: Decimal
    autonomy_tier: str  # "high" | "medium" | "low"


class RemoveItemRequest(BaseModel):
    product_id: int = Field(..., gt=0)
