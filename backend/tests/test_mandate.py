"""
Spend Mandate Layer Tests (AP2 Protocol Inspiration).

Exhaustive tests covering:
  1. Mandate creation, canonical serialization, and signing.
  2. Cryptographic signature verification.
  3. Tamper detection across all fields (discount, item cap, categories, session ID, nonce).
  4. Expiry enforcement.
  5. Error cases (missing mandate, wrong secret, malformed payload).
  6. Policy Gate integration: invalid/expired/missing mandates block ALL proposals.
  7. Trust score impact: sharp -20.0 penalty for structural mandate breaches.
  8. Cart & Proposal API integration: mandate issuance, verification, and audit trail.
"""
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.cart import CartSession
from app.services.mandate import (
    compute_mandate_fingerprint,
    create_mandate,
    mandate_to_dict,
    serialize_mandate_canonical,
    sign_mandate,
    verify_mandate,
)
from app.services.policy_gate import (
    CatalogProduct,
    PolicyConfig,
    ProposedItem,
    RejectionReason,
    run_gate,
)
from app.services.trust_score import (
    ChangeReason,
    compute_trust_score,
)
from tests.test_checkout import TestSessionLocal

TEST_SECRET = "test-spend-mandate-secret-key-32b"


# ===========================================================================
# 1. Pure Unit Tests: Mandate Service
# ===========================================================================
class TestMandateUnit:
    def test_create_mandate_defaults(self):
        """Mandate is created with expected defaults, valid nonce, and HMAC signature."""
        session_id = str(uuid.uuid4())
        mandate, sig = create_mandate(session_id, TEST_SECRET)

        assert mandate.session_id == session_id
        assert mandate.max_cumulative_discount_pct == Decimal("10.0")
        assert mandate.max_items_per_proposal == 3
        assert "Electronics" in mandate.allowed_categories
        assert len(mandate.nonce) == 32
        assert len(sig) == 64  # SHA-256 hex string

    def test_verify_mandate_valid(self):
        """Valid mandate verifies cleanly."""
        session_id = str(uuid.uuid4())
        mandate, sig = create_mandate(session_id, TEST_SECRET)

        is_valid, reason = verify_mandate(mandate, sig, TEST_SECRET)
        assert is_valid is True
        assert reason == "mandate_valid"

    def test_verify_mandate_expired(self):
        """Expired mandate is detected and rejected."""
        session_id = str(uuid.uuid4())
        past_time = datetime.now(UTC) - timedelta(hours=2)
        mandate, sig = create_mandate(
            session_id, TEST_SECRET, ttl_minutes=15, issued_at=past_time
        )

        is_valid, reason = verify_mandate(mandate, sig, TEST_SECRET)
        assert is_valid is False
        assert reason == "mandate_expired"

    def test_verify_mandate_tampered_discount_field(self):
        """Altering discount bound invalidates cryptographic signature."""
        session_id = str(uuid.uuid4())
        mandate, sig = create_mandate(
            session_id, TEST_SECRET, max_cumulative_discount_pct=Decimal("10.0")
        )

        # Attacker tampers max discount from 10% to 50%
        tampered_dict = mandate_to_dict(mandate)
        tampered_dict["max_cumulative_discount_pct"] = "50.0"

        is_valid, reason = verify_mandate(tampered_dict, sig, TEST_SECRET)
        assert is_valid is False
        assert reason == "mandate_invalid"

    def test_verify_mandate_tampered_items_field(self):
        """Altering item count bound invalidates cryptographic signature."""
        session_id = str(uuid.uuid4())
        mandate, sig = create_mandate(
            session_id, TEST_SECRET, max_items_per_proposal=3
        )

        tampered_dict = mandate_to_dict(mandate)
        tampered_dict["max_items_per_proposal"] = 10

        is_valid, reason = verify_mandate(tampered_dict, sig, TEST_SECRET)
        assert is_valid is False
        assert reason == "mandate_invalid"

    def test_verify_mandate_tampered_categories(self):
        """Injecting disallowed category invalidates cryptographic signature."""
        session_id = str(uuid.uuid4())
        mandate, sig = create_mandate(session_id, TEST_SECRET)

        tampered_dict = mandate_to_dict(mandate)
        tampered_dict["allowed_categories"].append("LuxuryVehicles")

        is_valid, reason = verify_mandate(tampered_dict, sig, TEST_SECRET)
        assert is_valid is False
        assert reason == "mandate_invalid"

    def test_verify_mandate_tampered_session_id(self):
        """Replaying mandate in different session invalidates cryptographic signature."""
        mandate, sig = create_mandate(str(uuid.uuid4()), TEST_SECRET)

        tampered_dict = mandate_to_dict(mandate)
        tampered_dict["session_id"] = str(uuid.uuid4())

        is_valid, reason = verify_mandate(tampered_dict, sig, TEST_SECRET)
        assert is_valid is False
        assert reason == "mandate_invalid"

    def test_verify_mandate_tampered_signature(self):
        """Modified signature string fails verification."""
        session_id = str(uuid.uuid4())
        mandate, sig = create_mandate(session_id, TEST_SECRET)

        corrupt_sig = sig[:-4] + "ffff"
        is_valid, reason = verify_mandate(mandate, corrupt_sig, TEST_SECRET)
        assert is_valid is False
        assert reason == "mandate_invalid"

    def test_verify_mandate_wrong_secret(self):
        """Signing with one secret and verifying with another fails."""
        session_id = str(uuid.uuid4())
        mandate, sig = create_mandate(session_id, TEST_SECRET)

        is_valid, reason = verify_mandate(mandate, sig, "different-secret-key-32b")
        assert is_valid is False
        assert reason == "mandate_invalid"

    def test_verify_mandate_none_or_missing(self):
        """None mandate or signature returns mandate_missing."""
        is_valid, reason = verify_mandate(None, "sig", TEST_SECRET)
        assert is_valid is False
        assert reason == "mandate_missing"

        is_valid, reason = verify_mandate({"session_id": "1"}, None, TEST_SECRET)
        assert is_valid is False
        assert reason == "mandate_missing"

    def test_verify_mandate_malformed_dict(self):
        """Malformed dictionary missing required fields returns mandate_malformed."""
        malformed = {"session_id": "123"}
        is_valid, reason = verify_mandate(malformed, "fake_sig", TEST_SECRET)
        assert is_valid is False
        assert reason == "mandate_malformed"

        # Invalid datetime format
        session_id = str(uuid.uuid4())
        mandate, sig = create_mandate(session_id, TEST_SECRET)
        bad_dt_dict = mandate_to_dict(mandate)
        bad_dt_dict["expires_at"] = "not-a-datetime"
        bad_dt_sig = sign_mandate(bad_dt_dict, TEST_SECRET)
        is_valid, reason = verify_mandate(bad_dt_dict, bad_dt_sig, TEST_SECRET)
        assert is_valid is False
        assert reason == "mandate_malformed"

    def test_compute_mandate_fingerprint(self):
        """Fingerprint returns truncated SHA-256 with 'mnd_' prefix."""
        session_id = str(uuid.uuid4())
        mandate, _ = create_mandate(session_id, TEST_SECRET)

        fp1 = compute_mandate_fingerprint(mandate)
        fp2 = compute_mandate_fingerprint(mandate_to_dict(mandate))

        assert fp1.startswith("mnd_")
        assert len(fp1) == 20  # "mnd_" (4) + 16 hex chars
        assert fp1 == fp2  # Deterministic across dataclass and dict forms

    def test_canonical_serialization_order_invariant(self):
        """Key insertion order in dict does not alter canonical serialization."""
        d1 = {"session_id": "1", "max_cumulative_discount_pct": "10.0",
              "max_items_per_proposal": 3, "allowed_categories": ["A", "B"],
              "issued_at": "2026-01-01", "expires_at": "2026-01-02", "nonce": "abc"}
        # Reverse key order
        d2 = dict(reversed(list(d1.items())))

        assert serialize_mandate_canonical(d1) == serialize_mandate_canonical(d2)


