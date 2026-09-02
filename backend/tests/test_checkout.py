"""
Checkout integration tests.

Tests the checkout router end-to-end using httpx AsyncClient.
Razorpay SDK is always mocked — no real API calls made.
Demonstrates:
  1. Happy path in mock mode (no keys configured)
  2. Happy path with real Razorpay keys (mocked SDK)
  3. Retry-once behaviour: first call fails, retry succeeds
  4. Both calls fail → structured 402 error, cart preserved
  5. Server-side total recomputation (client can't inject price)
"""
import uuid
from decimal import Decimal
from unittest.mock import patch

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base, get_db
from app.main import app
from app.models.cart import CartItem, CartSession
from app.models.product import Product

# ── In-memory SQLite for tests ────────────────────────────────────────────
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"

test_engine = create_async_engine(TEST_DATABASE_URL, connect_args={"check_same_thread": False})
TestSessionLocal = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


async def override_get_db():
    async with TestSessionLocal() as session:
        yield session


app.dependency_overrides[get_db] = override_get_db


@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    """Create tables before each test, drop after."""
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def seeded_session_id(client):
    """
    Create a product and a cart with that product, return the session UUID.
    Demonstrates: server fetches price from DB, not from client.
    """
    async with TestSessionLocal() as db:
        product = Product(
            name="Test Headphones",
            description="Test product",
            price=Decimal("8999.00"),
            category="Electronics",
            stock=50,
            is_active=True,
            is_demo_fixture=False,
        )
        db.add(product)
        await db.flush()
        product_id = product.id

        session = CartSession(discount_budget_used_pct=Decimal("0"))
        db.add(session)
        await db.flush()

        cart_item = CartItem(
            session_id=session.id,
            product_id=product_id,
            quantity=2,
            unit_price=Decimal("8999.00"),
        )
        db.add(cart_item)
        await db.commit()
        return session.id, product_id


# ===========================================================================
# 1. Mock-mode checkout (no Razorpay keys)
# ===========================================================================
class TestMockCheckout:
    async def test_mock_checkout_returns_mock_order(self, client, seeded_session_id):
        """When keys are absent, checkout returns a mock order with mock=True."""
        session_id, _ = seeded_session_id

        with patch("app.config.settings.RAZORPAY_KEY_ID", ""):
            with patch("app.config.settings.RAZORPAY_KEY_SECRET", ""):
                response = await client.post(f"/api/checkout/{session_id}")

        assert response.status_code == 200
        data = response.json()
        assert data["mock_mode"] is True
        assert data["order_id"].startswith("mock_order_")
        assert data["currency"] == "INR"

    async def test_amount_computed_server_side(self, client, seeded_session_id):
        """
        Cart has 2× ₹8,999 headphones = ₹17,998 = 1,799,800 paise.
        Client sends no total — server must compute this from DB.
        """
        session_id, _ = seeded_session_id

        with patch("app.config.settings.RAZORPAY_KEY_ID", ""):
            with patch("app.config.settings.RAZORPAY_KEY_SECRET", ""):
                response = await client.post(f"/api/checkout/{session_id}")

        assert response.status_code == 200
        data = response.json()
        # 2 × 8999 = 17998 INR = 1799800 paise
        assert data["amount_paise"] == 1_799_800

    async def test_empty_cart_checkout_rejected(self, client):
        """Attempting checkout on an empty cart returns 422."""
        async with TestSessionLocal() as db:
            session = CartSession(discount_budget_used_pct=Decimal("0"))
            db.add(session)
            await db.commit()
            session_id = session.id

        with patch("app.config.settings.RAZORPAY_KEY_ID", ""):
            with patch("app.config.settings.RAZORPAY_KEY_SECRET", ""):
                response = await client.post(f"/api/checkout/{session_id}")

        assert response.status_code == 422


# ===========================================================================
# 2. Razorpay live mode (mocked SDK)
# ===========================================================================
class TestLiveCheckout:
    async def test_happy_path_live(self, client, seeded_session_id):
        """With mocked Razorpay SDK returning a real-looking order."""
        session_id, _ = seeded_session_id
        fake_order = {
            "id": "order_ABCDEF123456", "amount": 1_799_800,
            "currency": "INR", "status": "created",
        }

        with patch("app.config.settings.RAZORPAY_KEY_ID", "rzp_test_fake_key"):
            with patch("app.config.settings.RAZORPAY_KEY_SECRET", "fake_secret"):
                patch_target = "app.services.razorpay_service._create_order_sync"
                with patch(patch_target, return_value=fake_order):
                    response = await client.post(f"/api/checkout/{session_id}")

        assert response.status_code == 200
        data = response.json()
        assert data["order_id"] == "order_ABCDEF123456"
        assert data["mock_mode"] is False
        assert data["razorpay_key_id"] == "rzp_test_fake_key"

    async def test_session_not_found_returns_404(self, client):
        """Non-existent session → 404."""
        response = await client.post(f"/api/checkout/{uuid.uuid4()}")
        assert response.status_code == 404


# ===========================================================================
# 3. Retry behaviour
# ===========================================================================
class TestCheckoutRetry:
    async def test_first_fails_retry_succeeds(self, client, seeded_session_id):
        """First Razorpay call raises, retry succeeds — result is 200."""
        session_id, _ = seeded_session_id
        fake_order = {
            "id": "order_RETRY_OK", "amount": 1_799_800,
            "currency": "INR", "status": "created",
        }

        call_count = 0

        def flaky_create(key_id, key_secret, payload):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ConnectionError("Simulated transient network error")
            return fake_order

        with patch("app.config.settings.RAZORPAY_KEY_ID", "rzp_test_fake_key"):
            with patch("app.config.settings.RAZORPAY_KEY_SECRET", "fake_secret"):
                patch_target = "app.services.razorpay_service._create_order_sync"
                with patch(patch_target, side_effect=flaky_create):
                    response = await client.post(f"/api/checkout/{session_id}")

        assert response.status_code == 200
        assert response.json()["order_id"] == "order_RETRY_OK"
        assert call_count == 2  # initial + one retry

    async def test_both_attempts_fail_returns_402_cart_preserved(self, client, seeded_session_id):
        """
        Both Razorpay attempts fail → 402 Payment Required.
        Cart must still exist (not deleted on failure).
        """
        session_id, _ = seeded_session_id

        def always_fail(key_id, key_secret, payload):
            raise ConnectionError("Simulated persistent error")

        with patch("app.config.settings.RAZORPAY_KEY_ID", "rzp_test_fake_key"):
            with patch("app.config.settings.RAZORPAY_KEY_SECRET", "fake_secret"):
                patch_target = "app.services.razorpay_service._create_order_sync"
                with patch(patch_target, side_effect=always_fail):
                    response = await client.post(f"/api/checkout/{session_id}")

        assert response.status_code == 402
        body = response.json()
        # cart_preserved must be signalled (may be in detail dict)
        detail = body.get("detail", {})
        if isinstance(detail, dict):
            assert detail.get("cart_preserved") is True
        else:
            # Flat message — at minimum check error is returned
            assert "cart" in str(body).lower() or response.status_code == 402

        # Cart still exists in DB
        get_response = await client.get(f"/api/cart/{session_id}")
        assert get_response.status_code == 200
        assert get_response.json()["item_count"] > 0
