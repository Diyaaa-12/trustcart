"""
Audit router tests (backend/app/routers/audit.py).

Comprehensive test coverage targeting >= 90% coverage for:
  - GET /api/audit/{session_id} happy path
  - GET /api/audit/{session_id} empty state (session exists, 0 events)
  - GET /api/audit/{session_id} 404 for nonexistent session
  - GET /api/audit/{session_id}/replay happy path (covers all replay step categories)
  - GET /api/audit/{session_id}/replay empty state
  - GET /api/audit/{session_id}/replay 404 for nonexistent session
"""
import uuid
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from fastapi import HTTPException

from app.models.proposal import AuditLog
from app.routers.audit import get_session_replay, get_session_timeline
from tests.test_checkout import TestSessionLocal


@pytest_asyncio.fixture
async def audit_test_session(client):
    """Create a clean cart session in the test DB via the API."""
    res = await client.post("/api/cart")
    assert res.status_code == 201
    return uuid.UUID(res.json()["session_id"])


@pytest_asyncio.fixture
async def rich_event_session(client, audit_test_session):
    """Seed a session with every category and branch of audit events."""
    session_id = audit_test_session
    now = datetime.now(UTC)

    events_data = [
        # 1. cart.created
        ("cart.created", {"session_id": str(session_id)}),
        # 2. cart.item_added
        (
            "cart.item_added",
            {
                "product_id": 1,
                "product_name": "Wireless Headphones",
                "quantity": 2,
                "unit_price": 8999.0,
                "subtotal": 17998.0,
            },
        ),
        # 3. cart.item_removed
        ("cart.item_removed", {"product_id": 1}),
        # 4. agent.proposed
        (
            "agent.proposed",
            {
                "proposed_count": 2,
                "proposed_ids": [2, 3],
                "autonomy_tier": "high",
            },
        ),
        # 5. gate.decision - accepted
        (
            "gate.decision",
            {
                "gate_result": "accepted",
                "accepted_ids": [2],
                "rejected_ids": [],
                "rejection_reasons": [],
            },
        ),
        # 6. gate.decision - partial
        (
            "gate.decision",
            {
                "gate_result": "partial",
                "accepted_ids": [2],
                "rejected_ids": [3],
                "rejection_reasons": [{"reason": "item_discount_exceeded"}],
            },
        ),
        # 7. gate.decision - rejected
        (
            "gate.decision",
            {
                "gate_result": "rejected",
                "accepted_ids": [],
                "rejected_ids": [99],
                "rejection_reasons": [{"reason": "item_discount_exceeded"}],
            },
        ),
        # 8. trust_score.updated - gain (delta > 0)
        (
            "trust_score.updated",
            {
                "old_score": 98.0,
                "new_score": 100.0,
                "delta": 2.0,
                "reason": "clean_acceptance",
                "autonomy_tier": "high",
                "detail": "Proposal cleanly accepted.",
            },
        ),
        # 9. trust_score.updated - injection penalty (delta < 0, injection in reason)
        (
            "trust_score.updated",
            {
                "old_score": 100.0,
                "new_score": 85.0,
                "delta": -15.0,
                "reason": "rejection_injection_signal",
                "autonomy_tier": "high",
                "detail": "Adversarial injection signal detected.",
            },
        ),
        # 10. trust_score.updated - standard rejection (delta < 0, standard reason)
        (
            "trust_score.updated",
            {
                "old_score": 85.0,
                "new_score": 80.0,
                "delta": -5.0,
                "reason": "standard_rejection",
                "autonomy_tier": "high",
                "detail": "Policy gate rejected proposal.",
            },
        ),
        # 11. trust_score.updated - neutral (delta == 0)
        (
            "trust_score.updated",
            {
                "old_score": 80.0,
                "new_score": 80.0,
                "delta": 0.0,
                "reason": "neutral",
                "autonomy_tier": "high",
                "detail": "No change.",
            },
        ),
        # 12. user.reviewed
        ("user.reviewed", {"autonomy_tier": "medium"}),
        # 13. user.accepted
        ("user.accepted", {"proposal_id": str(uuid.uuid4())}),
        # 14. user.declined
        ("user.declined", {"proposal_id": str(uuid.uuid4())}),
        # 15. checkout.created - Live mode
        (
            "checkout.created",
            {
                "amount_paise": 1799800,
                "order_id": "order_live_123",
                "mock_mode": False,
            },
        ),
        # 16. checkout.created - Mock mode
        (
            "checkout.created",
            {
                "amount_paise": 50000,
                "order_id": "mock_order_trustcart_xyz",
                "mock_mode": True,
            },
        ),
        # 17. checkout.failed
        (
            "checkout.failed",
            {
                "error": "Payment provider timed out",
                "cart_preserved": True,
            },
        ),
        # 18. unknown system event
        ("system.maintenance", {"notice": "Catalog index refreshed"}),
    ]

    async with TestSessionLocal() as db:
        for etype, payload in events_data:
            entry = AuditLog(
                session_id=session_id,
                event_type=etype,
                payload=payload,
                created_at=now,
            )
            db.add(entry)
        await db.commit()

    return session_id


