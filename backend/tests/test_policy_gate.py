"""
Policy gate unit tests — the most important test file in the project.

These tests are pure Python: no database, no network, no mocks needed.
The gate's correctness is fully verified by driving it with controlled inputs.

Coverage targets:
  - All 9 gate check paths
  - Boundary conditions for discount thresholds
  - Multi-item batches (partial accept / full reject / full accept)
  - Budget accumulation across sequential gate calls (simulating multiple proposals)
  - The prompt-injection scenario (Phase 2 preview)
"""
from decimal import Decimal

import pytest

from app.services.policy_gate import (
    CatalogProduct,
    PolicyConfig,
    ProposedItem,
    RejectionReason,
    run_gate,
)


# ===========================================================================
# Helpers
# ===========================================================================
def _item(pid: int, disc: str = "5.0") -> ProposedItem:
    return ProposedItem(product_id=pid, discount_pct=Decimal(disc))


# ===========================================================================
# 1. Happy path — single item, all checks pass
# ===========================================================================
class TestHappyPath:
    def test_single_item_accepted(self, default_config, sample_catalog, electronics_cart):
        """Laptop Sleeve (Accessories) is a valid cross-sell for an Electronics cart."""
        result = run_gate(
            proposed_items=[_item(2, "5.0")],    # Laptop Sleeve — Accessories
            catalog=sample_catalog,
            cart_items=electronics_cart,
            session_budget_used_pct=Decimal("0"),
            config=default_config,
        )
        assert result.passed is True
        assert len(result.accepted_items) == 1
        assert result.accepted_items[0].product_id == 2
        assert len(result.rejected_items) == 0
        assert result.new_budget_used_pct == Decimal("5.0")

    def test_zero_discount_accepted(self, default_config, sample_catalog, electronics_cart):
        """Zero discount is valid."""
        result = run_gate(
            proposed_items=[_item(2, "0")],
            catalog=sample_catalog,
            cart_items=electronics_cart,
            session_budget_used_pct=Decimal("0"),
            config=default_config,
        )
        assert result.passed is True
        assert result.new_budget_used_pct == Decimal("0")

    def test_max_allowed_discount_accepted(self, default_config, sample_catalog, electronics_cart):
        """Exactly max_item_discount_pct (20%) — but only if session budget can absorb it.
        With max_discount_budget_pct=10, a 20% item discount would exceed budget.
        Use a config with a larger budget for this specific test."""
        big_budget_config = PolicyConfig(
            max_discount_budget_pct=Decimal("25.0"),
            max_proposals_per_cart=3,
            max_item_discount_pct=Decimal("20.0"),
        )
        result = run_gate(
            proposed_items=[_item(2, "20.0")],
            catalog=sample_catalog,
            cart_items=electronics_cart,
            session_budget_used_pct=Decimal("0"),
            config=big_budget_config,
        )
        assert result.passed is True
        assert result.accepted_items[0].discount_pct == Decimal("20.0")

    def test_full_batch_accepted(self, default_config, sample_catalog, electronics_cart):
        """Three valid items all accepted within budget.
        All must be allowed cross-sells for Electronics: Accessories (id=2) and Books (id=5).
        Use two items + one same-category item to hit 3.
        """
        extended = {**sample_catalog, 10: CatalogProduct(
            id=10, name="USB Hub", price=Decimal("2499"), category="Electronics",
            stock=100, is_active=True
        )}
        result = run_gate(
            proposed_items=[_item(2, "3.0"), _item(5, "3.0"), _item(10, "3.0")],
            catalog=extended,
            cart_items=electronics_cart,
            session_budget_used_pct=Decimal("0"),
            config=default_config,
        )
        assert result.passed is True
        assert len(result.accepted_items) == 3
        assert len(result.rejected_items) == 0
        assert result.new_budget_used_pct == Decimal("9.0")


