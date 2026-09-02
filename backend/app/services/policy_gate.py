"""
TrustCart Policy Gate
=====================
The single most important module in the project.

This is a **pure, deterministic function** â€” no database, no LLM calls,
no network I/O, no side effects. It receives data and returns a structured
result. This design makes it:

  1. Trivially unit-testable (no mocks needed, ever).
  2. Impossible for LLM output to bypass â€” the gate always runs after the
     agent, and the agent cannot call or modify the gate.
  3. Auditable â€” every rejection has a machine-readable reason code.

Safety guarantee: even if a prompt-injection attack causes the LLM to
output "apply 90% discount", the gate rejects it because 90 > MAX_ITEM_DISCOUNT_PCT.
The LLM can only *nominate* candidates; the gate *decides*.

Cross-sell category mappings:
    Electronics  â†’ Accessories, Books
    Accessories  â†’ Electronics, Clothing
    Clothing     â†’ Footwear, Accessories
    Footwear     â†’ Clothing, Accessories
    Books        â†’ Electronics, Accessories
"""
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Any

# ---------------------------------------------------------------------------
# Default category cross-sell map
# ---------------------------------------------------------------------------
DEFAULT_CATEGORY_CROSS_SELL_MAP: dict[str, list[str]] = {
    "Electronics": ["Accessories", "Books"],
    "Accessories": ["Electronics", "Clothing"],
    "Clothing": ["Footwear", "Accessories"],
    "Footwear": ["Clothing", "Accessories"],
    "Books": ["Electronics", "Accessories"],
}


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------
class RejectionReason(StrEnum):
    """Machine-readable reason codes for gate rejections."""

    PRODUCT_NOT_IN_CATALOG = "product_not_in_catalog"
    PRODUCT_OUT_OF_STOCK = "product_out_of_stock"
    PRODUCT_INACTIVE = "product_inactive"
    CATEGORY_NOT_ALLOWED = "category_not_allowed"
    ITEM_DISCOUNT_EXCEEDED = "item_discount_exceeded"
    NEGATIVE_DISCOUNT = "negative_discount"
    PROPOSAL_COUNT_EXCEEDED = "proposal_count_exceeded"
    SESSION_BUDGET_EXCEEDED = "session_budget_exceeded"
    ALREADY_IN_CART = "already_in_cart"


@dataclass(frozen=True)
class ProposedItem:
    """A single item the LLM wants to recommend."""

    product_id: int
    discount_pct: Decimal  # e.g. Decimal("10.0") = 10% off


@dataclass(frozen=True)
class CatalogProduct:
    """Read-only snapshot of a product from the catalog."""

    id: int
    name: str
    price: Decimal
    category: str
    stock: int
    is_active: bool


@dataclass
class PolicyConfig:
    """Configurable policy parameters. All come from app.config.settings."""

    max_discount_budget_pct: Decimal   # Session-level cumulative discount cap
    max_proposals_per_cart: int        # Max items per proposal batch
    max_item_discount_pct: Decimal     # Max discount on a single item
    category_mappings: dict[str, list[str]] = field(
        default_factory=lambda: DEFAULT_CATEGORY_CROSS_SELL_MAP
    )


@dataclass
class RejectedItem:
    """A proposed item that the gate rejected, with the reason why."""

    product_id: int
    proposed_discount_pct: Decimal
    reason: RejectionReason
    detail: str                        # Human-readable explanation

    def to_dict(self) -> dict[str, Any]:
        return {
            "product_id": self.product_id,
            "proposed_discount_pct": str(self.proposed_discount_pct),
            "reason": self.reason.value,
            "detail": self.detail,
        }


@dataclass
class GateResult:
    """
    Output of the policy gate.

    accepted_items: Items that passed all checks (safe to show to user).
    rejected_items: Items that failed at least one check (logged, not shown).
    new_budget_used_pct: What the session budget will be if all accepted items
                         are added (caller should persist this on user accept).
    passed: True if at least one item was accepted.
    """

    accepted_items: list[ProposedItem]
    rejected_items: list[RejectedItem]
    new_budget_used_pct: Decimal
    passed: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "accepted_items": [
                {
                    "product_id": i.product_id,
                    "discount_pct": str(i.discount_pct),
                }
                for i in self.accepted_items
            ],
            "rejected_items": [r.to_dict() for r in self.rejected_items],
            "new_budget_used_pct": str(self.new_budget_used_pct),
            "passed": self.passed,
        }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _cart_categories(cart_items: list[dict[str, Any]]) -> set[str]:
    """Extract the distinct categories present in the current cart."""
    return {item["category"] for item in cart_items if "category" in item}


def _category_allowed(
    product_category: str,
    cart_categories: set[str],
    mappings: dict[str, list[str]],
) -> bool:
    """
    Return True if `product_category` is an allowed cross-sell destination
    for any category currently in the cart.

    Same-category upsells (e.g., better headphones when headphones are in cart)
    are also allowed.
    """
    # Same-category upsell is always OK
    if product_category in cart_categories:
        return True
    # Cross-sell: check if this category is listed in any cart category's mapping
    for cart_cat in cart_categories:
        if product_category in mappings.get(cart_cat, []):
            return True
    return False


