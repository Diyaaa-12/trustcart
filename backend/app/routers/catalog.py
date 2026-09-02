"""Catalog router — GET /api/catalog."""
import logging
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.product import Product

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
    until Phase 2. Active products with stock > 0 are shown first.
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