# ===========================================================================
# 2. Product validation failures
# ===========================================================================
class TestProductValidation:
    def test_product_not_in_catalog(self, default_config, sample_catalog, electronics_cart):
        """Product ID that doesn't exist in catalog → PRODUCT_NOT_IN_CATALOG."""
        result = run_gate(
            proposed_items=[_item(9999, "5.0")],
            catalog=sample_catalog,
            cart_items=electronics_cart,
            session_budget_used_pct=Decimal("0"),
            config=default_config,
        )
        assert result.passed is False
        assert len(result.rejected_items) == 1
        assert result.rejected_items[0].reason == RejectionReason.PRODUCT_NOT_IN_CATALOG

    def test_inactive_product_rejected(self, default_config, sample_catalog, electronics_cart):
        """Product 6 is inactive → PRODUCT_INACTIVE."""
        result = run_gate(
            proposed_items=[_item(6, "5.0")],
            catalog=sample_catalog,
            cart_items=electronics_cart,
            session_budget_used_pct=Decimal("0"),
            config=default_config,
        )
        assert result.passed is False
        assert result.rejected_items[0].reason == RejectionReason.PRODUCT_INACTIVE

    def test_out_of_stock_rejected(self, default_config, sample_catalog, electronics_cart):
        """Product 7 has stock=0 → PRODUCT_OUT_OF_STOCK."""
        result = run_gate(
            proposed_items=[_item(7, "5.0")],
            catalog=sample_catalog,
            cart_items=electronics_cart,
            session_budget_used_pct=Decimal("0"),
            config=default_config,
        )
        assert result.passed is False
        assert result.rejected_items[0].reason == RejectionReason.PRODUCT_OUT_OF_STOCK

    def test_already_in_cart_rejected(self, default_config, sample_catalog, electronics_cart):
        """Product 1 is already in the electronics cart → ALREADY_IN_CART."""
        result = run_gate(
            proposed_items=[_item(1, "5.0")],  # product_id=1 is in electronics_cart
            catalog=sample_catalog,
            cart_items=electronics_cart,
            session_budget_used_pct=Decimal("0"),
            config=default_config,
        )
        assert result.passed is False
        assert result.rejected_items[0].reason == RejectionReason.ALREADY_IN_CART


# ===========================================================================
# 3. Category mapping checks
# ===========================================================================
class TestCategoryMapping:
    def test_allowed_cross_sell_accepted(self, default_config, sample_catalog, electronics_cart):
        """Books (id=5) is an allowed cross-sell for Electronics cart."""
        result = run_gate(
            proposed_items=[_item(5, "5.0")],
            catalog=sample_catalog,
            cart_items=electronics_cart,
            session_budget_used_pct=Decimal("0"),
            config=default_config,
        )
        assert result.passed is True

    def test_disallowed_category_rejected(self, default_config, sample_catalog, electronics_cart):
        """Footwear (id=3) is NOT in Electronics cross-sell map → CATEGORY_NOT_ALLOWED."""
        result = run_gate(
            proposed_items=[_item(3, "5.0")],  # Running Sneakers — Footwear
            catalog=sample_catalog,
            cart_items=electronics_cart,
            session_budget_used_pct=Decimal("0"),
            config=default_config,
        )
        assert result.passed is False
        assert result.rejected_items[0].reason == RejectionReason.CATEGORY_NOT_ALLOWED

    def test_same_category_upsell_allowed(self, default_config, sample_catalog, electronics_cart):
        """Another Electronics product (Books is allowed, but Electronics itself is same-category)."""
        # Add another Electronics product to the catalog for this test
        extended = {**sample_catalog, 10: CatalogProduct(
            id=10, name="USB Hub", price=Decimal("2499"), category="Electronics", stock=100, is_active=True
        )}
        result = run_gate(
            proposed_items=[_item(10, "5.0")],
            catalog=extended,
            cart_items=electronics_cart,
            session_budget_used_pct=Decimal("0"),
            config=default_config,
        )
        assert result.passed is True

    def test_clothing_to_footwear_allowed(self, default_config, sample_catalog, clothing_cart):
        """Footwear (id=3) is an allowed cross-sell for a Clothing cart."""
        result = run_gate(
            proposed_items=[_item(3, "5.0")],
            catalog=sample_catalog,
            cart_items=clothing_cart,
            session_budget_used_pct=Decimal("0"),
            config=default_config,
        )
        assert result.passed is True

    def test_empty_cart_categories_all_rejected(self, default_config, sample_catalog):
        """With an empty cart there are no categories → all cross-sells rejected."""
        result = run_gate(
            proposed_items=[_item(2, "5.0")],
            catalog=sample_catalog,
            cart_items=[],  # empty cart
            session_budget_used_pct=Decimal("0"),
            config=default_config,
        )
        # Laptop Sleeve (Accessories) has no cart category to match against
        assert result.passed is False
        assert result.rejected_items[0].reason == RejectionReason.CATEGORY_NOT_ALLOWED


