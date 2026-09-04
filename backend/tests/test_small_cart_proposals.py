"""
Regression tests for small-cart candidate selection, cross-sell mapping,
and atomic cart item quantity updates (PATCH).
"""
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.database import Base, get_db
from app.main import app
from app.models.cart import CartItem, CartSession
from app.models.product import Product
from app.services.policy_gate import (
    CatalogProduct,
    PolicyConfig,
    ProposedItem,
    RejectionReason,
    run_gate,
)
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


class TestSmallCartCandidateSelection:
    """Unit tests for small/partial cart candidate selection and category mapping."""

    @pytest.fixture
    def policy_config(self):
        return PolicyConfig(
            max_discount_budget_pct=Decimal("10.0"),
            max_proposals_per_cart=3,
            max_item_discount_pct=Decimal("20.0"),
        )

    @pytest.fixture
    def sample_catalog(self):
        return {
            1: CatalogProduct(id=1, name="Headphones", price=Decimal("5000"), category="Electronics", stock=10, is_active=True),
            2: CatalogProduct(id=2, name="Laptop Sleeve", price=Decimal("900"), category="Accessories", stock=50, is_active=True),
            3: CatalogProduct(id=3, name="Clean Code", price=Decimal("1200"), category="Books", stock=30, is_active=True),
            4: CatalogProduct(id=4, name="T-Shirt", price=Decimal("800"), category="Clothing", stock=40, is_active=True),
            5: CatalogProduct(id=5, name="Running Shoes", price=Decimal("3500"), category="Footwear", stock=25, is_active=True),
        }

    def test_single_item_electronics_cart_accepts_cross_sell_candidates(
        self, policy_config, sample_catalog
    ):
        """
        A fresh cart with exactly 1 Electronics item must cleanly accept candidates
        from mapped categories (Accessories, Books) as well as same-category items.
        """
        cart_items = [
            {"product_id": 1, "name": "Headphones", "category": "Electronics", "quantity": 1, "unit_price": 5000.0}
        ]

        # Propose 1 Accessories (ID 2) and 1 Books (ID 3)
        proposed = [
            ProposedItem(product_id=2, discount_pct=Decimal("5.0")),
            ProposedItem(product_id=3, discount_pct=Decimal("5.0")),
        ]

        result = run_gate(
            proposed_items=proposed,
            catalog=sample_catalog,
            cart_items=cart_items,
            session_budget_used_pct=Decimal("0.0"),
            config=policy_config,
        )

        assert result.passed is True
        assert len(result.accepted_items) == 2
        assert len(result.rejected_items) == 0
        accepted_ids = [i.product_id for i in result.accepted_items]
        assert accepted_ids == [2, 3]

    def test_already_in_cart_does_not_exclude_unpurchased_items(
        self, policy_config, sample_catalog
    ):
        """
        Confirm 'already in cart' exclusion only excludes the specific cart item ID (1),
        not unpurchased candidates (2, 3).
        """
        cart_items = [
            {"product_id": 1, "name": "Headphones", "category": "Electronics", "quantity": 1, "unit_price": 5000.0}
        ]

        proposed = [
            ProposedItem(product_id=1, discount_pct=Decimal("5.0")),  # in cart
            ProposedItem(product_id=2, discount_pct=Decimal("5.0")),  # not in cart
        ]

        result = run_gate(
            proposed_items=proposed,
            catalog=sample_catalog,
            cart_items=cart_items,
            session_budget_used_pct=Decimal("0.0"),
            config=policy_config,
        )

        assert len(result.accepted_items) == 1
        assert result.accepted_items[0].product_id == 2
        assert len(result.rejected_items) == 1
        assert result.rejected_items[0].product_id == 1
        assert result.rejected_items[0].reason == RejectionReason.ALREADY_IN_CART

    def test_disallowed_category_rejected_for_single_category_cart(
        self, policy_config, sample_catalog
    ):
        """
        For a cart with only Electronics, Clothing is not an authorized cross-sell.
        """
        cart_items = [
            {"product_id": 1, "name": "Headphones", "category": "Electronics", "quantity": 1, "unit_price": 5000.0}
        ]

        proposed = [
            ProposedItem(product_id=4, discount_pct=Decimal("5.0")),  # Clothing
        ]

        result = run_gate(
            proposed_items=proposed,
            catalog=sample_catalog,
            cart_items=cart_items,
            session_budget_used_pct=Decimal("0.0"),
            config=policy_config,
        )

        assert len(result.accepted_items) == 0
        assert len(result.rejected_items) == 1
        assert result.rejected_items[0].reason == RejectionReason.CATEGORY_NOT_ALLOWED

    def test_full_cart_all_catalog_items_in_cart_results_in_no_proposals(
        self, policy_config, sample_catalog
    ):
        """
        When all catalog items are already present in the cart, all proposals are
        rejected with ALREADY_IN_CART, producing 0 accepted items.
        """
        cart_items = [
            {"product_id": pid, "name": p.name, "category": p.category, "quantity": 1, "unit_price": float(p.price)}
            for pid, p in sample_catalog.items()
        ]

        proposed = [
            ProposedItem(product_id=1, discount_pct=Decimal("5.0")),
            ProposedItem(product_id=2, discount_pct=Decimal("5.0")),
        ]

        result = run_gate(
            proposed_items=proposed,
            catalog=sample_catalog,
            cart_items=cart_items,
            session_budget_used_pct=Decimal("0.0"),
            config=policy_config,
        )

        assert result.passed is False
        assert len(result.accepted_items) == 0
        assert all(r.reason == RejectionReason.ALREADY_IN_CART for r in result.rejected_items)