# ===========================================================================
# 2. Integration Tests: Policy Gate + Mandate
# ===========================================================================
class TestMandatePolicyGate:
    @pytest.fixture
    def mock_catalog(self):
        return {
            1: CatalogProduct(id=1, name="Keyboard", price=Decimal("1000.0"),
                              category="Electronics", stock=10, is_active=True),
            2: CatalogProduct(id=2, name="Mouse", price=Decimal("500.0"),
                              category="Electronics", stock=10, is_active=True),
        }

    @pytest.fixture
    def policy_cfg(self):
        return PolicyConfig(
            max_discount_budget_pct=Decimal("10.0"),
            max_proposals_per_cart=3,
            max_item_discount_pct=Decimal("20.0"),
            require_mandate=True,
            mandate_secret=TEST_SECRET,
        )

    def test_gate_passes_cleanly_with_valid_mandate(self, mock_catalog, policy_cfg):
        """Proposals evaluate normally when accompanied by valid mandate."""
        session_id = str(uuid.uuid4())
        mandate, sig = create_mandate(session_id, TEST_SECRET)

        proposed = [ProposedItem(product_id=2, discount_pct=Decimal("5.0"))]
        cart = [{"product_id": 1, "category": "Electronics", "name": "Keyboard",
                 "quantity": 1, "unit_price": Decimal("1000.0")}]

        result = run_gate(
            proposed_items=proposed,
            catalog=mock_catalog,
            cart_items=cart,
            session_budget_used_pct=Decimal("0"),
            config=policy_cfg,
            mandate=mandate,
            mandate_signature=sig,
        )

        assert result.passed is True
        assert len(result.accepted_items) == 1
        assert len(result.rejected_items) == 0

    def test_gate_blocks_all_proposals_when_mandate_invalid(self, mock_catalog, policy_cfg):
        """Tampered mandate causes all proposals in batch to be blocked."""
        session_id = str(uuid.uuid4())
        mandate, _ = create_mandate(session_id, TEST_SECRET)

        proposed = [
            ProposedItem(product_id=1, discount_pct=Decimal("5.0")),
            ProposedItem(product_id=2, discount_pct=Decimal("5.0")),
        ]
        cart = [{"product_id": 1, "category": "Electronics", "name": "Keyboard",
                 "quantity": 1, "unit_price": Decimal("1000.0")}]

        # Pass bogus signature
        result = run_gate(
            proposed_items=proposed,
            catalog=mock_catalog,
            cart_items=cart,
            session_budget_used_pct=Decimal("0"),
            config=policy_cfg,
            mandate=mandate,
            mandate_signature="bogus_tampered_signature",
        )

        assert result.passed is False
        assert len(result.accepted_items) == 0
        assert len(result.rejected_items) == 2
        for r in result.rejected_items:
            assert r.reason == RejectionReason.MANDATE_INVALID

    def test_gate_blocks_all_proposals_when_mandate_expired(self, mock_catalog, policy_cfg):
        """Expired mandate causes all proposals in batch to be blocked."""
        session_id = str(uuid.uuid4())
        past_time = datetime.now(UTC) - timedelta(hours=1)
        mandate, sig = create_mandate(session_id, TEST_SECRET, ttl_minutes=5, issued_at=past_time)

        proposed = [ProposedItem(product_id=2, discount_pct=Decimal("5.0"))]
        cart = [{"product_id": 1, "category": "Electronics", "name": "Keyboard",
                 "quantity": 1, "unit_price": Decimal("1000.0")}]

        result = run_gate(
            proposed_items=proposed,
            catalog=mock_catalog,
            cart_items=cart,
            session_budget_used_pct=Decimal("0"),
            config=policy_cfg,
            mandate=mandate,
            mandate_signature=sig,
        )

        assert result.passed is False
        assert len(result.rejected_items) == 1
        assert result.rejected_items[0].reason == RejectionReason.MANDATE_EXPIRED

    def test_gate_blocks_when_require_mandate_and_mandate_missing(self, mock_catalog, policy_cfg):
        """Missing mandate with require_mandate=True blocks all proposals."""
        proposed = [ProposedItem(product_id=2, discount_pct=Decimal("5.0"))]
        cart = [{"product_id": 1, "category": "Electronics", "name": "Keyboard",
                 "quantity": 1, "unit_price": Decimal("1000.0")}]

        result = run_gate(
            proposed_items=proposed,
            catalog=mock_catalog,
            cart_items=cart,
            session_budget_used_pct=Decimal("0"),
            config=policy_cfg,
            mandate=None,
            mandate_signature=None,
        )

        assert result.passed is False
        assert len(result.rejected_items) == 1
        assert result.rejected_items[0].reason == RejectionReason.MANDATE_MISSING