# ===========================================================================
# 4. Discount threshold checks
# ===========================================================================
class TestDiscountThresholds:
    def test_item_discount_exactly_at_max(self, default_config, sample_catalog, electronics_cart):
        """20.0% == max_item_discount_pct → item check passes.
        Budget check: 20% > 10% session budget, so we need a bigger budget config.
        This test isolates the item-level check from the budget check."""
        big_budget_config = PolicyConfig(
            max_discount_budget_pct=Decimal("25.0"),
            max_proposals_per_cart=3,
            max_item_discount_pct=Decimal("20.0"),
        )
        result = run_gate(
            proposed_items=[_item(2, "20.0")],
            catalog=sample_catalog,
            cart_items=electronics_cart,
            session_budget_used_pct=Decimal("0"),
            config=big_budget_config,
        )
        assert result.passed is True

    def test_item_discount_one_pct_over_max(self, default_config, sample_catalog, electronics_cart):
        """20.01% > max_item_discount_pct → ITEM_DISCOUNT_EXCEEDED."""
        result = run_gate(
            proposed_items=[_item(2, "20.01")],
            catalog=sample_catalog,
            cart_items=electronics_cart,
            session_budget_used_pct=Decimal("0"),
            config=default_config,
        )
        assert result.passed is False
        assert result.rejected_items[0].reason == RejectionReason.ITEM_DISCOUNT_EXCEEDED

    def test_negative_discount_rejected(self, default_config, sample_catalog, electronics_cart):
        """Negative discount → NEGATIVE_DISCOUNT (e.g. injection attempt to upcharge)."""
        result = run_gate(
            proposed_items=[_item(2, "-5.0")],
            catalog=sample_catalog,
            cart_items=electronics_cart,
            session_budget_used_pct=Decimal("0"),
            config=default_config,
        )
        assert result.passed is False
        assert result.rejected_items[0].reason == RejectionReason.NEGATIVE_DISCOUNT

    def test_very_large_discount_rejected(self, default_config, sample_catalog, electronics_cart):
        """90% discount (injection attack value) → ITEM_DISCOUNT_EXCEEDED."""
        result = run_gate(
            proposed_items=[_item(2, "90.0")],
            catalog=sample_catalog,
            cart_items=electronics_cart,
            session_budget_used_pct=Decimal("0"),
            config=default_config,
        )
        assert result.passed is False
        assert result.rejected_items[0].reason == RejectionReason.ITEM_DISCOUNT_EXCEEDED

    def test_hundred_pct_discount_rejected(self, default_config, sample_catalog, electronics_cart):
        """100% discount → ITEM_DISCOUNT_EXCEEDED."""
        result = run_gate(
            proposed_items=[_item(2, "100.0")],
            catalog=sample_catalog,
            cart_items=electronics_cart,
            session_budget_used_pct=Decimal("0"),
            config=default_config,
        )
        assert result.passed is False
        assert result.rejected_items[0].reason == RejectionReason.ITEM_DISCOUNT_EXCEEDED


# ===========================================================================
# 5. Session budget checks
# ===========================================================================
class TestSessionBudget:
    def test_budget_exactly_at_limit_accepted(self, default_config, sample_catalog, electronics_cart):
        """Budget exactly at max (10%) → accepted."""
        result = run_gate(
            proposed_items=[_item(2, "10.0")],
            catalog=sample_catalog,
            cart_items=electronics_cart,
            session_budget_used_pct=Decimal("0"),
            config=default_config,
        )
        assert result.passed is True
        assert result.new_budget_used_pct == Decimal("10.0")

    def test_budget_one_pct_over_rejected(self, default_config, sample_catalog, electronics_cart):
        """Already used 5%, proposing 6% → total 11% > 10% → SESSION_BUDGET_EXCEEDED."""
        result = run_gate(
            proposed_items=[_item(2, "6.0")],
            catalog=sample_catalog,
            cart_items=electronics_cart,
            session_budget_used_pct=Decimal("5.0"),
            config=default_config,
        )
        assert result.passed is False
        assert result.rejected_items[0].reason == RejectionReason.SESSION_BUDGET_EXCEEDED

    def test_budget_exactly_exhausted_second_item_rejected(
        self, default_config, sample_catalog, electronics_cart
    ):
        """Budget used=10% → any non-zero discount rejected."""
        result = run_gate(
            proposed_items=[_item(2, "0.01")],
            catalog=sample_catalog,
            cart_items=electronics_cart,
            session_budget_used_pct=Decimal("10.0"),
            config=default_config,
        )
        assert result.passed is False
        assert result.rejected_items[0].reason == RejectionReason.SESSION_BUDGET_EXCEEDED

    def test_zero_discount_accepted_even_when_budget_exhausted(
        self, default_config, sample_catalog, electronics_cart
    ):
        """Zero-discount proposal is always within budget."""
        result = run_gate(
            proposed_items=[_item(2, "0")],
            catalog=sample_catalog,
            cart_items=electronics_cart,
            session_budget_used_pct=Decimal("10.0"),
            config=default_config,
        )
        assert result.passed is True
        assert result.new_budget_used_pct == Decimal("10.0")

    def test_budget_accumulates_across_proposals(self, default_config, sample_catalog, electronics_cart):
        """
        Simulate two sequential gate calls sharing a budget.
        Call 1: 6% accepted → budget = 6%.
        Call 2: 5% rejected (6+5=11 > 10).
        """
        result1 = run_gate(
            proposed_items=[_item(2, "6.0")],
            catalog=sample_catalog,
            cart_items=electronics_cart,
            session_budget_used_pct=Decimal("0"),
            config=default_config,
        )
        assert result1.passed is True
        assert result1.new_budget_used_pct == Decimal("6.0")

        # Simulate: cart now has Accessories in it (product 2 was added)
        updated_cart = electronics_cart + [
            {"product_id": 2, "name": "Laptop Sleeve", "category": "Accessories",
             "quantity": 1, "unit_price": 899.0}
        ]
        result2 = run_gate(
            proposed_items=[_item(5, "5.0")],  # Books — allowed for Electronics
            catalog=sample_catalog,
            cart_items=updated_cart,
            session_budget_used_pct=result1.new_budget_used_pct,  # carry forward
            config=default_config,
        )
        assert result2.passed is False
        assert result2.rejected_items[0].reason == RejectionReason.SESSION_BUDGET_EXCEEDED

    def test_budget_split_across_two_items(self, default_config, sample_catalog, electronics_cart):
        """5% + 5% = 10% exactly at budget — both accepted."""
        result = run_gate(
            proposed_items=[_item(2, "5.0"), _item(5, "5.0")],
            catalog=sample_catalog,
            cart_items=electronics_cart,
            session_budget_used_pct=Decimal("0"),
            config=default_config,
        )
        assert result.passed is True
        assert len(result.accepted_items) == 2
        assert result.new_budget_used_pct == Decimal("10.0")