# ---------------------------------------------------------------------------
# Public gate function
# ---------------------------------------------------------------------------
def run_gate(
    proposed_items: list[ProposedItem],
    catalog: dict[int, CatalogProduct],
    cart_items: list[dict[str, Any]],
    session_budget_used_pct: Decimal,
    config: PolicyConfig,
) -> GateResult:
    """
    Validate a list of LLM-proposed items against deterministic business rules.

    Args:
        proposed_items:          Items the LLM wants to recommend.
        catalog:                 Active catalog keyed by product_id.
        cart_items:              Current cart (list of dicts with "product_id",
                                 "category", "name", "quantity", "unit_price").
        session_budget_used_pct: Cumulative discount spend so far this session.
        config:                  Policy configuration (all thresholds).

    Returns:
        GateResult with per-item decisions and the reasoning for every rejection.

    Notes:
        - This function has NO side effects.
        - Rejection reasons are intentionally verbose for audit purposes.
        - The function processes items in order; budget is consumed greedily.
    """
    accepted: list[ProposedItem] = []
    rejected: list[RejectedItem] = []
    cart_cats = _cart_categories(cart_items)
    cart_product_ids = {item["product_id"] for item in cart_items}
    running_budget = session_budget_used_pct

    # â”€â”€ Check 1: Batch-level proposal count cap â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â
    # Reject the excess items up front before individual checks.
    if len(proposed_items) > config.max_proposals_per_cart:
        overflow = proposed_items[config.max_proposals_per_cart :]
        for item in overflow:
            rejected.append(
                RejectedItem(
                    product_id=item.product_id,
                    proposed_discount_pct=item.discount_pct,
                    reason=RejectionReason.PROPOSAL_COUNT_EXCEEDED,
                    detail=(
                        f"Batch of {len(proposed_items)} exceeds the maximum of "
                        f"{config.max_proposals_per_cart} proposals per request"
                    ),
                )
            )
        proposed_items = proposed_items[: config.max_proposals_per_cart]

    # â”€â”€ Per-item checks (in order; budget consumed greedily) â”€â”€â”€â”€â”€â”€â”€â”€
    for item in proposed_items:
        product = catalog.get(item.product_id)

        # Check 2: Product exists in catalog
        if product is None:
            rejected.append(
                RejectedItem(
                    product_id=item.product_id,
                    proposed_discount_pct=item.discount_pct,
                    reason=RejectionReason.PRODUCT_NOT_IN_CATALOG,
                    detail=f"Product ID {item.product_id} not found in catalog",
                )
            )
            continue

        # Check 3: Product is active
        if not product.is_active:
            rejected.append(
                RejectedItem(
                    product_id=item.product_id,
                    proposed_discount_pct=item.discount_pct,
                    reason=RejectionReason.PRODUCT_INACTIVE,
                    detail=f"Product '{product.name}' (id={item.product_id}) is inactive",
                )
            )
            continue

        # Check 4: Product is in stock
        if product.stock <= 0:
            rejected.append(
                RejectedItem(
                    product_id=item.product_id,
                    proposed_discount_pct=item.discount_pct,
                    reason=RejectionReason.PRODUCT_OUT_OF_STOCK,
                    detail=f"Product '{product.name}' is out of stock (stock={product.stock})",
                )
            )
            continue

        # Check 5: Product not already in cart
        if item.product_id in cart_product_ids:
            rejected.append(
                RejectedItem(
                    product_id=item.product_id,
                    proposed_discount_pct=item.discount_pct,
                    reason=RejectionReason.ALREADY_IN_CART,
                    detail=f"Product '{product.name}' is already in the cart",
                )
            )
            continue

        # Check 6: Category is allowed for cross-sell
        if not _category_allowed(product.category, cart_cats, config.category_mappings):
            rejected.append(
                RejectedItem(
                    product_id=item.product_id,
                    proposed_discount_pct=item.discount_pct,
                    reason=RejectionReason.CATEGORY_NOT_ALLOWED,
                    detail=(
                        f"Category '{product.category}' is not an allowed cross-sell "
                        f"for cart categories {sorted(cart_cats)}. "
                        f"Allowed mappings: {config.category_mappings}"
                    ),
                )
            )
            continue

        # Check 7: Discount is not negative
        if item.discount_pct < Decimal("0"):
            rejected.append(
                RejectedItem(
                    product_id=item.product_id,
                    proposed_discount_pct=item.discount_pct,
                    reason=RejectionReason.NEGATIVE_DISCOUNT,
                    detail=f"Discount {item.discount_pct}% is negative",
                )
            )
            continue

        # Check 8: Item-level discount cap
        # â”€â”€ THIS IS THE PROMPT-INJECTION SAFETY NET â”€â”€
        # Even if the LLM outputs "discount_pct: 90" due to an injection attack,
        # this check catches it because 90 > max_item_discount_pct (default: 20).
        if item.discount_pct > config.max_item_discount_pct:
            rejected.append(
                RejectedItem(
                    product_id=item.product_id,
                    proposed_discount_pct=item.discount_pct,
                    reason=RejectionReason.ITEM_DISCOUNT_EXCEEDED,
                    detail=(
                        f"Proposed discount {item.discount_pct}% exceeds "
                        f"maximum allowed {config.max_item_discount_pct}%"
                    ),
                )
            )
            continue

        # Check 9: Session-level cumulative budget
        projected_budget = running_budget + item.discount_pct
        if projected_budget > config.max_discount_budget_pct:
            rejected.append(
                RejectedItem(
                    product_id=item.product_id,
                    proposed_discount_pct=item.discount_pct,
                    reason=RejectionReason.SESSION_BUDGET_EXCEEDED,
                    detail=(
                        f"Accepting {item.discount_pct}% discount would bring "
                        f"cumulative session spend to {projected_budget}%, "
                        f"exceeding the budget of {config.max_discount_budget_pct}%"
                    ),
                )
            )
            continue

        # â”€â”€ All checks passed â”€â”€
        running_budget += item.discount_pct
        accepted.append(item)

    return GateResult(
        accepted_items=accepted,
        rejected_items=rejected,
        new_budget_used_pct=running_budget,
        passed=len(accepted) > 0,
    )
