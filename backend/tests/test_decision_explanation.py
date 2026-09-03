"""
Comprehensive tests for Feature B:
Plain-language decision explanation generator and endpoint:
GET /api/audit/{session_id}/explain/{proposal_id}
"""
import uuid
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.database import Base, get_db
from app.main import app
from app.models.cart import CartSession
from app.models.product import Product
from app.models.proposal import AuditLog, Proposal
from app.routers.audit import explain_proposal_decision
from app.services.explanation import (
    DecisionExplanationOut,
    build_decision_explanation,
)
from app.services.policy_gate import RejectionReason
from tests.test_checkout import TestSessionLocal, test_engine


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c



async def override_get_db():
    async with TestSessionLocal() as session:
        yield session

app.dependency_overrides[get_db] = override_get_db

@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


class TestPureDecisionExplanation:
    """Unit tests verifying deterministic text generation across all scenarios."""

    def test_clean_acceptance_narrative(self):
        result = build_decision_explanation(
            proposal_id="p-123",
            session_id="s-456",
            gate_result="accepted",
            proposed_items=[{"product_id": 1, "discount_pct": "10.0"}],
            accepted_items=[{"product_id": 1, "discount_pct": "10.0"}],
            rejected_items=[],
            product_names={1: "Sony WH-1000XM5"},
            old_score=98.0,
            new_score=100.0,
            score_delta=2.0,
            old_autonomy_tier="high",
            new_autonomy_tier="high",
            mandate_fingerprint="mnd_abc12345",
            mandate_verified=True,
        )
        assert result.gate_result == "accepted"
        assert "Sony WH-1000XM5 at 10.0% off" in result.explanation
        assert "mandate was verified (mnd_abc12345)" in result.explanation
        assert "increased from 98.0 to 100.0 (+2.0 pts)" in result.explanation
        assert "maintaining HIGH autonomy" in result.explanation
        assert result.mandate_verified is True
        assert len(result.factors) >= 2

    def test_item_discount_exceeded_and_injection_pattern(self):
        result = build_decision_explanation(
            proposal_id="p-inj",
            session_id="s-456",
            gate_result="rejected",
            proposed_items=[{"product_id": 99, "discount_pct": "90.0"}],
            accepted_items=[],
            rejected_items=[
                {
                    "product_id": 99,
                    "proposed_discount_pct": 90.0,
                    "reason": RejectionReason.ITEM_DISCOUNT_EXCEEDED.value,
                    "detail": "Discount 90% exceeds 10%",
                }
            ],
            product_names={99: "Compromised Speaker SKU"},
            old_score=100.0,
            new_score=85.0,
            score_delta=-15.0,
            old_autonomy_tier="high",
            new_autonomy_tier="medium",
            mandate_max_discount_pct=10.0,
        )
        assert result.gate_result == "rejected"
        assert "Compromised Speaker SKU at 90.0% off" in result.explanation
        assert "exceeded the session's mandate-authorized maximum of 10.0%" in result.explanation
        assert "dropped from 100.0 to 85.0 (-15.0 pts)" in result.explanation
        assert "moving it from HIGH to MEDIUM autonomy" in result.explanation

    def test_mandate_breach_narrative(self):
        result = build_decision_explanation(
            proposal_id="p-mnd",
            session_id="s-456",
            gate_result="rejected",
            proposed_items=[{"product_id": 1, "discount_pct": "5.0"}],
            accepted_items=[],
            rejected_items=[
                {
                    "product_id": 1,
                    "proposed_discount_pct": 5.0,
                    "reason": RejectionReason.MANDATE_INVALID.value,
                    "detail": "HMAC signature mismatch",
                }
            ],
            product_names={1: "Sony Headphones"},
            old_score=90.0,
            new_score=70.0,
            score_delta=-20.0,
            old_autonomy_tier="high",
            new_autonomy_tier="high",
            mandate_verified=False,
            mandate_failure_reason="HMAC signature mismatch",
        )
        assert result.mandate_verified is False
        assert "cryptographic mandate verification failed" in result.explanation
        assert "dropped from 90.0 to 70.0 (-20.0 pts)" in result.explanation
        assert result.summary == "Proposal rejected: spend mandate verification failed."

    def test_out_of_stock_narrative(self):
        result = build_decision_explanation(
            proposal_id="p-oos",
            session_id="s-1",
            gate_result="rejected",
            proposed_items=[{"product_id": 2, "discount_pct": "10.0"}],
            accepted_items=[],
            rejected_items=[
                {
                    "product_id": 2,
                    "proposed_discount_pct": 10.0,
                    "reason": RejectionReason.PRODUCT_OUT_OF_STOCK.value,
                }
            ],
            product_names={2: "Wireless Mouse"},
            old_score=80.0,
            new_score=75.0,
            score_delta=-5.0,
            old_autonomy_tier="high",
            new_autonomy_tier="high",
        )
        assert "0 inventory units in stock" in result.explanation
        assert "Wireless Mouse" in result.explanation

    def test_product_not_in_catalog_narrative(self):
        result = build_decision_explanation(
            proposal_id="p-nic",
            session_id="s-1",
            gate_result="rejected",
            proposed_items=[{"product_id": 9999, "discount_pct": "5.0"}],
            accepted_items=[],
            rejected_items=[
                {
                    "product_id": 9999,
                    "proposed_discount_pct": 5.0,
                    "reason": RejectionReason.PRODUCT_NOT_IN_CATALOG.value,
                }
            ],
            product_names={},
            old_score=80.0,
            new_score=65.0,
            score_delta=-15.0,
            old_autonomy_tier="high",
            new_autonomy_tier="medium",
        )
        assert "does not exist in the active catalog" in result.explanation
        assert "Product #9999" in result.explanation

    def test_product_inactive_narrative(self):
        result = build_decision_explanation(
            proposal_id="p-ina",
            session_id="s-1",
            gate_result="rejected",
            proposed_items=[{"product_id": 3, "discount_pct": "5.0"}],
            accepted_items=[],
            rejected_items=[
                {
                    "product_id": 3,
                    "proposed_discount_pct": 5.0,
                    "reason": RejectionReason.PRODUCT_INACTIVE.value,
                }
            ],
            product_names={3: "Discontinued Cable"},
            old_score=80.0,
            new_score=75.0,
            score_delta=-5.0,
        )
        assert "deactivated in the merchant catalog" in result.explanation

    def test_category_not_allowed_narrative(self):
        result = build_decision_explanation(
            proposal_id="p-cat",
            session_id="s-1",
            gate_result="rejected",
            proposed_items=[{"product_id": 4, "discount_pct": "5.0"}],
            accepted_items=[],
            rejected_items=[
                {
                    "product_id": 4,
                    "proposed_discount_pct": 5.0,
                    "reason": RejectionReason.CATEGORY_NOT_ALLOWED.value,
                }
            ],
            product_names={4: "Winter Scarf"},
            old_score=85.0,
            new_score=70.0,
            score_delta=-15.0,
        )
        assert "not an authorized cross-sell destination" in result.explanation

    def test_already_in_cart_narrative(self):
        result = build_decision_explanation(
            proposal_id="p-aic",
            session_id="s-1",
            gate_result="rejected",
            proposed_items=[{"product_id": 5, "discount_pct": "5.0"}],
            accepted_items=[],
            rejected_items=[
                {
                    "product_id": 5,
                    "proposed_discount_pct": 5.0,
                    "reason": RejectionReason.ALREADY_IN_CART.value,
                }
            ],
            product_names={5: "USB Cable"},
            old_score=80.0,
            new_score=75.0,
            score_delta=-5.0,
        )
        assert "already present in your cart" in result.explanation

    def test_session_budget_exceeded_narrative(self):
        result = build_decision_explanation(
            proposal_id="p-bud",
            session_id="s-1",
            gate_result="rejected",
            proposed_items=[{"product_id": 6, "discount_pct": "15.0"}],
            accepted_items=[],
            rejected_items=[
                {
                    "product_id": 6,
                    "proposed_discount_pct": 15.0,
                    "reason": RejectionReason.SESSION_BUDGET_EXCEEDED.value,
                }
            ],
            product_names={6: "Premium Adapter"},
            old_score=80.0,
            new_score=75.0,
            score_delta=-5.0,
        )
        assert "exceed the session's cumulative discount budget" in result.explanation

    def test_negative_discount_narrative(self):
        result = build_decision_explanation(
            proposal_id="p-neg",
            session_id="s-1",
            gate_result="rejected",
            proposed_items=[{"product_id": 7, "discount_pct": "-5.0"}],
            accepted_items=[],
            rejected_items=[
                {
                    "product_id": 7,
                    "proposed_discount_pct": -5.0,
                    "reason": RejectionReason.NEGATIVE_DISCOUNT.value,
                }
            ],
            product_names={7: "Glitched Item"},
            old_score=80.0,
            new_score=75.0,
            score_delta=-5.0,
        )
        assert "invalid negative discount" in result.explanation

    def test_proposal_count_exceeded_narrative(self):
        result = build_decision_explanation(
            proposal_id="p-cnt",
            session_id="s-1",
            gate_result="rejected",
            proposed_items=[{"product_id": 8, "discount_pct": "5.0"}],
            accepted_items=[],
            rejected_items=[
                {
                    "product_id": 8,
                    "proposed_discount_pct": 5.0,
                    "reason": RejectionReason.PROPOSAL_COUNT_EXCEEDED.value,
                }
            ],
            product_names={8: "Extra Item"},
            old_score=80.0,
            new_score=75.0,
            score_delta=-5.0,
        )
        assert "exceeding the maximum allowed item count" in result.explanation

    def test_partial_acceptance_narrative(self):
        result = build_decision_explanation(
            proposal_id="p-part",
            session_id="s-1",
            gate_result="partial",
            proposed_items=[
                {"product_id": 1, "discount_pct": "10.0"},
                {"product_id": 2, "discount_pct": "50.0"},
            ],
            accepted_items=[{"product_id": 1, "discount_pct": "10.0"}],
            rejected_items=[
                {
                    "product_id": 2,
                    "proposed_discount_pct": 50.0,
                    "reason": "item_discount_exceeded",
                }
            ],
            product_names={1: "Keyboard", 2: "Mouse"},
            old_score=100.0,
            new_score=85.0,
            score_delta=-15.0,
            old_autonomy_tier="high",
            new_autonomy_tier="medium",
        )
        assert "accepted Keyboard (10.0% off)" in result.explanation
        assert "rejecting Mouse (item_discount_exceeded)" in result.explanation
        assert "moving it from HIGH to MEDIUM autonomy" in result.explanation


