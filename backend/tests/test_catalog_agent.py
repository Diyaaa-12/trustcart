"""
Tests for agent-readable catalog endpoint:
GET /api/catalog/agent-readable
"""
import uuid
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.database import Base, get_db
from app.main import app
from app.models.product import Product
from app.routers.catalog import get_agent_readable_catalog
from app.schemas.catalog import AgentCatalogResponse
from app.services.policy_gate import DEFAULT_CATEGORY_CROSS_SELL_MAP
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
async def setup_catalog_db():
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with TestSessionLocal() as db:
        # Seed standard products
        p1 = Product(
            id=101,
            name="Studio Monitor Headphones",
            description="Professional audio monitoring",
            price=Decimal("4999.00"),
            category="Electronics",
            stock=15,
            is_active=True,
            is_demo_fixture=False,
        )
        p2 = Product(
            id=102,
            name="Audio Cable 3.5mm",
            description="Gold-plated auxiliary cable",
            price=Decimal("399.00"),
            category="Accessories",
            stock=50,
            is_active=True,
            is_demo_fixture=False,
        )
        p3 = Product(
            id=103,
            name="Out of Stock Keyboard",
            description="Mechanical keyboard",
            price=Decimal("2999.00"),
            category="Electronics",
            stock=0,
            is_active=True,
            is_demo_fixture=False,
        )
        # Adversarial fixture product
        p_fixture = Product(
            id=999,
            name="[FIXTURE] Adversarial SKU",
            description="Demo fixture payload",
            price=Decimal("9999.00"),
            category="Electronics",
            stock=1,
            is_active=True,
            is_demo_fixture=True,
        )
        db.add_all([p1, p2, p3, p_fixture])
        await db.commit()

    yield

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


class TestAgentReadableCatalog:
    @pytest.mark.asyncio
    async def test_agent_readable_schema_correctness(self, client):
        """Verify response matches AgentCatalogResponse schema with all policy fields."""
        response = await client.get("/api/catalog/agent-readable")
        assert response.status_code == 200
        data = response.json()

        # Validate with Pydantic
        parsed = AgentCatalogResponse(**data)
        assert parsed.catalog_version == "1.0.0"
        assert parsed.total_items == 3
        assert len(parsed.items) == 3

        # Check item structure
        item = next(i for i in parsed.items if i.id == 101)
        assert item.name == "Studio Monitor Headphones"
        assert item.category == "Electronics"
        assert item.price == 4999.00
        assert item.stock == 15
        assert item.cross_sell_category_hints == DEFAULT_CATEGORY_CROSS_SELL_MAP.get(
            "Electronics", []
        )
        assert item.policy_metadata.max_allowed_discount_pct == 20.0
        assert item.policy_metadata.eligible_for_proposal is True
        assert item.policy_metadata.requires_in_stock is True

    @pytest.mark.asyncio
    async def test_demo_fixture_exclusion(self, client):
        """Adversarial fixture products must never appear in the agent-readable feed."""
        response = await client.get("/api/catalog/agent-readable")
        assert response.status_code == 200
        data = response.json()

        item_ids = [item["id"] for item in data["items"]]
        assert 999 not in item_ids
        for item in data["items"]:
            assert "FIXTURE" not in item["name"]

    @pytest.mark.asyncio
    async def test_out_of_stock_eligibility_metadata(self, client):
        """Out-of-stock items are included with eligible_for_proposal=False."""
        response = await client.get("/api/catalog/agent-readable")
        assert response.status_code == 200
        data = response.json()

        item = next(i for i in data["items"] if i["id"] == 103)
        assert item["stock"] == 0
        assert item["policy_metadata"]["eligible_for_proposal"] is False

    @pytest.mark.asyncio
    async def test_category_filtering(self, client):
        """Category filter returns only products in that category."""
        response = await client.get("/api/catalog/agent-readable?category=Accessories")
        assert response.status_code == 200
        data = response.json()

        assert data["total_items"] == 1
        assert data["items"][0]["category"] == "Accessories"
        assert data["items"][0]["id"] == 102

    @pytest.mark.asyncio
    async def test_empty_catalog_edge_case(self, client):
        """When no products match filter, return empty items list with valid metadata."""
        response = await client.get(
            f"/api/catalog/agent-readable?category=Nonexistent-{uuid.uuid4().hex[:6]}"
        )
        assert response.status_code == 200
        data = response.json()

        assert data["catalog_version"] == "1.0.0"
        assert data["total_items"] == 0
        assert data["items"] == []

    @pytest.mark.asyncio
    async def test_direct_get_agent_readable_catalog(self):
        async with TestSessionLocal() as db:
            result = await get_agent_readable_catalog(category="Electronics", db=db)
            assert result.catalog_version == "1.0.0"
            assert result.total_items == 2
            for item in result.items:
                assert item.category == "Electronics"
