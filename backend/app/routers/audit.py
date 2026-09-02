"""Audit router -- session timeline and trust score history."""
import logging
import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.cart import CartSession
from app.models.proposal import AuditLog
from app.schemas.proposal import AuditEventOut, SessionTimelineOut, TrustScoreHistoryEntry

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("/{session_id}", response_model=SessionTimelineOut)
async def get_session_timeline(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> SessionTimelineOut:
    """
    Return the full ordered event timeline and trust score history for a session.

    Events are in chronological order so the caller can reconstruct exactly
    what happened: proposals generated, gate decisions, user actions, checkout,
    and trust score changes.
    """
    # 1. Fetch current session for trust score & autonomy tier
    session_res = await db.execute(
        select(CartSession).where(CartSession.id == session_id)
    )
    session = session_res.scalar_one_or_none()
    current_trust = float(session.trust_score) if session else 100.0
    current_tier = session.autonomy_tier.value if session else "high"

    # 2. Fetch all audit logs ordered chronologically
    result = await db.execute(
        select(AuditLog)
        .where(AuditLog.session_id == session_id)
        .order_by(AuditLog.created_at.asc())
    )
    events = result.scalars().all()

    # 3. Extract trust score history
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

    logger.info(
        "Audit timeline fetched",
        extra={
            "session_id": str(session_id),
            "events": len(events),
            "trust_history_count": len(trust_history),
        },
    )

    return SessionTimelineOut(
        session_id=session_id,
        events=event_outs,
        total_events=len(event_outs),
        current_trust_score=current_trust,
        current_autonomy_tier=current_tier,
        trust_score_history=trust_history,
    )
