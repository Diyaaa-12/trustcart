"""Pydantic schemas for proposal and audit endpoints."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class AcceptedItemOut(BaseModel):
    product_id: int
    product_name: str
    original_price: float
    discount_pct: float
    discounted_price: float


class RejectedItemOut(BaseModel):
    product_id: int
    proposed_discount_pct: float
    reason: str
    detail: str


class CounterfactualComparison(BaseModel):
    """
    Side-by-side comparison of raw LLM proposals vs policy gate decisions.
    Proves whether the gate modified or rejected what the LLM suggested.
    """
    llm_proposed_items: list[dict[str, Any]] = []
    gate_accepted_items: list[AcceptedItemOut] = []
    gate_rejected_items: list[RejectedItemOut] = []
    divergence_detected: bool = False
    summary: str = ""


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
    counterfactual: CounterfactualComparison | None = None
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


class ReplayStepOut(BaseModel):
    """A human-readable, sequential step in an audit replay timeline."""
    step_number: int
    timestamp: datetime
    category: str      # "cart", "agent", "gate", "trust", "user", "checkout", "system"
    title: str
    summary: str
    status: str        # "info", "success", "warning", "danger"
    details: dict[str, Any] = {}


class SessionTimelineOut(BaseModel):
    session_id: uuid.UUID
    events: list[AuditEventOut]
    total_events: int
    current_trust_score: float = 100.0
    current_autonomy_tier: str = "high"
    trust_score_history: list[TrustScoreHistoryEntry] = []
    replay_steps: list[ReplayStepOut] = []


class AuditReplayOut(BaseModel):
    session_id: uuid.UUID
    total_steps: int
    current_trust_score: float
    current_autonomy_tier: str
    steps: list[ReplayStepOut]
