"""
Proposals router -- agent call + gate + persistence + trust scoring.

Flow:
  POST /api/proposals/{session_id}
    1. Load cart from DB (server-side -- client cannot inject cart state)
    2. Check session autonomy tier -- if LOW, throttle max proposals to 1
    3. Load full catalog from DB
    4. Call agent -> LLM proposes items (may include bad items due to injection)
    5. Run policy gate -> pure deterministic validation
    6. Update session trust score & autonomy tier based on gate result
    7. Write trust_score.updated audit event if score changed
    8. Determine if proposal requires review based on autonomy tier
    9. Persist Proposal row with full audit data
    10. Write gate audit events
    11. Return accepted items + rejected summary + autonomy state to client

  POST /api/proposals/{session_id}/{proposal_id}/action
    Record user action:
    - If proposal in "review_required": user must confirm ("reviewed") before actionable
    - If proposal in "pending" or "reviewed": user can accept or decline
"""
import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database import get_db
from app.models.cart import CartItem, CartSession
from app.models.product import Product
from app.models.proposal import AuditLog, Proposal
from app.schemas.proposal import AcceptedItemOut, ProposalOut, RejectedItemOut, UserActionRequest
from app.services.agent import get_proposals
from app.services.policy_gate import CatalogProduct, PolicyConfig, ProposedItem, run_gate
from app.services.trust_score import (
    AutonomyTier,
    ProposalRecord,
    compute_trust_score,
)

logger = logging.getLogger(__name__)
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
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cart session not found")
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