class TestAuditTimeline:
    @pytest.mark.asyncio
    async def test_get_timeline_404_nonexistent_session(self, client):
        """GET /api/audit/{session_id} returns 404 if session does not exist."""
        nonexistent = uuid.uuid4()
        res = await client.get(f"/api/audit/{nonexistent}")
        assert res.status_code == 404
        assert res.json()["detail"] == "Cart session not found"

        # Direct router handler invocation
        async with TestSessionLocal() as db:
            with pytest.raises(HTTPException) as exc_info:
                await get_session_timeline(nonexistent, db)
            assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_get_timeline_empty_state(self, client, audit_test_session):
        """GET /api/audit/{session_id} on a brand new session with no events."""
        session_id = audit_test_session
        res = await client.get(f"/api/audit/{session_id}")
        assert res.status_code == 200
        data = res.json()

        assert data["session_id"] == str(session_id)
        assert data["total_events"] == 0
        assert data["events"] == []
        assert data["trust_score_history"] == []
        assert data["replay_steps"] == []
        assert data["current_trust_score"] == 100.0
        assert data["current_autonomy_tier"] == "high"

        # Direct router handler invocation
        async with TestSessionLocal() as db:
            timeline = await get_session_timeline(session_id, db)
            assert timeline.total_events == 0
            assert timeline.events == []

    @pytest.mark.asyncio
    async def test_get_timeline_happy_path(self, client, rich_event_session):
        """GET /api/audit/{session_id} returns populated timeline and trust score history."""
        session_id = rich_event_session
        res = await client.get(f"/api/audit/{session_id}")
        assert res.status_code == 200
        data = res.json()

        assert data["session_id"] == str(session_id)
        assert data["total_events"] == 18
        assert len(data["events"]) == 18

        # Verify trust score history extracted specifically from trust_score.updated events
        assert len(data["trust_score_history"]) == 4
        assert data["trust_score_history"][0]["delta"] == 2.0
        assert data["trust_score_history"][1]["delta"] == -15.0
        assert data["trust_score_history"][2]["delta"] == -5.0
        assert data["trust_score_history"][3]["delta"] == 0.0

        # Verify replay_steps populated on timeline
        assert len(data["replay_steps"]) == 18

        # Direct router handler invocation
        async with TestSessionLocal() as db:
            timeline = await get_session_timeline(session_id, db)
            assert timeline.total_events == 18
            assert len(timeline.trust_score_history) == 4


