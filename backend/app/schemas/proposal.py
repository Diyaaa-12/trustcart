"""Pydantic schemas for proposal and audit endpoints."""
import uuid
from decimal import Decimal
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class AcceptedItemOut(BaseModel):
    product_id: int
    product_name: str
    original_price: Decimal
    discount_pct: Decimal
    discounted_price: Decimal


class RejectedItemOut(BaseModel):
    product_id: int
    proposed_discount_pct: Decimal
    reason: str
    detail: str


class ProposalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    session_id: uuid.UUID
    gate_result: str          # "accepted" | "rejected" | "partial"
    accepted_items: list[AcceptedItemOut]
    rejected_items: list[RejectedItemOut]
    user_action: str          # "pending" | "review_required" | "reviewed" | "accepted" | "declined"
    autonomy_tier: str = "high"
    requires_review: bool = False
    created_at: datetime


class UserActionRequest(BaseModel):
    action: str               # "accepted" | "declined" | "reviewed" | "confirmed"

    def is_valid(self) -> bool:
        return self.action in {"accepted", "declined", "reviewed", "confirm", "confirmed", "review"}


class AuditEventOut(BaseModel):
    id: uuid.UUID
    event_type: str
    payload: dict[str, Any]
    request_id: str
    created_at: datetime


class TrustScoreHistoryEntry(BaseModel):
    old_score: float
    new_score: float
    delta: float
    reason: str
    detail: str
    autonomy_tier: str
    created_at: datetime


class SessionTimelineOut(BaseModel):
    session_id: uuid.UUID
    events: list[AuditEventOut]
    total_events: int
    current_trust_score: float = 100.0
    current_autonomy_tier: str = "high"
    trust_score_history: list[TrustScoreHistoryEntry] = []
