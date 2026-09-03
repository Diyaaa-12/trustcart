"""
Tests for rate limiting and health check endpoints.
"""
import uuid
from decimal import Decimal
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.database import Base
from app.main import app
from app.models.cart import CartSession
from app.models.product import Product
from app.services.rate_limiter import InMemoryRateLimiter, limiter
from tests.test_checkout import TestSessionLocal, test_engine


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture(autouse=True)
async def clean_limiter_and_db():
    limiter.reset()
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    limiter.reset()
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


class TestInMemoryRateLimiter:
    """Unit tests for sliding window rate limiter."""

    def test_requests_within_limit_allowed(self):
        rl = InMemoryRateLimiter()
        for _ in range(5):
            allowed, retry_after = rl.is_allowed("session-1", max_requests=5, window_seconds=60.0)
            assert allowed is True
            assert retry_after == 0

    def test_request_exceeding_limit_rejected(self):
        rl = InMemoryRateLimiter()
        # 3 allowed
        for _ in range(3):
            allowed, _ = rl.is_allowed("session-1", max_requests=3, window_seconds=60.0)
            assert allowed is True

        # 4th rejected
        allowed, retry_after = rl.is_allowed("session-1", max_requests=3, window_seconds=60.0)
        assert allowed is False
        assert retry_after > 0
        assert retry_after <= 60

    def test_different_keys_isolated(self):
        rl = InMemoryRateLimiter()
        for _ in range(3):
            rl.is_allowed("key-A", max_requests=3, window_seconds=60.0)

        # key-A blocked
        assert rl.is_allowed("key-A", max_requests=3, window_seconds=60.0)[0] is False
        # key-B still allowed
        assert rl.is_allowed("key-B", max_requests=3, window_seconds=60.0)[0] is True

    def test_window_expiration(self):
        rl = InMemoryRateLimiter()
        base_time = 1000.0

        for _ in range(2):
            allowed, _ = rl.is_allowed("k", max_requests=2, window_seconds=10.0, now=base_time)
            assert allowed is True

        # At time 1005: still blocked
        allowed, _ = rl.is_allowed("k", max_requests=2, window_seconds=10.0, now=base_time + 5.0)
        assert allowed is False

        # At time 1011: earlier requests expired, allowed
        allowed, _ = rl.is_allowed("k", max_requests=2, window_seconds=10.0, now=base_time + 11.0)
        assert allowed is True


class TestRateLimiterEndpointIntegration:
    """Integration test verifying 429 Too Many Requests on proposal endpoint."""

    @pytest.mark.asyncio
    async def test_proposals_rate_limit_triggers_429(self, client):
        session_id = uuid.uuid4()
        async with TestSessionLocal() as db:
            p = Product(
                id=301,
                name="Headphones",
                description="Test",
                price=Decimal("1000.00"),
                category="Electronics",
                stock=10,
                is_active=True,
                is_demo_fixture=False,
            )
            sess = CartSession(
                id=session_id,
                discount_budget_used_pct=Decimal("0.0"),
                trust_score=Decimal("100.0"),
            )
            db.add_all([p, sess])
            await db.commit()

        with patch("app.config.settings.RATE_LIMIT_PROPOSALS_PER_MINUTE", 2):
            with patch("app.services.agent.get_proposals", return_value=([], None)):
                # 1st request -> 201
                r1 = await client.post(f"/api/proposals/{session_id}")
                assert r1.status_code == 201

                # 2nd request -> 201
                r2 = await client.post(f"/api/proposals/{session_id}")
                assert r2.status_code == 201

                # 3rd request -> 429 Rate Limit Exceeded
                r3 = await client.post(f"/api/proposals/{session_id}")
                assert r3.status_code == 429
                assert "Rate limit exceeded" in r3.json()["detail"]
                assert "Retry-After" in r3.headers


class TestHealthCheckEndpoint:
    """Verify both /health and /healthz endpoints."""

    @pytest.mark.asyncio
    async def test_health_endpoint(self, client):
        res = await client.get("/health")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "ok"
        assert "mock_checkout" in data
        assert "llm_provider" in data

    @pytest.mark.asyncio
    async def test_healthz_endpoint(self, client):
        res = await client.get("/healthz")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "ok"
