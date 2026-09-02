"""
Proposal and AuditLog ORM models.

Proposal: every LLM recommendation batch -- stores raw LLM output, gate result,
and user action for full auditability.

AuditLog: append-only event log for every meaningful action in a session.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.cart import CartSession


class Proposal(Base):
    __tablename__ = "proposals"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cart_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Cart state when the proposal was generated (JSON snapshot)
    cart_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)

    # Raw LLM response (preserved for audit / debugging)
    llm_raw_output: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    # All items the LLM proposed (before gate)
    proposed_items: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)

    # Items accepted by the gate
    accepted_items: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)

    # Items rejected by the gate (includes rejection reason)
    rejected_items: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)

    # Gate decision: "accepted" | "rejected" | "partial"
    gate_result: Mapped[str] = mapped_column(String(20), nullable=False)

    # User's final action: "pending" | "accepted" | "declined" | "review_required" | "reviewed"
    user_action: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    acted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Relationships
    session: Mapped[CartSession] = relationship(
        "CartSession", back_populates="proposals"
    )

    def __repr__(self) -> str:
        return (
            f"<Proposal id={self.id} gate={self.gate_result} "
            f"user_action={self.user_action}>"
        )


class AuditLog(Base):
    """Append-only event log. Never updated, only inserted."""

    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cart_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # e.g. "cart.item_added", "proposal.generated", "gate.rejected",
    #      "user.accepted", "checkout.created", "checkout.failed"
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)

    # Arbitrary JSON payload for the event
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    # Request ID for end-to-end tracing
    request_id: Mapped[str] = mapped_column(String(80), nullable=False, default="")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

    # Relationships
    session: Mapped[CartSession] = relationship(
        "CartSession", back_populates="audit_logs"
    )

    def __repr__(self) -> str:
        return f"<AuditLog event={self.event_type} session={self.session_id}>"
