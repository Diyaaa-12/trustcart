"""
Deterministic, plain-language decision explanation generator.

Transforms stored proposal gate results, mandate verifications, counterfactuals,
and trust score deltas into an audit-traceable, plain-English narrative.
Zero LLM calls -- 100% deterministic, grounded strictly in stored audit state.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.services.policy_gate import RejectionReason


class DecisionFactor(BaseModel):
    """Structured contributing factor for a policy decision."""

    category: str = Field(..., description="Factor category (mandate, discount, stock, etc.).")
    passed: bool = Field(..., description="Whether this evaluation factor passed.")
    title: str = Field(..., description="Short factor name.")
    detail: str = Field(..., description="Detailed factor explanation.")


class DecisionExplanationOut(BaseModel):
    """Plain-language explanation response model for proposals."""

    proposal_id: str = Field(..., description="UUID of the explained proposal.")
    session_id: str = Field(..., description="UUID of the cart session.")
    gate_result: str = Field(..., description="Gate decision: accepted | rejected | partial.")
    summary: str = Field(..., description="Concise headline summary.")
    explanation: str = Field(..., description="Deterministic plain-English explanatory text.")
    mandate_fingerprint: str | None = Field(None, description="Active AP2 mandate fingerprint.")
    mandate_verified: bool = Field(True, description="Whether mandate check succeeded.")
    old_score: float = Field(..., description="Trust score before evaluation.")
    new_score: float = Field(..., description="Trust score after evaluation.")
    score_delta: float = Field(..., description="Trust score delta.")
    old_autonomy_tier: str = Field(..., description="Autonomy tier before proposal.")
    new_autonomy_tier: str = Field(..., description="Autonomy tier after proposal.")
    requires_review: bool = Field(
        False, description="Whether proposal requires user confirmation."
    )
    factors: list[DecisionFactor] = Field(
        default_factory=list, description="Factor breakdown."
    )


def _item_name(pid: int, product_names: dict[int, str]) -> str:
    return product_names.get(pid, f"Product #{pid}")


def build_decision_explanation(
    proposal_id: str,
    session_id: str,
    gate_result: str,
    proposed_items: list[dict[str, Any]],
    accepted_items: list[dict[str, Any]],
    rejected_items: list[dict[str, Any]],
    product_names: dict[int, str],
    old_score: float = 100.0,
    new_score: float = 100.0,
    score_delta: float = 0.0,
    old_autonomy_tier: str = "high",
    new_autonomy_tier: str = "high",
    requires_review: bool = False,
    mandate_fingerprint: str | None = None,
    mandate_verified: bool = True,
    mandate_failure_reason: str | None = None,
    mandate_max_discount_pct: float | None = 10.0,
) -> DecisionExplanationOut:
    """Construct a deterministic plain-English narrative explaining a proposal decision."""
    factors: list[DecisionFactor] = []

    old_t = old_autonomy_tier.upper()
    new_t = new_autonomy_tier.upper()
    if old_t != new_t:
        tier_phrase = f"moving it from {old_t} to {new_t} autonomy"
    else:
        tier_phrase = f"maintaining {new_t} autonomy"

    mandate_reasons = {
        RejectionReason.MANDATE_INVALID.value,
        RejectionReason.MANDATE_EXPIRED.value,
        RejectionReason.MANDATE_MISSING.value,
        "mandate_invalid",
        "mandate_expired",
        "mandate_missing",
    }
    rejected_reason_codes = [str(r.get("reason", "")) for r in rejected_items]
    has_mandate_rejection = any(code in mandate_reasons for code in rejected_reason_codes)

    if not mandate_verified or has_mandate_rejection:
        fail_detail = mandate_failure_reason or (
            rejected_items[0].get("detail", "Signature mismatch or expired token")
            if rejected_items
            else "Cryptographic signature validation failed"
        )
        factors.append(
            DecisionFactor(
                category="mandate",
                passed=False,
                title="AP2 Spend Mandate Verification",
                detail=f"Verification failed: {fail_detail}",
            )
        )
        summary = "Proposal rejected: spend mandate verification failed."
        explanation = (
            "This proposal was evaluated against the session's AP2 spend mandate. "
            "The policy gate rejected all proposed items because cryptographic mandate "
            f"verification failed ({fail_detail}). As a result, the session's trust score "
            f"dropped from {old_score:.1f} to {new_score:.1f} ({score_delta:+.1f} pts), "
            f"{tier_phrase}."
        )
        return DecisionExplanationOut(
            proposal_id=proposal_id,
            session_id=session_id,
            gate_result="rejected",
            summary=summary,
            explanation=explanation,
            mandate_fingerprint=mandate_fingerprint,
            mandate_verified=False,
            old_score=old_score,
            new_score=new_score,
            score_delta=score_delta,
            old_autonomy_tier=old_autonomy_tier,
            new_autonomy_tier=new_autonomy_tier,
            requires_review=requires_review,
            factors=factors,
        )

    # Mandate was valid
    factors.append(
        DecisionFactor(
            category="mandate",
            passed=True,
            title="AP2 Spend Mandate Verification",
            detail=f"Verified signature with fingerprint {mandate_fingerprint or 'mnd_active'}",
        )
    )

    max_allowed = mandate_max_discount_pct or 20.0

    if gate_result == "rejected":
        primary = rejected_items[0] if rejected_items else {}
        pid = int(primary.get("product_id", 0))
        pname = _item_name(pid, product_names)
        reason = str(primary.get("reason", ""))
        p_disc = float(primary.get("proposed_discount_pct", 0.0))

        if reason == RejectionReason.ITEM_DISCOUNT_EXCEEDED.value:
            summary = f"Proposal for {pname} rejected: discount of {p_disc:.1f}% exceeded cap."
            explanation = (
                f"This proposal offered {pname} at {p_disc:.1f}% off. "
                "The policy gate rejected it because the discount exceeded the session's "
                f"mandate-authorized maximum of {max_allowed:.1f}%. "
                f"As a result, the session's trust score dropped from {old_score:.1f} "
                f"to {new_score:.1f} ({score_delta:+.1f} pts), {tier_phrase}."
            )
            factors.append(
                DecisionFactor(
                    category="discount",
                    passed=False,
                    title="Item Discount Cap",
                    detail=(
                        f"Proposed discount {p_disc:.1f}% exceeded maximum allowed "
                        f"{max_allowed:.1f}%."
                    ),
                )
            )
        elif reason == RejectionReason.PRODUCT_OUT_OF_STOCK.value:
            summary = f"Proposal for {pname} rejected: item is out of stock."
            explanation = (
                f"This proposal offered {pname} at {p_disc:.1f}% off. "
                "The policy gate rejected it because the item currently has 0 inventory units in "
                "stock. "
                f"As a result, the session's trust score dropped from {old_score:.1f} "
                f"to {new_score:.1f} ({score_delta:+.1f} pts), {tier_phrase}."
            )
            factors.append(
                DecisionFactor(
                    category="inventory",
                    passed=False,
                    title="Inventory Availability",
                    detail=f"{pname} has 0 units in stock.",
                )
            )
        elif reason == RejectionReason.PRODUCT_NOT_IN_CATALOG.value:
            summary = f"Proposal for product #{pid} rejected: item not in catalog."
            explanation = (
                f"This proposal offered Product #{pid}. "
                "The policy gate rejected it because the product does not exist in the active "
                "catalog. "
                f"As a result, the session's trust score dropped from {old_score:.1f} "
                f"to {new_score:.1f} ({score_delta:+.1f} pts), {tier_phrase}."
            )
            factors.append(
                DecisionFactor(
                    category="catalog",
                    passed=False,
                    title="Catalog Existence",
                    detail=f"Product #{pid} not found in database.",
                )
            )
        elif reason == RejectionReason.PRODUCT_INACTIVE.value:
            summary = f"Proposal for {pname} rejected: product is inactive."
            explanation = (
                f"This proposal offered {pname}. "
                "The policy gate rejected it because the product has been deactivated in the "
                "merchant catalog. "
                f"As a result, the session's trust score dropped from {old_score:.1f} "
                f"to {new_score:.1f} ({score_delta:+.1f} pts), {tier_phrase}."
            )
            factors.append(
                DecisionFactor(
                    category="catalog",
                    passed=False,
                    title="Product Status",
                    detail=f"{pname} is currently inactive.",
                )
            )
        elif reason == RejectionReason.CATEGORY_NOT_ALLOWED.value:
            summary = f"Proposal for {pname} rejected: category not allowed for cross-sell."
            explanation = (
                f"This proposal offered {pname} at {p_disc:.1f}% off. "
                "The policy gate rejected it because its category is not an authorized "
                "cross-sell destination for items in the customer's current cart. "
                f"As a result, the session's trust score dropped from {old_score:.1f} "
                f"to {new_score:.1f} ({score_delta:+.1f} pts), {tier_phrase}."
            )
            factors.append(
                DecisionFactor(
                    category="category",
                    passed=False,
                    title="Cross-sell Category Mapping",
                    detail=f"Category for {pname} is not permitted for current cart contents.",
                )
            )
        elif reason == RejectionReason.ALREADY_IN_CART.value:
            summary = f"Proposal for {pname} rejected: item already in cart."
            explanation = (
                f"This proposal offered {pname}. "
                "The policy gate rejected it because the item is already present in your cart. "
                f"As a result, the session's trust score dropped from {old_score:.1f} "
                f"to {new_score:.1f} ({score_delta:+.1f} pts), {tier_phrase}."
            )
            factors.append(
                DecisionFactor(
                    category="cart",
                    passed=False,
                    title="Cart Duplicate Prevention",
                    detail=f"{pname} is already in the cart.",
                )
            )
        elif reason == RejectionReason.SESSION_BUDGET_EXCEEDED.value:
            summary = f"Proposal for {pname} rejected: session discount budget exceeded."
            explanation = (
                f"This proposal offered {pname} at {p_disc:.1f}% off. "
                "The policy gate rejected it because applying this discount would exceed "
                "the session's cumulative discount budget. "
                f"As a result, the session's trust score dropped from {old_score:.1f} "
                f"to {new_score:.1f} ({score_delta:+.1f} pts), {tier_phrase}."
            )
            factors.append(
                DecisionFactor(
                    category="budget",
                    passed=False,
                    title="Session Discount Budget",
                    detail=f"Discount of {p_disc:.1f}% exceeds remaining session budget.",
                )
            )
        elif reason == RejectionReason.NEGATIVE_DISCOUNT.value:
            summary = f"Proposal for {pname} rejected: negative discount invalid."
            explanation = (
                f"This proposal offered {pname} with an invalid negative "
                f"discount of {p_disc:.1f}%. "
                "The policy gate rejected it because discount percentages must be non-negative. "
                f"As a result, the session's trust score dropped from {old_score:.1f} "
                f"to {new_score:.1f} ({score_delta:+.1f} pts), {tier_phrase}."
            )
            factors.append(
                DecisionFactor(
                    category="discount",
                    passed=False,
                    title="Non-negative Discount Check",
                    detail=f"Invalid discount {p_disc:.1f}%.",
                )
            )
        elif reason == RejectionReason.PROPOSAL_COUNT_EXCEEDED.value:
            summary = "Proposal batch rejected: proposal count exceeded limit."
            explanation = (
                "The agent submitted a recommendation batch exceeding the maximum allowed "
                "item count. The policy gate rejected the excess items. "
                f"As a result, the session's trust score dropped from {old_score:.1f} "
                f"to {new_score:.1f} ({score_delta:+.1f} pts), {tier_phrase}."
            )
            factors.append(
                DecisionFactor(
                    category="volume",
                    passed=False,
                    title="Batch Volume Limit",
                    detail="Batch exceeded maximum allowed proposals.",
                )
            )
        else:
            detail_str = str(primary.get("detail", reason))
            summary = f"Proposal for {pname} rejected by policy gate."
            explanation = (
                f"This proposal offered {pname} at {p_disc:.1f}% off. "
                f"The policy gate rejected it ({detail_str}). "
                f"As a result, the session's trust score dropped from {old_score:.1f} "
                f"to {new_score:.1f} ({score_delta:+.1f} pts), {tier_phrase}."
            )
            factors.append(
                DecisionFactor(
                    category="gate",
                    passed=False,
                    title="Policy Gate Decision",
                    detail=detail_str,
                )
            )

    elif gate_result == "accepted":
        items_parts = []
        for i in accepted_items:
            it_pid = int(i.get("product_id", 0))
            it_name = _item_name(it_pid, product_names)
            it_disc = float(i.get("discount_pct", 0.0))
            items_parts.append(f"{it_name} at {it_disc:.1f}% off")

        items_desc = ", ".join(items_parts)
        summary = f"Proposal accepted: {len(accepted_items)} item(s) approved by policy gate."
        explanation = (
            f"This proposal offered {items_desc}. "
            f"The session's spend mandate was verified ({mandate_fingerprint or 'active'}), "
            "and all items satisfied inventory, category cross-sell rules, and budget limits. "
            "The policy gate accepted the proposal. "
            f"As a result, the session's trust score increased from {old_score:.1f} "
            f"to {new_score:.1f} ({score_delta:+.1f} pts), {tier_phrase}."
        )
        factors.append(
            DecisionFactor(
                category="gate",
                passed=True,
                title="Policy Gate Acceptance",
                detail=f"All {len(accepted_items)} proposed item(s) satisfied safety invariants.",
            )
        )

    else:
        # Partial acceptance
        acc_parts = []
        for i in accepted_items:
            it_pid = int(i.get("product_id", 0))
            it_name = _item_name(it_pid, product_names)
            it_disc = float(i.get("discount_pct", 0.0))
            acc_parts.append(f"{it_name} ({it_disc:.1f}% off)")

        rej_parts = []
        for r in rejected_items:
            it_pid = int(r.get("product_id", 0))
            it_name = _item_name(it_pid, product_names)
            it_reason = str(r.get("reason", "rejected"))
            rej_parts.append(f"{it_name} ({it_reason})")

        acc_desc = ", ".join(acc_parts)
        rej_desc = ", ".join(rej_parts)
        summary = (
            f"Proposal partially accepted: {len(accepted_items)} approved, "
            f"{len(rejected_items)} rejected."
        )
        explanation = (
            f"The agent proposed {len(proposed_items)} items. "
            f"The policy gate accepted {acc_desc} while rejecting {rej_desc}. "
            f"As a result, the session's trust score changed from {old_score:.1f} "
            f"to {new_score:.1f} ({score_delta:+.1f} pts), {tier_phrase}."
        )
        factors.append(
            DecisionFactor(
                category="gate",
                passed=True,
                title="Partial Gate Decision",
                detail=(
                    f"Accepted {len(accepted_items)} item(s), "
                    f"rejected {len(rejected_items)} item(s)."
                ),
            )
        )

    return DecisionExplanationOut(
        proposal_id=proposal_id,
        session_id=session_id,
        gate_result=gate_result,
        summary=summary,
        explanation=explanation,
        mandate_fingerprint=mandate_fingerprint,
        mandate_verified=True,
        old_score=old_score,
        new_score=new_score,
        score_delta=score_delta,
        old_autonomy_tier=old_autonomy_tier,
        new_autonomy_tier=new_autonomy_tier,
        requires_review=requires_review,
        factors=factors,
    )
