"""Catalog router — GET /api/catalog."""
import logging
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.product import Product
from app.schemas.catalog import (
    AgentCatalogEntry,
    AgentCatalogResponse,
    AgentPolicyConstraints,
)
from app.services.policy_gate import DEFAULT_CATEGORY_CROSS_SELL_MAP

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/catalog", tags=["catalog"])


@router.get("", response_model=list[dict[str, Any]])
async def list_catalog(
    category: str | None = Query(None, description="Filter by category"),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    """
    Return all active, non-fixture products.

    Demo fixtures (is_demo_fixture=True) are excluded from this endpoint
    to isolate adversarial test fixtures. Active products with stock > 0 are shown first.
    """
    stmt = (
        select(Product)
        .where(Product.is_active == True)  # noqa: E712
        .where(Product.is_demo_fixture == False)  # noqa: E712
    )
    if category:
        stmt = stmt.where(Product.category == category)
    stmt = stmt.order_by(Product.category, Product.name)

    result = await db.execute(stmt)
    products = result.scalars().all()

    logger.info("Catalog fetched", extra={"count": len(products), "category_filter": category})

    return [
        {
            "id": p.id,
            "name": p.name,
            "description": p.description,
            "price": float(p.price),
            "category": p.category,
            "stock": p.stock,
        }
        for p in products
    ]


@router.get("/categories", response_model=list[str])
async def list_categories(db: AsyncSession = Depends(get_db)) -> list[str]:
    """Return the distinct categories in the active catalog."""
    from sqlalchemy import distinct

    result = await db.execute(
        select(distinct(Product.category))
        .where(Product.is_active == True)  # noqa: E712
        .where(Product.is_demo_fixture == False)  # noqa: E712
        .order_by(Product.category)
    )
    return list(result.scalars().all())


@router.get(
    "/agent-readable",
    response_model=AgentCatalogResponse,
    summary="Agent-readable catalog discovery feed",
    description=(
        "Returns the active product catalog in a structured, machine-consumable format "
        "designed specifically for AI buyer agents to parse and reason over. Includes "
        "per-product cross-sell category hints and pre-flight policy metadata (e.g. max "
        "allowable discount percentage, proposal eligibility) so external agents can evaluate "
        "policy constraints upfront before submitting proposals. Excludes adversarial demo "
        "fixtures by default."
    ),
)
async def get_agent_readable_catalog(
    category: str | None = Query(None, description="Optional filter by product category"),
    db: AsyncSession = Depends(get_db),
) -> AgentCatalogResponse:
    """Structured machine-consumable catalog for AI agent reasoning and pre-flight policy checks."""
    stmt = (
        select(Product)
        .where(Product.is_active == True)  # noqa: E712
        .where(Product.is_demo_fixture == False)  # noqa: E712
    )
    if category:
        stmt = stmt.where(Product.category == category)
    stmt = stmt.order_by(Product.category, Product.name)

    result = await db.execute(stmt)
    products = result.scalars().all()

    max_disc = float(settings.MAX_ITEM_DISCOUNT_PCT)
    entries: list[AgentCatalogEntry] = [
        AgentCatalogEntry(
            id=p.id,
            name=p.name,
            description=p.description,
            price=float(p.price),
            category=p.category,
            stock=p.stock,
            cross_sell_category_hints=DEFAULT_CATEGORY_CROSS_SELL_MAP.get(p.category, []),
            policy_metadata=AgentPolicyConstraints(
                max_allowed_discount_pct=max_disc,
                eligible_for_proposal=bool(p.is_active and p.stock > 0),
                requires_in_stock=True,
                same_category_allowed=True,
            ),
        )
        for p in products
    ]

    logger.info(
        "Agent-readable catalog fetched",
        extra={"count": len(entries), "category": category},
    )

    return AgentCatalogResponse(
        catalog_version="1.0.0",
        total_items=len(entries),
        items=entries,
    )
