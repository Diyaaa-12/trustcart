"""
Cart router -- CRUD operations on CartSession and CartItem.

Security invariant: clients NEVER send prices. All unit_price values are
fetched from the database (catalog) at the moment the item is added.
Cart totals are always computed server-side.
"""
import logging
import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database import get_db
from app.models.cart import CartItem, CartSession
from app.models.product import Product
from app.models.proposal import AuditLog
from app.schemas.cart import AddItemRequest, CartItemOut, CartOut

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/cart", tags=["cart"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def _get_session_or_404(session_id: uuid.UUID, db: AsyncSession) -> CartSession:
    result = await db.execute(
        select(CartSession)
        .options(selectinload(CartSession.items).selectinload(CartItem.product))
        .where(CartSession.id == session_id)
    )
    session = result.scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cart session not found")
    return session


def _build_cart_out(session: CartSession) -> CartOut:
    items_out = []
    subtotal = Decimal("0")
    for ci in session.items:
        line_total = ci.unit_price * ci.quantity
        subtotal += line_total
        items_out.append(
            CartItemOut(
                id=ci.id,
                product_id=ci.product_id,
                product_name=ci.product.name,
                category=ci.product.category,
                quantity=ci.quantity,
                unit_price=ci.unit_price,
                line_total=line_total,
            )
        )
    budget_remaining = max(
        Decimal("0"),
        settings.MAX_DISCOUNT_BUDGET_PCT - session.discount_budget_used_pct,
    )
    mandate_out = None
    if session.mandate_payload and isinstance(session.mandate_payload, dict):
        from datetime import UTC, datetime

        from app.schemas.cart import MandateOut
        from app.services.mandate import compute_mandate_fingerprint

        exp_str = str(session.mandate_payload.get("expires_at", ""))
        try:
            exp_dt = datetime.fromisoformat(exp_str)
            m_status = "active" if datetime.now(UTC) <= exp_dt else "expired"
        except Exception:
            m_status = "active"

        mandate_out = MandateOut(
            fingerprint=compute_mandate_fingerprint(session.mandate_payload),
            max_cumulative_discount_pct=float(
                session.mandate_payload.get("max_cumulative_discount_pct", 10.0)
            ),
            max_items_per_proposal=int(session.mandate_payload.get("max_items_per_proposal", 3)),
            expires_at=exp_str,
            status=m_status,
        )

    return CartOut(
        session_id=session.id,
        items=items_out,
        subtotal=subtotal,
        discount_budget_used_pct=session.discount_budget_used_pct,
        discount_budget_remaining_pct=budget_remaining,
        item_count=sum(ci.quantity for ci in session.items),
        trust_score=session.trust_score,
        autonomy_tier=session.autonomy_tier.value,
        mandate=mandate_out,
    )


async def _write_audit(
    db: AsyncSession,
    session_id: uuid.UUID,
    event_type: str,
    payload: dict,
) -> None:
    import structlog

    ctx = structlog.contextvars.get_contextvars()
    log = AuditLog(
        session_id=session_id,
        event_type=event_type,
        payload=payload,
        request_id=ctx.get("request_id", ""),
    )
    db.add(log)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@router.post("", response_model=CartOut, status_code=status.HTTP_201_CREATED)
async def create_cart(db: AsyncSession = Depends(get_db)) -> CartOut:
    """Create a new cart session."""
    session = CartSession(id=uuid.uuid4())

    # Issue cryptographically signed spend mandate (AP2 protocol)
    from app.services.mandate import (
        compute_mandate_fingerprint,
        create_mandate,
        mandate_to_dict,
    )

    mandate_obj, signature = create_mandate(
        session_id=session.id,
        secret=settings.MANDATE_SECRET,
        max_cumulative_discount_pct=settings.MAX_DISCOUNT_BUDGET_PCT,
        max_items_per_proposal=settings.MAX_PROPOSALS_PER_CART,
        ttl_minutes=settings.MANDATE_TTL_MINUTES,
    )
    session.mandate_payload = mandate_to_dict(mandate_obj)
    session.mandate_signature = signature

    db.add(session)
    await _write_audit(
        db,
        session.id,
        "mandate.issued",
        {
            "mandate_fingerprint": compute_mandate_fingerprint(session.mandate_payload),
            "max_cumulative_discount_pct": float(mandate_obj.max_cumulative_discount_pct),
            "max_items": mandate_obj.max_items_per_proposal,
            "expires_at": mandate_obj.expires_at,
            "issued_at": mandate_obj.issued_at,
        },
    )
    await db.commit()
    await db.refresh(session)
    # Re-fetch with relationships
    session = await _get_session_or_404(session.id, db)
    logger.info("Cart session created", extra={"session_id": str(session.id)})
    return _build_cart_out(session)


@router.get("/{session_id}", response_model=CartOut)
async def get_cart(
    session_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> CartOut:
    """Fetch current cart state."""
    session = await _get_session_or_404(session_id, db)
    return _build_cart_out(session)


@router.post("/{session_id}/items", response_model=CartOut)
async def add_item(
    session_id: uuid.UUID,
    body: AddItemRequest,
    db: AsyncSession = Depends(get_db),
) -> CartOut:
    """
    Add a product to the cart.

    - Product price is fetched from the catalog (client-sent price is ignored).
    - If the product is already in the cart, quantity is incremented.
    """
    session = await _get_session_or_404(session_id, db)

    # Validate product exists and is purchasable
    product_result = await db.execute(
        select(Product).where(Product.id == body.product_id, Product.is_active == True)  # noqa: E712
    )
    product = product_result.scalar_one_or_none()
    if product is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Product {body.product_id} not found or inactive",
        )
    if product.stock < body.quantity:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Insufficient stock: requested {body.quantity}, available {product.stock}",
        )

    # Check if already in cart -> increment quantity
    existing_result = await db.execute(
        select(CartItem).where(
            CartItem.session_id == session_id,
            CartItem.product_id == body.product_id,
        )
    )
    existing = existing_result.scalar_one_or_none()
    if existing:
        existing.quantity += body.quantity
    else:
        cart_item = CartItem(
            session_id=session_id,
            product_id=body.product_id,
            quantity=body.quantity,
            unit_price=product.price,  # server-side price, never client-sent
        )
        db.add(cart_item)

    await _write_audit(
        db, session_id, "cart.item_added",
        {
            "product_id": body.product_id,
            "product_name": product.name,
            "quantity": body.quantity,
            "unit_price": float(product.price),
        },
    )
    await db.commit()

    session = await _get_session_or_404(session_id, db)
    logger.info(
        "Item added to cart", extra={"session_id": str(session_id), "product_id": body.product_id}
    )
    return _build_cart_out(session)


@router.delete("/{session_id}/items/{product_id}", response_model=CartOut)
async def remove_item(
    session_id: uuid.UUID,
    product_id: int,
    db: AsyncSession = Depends(get_db),
) -> CartOut:
    """Remove a product from the cart entirely (regardless of quantity)."""
    await _get_session_or_404(session_id, db)

    result = await db.execute(
        select(CartItem).where(
            CartItem.session_id == session_id,
            CartItem.product_id == product_id,
        )
    )
    item = result.scalar_one_or_none()
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Product {product_id} not in cart",
        )

    await db.delete(item)
    await _write_audit(db, session_id, "cart.item_removed", {"product_id": product_id})
    await db.commit()

    session = await _get_session_or_404(session_id, db)
    logger.info(
        "Item removed from cart", extra={"session_id": str(session_id), "product_id": product_id}
    )
    return _build_cart_out(session)