# ===========================================================================
# 6. Proposal count cap
# ===========================================================================
class TestProposalCountCap:
    def test_exact_max_proposals_accepted(self, default_config, sample_catalog, electronics_cart):
        """Exactly 3 proposals (== max) → all processed."""
        result = run_gate(
            proposed_items=[_item(2, "3.0"), _item(5, "3.0"), _item(3, "3.0")],
            catalog=sample_catalog,
            cart_items=electronics_cart,
            session_budget_used_pct=Decimal("0"),
            config=default_config,
        )
        # id=3 (Footwear) rejected by category, but the count cap is not triggered
        assert len(result.accepted_items) + len(result.rejected_items) == 3

    def test_overflow_proposals_rejected_with_reason(self, default_config, sample_catalog, electronics_cart):
        """4 proposals when max=3 → item 4 rejected with PROPOSAL_COUNT_EXCEEDED."""
        result = run_gate(
            proposed_items=[_item(2, "2.0"), _item(5, "2.0"), _item(3, "2.0"), _item(4, "2.0")],
            catalog=sample_catalog,
            cart_items=electronics_cart,
            session_budget_used_pct=Decimal("0"),
            config=default_config,
        )
        count_exceeded = [
            r for r in result.rejected_items
            if r.reason == RejectionReason.PROPOSAL_COUNT_EXCEEDED
        ]
        assert len(count_exceeded) == 1
        assert count_exceeded[0].product_id == 4

    def test_empty_proposal_list(self, default_config, sample_catalog, electronics_cart):
        """Empty proposal list → passed=False, no errors."""
        result = run_gate(
            proposed_items=[],
            catalog=sample_catalog,
            cart_items=electronics_cart,
            session_budget_used_pct=Decimal("0"),
            config=default_config,
        )
        assert result.passed is False
        assert result.accepted_items == []
        assert result.rejected_items == []


# ===========================================================================
# 7. Mixed batch (partial acceptance)
# ===========================================================================
class TestMixedBatch:
    def test_partial_accept(self, default_config, sample_catalog, electronics_cart):
        """
        Batch: Laptop Sleeve (valid), Running Sneakers (wrong category).
        Expected: Sleeve accepted, Sneakers rejected.
        """
        result = run_gate(
            proposed_items=[_item(2, "5.0"), _item(3, "5.0")],
            catalog=sample_catalog,
            cart_items=electronics_cart,
            session_budget_used_pct=Decimal("0"),
            config=default_config,
        )
        assert result.passed is True
        assert len(result.accepted_items) == 1
        assert result.accepted_items[0].product_id == 2
        assert len(result.rejected_items) == 1
        assert result.rejected_items[0].product_id == 3
        assert result.rejected_items[0].reason == RejectionReason.CATEGORY_NOT_ALLOWED

    def test_first_item_uses_budget_second_rejected(self, default_config, sample_catalog, electronics_cart):
        """
        Budget = 10. Item 1: 7% accepted. Item 2: 5% rejected (7+5=12 > 10).
        Budget after = 7 (only item 1 consumed budget).
        """
        result = run_gate(
            proposed_items=[_item(2, "7.0"), _item(5, "5.0")],
            catalog=sample_catalog,
            cart_items=electronics_cart,
            session_budget_used_pct=Decimal("0"),
            config=default_config,
        )
        assert len(result.accepted_items) == 1
        assert result.accepted_items[0].product_id == 2
        assert len(result.rejected_items) == 1
        assert result.rejected_items[0].reason == RejectionReason.SESSION_BUDGET_EXCEEDED
        assert result.new_budget_used_pct == Decimal("7.0")