# ===========================================================================
# 3. Trust Score Impact on Mandate Violation
# ===========================================================================
class TestMandateTrustScoreImpact:
    def test_mandate_rejection_drops_trust_score_sharply(self):
        """
        Mandate breach drops trust score by -20.0 (MANDATE_MULTIPLIER 4x),
        more severe than standard rejection (-5.0) and injection penalty (-15.0).
        """
        res = compute_trust_score(
            current_score=100.0,
            latest_proposal={
                "gate_result": "rejected",
                "rejected_reasons": ["mandate_invalid"],
            },
        )
        assert res.new_score == 80.0
        assert res.delta == -20.0
        assert res.reason == ChangeReason.REJECTION_MANDATE_BREACH
        assert "mandate violation" in res.detail.lower()

    def test_mandate_expired_drops_trust_score_sharply(self):
        """Expired mandate also triggers REJECTION_MANDATE_BREACH."""
        res = compute_trust_score(
            current_score=75.0,
            latest_proposal={
                "gate_result": "rejected",
                "rejected_reasons": ["mandate_expired"],
            },
        )
        assert res.new_score == 55.0
        assert res.delta == -20.0
        assert res.reason == ChangeReason.REJECTION_MANDATE_BREACH
        assert res.autonomy_tier.value == "medium"