class TestAuditReplay:
    @pytest.mark.asyncio
    async def test_get_replay_404_nonexistent_session(self, client):
        """GET /api/audit/{session_id}/replay returns 404 if session does not exist."""
        nonexistent = uuid.uuid4()
        res = await client.get(f"/api/audit/{nonexistent}/replay")
        assert res.status_code == 404
        assert res.json()["detail"] == "Cart session not found"

        # Direct router handler invocation
        async with TestSessionLocal() as db:
            with pytest.raises(HTTPException) as exc_info:
                await get_session_replay(nonexistent, db)
            assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_get_replay_empty_state(self, client, audit_test_session):
        """GET /api/audit/{session_id}/replay on a session with no recorded events."""
        session_id = audit_test_session
        res = await client.get(f"/api/audit/{session_id}/replay")
        assert res.status_code == 200
        data = res.json()

        assert data["session_id"] == str(session_id)
        assert data["total_steps"] == 0
        assert data["steps"] == []
        assert data["current_trust_score"] == 100.0
        assert data["current_autonomy_tier"] == "high"

        # Direct router handler invocation
        async with TestSessionLocal() as db:
            replay = await get_session_replay(session_id, db)
            assert replay.total_steps == 0
            assert replay.steps == []

    @pytest.mark.asyncio
    async def test_get_replay_happy_path_all_categories(self, client, rich_event_session):
        """
        GET /api/audit/{session_id}/replay reconstructs full sequential narrative
        covering every step category and status value.
        """
        session_id = rich_event_session
        res = await client.get(f"/api/audit/{session_id}/replay")
        assert res.status_code == 200
        data = res.json()

        assert data["session_id"] == str(session_id)
        assert data["total_steps"] == 18
        steps = data["steps"]

        # Step 1: cart.created
        assert steps[0]["category"] == "cart"
        assert steps[0]["title"] == "Shopping Session Initialized"
        assert steps[0]["status"] == "info"

        # Step 2: cart.item_added
        assert steps[1]["category"] == "cart"
        assert "Item Added to Cart" in steps[1]["title"]
        assert "Wireless Headphones" in steps[1]["summary"]

        # Step 3: cart.item_removed
        assert steps[2]["category"] == "cart"
        assert steps[2]["title"] == "Item Removed from Cart"

        # Step 4: agent.proposed
        assert steps[3]["category"] == "agent"
        assert "LLM Agent Generated 2 Recommendation(s)" in steps[3]["title"]

        # Step 5: gate.decision - accepted
        assert steps[4]["category"] == "gate"
        assert steps[4]["title"] == "Policy Gate: Fully Approved"
        assert steps[4]["status"] == "success"

        # Step 6: gate.decision - partial
        assert steps[5]["category"] == "gate"
        assert "Partial Acceptance" in steps[5]["title"]
        assert steps[5]["status"] == "warning"

        # Step 7: gate.decision - rejected
        assert steps[6]["category"] == "gate"
        assert steps[6]["title"] == "Policy Gate: Rejected All Proposals"
        assert steps[6]["status"] == "danger"

        # Step 8: trust_score.updated - positive delta
        assert steps[7]["category"] == "trust"
        assert steps[7]["status"] == "success"
        assert "+2" in steps[7]["title"]

        # Step 9: trust_score.updated - injection penalty
        assert steps[8]["category"] == "trust"
        assert steps[8]["status"] == "danger"
        assert "-15" in steps[8]["title"]

        # Step 10: trust_score.updated - standard negative delta
        assert steps[9]["category"] == "trust"
        assert steps[9]["status"] == "warning"
        assert "-5" in steps[9]["title"]

        # Step 11: trust_score.updated - zero delta
        assert steps[10]["category"] == "trust"
        assert steps[10]["status"] == "info"

        # Step 12: user.reviewed
        assert steps[11]["category"] == "user"
        assert steps[11]["title"] == "User Confirmation & Review Completed"

        # Step 13: user.accepted
        assert steps[12]["category"] == "user"
        assert steps[12]["title"] == "User Accepted Upsell Proposal"
        assert steps[12]["status"] == "success"

        # Step 14: user.declined
        assert steps[13]["category"] == "user"
        assert steps[13]["title"] == "User Declined Recommendation"

        # Step 15: checkout.created (live)
        assert steps[14]["category"] == "checkout"
        assert "Live Mode" in steps[14]["title"]
        assert steps[14]["status"] == "success"

        # Step 16: checkout.created (mock)
        assert steps[15]["category"] == "checkout"
        assert "Mock Mode" in steps[15]["title"]
        assert steps[15]["status"] == "success"

        # Step 17: checkout.failed
        assert steps[16]["category"] == "checkout"
        assert "Checkout Failed (Cart Preserved)" in steps[16]["title"]
        assert steps[16]["status"] == "danger"

        # Step 18: system fallback
        assert steps[17]["category"] == "system"
        assert steps[17]["title"] == "System Maintenance"

        # Direct router handler invocation
        async with TestSessionLocal() as db:
            replay = await get_session_replay(session_id, db)
            assert replay.total_steps == 18
            assert replay.steps[0].category == "cart"