class TestSmallCartAndQuantityEndpoints:
    """Integration tests for small cart proposals and PATCH quantity endpoint."""

    @pytest_asyncio.fixture
    async def seeded_catalog(self):
        async with TestSessionLocal() as db:
            p1 = Product(id=1, name="Headphones", price=Decimal("5000"), category="Electronics", stock=20, is_active=True)
            p2 = Product(id=2, name="Laptop Sleeve", price=Decimal("900"), category="Accessories", stock=50, is_active=True)
            p3 = Product(id=3, name="Clean Code", price=Decimal("1200"), category="Books", stock=30, is_active=True)
            db.add_all([p1, p2, p3])
            await db.commit()

    @pytest.mark.asyncio
    async def test_small_cart_proposal_endpoint_happy_path(self, client, seeded_catalog):
        """
        Test explicitly: fresh cart, add exactly ONE item (Electronics),
        request proposals -> candidates exist and are accepted.
        """
        # 1. Create cart
        cart_res = await client.post("/api/cart")
        assert cart_res.status_code == 201
        session_id = cart_res.json()["session_id"]

        # 2. Add 1 Electronics item
        add_res = await client.post(f"/api/cart/{session_id}/items", json={"product_id": 1, "quantity": 1})
        assert add_res.status_code == 200
        cart_data = add_res.json()
        assert len(cart_data["items"]) == 1
        assert cart_data["items"][0]["product_id"] == 1

        # 3. Request proposals with complementary item from Accessories
        mock_proposed = [ProposedItem(product_id=2, discount_pct=Decimal("8.0"))]
        with patch("app.routers.proposals.get_proposals", new=AsyncMock(return_value=(mock_proposed, {}))):
            prop_res = await client.post(f"/api/proposals/{session_id}")
            assert prop_res.status_code == 201
            prop_data = prop_res.json()
            assert prop_data["gate_result"] == "accepted"
            assert len(prop_data["accepted_items"]) == 1
            assert prop_data["accepted_items"][0]["product_id"] == 2
            assert prop_data["accepted_items"][0]["discount_pct"] == 8.0
            assert len(prop_data["rejected_items"]) == 0

    @pytest.mark.asyncio
    async def test_patch_quantity_updates_atomically(self, client, seeded_catalog):
        """
        Test PATCH /api/cart/{session_id}/items/{product_id}
        supports updating quantity up, down, and removing when quantity is 0.
        """
        # 1. Create cart and add item
        cart_res = await client.post("/api/cart")
        session_id = cart_res.json()["session_id"]
        await client.post(f"/api/cart/{session_id}/items", json={"product_id": 1, "quantity": 1})

        # 2. Update quantity to 4
        patch_res = await client.patch(
            f"/api/cart/{session_id}/items/1",
            json={"quantity": 4},
        )
        assert patch_res.status_code == 200
        data = patch_res.json()
        assert data["item_count"] == 4
        assert data["items"][0]["quantity"] == 4
        assert data["items"][0]["line_total"] == 20000.0
        assert data["subtotal"] == 20000.0

        # 3. Decrement quantity to 3
        patch_res2 = await client.patch(
            f"/api/cart/{session_id}/items/1",
            json={"quantity": 3},
        )
        assert patch_res2.status_code == 200
        assert patch_res2.json()["items"][0]["quantity"] == 3

        # 4. Decrement to 0 -> removes item from cart
        patch_res3 = await client.patch(
            f"/api/cart/{session_id}/items/1",
            json={"quantity": 0},
        )
        assert patch_res3.status_code == 200
        assert len(patch_res3.json()["items"]) == 0
        assert patch_res3.json()["item_count"] == 0
        assert patch_res3.json()["subtotal"] == 0.0

    @pytest.mark.asyncio
    async def test_patch_quantity_nonexistent_item_returns_404(self, client, seeded_catalog):
        """PATCH on an item not in the cart returns 404."""
        cart_res = await client.post("/api/cart")
        session_id = cart_res.json()["session_id"]

        patch_res = await client.patch(
            f"/api/cart/{session_id}/items/999",
            json={"quantity": 2},
        )
        assert patch_res.status_code == 404
    @pytest.mark.asyncio
    async def test_expired_mandate_classified_as_mandate_expired_not_no_proposals(
        self, client, seeded_catalog
    ):
        """
        Regression test: When a mandate is expired, requesting proposals
        must NEVER be classified as 'no_proposals' (neutral state).
        It must be an explicit mandate failure state ('mandate_expired').
        """
        from datetime import UTC, datetime, timedelta
        import uuid
        from app.services.mandate import sign_mandate
        from app.config import settings

        # 1. Create cart and add item
        cart_res = await client.post("/api/cart")
        sid = uuid.UUID(cart_res.json()["session_id"])
        await client.post(f"/api/cart/{sid}/items", json={"product_id": 1, "quantity": 1})

        # 2. Deliberately expire mandate in DB
        async with TestSessionLocal() as db:
            s_res = await db.execute(select(CartSession).where(CartSession.id == sid))
            sess = s_res.scalar_one()
            payload = dict(sess.mandate_payload)
            payload["expires_at"] = (datetime.now(UTC) - timedelta(minutes=60)).isoformat()
            sess.mandate_payload = payload
            sess.mandate_signature = sign_mandate(payload, settings.MANDATE_SECRET)
            await db.commit()

        # 3. Request proposals (even if agent proposed 0 items)
        with patch("app.routers.proposals.get_proposals", new=AsyncMock(return_value=([], {}))):
            prop_res = await client.post(f"/api/proposals/{sid}")
            assert prop_res.status_code == 201
            prop_data = prop_res.json()

            # Must NOT be 'no_proposals'
            assert prop_data["gate_result"] != "no_proposals"
            assert prop_data["gate_result"] == "mandate_expired"
            assert len(prop_data["accepted_items"]) == 0
            assert "nothing to evaluate" not in prop_data["counterfactual"]["summary"].lower()
            assert "mandate" in prop_data["counterfactual"]["summary"].lower()

    @pytest.mark.asyncio
    async def test_mandate_refresh_endpoint_preserves_cart_and_restores_authorization(
        self, client, seeded_catalog
    ):
        """
        POST /api/cart/{session_id}/mandate/refresh reissues a valid mandate
        for the SAME session without losing cart items, logging mandate.reissued.
        """
        from datetime import UTC, datetime, timedelta
        import uuid
        from app.services.mandate import sign_mandate
        from app.config import settings

        # 1. Create cart and add 2 items
        cart_res = await client.post("/api/cart")
        sid = uuid.UUID(cart_res.json()["session_id"])
        await client.post(f"/api/cart/{sid}/items", json={"product_id": 1, "quantity": 2})

        # 2. Expire mandate
        async with TestSessionLocal() as db:
            s_res = await db.execute(select(CartSession).where(CartSession.id == sid))
            sess = s_res.scalar_one()
            payload = dict(sess.mandate_payload)
            payload["expires_at"] = (datetime.now(UTC) - timedelta(minutes=60)).isoformat()
            sess.mandate_payload = payload
            sess.mandate_signature = sign_mandate(payload, settings.MANDATE_SECRET)
            await db.commit()

        # 3. Refresh mandate on same cart session
        refresh_res = await client.post(f"/api/cart/{sid}/mandate/refresh")
        assert refresh_res.status_code == 200
        refreshed_data = refresh_res.json()
        assert refreshed_data["session_id"] == str(sid)
        assert len(refreshed_data["items"]) == 1
        assert refreshed_data["items"][0]["quantity"] == 2
        assert refreshed_data["mandate"]["status"] == "active"

        # 4. Request proposals with active mandate -> accepted!
        mock_proposed = [ProposedItem(product_id=2, discount_pct=Decimal("5.0"))]
        with patch("app.routers.proposals.get_proposals", new=AsyncMock(return_value=(mock_proposed, {}))):
            prop_res = await client.post(f"/api/proposals/{sid}")
            assert prop_res.status_code == 201
            prop_data = prop_res.json()
            assert prop_data["gate_result"] == "accepted"
            assert len(prop_data["accepted_items"]) == 1

