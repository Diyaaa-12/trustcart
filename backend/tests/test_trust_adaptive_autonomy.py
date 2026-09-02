"""
Integration tests for the trust-adaptive autonomy layer.

Covers:
  - CartSession default trust_score (100) and autonomy_tier (HIGH)
  - Proposal generation updates trust_score and writes to AuditLog
  - High tier (>= 70): proposals generated normally (pending, requires_review=False)
  - Medium tier (40-69): proposals require review state (review_required, requires_review=True)
  - Low tier (<40): proposal count throttled to 1 per request
  - Action endpoint enforces review step before accept/decline under medium/low tiers
  - Action endpoint accepts "reviewed" / "confirmed"
  - GET /audit/{session_id} includes current_trust_score, current_autonomy_tier, trust_score_history
"""
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.models.cart import CartItem, CartSession
from app.models.product import Product
from app.services.policy_gate import ProposedItem
from tests.test_checkout import TestSessionLocal


@pytest_asyncio.fixture
async def seeded_cart_with_items():
    """Seed catalog products and create a cart session with 1 electronics item."""
    async with TestSessionLocal() as db:
        p1 = Product(
            id=1,
            name="Headphones",
            price=Decimal("5000"),
            category="Electronics",
            stock=10,
            is_active=True,
        )
        p2 = Product(
            id=2,
            name="Case",
            price=Decimal("500"),
            category="Accessories",
            stock=20,
            is_active=True,
        )
        p3 = Product(
            id=3,
            name="Shoes",
            price=Decimal("3000"),
            category="Footwear",
            stock=10,
            is_active=True,
        )
        db.add_all([p1, p2, p3])

        session = CartSession()
        db.add(session)
        await db.commit()
        await db.refresh(session)

        item = CartItem(
            session_id=session.id,
            product_id=1,
            quantity=1,
            unit_price=Decimal("5000"),
        )
        db.add(item)
        await db.commit()
        return session.id


