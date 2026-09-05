"""
Proposals router: agent generation, policy gate execution, user action.

Key invariants:
  - Agent (LLM) is called to generate recommendations; raw output is preserved.
  - Policy gate is ALWAYS executed before proposals are shown to user.
  - Trust score is evaluated and updated based on gate decision.
  - Hard policy caps NEVER change based on tier.
    Tier only adjusts UX friction and proposal volume.
  - In LOW tier (<40), proposal volume is throttled to 1 item per request.
  - In MEDIUM / LOW tier, proposals require explicit confirmation ("reviewed").
  - All decisions, counterfactuals, and score changes are logged to AuditLog.
"""
from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database import get_db
from app.models.cart import CartItem, CartSession
from app.models.product import Product
from app.models.proposal import AuditLog, Proposal
from app.schemas.proposal import (
    AcceptedItemOut,
    CounterfactualComparison,
    ProposalOut,
    RejectedItemOut,
    UserActionRequest,
)
from app.services.agent import get_proposals
from app.services.policy_gate import (
    CatalogProduct,
    PolicyConfig,
    run_gate,
)
from app.services.rate_limiter import limiter
from app.services.trust_score import (
    AutonomyTier,
    ProposalRecord,
    compute_trust_score,
)

logger = logging.getLogger(__name__)
slogger = structlog.get_logger(__name__)
router = APIRouter(prefix="/proposals", tags=["proposals"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def _get_session_or_404(session_id: uuid.UUID, db: AsyncSession) -> CartSession:
    result = await db.execute(
        select(CartSession)
        .options(selectinload(CartSession.items).selectinload(CartItem.product))
        .where(CartSession.id == session_id)
    )
    session = result.scalar_one_or_none()
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Cart session not found",
        )
    return session


def _cart_items_to_dicts(session: CartSession) -> list[dict]:
    return [
        {
            "product_id": ci.product_id,
            "name": ci.product.name,
            "category": ci.product.category,
            "quantity": ci.quantity,
            "unit_price": float(ci.unit_price),
        }
        for ci in session.items
    ]


def _cart_snapshot(session: CartSession) -> dict:
    items = _cart_items_to_dicts(session)
    subtotal = sum(i["unit_price"] * i["quantity"] for i in items)
    return {
        "session_id": str(session.id),
        "items": items,
        "subtotal": subtotal,
        "discount_budget_used_pct": float(session.discount_budget_used_pct),
        "trust_score": float(session.trust_score),
        "autonomy_tier": session.autonomy_tier.value,
    }


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


def _gate_result_label(
    accepted: int,
    rejected: int,
    mandate_valid: bool = True,
    mandate_reason: str = "",
) -> str:
    if not mandate_valid:
        return "mandate_expired" if mandate_reason == "mandate_expired" else "mandate_invalid"
    if accepted == 0 and rejected == 0:
        return "no_proposals"
    if accepted > 0 and rejected == 0:
        return "accepted"
    if accepted == 0:
        return "rejected"
    return "partial"


# ---------------------------------------------------------------------------
# POST /proposals/{session_id}
# ---------------------------------------------------------------------------
@router.post("/{session_id}", response_model=ProposalOut, status_code=status.HTTP_201_CREATED)
async def generate_proposals(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> ProposalOut:
    """Generate upsell/cross-sell proposals for a cart session."""
    allowed, retry_after = limiter.is_allowed(
        key=str(session_id),
        max_requests=settings.RATE_LIMIT_PROPOSALS_PER_MINUTE,
        window_seconds=60.0,
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                "Rate limit exceeded for proposal generation. "
                f"Please wait {retry_after} second(s) before requesting more recommendations."
            ),
            headers={"Retry-After": str(retry_after)},
        )

    session = await _get_session_or_404(session_id, db)
    snapshot = _cart_snapshot(session)
    cart_items_data = _cart_items_to_dicts(session)

    current_tier = session.autonomy_tier
    max_proposals = (
        1 if current_tier == AutonomyTier.LOW else settings.MAX_PROPOSALS_PER_CART
    )

    catalog_result = await db.execute(
        select(Product).where(Product.is_active.is_(True))
    )
    all_products = catalog_result.scalars().all()
    catalog_dicts = [
        {
            "id": p.id,
            "name": p.name,
            "price": float(p.price),
            "category": p.category,
            "stock": p.stock,
            "is_demo_fixture": p.is_demo_fixture,
        }
        for p in all_products
    ]
    catalog_for_gate = {
        p.id: CatalogProduct(
            id=p.id,
            name=p.name,
            price=p.price,
            category=p.category,
            stock=p.stock,
            is_active=p.is_active,
        )
        for p in all_products
    }

    logger.info(
        "Calling agent",
        extra={"session_id": str(session_id), "tier": current_tier.value},
    )
    proposed_items, llm_raw = await get_proposals(cart_items_data, catalog_dicts)
    logger.info(
        "Agent returned proposals",
        extra={"session_id": str(session_id), "count": len(proposed_items)},
    )

    await _write_audit(db, session_id, "agent.proposed", {
        "proposed_count": len(proposed_items),
        "proposed_ids": [i.product_id for i in proposed_items],
        "autonomy_tier": current_tier.value,
    })

    # Ensure session has a valid mandate (auto-issued on first proposal if missing)
    from app.services.mandate import (
        compute_mandate_fingerprint,
        create_mandate,
        mandate_to_dict,
        verify_mandate,
    )

    if session.mandate_payload is None:
        mandate_obj, signature = create_mandate(
            session_id=session.id,
            secret=settings.MANDATE_SECRET,
            max_cumulative_discount_pct=settings.MAX_DISCOUNT_BUDGET_PCT,
            max_items_per_proposal=settings.MAX_PROPOSALS_PER_CART,
            ttl_minutes=settings.MANDATE_TTL_MINUTES,
        )
        session.mandate_payload = mandate_to_dict(mandate_obj)
        session.mandate_signature = signature
        await _write_audit(db, session_id, "mandate.issued", {
            "mandate_fingerprint": compute_mandate_fingerprint(session.mandate_payload),
            "max_cumulative_discount_pct": float(mandate_obj.max_cumulative_discount_pct),
            "max_items": mandate_obj.max_items_per_proposal,
            "expires_at": mandate_obj.expires_at,
            "issued_at": mandate_obj.issued_at,
        })
        await db.commit()

    # Verify spend mandate (AP2 protocol checkpoint)
    mandate_fp = compute_mandate_fingerprint(session.mandate_payload)
    is_m_valid, m_reason = verify_mandate(
        mandate=session.mandate_payload,
        signature=session.mandate_signature,
        secret=settings.MANDATE_SECRET,
    )
    await _write_audit(db, session_id, "mandate.verified", {
        "mandate_fingerprint": mandate_fp,
        "is_valid": is_m_valid,
        "verification_result": m_reason,
    })

    config = PolicyConfig(
        max_discount_budget_pct=settings.MAX_DISCOUNT_BUDGET_PCT,
        max_proposals_per_cart=max_proposals,
        max_item_discount_pct=settings.MAX_ITEM_DISCOUNT_PCT,
        require_mandate=True,
        mandate_secret=settings.MANDATE_SECRET,
    )
    slogger.info(
        "DETAILED_LOG: What reached policy_gate.py",
        session_id=str(session_id),
        proposed_items_count=len(proposed_items),
        proposed_items=[
            {"product_id": i.product_id, "discount_pct": str(i.discount_pct)}
            for i in proposed_items
        ],
        llm_raw_output=llm_raw,
        cart_items=cart_items_data,
    )
    gate_result = run_gate(
        proposed_items=proposed_items,
        catalog=catalog_for_gate,
        cart_items=cart_items_data,
        session_budget_used_pct=session.discount_budget_used_pct,
        config=config,
        mandate=session.mandate_payload,
        mandate_signature=session.mandate_signature,
        mandate_secret=settings.MANDATE_SECRET,
    )
    slogger.info(
        "DETAILED_LOG: policy_gate.py evaluation results",
        session_id=str(session_id),
        accepted_count=len(gate_result.accepted_items),
        accepted_items=[
            {"product_id": a.product_id, "discount_pct": str(a.discount_pct)}
            for a in gate_result.accepted_items
        ],
        rejected_count=len(gate_result.rejected_items),
        rejected_items=[
            {"product_id": r.product_id, "reason": r.reason.value, "detail": r.detail}
            for r in gate_result.rejected_items
        ],
    )
    gate_label = _gate_result_label(
        len(gate_result.accepted_items),
        len(gate_result.rejected_items),
        mandate_valid=is_m_valid,
        mandate_reason=m_reason,
    )
    logger.info(
        "Gate decision",
        extra={
            "session_id": str(session_id),
            "gate_result": gate_label,
            "accepted": len(gate_result.accepted_items),
            "rejected": len(gate_result.rejected_items),
        },
    )

    record = ProposalRecord(
        gate_result=gate_label,
        rejected_reasons=[r.reason for r in gate_result.rejected_items],
    )
    score_result = compute_trust_score(
        record,
        current_score=float(session.trust_score),
    )

    session.trust_score = Decimal(f"{score_result.new_score:.2f}")
    await _write_audit(db, session_id, "trust_score.updated", {
        "old_score": score_result.old_score,
        "new_score": score_result.new_score,
        "delta": score_result.delta,
        "reason": score_result.reason.value,
        "detail": score_result.detail,
        "autonomy_tier": score_result.autonomy_tier.value,
    })

    effective_tier = score_result.autonomy_tier
    requires_review = effective_tier in (AutonomyTier.MEDIUM, AutonomyTier.LOW)
    initial_action = "review_required" if requires_review else "pending"

    accepted_for_db = [
        {"product_id": i.product_id, "discount_pct": str(i.discount_pct)}
        for i in gate_result.accepted_items
    ]
    rejected_for_db = [r.to_dict() for r in gate_result.rejected_items]

    proposal = Proposal(
        session_id=session_id,
        cart_snapshot=snapshot,
        llm_raw_output=llm_raw,
        proposed_items=[
            {"product_id": i.product_id, "discount_pct": str(i.discount_pct)}
            for i in proposed_items
        ],
        accepted_items=accepted_for_db,
        rejected_items=rejected_for_db,
        gate_result=gate_label,
        user_action=initial_action,
    )
    db.add(proposal)

    await _write_audit(db, session_id, "gate.decision", {
        "proposal_id": str(proposal.id),
        "gate_result": gate_label,
        "accepted_ids": [i.product_id for i in gate_result.accepted_items],
        "rejected_ids": [r.product_id for r in gate_result.rejected_items],
        "rejection_reasons": [r.to_dict() for r in gate_result.rejected_items],
        "autonomy_tier": effective_tier.value,
        "requires_review": requires_review,
        "counterfactual": {
            "proposed_count": len(proposed_items),
            "accepted_count": len(gate_result.accepted_items),
            "rejected_count": len(gate_result.rejected_items),
            "divergence_detected": len(gate_result.rejected_items) > 0,
        },
    })

    await db.commit()
    await db.refresh(proposal)

    product_map = {p.id: p for p in all_products}
    accepted_out = []
    for acc in gate_result.accepted_items:
        p = product_map.get(acc.product_id)
        if p:
            disc = acc.discount_pct / Decimal("100")
            accepted_out.append(AcceptedItemOut(
                product_id=acc.product_id,
                product_name=p.name,
                original_price=float(p.price),
                discount_pct=float(acc.discount_pct),
                discounted_price=float(p.price * (1 - disc)),
            ))

    rejected_out = [
        RejectedItemOut(
            product_id=r.product_id,
            proposed_discount_pct=float(r.proposed_discount_pct),
            reason=r.reason.value,
            detail=r.detail,
        )
        for r in gate_result.rejected_items
    ]

    llm_items_out = []
    for i in proposed_items:
        p_obj = product_map.get(i.product_id)
        p_name = p_obj.name if p_obj else f"Item #{i.product_id}"
        llm_items_out.append({
            "product_id": i.product_id,
            "product_name": p_name,
            "discount_pct": float(i.discount_pct),
        })

    if not is_m_valid:
        if m_reason == "mandate_expired":
            summary_msg = (
                "Evaluation blocked: AP2 spend mandate has expired. "
                "Reissue authorization to continue."
            )
        else:
            summary_msg = (
                "Evaluation blocked: Cryptographic spend mandate signature "
                "verification failed or tampered."
            )
    elif len(proposed_items) == 0:
        summary_msg = "Agent proposed 0 items; nothing to evaluate."
    elif len(rejected_out) > 0:
        summary_msg = (
            f"Policy gate intercepted proposals: {len(accepted_out)} allowed, "
            f"{len(rejected_out)} rejected."
        )
    else:
        summary_msg = f"Policy gate cleanly approved all {len(accepted_out)} proposed item(s)."

    counterfactual = CounterfactualComparison(
        llm_proposed_items=llm_items_out,
        gate_accepted_items=accepted_out,
        gate_rejected_items=rejected_out,
        divergence_detected=len(rejected_out) > 0,
        summary=summary_msg,
    )

    return ProposalOut(
        id=proposal.id,
        session_id=session_id,
        gate_result=gate_label,
        accepted_items=accepted_out,
        rejected_items=rejected_out,
        user_action=proposal.user_action,
        autonomy_tier=effective_tier.value,
        requires_review=requires_review,
        counterfactual=counterfactual,
        created_at=proposal.created_at,
    )


# ---------------------------------------------------------------------------
# POST /proposals/{session_id}/{proposal_id}/action
# ---------------------------------------------------------------------------
@router.post("/{session_id}/{proposal_id}/action", response_model=ProposalOut)
async def record_user_action(
    session_id: uuid.UUID,
    proposal_id: uuid.UUID,
    body: UserActionRequest,
    db: AsyncSession = Depends(get_db),
) -> ProposalOut:
    """Record user action: review step (if required) or final accept/decline."""
    if not body.is_valid():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid action '{body.action}'. Must be 'accepted', 'declined', or 'reviewed'",
        )

    result = await db.execute(
        select(Proposal).where(
            Proposal.id == proposal_id,
            Proposal.session_id == session_id,
        )
    )
    proposal = result.scalar_one_or_none()
    if proposal is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Proposal not found",
        )

    session_result = await db.execute(
        select(CartSession)
        .options(selectinload(CartSession.items).selectinload(CartItem.product))
        .where(CartSession.id == session_id)
    )
    session = session_result.scalar_one_or_none()
    current_tier = session.autonomy_tier if session else AutonomyTier.HIGH

    if proposal.user_action in ("accepted", "declined"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Proposal already actioned: {proposal.user_action}",
        )

    if proposal.user_action == "review_required":
        if body.action in ("reviewed", "confirm", "confirmed", "review"):
            proposal.user_action = "reviewed"
            proposal.acted_at = datetime.now(UTC)
            await _write_audit(db, session_id, "user.reviewed", {
                "proposal_id": str(proposal_id),
                "autonomy_tier": current_tier.value,
            })
            await db.commit()
            await db.refresh(proposal)
        elif body.action in ("accepted", "declined"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Proposal requires an explicit confirmation/review step "
                    f"under autonomy tier '{current_tier.value}' before being actionable."
                ),
            )
    elif proposal.user_action in ("pending", "reviewed"):
        if body.action in ("accepted", "declined"):
            proposal.user_action = body.action
            proposal.acted_at = datetime.now(UTC)

            if body.action == "accepted" and proposal.accepted_items and session:
                total_disc = sum(
                    Decimal(item["discount_pct"]) for item in proposal.accepted_items
                )
                session.discount_budget_used_pct += total_disc

            await _write_audit(db, session_id, f"user.{body.action}", {
                "proposal_id": str(proposal_id),
                "accepted_items": proposal.accepted_items,
            })
            await db.commit()
            await db.refresh(proposal)
        elif body.action in ("reviewed", "confirm", "confirmed", "review"):
            proposal.user_action = "reviewed"
            await db.commit()
            await db.refresh(proposal)

    catalog_result = await db.execute(select(Product))
    product_map = {p.id: p for p in catalog_result.scalars().all()}

    accepted_out = []
    for acc in proposal.accepted_items:
        p = product_map.get(acc["product_id"])
        if p:
            disc = Decimal(acc["discount_pct"]) / Decimal("100")
            accepted_out.append(AcceptedItemOut(
                product_id=acc["product_id"],
                product_name=p.name,
                original_price=float(p.price),
                discount_pct=float(acc["discount_pct"]),
                discounted_price=float(p.price * (1 - disc)),
            ))

    rejected_out = [
        RejectedItemOut(
            product_id=r["product_id"],
            proposed_discount_pct=float(r["proposed_discount_pct"]),
            reason=r["reason"],
            detail=r["detail"],
        )
        for r in proposal.rejected_items
    ]

    llm_items_out = []
    for i in proposal.proposed_items:
        pid = int(i["product_id"])
        p_obj = product_map.get(pid)
        p_name = p_obj.name if p_obj else f"Item #{pid}"
        llm_items_out.append({
            "product_id": pid,
            "product_name": p_name,
            "discount_pct": float(Decimal(str(i["discount_pct"]))),
        })

    if proposal.gate_result in ("mandate_invalid", "mandate_expired"):
        summary_msg = "Evaluation blocked: AP2 spend mandate verification failed."
    elif len(proposal.proposed_items) == 0:
        summary_msg = "Agent proposed 0 items; nothing to evaluate."
    elif len(rejected_out) > 0:
        summary_msg = (
            f"Policy gate intercepted proposals: {len(accepted_out)} allowed, "
            f"{len(rejected_out)} rejected."
        )
    else:
        summary_msg = f"Policy gate cleanly approved all {len(accepted_out)} proposed item(s)."

    counterfactual = CounterfactualComparison(
        llm_proposed_items=llm_items_out,
        gate_accepted_items=accepted_out,
        gate_rejected_items=rejected_out,
        divergence_detected=len(rejected_out) > 0,
        summary=summary_msg,
    )

    return ProposalOut(
        id=proposal.id,
        session_id=session_id,
        gate_result=proposal.gate_result,
        accepted_items=accepted_out,
        rejected_items=rejected_out,
        user_action=proposal.user_action,
        autonomy_tier=current_tier.value,
        requires_review=proposal.user_action == "review_required",
        counterfactual=counterfactual,
        created_at=proposal.created_at,
    )

