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
from app.models.product import Product
from app.models.proposal import AuditLog, Proposal
from app.schemas.proposal import (
    AuditEventOut,
    AuditReplayOut,
    ReplayStepOut,
    SessionTimelineOut,
    TrustScoreHistoryEntry,
)
from app.services.explanation import (
    DecisionExplanationOut,
    build_decision_explanation,
)
from app.services.mandate import compute_mandate_fingerprint

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
        elif etype in ("mandate.issued", "mandate.reissued"):
            fp = payload.get("mandate_fingerprint", "mnd_unknown")
            max_disc = float(payload.get("max_cumulative_discount_pct", 10.0))
            max_items = int(payload.get("max_items", 3))
            exp = payload.get("expires_at", "")
            action_label = "Reissued" if etype == "mandate.reissued" else "Issued"
            prefix = "Fresh cryptographic" if etype == "mandate.reissued" else "Cryptographic"
            steps.append(ReplayStepOut(
                step_number=step_num,
                timestamp=ts,
                category="mandate",
                title=f"Spend Mandate {action_label} [{fp}]",
                summary=(
                    f"{prefix} spend mandate {action_label.lower()} under AP2 protocol: "
                    f"max cumulative discount {max_disc:.0f}%, max {max_items} item(s). "
                    f"Expires at {exp}."
                ),
                status="info",
                details=payload,
            ))
        elif etype == "mandate.verified":
            fp = payload.get("mandate_fingerprint", "mnd_unknown")
            is_valid = payload.get("is_valid", False)
            res = payload.get("verification_result", "unknown")
            if is_valid:
                mstatus = "success"
                mtitle = f"Mandate Verified: Invariant Bounds Active [{fp}]"
                msummary = (
                    "HMAC-SHA256 signature verified against server key. "
                    "Session bounds intact and active."
                )
            else:
                mstatus = "danger"
                mtitle = f"Mandate Verification Failed: {res.upper()} [{fp}]"
                msummary = (
                    f"Cryptographic verification rejected ({res}). "
                    "Agent authorization revoked for proposal batch."
                )
            steps.append(ReplayStepOut(
                step_number=step_num,
                timestamp=ts,
                category="mandate",
                title=mtitle,
                summary=msummary,
                status=mstatus,
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
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Cart session not found",
        )
    current_trust = float(session.trust_score)
    current_tier = session.autonomy_tier.value

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


@router.get(
    "/{session_id}/explain/{proposal_id}",
    response_model=DecisionExplanationOut,
    summary="Plain-language explanation of a proposal policy decision",
    description=(
        "Generates a deterministic, plain-English explanation for a specific proposal decision. "
        "Reconstructs the complete decision narrative directly from stored proposal gate results, "
        "counterfactual divergences, mandate verification status, and trust score deltas. "
        "100% deterministic with zero LLM hallucinations."
    ),
)
async def explain_proposal_decision(
    session_id: uuid.UUID,
    proposal_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> DecisionExplanationOut:
    """Generate a plain-English, deterministic explanation for a proposal's policy outcome."""
    session_res = await db.execute(
        select(CartSession).where(CartSession.id == session_id)
    )
    session = session_res.scalar_one_or_none()
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Cart session not found",
        )

    prop_res = await db.execute(
        select(Proposal).where(Proposal.id == proposal_id)
    )
    proposal = prop_res.scalar_one_or_none()
    if proposal is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Proposal not found",
        )
    if proposal.session_id != session_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Proposal does not belong to session",
        )

    # Collect product IDs from proposal to look up names
    product_ids: set[int] = set()
    for item in (proposal.proposed_items or []):
        if isinstance(item, dict) and "product_id" in item:
            product_ids.add(int(item["product_id"]))
    for item in (proposal.accepted_items or []):
        if isinstance(item, dict) and "product_id" in item:
            product_ids.add(int(item["product_id"]))
    for item in (proposal.rejected_items or []):
        if isinstance(item, dict) and "product_id" in item:
            product_ids.add(int(item["product_id"]))

    product_names: dict[int, str] = {}
    if product_ids:
        prod_res = await db.execute(
            select(Product).where(Product.id.in_(product_ids))
        )
        for p in prod_res.scalars().all():
            product_names[p.id] = p.name

    # Audit events for this session to get exact trust & mandate metrics
    events_res = await db.execute(
        select(AuditLog)
        .where(AuditLog.session_id == session_id)
        .order_by(AuditLog.created_at.asc())
    )
    events = list(events_res.scalars().all())

    gate_event = None
    gate_event_idx = -1
    for idx, e in enumerate(events):
        if e.event_type == "gate.decision" and isinstance(e.payload, dict):
            if str(e.payload.get("proposal_id")) == str(proposal_id):
                gate_event = e
                gate_event_idx = idx
                break

    trust_event = None
    if gate_event_idx >= 0:
        for idx in range(gate_event_idx, -1, -1):
            if events[idx].event_type == "trust_score.updated" and isinstance(
                events[idx].payload, dict
            ):
                trust_event = events[idx]
                break

    if trust_event is None:
        for e in events:
            if e.event_type == "trust_score.updated" and isinstance(e.payload, dict):
                trust_event = e
                break

    old_score = 100.0
    new_score = 100.0
    score_delta = 0.0
    old_tier = "high"
    new_tier = "high"
    if trust_event and isinstance(trust_event.payload, dict):
        old_score = float(trust_event.payload.get("old_score", 100.0))
        new_score = float(trust_event.payload.get("new_score", 100.0))
        score_delta = float(trust_event.payload.get("delta", 0.0))
        new_tier = str(trust_event.payload.get("autonomy_tier", "high"))
        if old_score >= 70.0:
            old_tier = "high"
        elif old_score >= 40.0:
            old_tier = "medium"
        else:
            old_tier = "low"
    else:
        new_score = float(session.trust_score)
        old_score = new_score
        new_tier = session.autonomy_tier.value
        old_tier = new_tier

    mandate_fp = None
    mandate_verified = True
    mandate_failure_reason = None
    mandate_max_disc = 10.0
    if session.mandate_payload:
        mandate_fp = compute_mandate_fingerprint(session.mandate_payload)
        mandate_max_disc = float(
            session.mandate_payload.get("max_cumulative_discount_pct", 10.0)
        )

    for e in events:
        if e.event_type == "mandate.verified" and isinstance(e.payload, dict):
            if e.payload.get("is_valid") is False:
                mandate_verified = False
                mandate_failure_reason = str(
                    e.payload.get("verification_result", "mandate_invalid")
                )

    requires_review = (
        gate_event.payload.get("requires_review", False)
        if (gate_event and isinstance(gate_event.payload, dict))
        else (new_tier.lower() in ("medium", "low"))
    )

    return build_decision_explanation(
        proposal_id=str(proposal_id),
        session_id=str(session_id),
        gate_result=proposal.gate_result,
        proposed_items=proposal.proposed_items or [],
        accepted_items=proposal.accepted_items or [],
        rejected_items=proposal.rejected_items or [],
        product_names=product_names,
        old_score=old_score,
        new_score=new_score,
        score_delta=score_delta,
        old_autonomy_tier=old_tier,
        new_autonomy_tier=new_tier,
        requires_review=requires_review,
        mandate_fingerprint=mandate_fp,
        mandate_verified=mandate_verified,
        mandate_failure_reason=mandate_failure_reason,
        mandate_max_discount_pct=mandate_max_disc,
    )
