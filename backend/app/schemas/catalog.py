"""Schemas for agent-readable catalog interfaces."""
from __future__ import annotations

from pydantic import BaseModel, Field


class AgentPolicyConstraints(BaseModel):
    """Policy-relevant metadata and constraints for an external AI buyer agent."""

    max_allowed_discount_pct: float = Field(
        ...,
        description=(
            "Maximum permissible discount percentage allowed by the merchant "
            "policy gate (e.g. 20.0%)."
        ),
    )
    eligible_for_proposal: bool = Field(
        ...,
        description="Whether this product can be nominated by an agent (active and stock > 0).",
    )
    requires_in_stock: bool = Field(
        True,
        description="Policy invariant: item must have stock > 0 at evaluation time.",
    )
    same_category_allowed: bool = Field(
        True,
        description="Policy rule: item is always eligible as a same-category upsell.",
    )


class AgentCatalogEntry(BaseModel):
    """Structured, machine-consumable representation of a product for an AI agent."""

    id: int = Field(..., description="Unique product ID to reference in proposal payloads.")
    name: str = Field(..., description="Product name.")
    description: str = Field(..., description="Detailed product description.")
    price: float = Field(..., description="Base unit price in INR.")
    category: str = Field(..., description="Catalog category.")
    stock: int = Field(..., description="Available inventory units.")
    cross_sell_category_hints: list[str] = Field(
        default_factory=list,
        description="Permissible cross-sell categories configured in merchant policy gate.",
    )
    policy_metadata: AgentPolicyConstraints = Field(
        ...,
        description="Pre-flight policy constraints and thresholds enforced by verification gate.",
    )


class AgentCatalogResponse(BaseModel):
    """Catalog response formatted for automated agent consumption and pre-flight policy."""

    catalog_version: str = Field(
        "1.0.0",
        description="Contract schema version for agent discovery.",
    )
    total_items: int = Field(
        ...,
        description="Total count of eligible catalog items returned.",
    )
    items: list[AgentCatalogEntry] = Field(
        ...,
        description="List of machine-readable catalog entries.",
    )