# ===========================================================================
# 8. Prompt-injection scenario (preview of Phase 2 test)
# ===========================================================================
class TestPromptInjection:
    def test_injection_item_90pct_discount_rejected(self, default_config, sample_catalog, electronics_cart):
        """
        The demo fixture (id=99) has an injection string in its name.
        Even if the LLM outputs it with 90% discount, the gate catches it:
          - 90% > MAX_ITEM_DISCOUNT_PCT (20%) → ITEM_DISCOUNT_EXCEEDED
        This proves the policy gate is the safety net, not the LLM's judgment.
        """
        result = run_gate(
            proposed_items=[ProposedItem(product_id=99, discount_pct=Decimal("90.0"))],
            catalog=sample_catalog,
            cart_items=electronics_cart,
            session_budget_used_pct=Decimal("0"),
            config=default_config,
        )
        assert result.passed is False
        assert result.rejected_items[0].reason == RejectionReason.ITEM_DISCOUNT_EXCEEDED
        assert "90" in result.rejected_items[0].detail

    def test_injection_item_with_valid_discount_still_in_catalog(
        self, default_config, sample_catalog, electronics_cart
    ):
        """
        Even with a valid discount, the injection fixture item (id=99, category=Electronics)
        is same-category as the cart → allowed by category check.
        But this is a Phase 2 concern: in practice, the agent never sees this item
        (it's excluded from the catalog view the agent receives).
        The test documents the expected behavior: category check passes, discount check passes.
        """
        result = run_gate(
            proposed_items=[ProposedItem(product_id=99, discount_pct=Decimal("5.0"))],
            catalog=sample_catalog,
            cart_items=electronics_cart,
            session_budget_used_pct=Decimal("0"),
            config=default_config,
        )
        # Category check: Electronics cart + Electronics product → same-category → allowed
        # Discount: 5% <= 20% → OK
        # This item passes the gate with a reasonable discount!
        # Phase 2 test will verify it never reaches the gate from the agent (agent can't see it).
        assert result.passed is True

    def test_injection_item_not_in_catalog_at_all_rejected(
        self, default_config, electronics_cart
    ):
        """
        If the agent hallucinates the injection product ID and it's not in the catalog
        the gate receives, it's rejected with PRODUCT_NOT_IN_CATALOG.
        """
        # Catalog that does NOT contain the fixture
        minimal_catalog = {
            1: CatalogProduct(id=1, name="Headphones", price=Decimal("8999"), category="Electronics", stock=50, is_active=True),
        }
        result = run_gate(
            proposed_items=[ProposedItem(product_id=99, discount_pct=Decimal("90.0"))],
            catalog=minimal_catalog,
            cart_items=electronics_cart,
            session_budget_used_pct=Decimal("0"),
            config=default_config,
        )
        assert result.passed is False
        assert result.rejected_items[0].reason == RejectionReason.PRODUCT_NOT_IN_CATALOG


# ===========================================================================
# 9. Gate result serialization
# ===========================================================================
class TestGateResultSerialization:
    def test_to_dict_structure(self, default_config, sample_catalog, electronics_cart):
        """GateResult.to_dict() produces the expected JSON-serializable structure."""
        result = run_gate(
            proposed_items=[_item(2, "5.0"), _item(9999, "5.0")],
            catalog=sample_catalog,
            cart_items=electronics_cart,
            session_budget_used_pct=Decimal("0"),
            config=default_config,
        )
        d = result.to_dict()
        assert "accepted_items" in d
        assert "rejected_items" in d
        assert "new_budget_used_pct" in d
        assert "passed" in d
        assert d["passed"] is True
        assert len(d["accepted_items"]) == 1
        assert len(d["rejected_items"]) == 1
        # Verify reason is a string (not enum)
        assert isinstance(d["rejected_items"][0]["reason"], str)