class TestExplainEndpointIntegration:
    """Integration tests for GET /api/audit/{session_id}/explain/{proposal_id}."""

    @pytest.mark.asyncio
    async def test_explain_endpoint_happy_path(self, client):
        session_id = uuid.uuid4()
        proposal_id = uuid.uuid4()

        async with TestSessionLocal() as db:
            p = Product(
                id=201,
                name="Ergonomic Mouse",
                description="Comfortable mouse",
                price=Decimal("1500.00"),
                category="Accessories",
                stock=20,
                is_active=True,
                is_demo_fixture=False,
            )
            sess = CartSession(
                id=session_id,
                discount_budget_used_pct=Decimal("0.0"),
                trust_score=Decimal("95.0"),
                                mandate_payload={
                    "session_id": str(session_id),
                    "max_cumulative_discount_pct": "10.0",
                    "max_items_per_proposal": 3,
                    "allowed_categories": ["Accessories"],
                    "issued_at": "2026-09-03T00:00:00Z",
                    "expires_at": "2026-09-03T01:00:00Z",
                    "nonce": "n123",
                },
                mandate_signature="sig123",
            )
            prop = Proposal(
                id=proposal_id,
                session_id=session_id,
                cart_snapshot={"items": []},
                proposed_items=[{"product_id": 201, "discount_pct": "5.0"}],
                accepted_items=[{"product_id": 201, "discount_pct": "5.0"}],
                rejected_items=[],
                gate_result="accepted",
                user_action="pending",
            )
            event_gate = AuditLog(
                session_id=session_id,
                event_type="gate.decision",
                payload={"proposal_id": str(proposal_id), "gate_result": "accepted"},
            )
            event_trust = AuditLog(
                session_id=session_id,
                event_type="trust_score.updated",
                payload={
                    "old_score": 93.0,
                    "new_score": 95.0,
                    "delta": 2.0,
                    "autonomy_tier": "high",
                },
            )
            db.add_all([p, sess, prop, event_gate, event_trust])
            await db.commit()

        async with TestSessionLocal() as db:
            direct_result = await explain_proposal_decision(session_id, proposal_id, db)
            assert direct_result.proposal_id == str(proposal_id)
            assert direct_result.gate_result == "accepted"
            assert "Ergonomic Mouse at 5.0% off" in direct_result.explanation
            assert direct_result.old_score == 93.0
            assert direct_result.new_score == 95.0

        response = await client.get(f"/api/audit/{session_id}/explain/{proposal_id}")
        assert response.status_code == 200
        data = response.json()

        parsed = DecisionExplanationOut(**data)
        assert parsed.proposal_id == str(proposal_id)
        assert parsed.session_id == str(session_id)
        assert parsed.gate_result == "accepted"
        assert "Ergonomic Mouse at 5.0% off" in parsed.explanation
        assert parsed.old_score == 93.0
        assert parsed.new_score == 95.0
        assert parsed.score_delta == 2.0
        assert parsed.mandate_verified is True

    @pytest.mark.asyncio
    async def test_explain_endpoint_404_session_not_found(self, client):
        nonexistent_s = uuid.uuid4()
        nonexistent_p = uuid.uuid4()
        async with TestSessionLocal() as db:
            from fastapi import HTTPException
            with pytest.raises(HTTPException) as exc:
                await explain_proposal_decision(nonexistent_s, nonexistent_p, db)
            assert exc.value.status_code == 404
            assert exc.value.detail == "Cart session not found"

        res = await client.get(f"/api/audit/{nonexistent_s}/explain/{nonexistent_p}")
        assert res.status_code == 404
        assert res.json()["detail"] == "Cart session not found"

    @pytest.mark.asyncio
    async def test_explain_endpoint_404_proposal_not_found(self, client):
        session_id = uuid.uuid4()
        async with TestSessionLocal() as db:
            sess = CartSession(
                id=session_id,
                discount_budget_used_pct=Decimal("0.0"),
                trust_score=Decimal("100.0"),
                            )
            db.add(sess)
            await db.commit()

        res = await client.get(f"/api/audit/{session_id}/explain/{uuid.uuid4()}")
        assert res.status_code == 404
        assert res.json()["detail"] == "Proposal not found"

    @pytest.mark.asyncio
    async def test_explain_endpoint_404_proposal_mismatch(self, client):
        session_id1 = uuid.uuid4()
        session_id2 = uuid.uuid4()
        proposal_id = uuid.uuid4()

        async with TestSessionLocal() as db:
            s1 = CartSession(
                id=session_id1,
                discount_budget_used_pct=Decimal("0.0"),
                trust_score=Decimal("100.0"),
                            )
            s2 = CartSession(
                id=session_id2,
                discount_budget_used_pct=Decimal("0.0"),
                trust_score=Decimal("100.0"),
                            )
            prop = Proposal(
                id=proposal_id,
                session_id=session_id2,
                cart_snapshot={},
                proposed_items=[],
                accepted_items=[],
                rejected_items=[],
                gate_result="accepted",
                user_action="pending",
            )
            db.add_all([s1, s2, prop])
            await db.commit()

        res = await client.get(f"/api/audit/{session_id1}/explain/{proposal_id}")
        assert res.status_code == 404
        assert res.json()["detail"] == "Proposal does not belong to session"