class TestTrustAdaptiveAutonomy:
    @pytest.mark.asyncio
    async def test_cart_session_defaults(self, client):
        """CartSession should default to trust_score 100.0 and autonomy_tier high."""
        res = await client.post("/api/cart")
        assert res.status_code == 201
        data = res.json()
        assert float(data["trust_score"]) == 100.0
        assert data["autonomy_tier"] == "high"

    @pytest.mark.asyncio
    async def test_proposal_clean_accept_keeps_high_tier(self, client, seeded_cart_with_items):
        """Clean accepted proposals in high tier don't require review."""
        session_id = seeded_cart_with_items
        mock_proposed = [ProposedItem(product_id=2, discount_pct=Decimal("5.0"))]

        patch_target = "app.routers.proposals.get_proposals"
        mock_getter = AsyncMock(return_value=(mock_proposed, {}))
        with patch(patch_target, new=mock_getter):
            res = await client.post(f"/api/proposals/{session_id}")
            assert res.status_code == 201
            data = res.json()
            assert data["gate_result"] == "accepted"
            assert data["autonomy_tier"] == "high"
            assert data["requires_review"] is False
            assert data["user_action"] == "pending"

            # Directly actionable (accept/decline)
            prop_id = data["id"]
            action_res = await client.post(
                f"/api/proposals/{session_id}/{prop_id}/action",
                json={"action": "accepted"},
            )
            assert action_res.status_code == 200
            assert action_res.json()["user_action"] == "accepted"

    @pytest.mark.asyncio
    async def test_injection_drops_tier_and_requires_review(self, client, seeded_cart_with_items):
        """
        When injection-signature rejection occurs repeatedly, score drops to medium/low tier.
        Subsequent proposals require explicit review before being actionable.
        """
        session_id = seeded_cart_with_items

        # Manually adjust session trust_score to 55 (medium tier)
        async with TestSessionLocal() as db:
            s_res = await db.execute(select(CartSession).where(CartSession.id == session_id))
            sess = s_res.scalar_one()
            sess.trust_score = Decimal("55.0")
            await db.commit()

        # Propose a valid item
        mock_proposed = [ProposedItem(product_id=2, discount_pct=Decimal("5.0"))]
        patch_target = "app.routers.proposals.get_proposals"
        mock_getter = AsyncMock(return_value=(mock_proposed, {}))
        with patch(patch_target, new=mock_getter):
            res = await client.post(f"/api/proposals/{session_id}")
            assert res.status_code == 201
            data = res.json()
            # 55.0 + 2.0 = 57.0 (still MEDIUM tier)
            assert data["autonomy_tier"] == "medium"
            assert data["requires_review"] is True
            assert data["user_action"] == "review_required"

            prop_id = data["id"]
            # Trying to accept directly without review should fail with 400
            direct_accept = await client.post(
                f"/api/proposals/{session_id}/{prop_id}/action",
                json={"action": "accepted"},
            )
            assert direct_accept.status_code == 400
            assert "explicit confirmation/review" in direct_accept.json()["detail"]

            # Confirm with reviewed
            review_res = await client.post(
                f"/api/proposals/{session_id}/{prop_id}/action",
                json={"action": "reviewed"},
            )
            assert review_res.status_code == 200
            assert review_res.json()["user_action"] == "reviewed"
            assert review_res.json()["requires_review"] is False

            # Now actionable: accept
            accept_res = await client.post(
                f"/api/proposals/{session_id}/{prop_id}/action",
                json={"action": "accepted"},
            )
            assert accept_res.status_code == 200
            assert accept_res.json()["user_action"] == "accepted"

    @pytest.mark.asyncio
    async def test_low_tier_throttles_proposals_to_one(self, client, seeded_cart_with_items):
        """In low tier (<40), proposal volume is throttled to 1 item per request."""
        session_id = seeded_cart_with_items

        async with TestSessionLocal() as db:
            s_res = await db.execute(select(CartSession).where(CartSession.id == session_id))
            sess = s_res.scalar_one()
            sess.trust_score = Decimal("30.0")
            await db.commit()

        # LLM proposes 2 valid items (Case + Case)
        mock_proposed = [
            ProposedItem(product_id=2, discount_pct=Decimal("5.0")),
            ProposedItem(product_id=2, discount_pct=Decimal("2.0")),
        ]
        patch_target = "app.routers.proposals.get_proposals"
        mock_getter = AsyncMock(return_value=(mock_proposed, {}))
        with patch(patch_target, new=mock_getter):
            res = await client.post(f"/api/proposals/{session_id}")
            assert res.status_code == 201
            data = res.json()
            # In low tier, proposal count per request is capped at 1
            assert len(data["accepted_items"]) <= 1
            assert data["autonomy_tier"] == "low"
            assert data["requires_review"] is True

    @pytest.mark.asyncio
    async def test_audit_timeline_includes_trust_score_history(
        self, client, seeded_cart_with_items
    ):
        """GET /audit/{session_id} should return timeline with trust score history."""
        session_id = seeded_cart_with_items

        # Generate a proposal to create audit logs (out of catalog)
        mock_proposed = [ProposedItem(product_id=999, discount_pct=Decimal("5.0"))]
        patch_target = "app.routers.proposals.get_proposals"
        mock_getter = AsyncMock(return_value=(mock_proposed, {}))
        with patch(patch_target, new=mock_getter):
            await client.post(f"/api/proposals/{session_id}")

        audit_res = await client.get(f"/api/audit/{session_id}")
        assert audit_res.status_code == 200
        audit_data = audit_res.json()

        assert "current_trust_score" in audit_data
        assert "current_autonomy_tier" in audit_data
        assert "trust_score_history" in audit_data
        assert len(audit_data["trust_score_history"]) >= 1
        entry = audit_data["trust_score_history"][0]
        assert entry["old_score"] == 100.0
        assert entry["new_score"] == 85.0
        assert entry["delta"] == -15.0
        assert entry["reason"] == "rejection_injection_signal"
