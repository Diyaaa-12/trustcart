"""
Cart ORM models: CartSession and CartItem.

Key invariant enforced at the API layer (not trusted from client):
- unit_price is copied from the catalog at add-time; client-sent prices are ignored.
- Cart totals are always recomputed server-side.
- discount_budget_used_pct tracks cumulative discount spend for the session.
- trust_score tracks the session autonomy level (0-100, default 100).
"""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.services.trust_score import AutonomyTier, _autonomy_tier

if TYPE_CHECKING:
    from app.models.product import Product
    from app.models.proposal import AuditLog, Proposal


class CartSession(Base):
    __tablename__ = "cart_sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # Cumulative discount budget used this session (sum of accepted discount_pct values)
    discount_budget_used_pct: Mapped[Decimal] = mapped_column(
        Numeric(6, 2), nullable=False, default=Decimal("0")
    )
    # Trust score for this session (0-100, default 100 = fully trusted).
    # Decreases on gate rejections; amplified decrease on injection-signal rejections.
    # Increases gradually on clean acceptances.
    trust_score: Mapped[Decimal] = mapped_column(
        Numeric(6, 2), nullable=False, default=Decimal("100")
    )

    # Relationships
    items: Mapped[list[CartItem]] = relationship(
        "CartItem", back_populates="session", cascade="all, delete-orphan"
    )
    proposals: Mapped[list[Proposal]] = relationship(
        "Proposal", back_populates="session"
    )
    audit_logs: Mapped[list[AuditLog]] = relationship(
        "AuditLog", back_populates="session"
    )

    @property
    def autonomy_tier(self) -> AutonomyTier:
        """Computed autonomy tier from the current trust score. Never persisted."""
        return _autonomy_tier(float(self.trust_score))

    def __repr__(self) -> str:
        return (
            f"<CartSession id={self.id} budget_used={self.discount_budget_used_pct}% "
            f"trust={self.trust_score} tier={self.autonomy_tier.value}>"
        )


class CartItem(Base):
    __tablename__ = "cart_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cart_sessions.id", ondelete="CASCADE"), nullable=False
    )
    product_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("products.id", ondelete="RESTRICT"), nullable=False
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # Snapshot of price at time of add -- not trusted from client
    unit_price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)

    # Relationships
    session: Mapped[CartSession] = relationship("CartSession", back_populates="items")
    product: Mapped[Product] = relationship("Product", back_populates="cart_items")

    def __repr__(self) -> str:
        return (
            f"<CartItem product_id={self.product_id} qty={self.quantity} "
            f"unit_price={self.unit_price}>"
        )
