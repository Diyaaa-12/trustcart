"""
Upsell / cross-sell agent.

Calls the configured LLM (Gemini 2.5 Flash by default) to generate
product recommendations for the current cart. The output is structured JSON;
it is ALWAYS routed through the policy gate before reaching the user.

Key design:
  - LLM is called with JSON mode to get deterministic output format.
  - Demo fixtures (is_demo_fixture=True) are excluded from the catalog view
    sent to the LLM. They exist only for Phase 2 injection tests.
  - Raw LLM output is preserved verbatim for the audit log.
  - If the LLM call fails, we return an empty proposal list (no crash).
"""
import json
import logging
from decimal import Decimal, InvalidOperation
from typing import Any

from app.config import settings
from app.services.policy_gate import ProposedItem

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------
_SYSTEM_PROMPT = """\
You are a helpful shopping assistant for TrustCart, an e-commerce platform.
Your job is to suggest additional products that genuinely complement what is
already in the customer's cart.

IMPORTANT RULES:
1. Only recommend products that appear in the catalog provided below.
2. Recommend between 1 and 3 products.
3. Each recommendation may include a discount_pct between 0 and 20 (percent).
4. Choose items that are actually useful together with the cart contents.

You MUST respond with valid JSON only — no markdown, no explanation, just JSON.

Response format:
{
  "recommendations": [
    {
      "product_id": <integer from catalog>,
      "reason": "<one sentence explaining why this complements the cart>",
      "discount_pct": <number 0-20>
    }
  ]
}
"""


def _build_user_prompt(
    cart_items: list[dict[str, Any]],
    catalog_items: list[dict[str, Any]],
) -> str:
    cart_lines = "\n".join(
        f"  - {item['name']} (qty: {item['quantity']}, "
        f"₹{item['unit_price']}, category: {item['category']})"
        for item in cart_items
    )
    catalog_lines = "\n".join(
        f"  - ID {p['id']}: {p['name']} | ₹{p['price']} | {p['category']}"
        for p in catalog_items
        if not p.get("is_demo_fixture", False)  # exclude demo fixtures
    )
    return (
        f"Current cart contents:\n{cart_lines}\n\n"
        f"Available product catalog:\n{catalog_lines}\n\n"
        "Please recommend 1–3 complementary products."
    )


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------
def _parse_recommendations(raw: str) -> tuple[list[ProposedItem], dict[str, Any]]:
    """
    Parse LLM JSON output into ProposedItem list.
    Returns (proposed_items, raw_output_dict).
    Errors are logged; malformed entries are skipped (not crashed).
    """
    raw_output: dict[str, Any] = {"raw_text": raw}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("LLM response is not valid JSON", extra={"error": str(exc), "raw": raw[:500]})
        raw_output["parse_error"] = str(exc)
        return [], raw_output

    raw_output["parsed"] = data
    recommendations = data.get("recommendations", [])
    if not isinstance(recommendations, list):
        logger.warning("LLM 'recommendations' field is not a list", extra={"data": data})
        return [], raw_output

    proposed: list[ProposedItem] = []
    for rec in recommendations:
        try:
            pid = int(rec["product_id"])
            disc = Decimal(str(rec.get("discount_pct", "0")))
            proposed.append(ProposedItem(product_id=pid, discount_pct=disc))
        except (KeyError, ValueError, TypeError, InvalidOperation) as exc:
            logger.warning("Skipping malformed recommendation", extra={"rec": rec, "error": str(exc)})

    return proposed, raw_output


# ---------------------------------------------------------------------------
# Provider implementations
# ---------------------------------------------------------------------------
async def _call_gemini(
    cart_items: list[dict[str, Any]],
    catalog_items: list[dict[str, Any]],
) -> tuple[list[ProposedItem], dict[str, Any]]:
    """Call Google Gemini with JSON mode enabled."""
    import google.generativeai as genai
    from google.generativeai.types import GenerationConfig

    genai.configure(api_key=settings.GEMINI_API_KEY)
    model = genai.GenerativeModel(
        model_name=settings.GEMINI_MODEL,
        system_instruction=_SYSTEM_PROMPT,
        generation_config=GenerationConfig(
            response_mime_type="application/json",
            temperature=0.3,
            max_output_tokens=512,
        ),
    )

    prompt = _build_user_prompt(cart_items, catalog_items)
    response = await model.generate_content_async(prompt)
    raw_text = response.text or ""
    return _parse_recommendations(raw_text)


async def _call_openai(
    cart_items: list[dict[str, Any]],
    catalog_items: list[dict[str, Any]],
) -> tuple[list[ProposedItem], dict[str, Any]]:
    """Call OpenAI with JSON mode enabled."""
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
    prompt = _build_user_prompt(cart_items, catalog_items)

    response = await client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        response_format={"type": "json_object"},
        temperature=0.3,
        max_tokens=512,
    )
    raw_text = response.choices[0].message.content or ""
    return _parse_recommendations(raw_text)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
async def get_proposals(
    cart_items: list[dict[str, Any]],
    catalog_items: list[dict[str, Any]],
) -> tuple[list[ProposedItem], dict[str, Any]]:
    """
    Call the configured LLM to get product recommendations.

    Args:
        cart_items:    Current cart (list of dicts with name/qty/price/category).
        catalog_items: Full catalog (demo fixtures will be excluded internally).

    Returns:
        (proposed_items, raw_llm_output) — raw output is stored verbatim in audit log.

    Note:
        This function NEVER raises. On any LLM failure it returns ([], error_dict).
        The caller (proposals router) handles the empty case gracefully.
    """
    if not cart_items:
        logger.info("Skipping agent call — cart is empty")
        return [], {"skipped": "empty cart"}

    try:
        if settings.LLM_PROVIDER == "gemini":
            return await _call_gemini(cart_items, catalog_items)
        elif settings.LLM_PROVIDER == "openai":
            return await _call_openai(cart_items, catalog_items)
        else:
            logger.error("Unknown LLM provider", extra={"provider": settings.LLM_PROVIDER})
            return [], {"error": f"Unknown provider: {settings.LLM_PROVIDER}"}
    except Exception as exc:  # noqa: BLE001
        logger.exception("LLM call failed", extra={"error": str(exc)})
        return [], {"error": str(exc)}