def _gate_result_label(accepted: int, rejected: int) -> str:
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
    """Run the agent -> gate pipeline and return safe proposals."""
    session = await _get_session_or_404(session_id, db)

    if not session.items:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Cannot generate proposals for an empty cart",
        )

    # Autonomy tier check before generation:
    # Low tier (<40) throttles proposal count per request to 1
    current_tier = session.autonomy_tier
    max_proposals = 1 if current_tier == AutonomyTier.LOW else settings.MAX_PROPOSALS_PER_CART

    # 1. Build cart context
    cart_items_data = _cart_items_to_dicts(session)
    snapshot = _cart_snapshot(session)

    # 2. Load full catalog for agent and gate
    catalog_result = await db.execute(
        select(Product).where(Product.is_active == True)  # noqa: E712
    )
    all_products = catalog_result.scalars().all()

    catalog_dicts = [
        {
            "id": p.id,
            "name": p.name,
            "description": p.description,
            "price": float(p.price),
            "category": p.category,
            "stock": p.stock,
            "is_demo_fixture": p.is_demo_fixture,
        }
        for p in all_products
    ]
    catalog_for_gate: dict[int, CatalogProduct] = {
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

    # 3. Call agent (LLM)
    logger.info("Calling agent", extra={"session_id": str(session_id), "tier": current_tier.value})
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

    # 4. Run policy gate (pure, deterministic -- no I/O)
    # Hard policy caps (discount %, item count, category rules) NEVER change based on tier.
    # Tier only throttles proposal volume in LOW tier.
    config = PolicyConfig(
        max_discount_budget_pct=settings.MAX_DISCOUNT_BUDGET_PCT,
        max_proposals_per_cart=max_proposals,
        max_item_discount_pct=settings.MAX_ITEM_DISCOUNT_PCT,
    )
    gate_result = run_gate(
        proposed_items=proposed_items,
        catalog=catalog_for_gate,
        cart_items=cart_items_data,
        session_budget_used_pct=session.discount_budget_used_pct,
        config=config,
    )

    gate_label = _gate_result_label(len(gate_result.accepted_items), len(gate_result.rejected_items))
    logger.info(
        "Gate decision",
        extra={
            "session_id": str(session_id),
            "gate_result": gate_label,
            "accepted": len(gate_result.accepted_items),
            "rejected": len(gate_result.rejected_items),
        },
    )

    # 5. Update Trust Score
    rejection_reasons = [r.reason.value for r in gate_result.rejected_items]
    proposal_record = ProposalRecord(
        gate_result=gate_label,
        rejected_reasons=rejection_reasons,
    )
    old_trust = float(session.trust_score)
    trust_res = compute_trust_score(
        proposal_history=proposal_record,
        current_score=old_trust,
    )
    session.trust_score = Decimal(str(round(trust_res.new_score, 2)))

    # Every trust score change is written to AuditLog with old, new, and reason
    if trust_res.delta != 0.0 or old_trust != trust_res.new_score:
        await _write_audit(db, session_id, "trust_score.updated", {
            "old_score": trust_res.old_score,
            "new_score": trust_res.new_score,
            "delta": trust_res.delta,
            "reason": trust_res.reason.value,
            "detail": trust_res.detail,
            "autonomy_tier": trust_res.autonomy_tier.value,
        })

    # Read autonomy tier BEFORE showing any proposal to the user:
    # - high tier (score >= 70): agent proposes normally
    # - medium tier (40-69): proposals require explicit confirmation ("review_required")
    # - low tier (<40): all proposals require confirmation
    effective_tier = trust_res.autonomy_tier
    requires_review = effective_tier in (AutonomyTier.MEDIUM, AutonomyTier.LOW)
    initial_action = "review_required" if requires_review else "pending"

    # 6. Build persistence payloads
    accepted_for_db = [
        {"product_id": i.product_id, "discount_pct": str(i.discount_pct)}
        for i in gate_result.accepted_items
    ]
    rejected_for_db = [r.to_dict() for r in gate_result.rejected_items]

    # 7. Persist proposal
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

    # 8. Write gate audit events
    await _write_audit(db, session_id, "gate.decision", {
        "proposal_id": str(proposal.id),
        "gate_result": gate_label,
        "accepted_ids": [i.product_id for i in gate_result.accepted_items],
        "rejected_ids": [r.product_id for r in gate_result.rejected_items],
        "rejection_reasons": [r.to_dict() for r in gate_result.rejected_items],
        "autonomy_tier": effective_tier.value,
        "requires_review": requires_review,
    })

    await db.commit()
    await db.refresh(proposal)

    # 9. Build response (enrich accepted items with product names/prices)
    product_map = {p.id: p for p in all_products}
    accepted_out = []
    for acc in gate_result.accepted_items:
        p = product_map[acc.product_id]
        disc = acc.discount_pct / Decimal("100")
        accepted_out.append(AcceptedItemOut(
            product_id=acc.product_id,
            product_name=p.name,
            original_price=p.price,
            discount_pct=acc.discount_pct,
            discounted_price=p.price * (1 - disc),
        ))

    rejected_out = [
        RejectedItemOut(
            product_id=r.product_id,
            proposed_discount_pct=r.proposed_discount_pct,
            reason=r.reason.value,
            detail=r.detail,
        )
        for r in gate_result.rejected_items
    ]

    return ProposalOut(
        id=proposal.id,
        session_id=session_id,
        gate_result=gate_label,
        accepted_items=accepted_out,
        rejected_items=rejected_out,
        user_action=proposal.user_action,
        autonomy_tier=effective_tier.value,
        requires_review=requires_review,
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
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Proposal not found")

    session_result = await db.execute(
        select(CartSession).where(CartSession.id == session_id)
    )
    session = session_result.scalar_one_or_none()
    current_tier = session.autonomy_tier if session else AutonomyTier.HIGH

    # Check if already completed
    if proposal.user_action in ("accepted", "declined"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Proposal already actioned: {proposal.user_action}",
        )

    # If proposal requires review step before being actionable
    if proposal.user_action == "review_required":
        if body.action in ("reviewed", "confirm", "confirmed", "review"):
            proposal.user_action = "reviewed"
            proposal.acted_at = datetime.now(timezone.utc)
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
            proposal.acted_at = datetime.now(timezone.utc)

            # If user accepted, update session discount budget
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
            # Already reviewed or pending -- keep as reviewed
            proposal.user_action = "reviewed"
            await db.commit()
            await db.refresh(proposal)

    # Re-build response
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
                original_price=p.price,
                discount_pct=Decimal(acc["discount_pct"]),
                discounted_price=p.price * (1 - disc),
            ))

    rejected_out = [
        RejectedItemOut(
            product_id=r["product_id"],
            proposed_discount_pct=Decimal(r["proposed_discount_pct"]),
            reason=r["reason"],
            detail=r["detail"],
        )
        for r in proposal.rejected_items
    ]

    return ProposalOut(
        id=proposal.id,
        session_id=session_id,
        gate_result=proposal.gate_result,
        accepted_items=accepted_out,
        rejected_items=rejected_out,
        user_action=proposal.user_action,
        autonomy_tier=current_tier.value,
        requires_review=(proposal.user_action == "review_required"),
        created_at=proposal.created_at,
    )
