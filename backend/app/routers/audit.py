"""
Audit router -- session timeline, trust score history, and audit replay.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.cart import CartSession
from app.models.proposal import AuditLog
from app.schemas.proposal import (
    AuditEventOut,
    AuditReplayOut,
    ReplayStepOut,
    SessionTimelineOut,
    TrustScoreHistoryEntry,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/audit", tags=["audit"])


def _build_replay_steps(events: list[AuditLog]) -> list[ReplayStepOut]:
    """
    Transform raw audit log events into an ordered, human-readable replay sequence.
    Reconstructs the full narrative of a session without requiring the reader
    to parse low-level JSON payloads.
    """
    steps: list[ReplayStepOut] = []
    step_num = 1

    for event in events:
        etype = event.event_type
        payload: dict[str, Any] = event.payload if isinstance(event.payload, dict) else {}
        ts = event.created_at

        if etype == "cart.created":
            steps.append(ReplayStepOut(
                step_number=step_num,
                timestamp=ts,
                category="cart",
                title="Shopping Session Initialized",
                summary="New cart created with baseline trust score 100.0 (HIGH tier).",
                status="info",
                details=payload,
            ))
        elif etype == "cart.item_added":
            pname = payload.get("product_name", f"Product #{payload.get('product_id', '')}")
            qty = payload.get("quantity", 1)
            unit_price = float(payload.get("unit_price", 0.0))
            subtotal = float(payload.get("subtotal", 0.0))
            steps.append(ReplayStepOut(
                step_number=step_num,
                timestamp=ts,
                category="cart",
                title=f"Item Added to Cart: {pname}",
                summary=(
                    f"Customer added {qty}x {pname} (Rs {unit_price:.2f}). "
                    f"Cart subtotal: Rs {subtotal:.2f}."
                ),
                status="info",
                details=payload,
            ))
        elif etype == "cart.item_removed":
            pid = payload.get("product_id", "")
            steps.append(ReplayStepOut(
                step_number=step_num,
                timestamp=ts,
                category="cart",
                title="Item Removed from Cart",
                summary=f"Product #{pid} removed from cart.",
                status="info",
                details=payload,
            ))
        elif etype == "agent.proposed":
            count = payload.get("proposed_count", 0)
            ids = payload.get("proposed_ids", [])
            tier = payload.get("autonomy_tier", "high")
            steps.append(ReplayStepOut(
                step_number=step_num,
                timestamp=ts,
                category="agent",
                title=f"LLM Agent Generated {count} Recommendation(s)",
                summary=f"Agent proposed items {ids} under session autonomy tier '{tier.upper()}'.",
                status="info",
                details=payload,
            ))
        elif etype == "gate.decision":
            gres = payload.get("gate_result", "unknown")
            acc_count = len(payload.get("accepted_ids", []))
            rej_count = len(payload.get("rejected_ids", []))
            reasons = payload.get("rejection_reasons", [])
            reasons_list = [r.get("reason", "") for r in reasons if isinstance(r, dict)]
            reasons_str = ", ".join(reasons_list) or "None"

            if gres == "accepted":
                status_val = "success"
                title = "Policy Gate: Fully Approved"
                summary = (
                    f"Gate cleanly approved all {acc_count} proposed item(s). "
                    f"Zero policy violations."
                )
            elif gres == "partial":
                status_val = "warning"
                title = "Policy Gate: Partial Acceptance (Counterfactual Divergence)"
                summary = (
                    f"Gate allowed {acc_count} item(s), rejected {rej_count} item(s) "
                    f"[{reasons_str}]."
                )
            else:
                status_val = "danger"
                title = "Policy Gate: Rejected All Proposals"
                summary = f"Gate intercepted and rejected all {rej_count} item(s) [{reasons_str}]."

            steps.append(ReplayStepOut(
                step_number=step_num,
                timestamp=ts,
                category="gate",
                title=title,
                summary=summary,
                status=status_val,
                details=payload,
            ))
        elif etype == "trust_score.updated":
            old_s = float(payload.get("old_score", 100.0))
            new_s = float(payload.get("new_score", 100.0))
            delta = float(payload.get("delta", 0.0))
            reason = payload.get("reason", "")
            tier = payload.get("autonomy_tier", "high")
            detail = payload.get("detail", "")

            if delta > 0:
                tstatus = "success"
            elif "injection" in reason:
                tstatus = "danger"
            elif delta < 0:
                tstatus = "warning"
            else:
                tstatus = "info"

            steps.append(ReplayStepOut(
                step_number=step_num,
                timestamp=ts,
                category="trust",
                title=f"Trust Score: {old_s:.0f} -> {new_s:.0f} ({delta:+.0f})",
                summary=f"{detail} Autonomy tier is now {tier.upper()}.",
                status=tstatus,
                details=payload,
            ))
        elif etype == "user.reviewed":
            tier = payload.get("autonomy_tier", "")
            steps.append(ReplayStepOut(
                step_number=step_num,
                timestamp=ts,
                category="user",
                title="User Confirmation & Review Completed",
                summary=f"User reviewed and unlocked proposal under {tier.upper()} tier protocol.",
                status="info",
                details=payload,
            ))
        elif etype == "user.accepted":
            steps.append(ReplayStepOut(
                step_number=step_num,
                timestamp=ts,
                category="user",
                title="User Accepted Upsell Proposal",
                summary="Customer accepted recommended item(s) into cart.",
                status="success",
                details=payload,
            ))
        elif etype == "user.declined":
            steps.append(ReplayStepOut(
                step_number=step_num,
                timestamp=ts,
                category="user",
                title="User Declined Recommendation",
                summary="Customer dismissed proposal.",
                status="info",
                details=payload,
            ))
        elif etype == "checkout.created":
            amt = float(payload.get("amount_paise", 0)) / 100.0
            order_id = payload.get("order_id", "")
            mode = "Mock Mode" if payload.get("mock_mode") else "Live Mode"
            steps.append(ReplayStepOut(
                step_number=step_num,
                timestamp=ts,
                category="checkout",
                title=f"Checkout Initialized ({mode})",
                summary=(
                    f"Order {order_id} generated for Rs {amt:.2f}. "
                    f"Server recomputed cart total."
                ),
                status="success",
                details=payload,
            ))
        elif etype == "checkout.failed":
            err = payload.get("error", "Gateway failure")
            steps.append(ReplayStepOut(
                step_number=step_num,
                timestamp=ts,
                category="checkout",
                title="Checkout Failed (Cart Preserved)",
                summary=f"Payment provider error: {err}. Cart contents remain safe.",
                status="danger",
                details=payload,
            ))
        else:
            steps.append(ReplayStepOut(
                step_number=step_num,
                timestamp=ts,
                category="system",
                title=etype.replace(".", " ").title(),
                summary=str(payload),
                status="info",
                details=payload,
            ))

        step_num += 1

    return steps


@router.get("/{session_id}", response_model=SessionTimelineOut)
async def get_session_timeline(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> SessionTimelineOut:
    """Return the ordered audit timeline, trust score history, and replay steps."""
    session_res = await db.execute(
        select(CartSession).where(CartSession.id == session_id)
    )
    session = session_res.scalar_one_or_none()
    current_trust = float(session.trust_score) if session else 100.0
    current_tier = session.autonomy_tier.value if session else "high"

    result = await db.execute(
        select(AuditLog)
        .where(AuditLog.session_id == session_id)
        .order_by(AuditLog.created_at.asc())
    )
    events = result.scalars().all()

    trust_history: list[TrustScoreHistoryEntry] = []
    for e in events:
        if e.event_type == "trust_score.updated" and isinstance(e.payload, dict):
            trust_history.append(
                TrustScoreHistoryEntry(
                    old_score=float(e.payload.get("old_score", 100.0)),
                    new_score=float(e.payload.get("new_score", 100.0)),
                    delta=float(e.payload.get("delta", 0.0)),
                    reason=str(e.payload.get("reason", "")),
                    detail=str(e.payload.get("detail", "")),
                    autonomy_tier=str(e.payload.get("autonomy_tier", "high")),
                    created_at=e.created_at,
                )
            )

    event_outs = [
        AuditEventOut(
            id=e.id,
            event_type=e.event_type,
            payload=e.payload,
            request_id=e.request_id,
            created_at=e.created_at,
        )
        for e in events
    ]

    replay_steps = _build_replay_steps(list(events))

    return SessionTimelineOut(
        session_id=session_id,
        events=event_outs,
        total_events=len(event_outs),
        current_trust_score=current_trust,
        current_autonomy_tier=current_tier,
        trust_score_history=trust_history,
        replay_steps=replay_steps,
    )


@router.get("/{session_id}/replay", response_model=AuditReplayOut)
async def get_session_replay(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> AuditReplayOut:
    """Return a clean, ordered, human-readable replay sequence for a session."""
    session_res = await db.execute(
        select(CartSession).where(CartSession.id == session_id)
    )
    session = session_res.scalar_one_or_none()
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Cart session not found",
        )

    result = await db.execute(
        select(AuditLog)
        .where(AuditLog.session_id == session_id)
        .order_by(AuditLog.created_at.asc())
    )
    events = result.scalars().all()
    replay_steps = _build_replay_steps(list(events))

    return AuditReplayOut(
        session_id=session_id,
        total_steps=len(replay_steps),
        current_trust_score=float(session.trust_score),
        current_autonomy_tier=session.autonomy_tier.value,
        steps=replay_steps,
    )