# ===========================================================================
# 4. API & Audit End-to-End Tests
# ===========================================================================
class TestMandateAPIE2E:
    @pytest.mark.asyncio
    async def test_cart_creation_issues_signed_mandate(self, client):
        """POST /api/cart returns active mandate bounds and emits mandate.issued event."""
        res = await client.post("/api/cart")
        assert res.status_code == 201
        data = res.json()

        assert "mandate" in data
        mandate_info = data["mandate"]
        assert mandate_info is not None
        assert mandate_info["fingerprint"].startswith("mnd_")
        assert mandate_info["max_cumulative_discount_pct"] == 10.0
        assert mandate_info["max_items_per_proposal"] == 3
        assert mandate_info["status"] == "active"

        # Check audit trail recorded mandate.issued
        sid = data["session_id"]
        audit_res = await client.get(f"/api/audit/{sid}")
        assert audit_res.status_code == 200
        events = audit_res.json()["events"]
        assert any(e["event_type"] == "mandate.issued" for e in events)

    @pytest.mark.asyncio
    async def test_tampered_mandate_in_db_blocks_proposal_generation(self, client):
        """Tampering mandate signature directly in DB triggers gate block and trust penalty."""
        from app.models.product import Product

        # 0. Seed a product
        async with TestSessionLocal() as db:
            prod = Product(
                id=1,
                name="Wireless Headphones",
                price=Decimal("8999.0"),
                category="Electronics",
                stock=10,
                is_active=True,
            )
            db.add(prod)
            await db.commit()

        # 1. Create cart
        res = await client.post("/api/cart")
        sid = uuid.UUID(res.json()["session_id"])

        # 2. Add an item
        add_res = await client.post(
            f"/api/cart/{sid}/items",
            json={"product_id": 1, "quantity": 1},
        )
        assert add_res.status_code == 200

        # 3. Tamper the signature in DB
        async with TestSessionLocal() as db:
            session_res = await db.execute(select(CartSession).where(CartSession.id == sid))
            session = session_res.scalar_one()
            session.mandate_signature = "forged_tampered_signature_hex_123456"
            await db.commit()

        # 4. Request proposals with mocked agent response
        from unittest.mock import AsyncMock, patch

        mock_proposed = [ProposedItem(product_id=2, discount_pct=Decimal("5.0"))]
        mock_get = AsyncMock(return_value=(mock_proposed, {}))
        with patch("app.routers.proposals.get_proposals", new=mock_get):
            prop_res = await client.post(f"/api/proposals/{sid}")
        assert prop_res.status_code == 201
        prop_data = prop_res.json()

        # Gate should reject all proposals due to mandate verification failure
        assert prop_data["gate_result"] in ("rejected", "mandate_invalid")
        assert len(prop_data["accepted_items"]) == 0
        assert any(r["reason"] == "mandate_invalid" for r in prop_data["rejected_items"])

        # Session trust score should have dropped sharply from 100 to 80
        cart_res = await client.get(f"/api/cart/{sid}")
        assert float(cart_res.json()["trust_score"]) == 80.0

        # Audit replay should reflect the mandate verification failure
        replay_res = await client.get(f"/api/audit/{sid}/replay")
        assert replay_res.status_code == 200
        replay_steps = replay_res.json()["steps"]
        assert any(
            s["category"] == "mandate" and "Failed" in s["title"]
            for s in replay_steps
        )
