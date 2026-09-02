"""
TrustCart Trust Score Engine
=============================
A pure, deterministic function -- no database, no LLM calls, no network
I/O, no side effects. Given a session proposal history and current score,
it returns an updated score and the reason for the change.

Design goals (mirrors policy_gate.py):
  1. Zero-mock unit-testable: drive it with plain Python dicts or ProposalRecords.
  2. Safety-critical: hard policy gate caps are NEVER touched by this module.
     Tier only affects UX friction and proposal volume -- it never bypasses the gate.
  3. Auditable: every output carries a machine-readable reason code and enough
     detail for the AuditLog payload.

Autonomy Tiers:
  HIGH   (score >= 70): Agent proposes normally; gate caps already enforced.
  MEDIUM (40 <= score < 70): Every proposal requires an extra "review" step
                              before becoming actionable (not just accept/decline).
  LOW    (score < 40): Proposal volume throttled to 1 per request; all
                       proposals require confirmation.

Injection-signature detection:
  Certain rejection reasons are stronger signals of adversarial input than
  ordinary gate failures. When a rejection matches an injection signature,
  the score decrease is amplified (INJECTION_MULTIPLIER).

  Injection signatures recognised:
    - PRODUCT_NOT_IN_CATALOG: LLM invented an ID that does not exist.
    - ITEM_DISCOUNT_EXCEEDED: LLM tried to push a far-above-cap discount.
    - CATEGORY_NOT_ALLOWED:   LLM proposed an implausible category pairing.

Score arithmetic:
  Rejection (clean):  - REJECT_DECAY      (default 5.0)
  Rejection (inject): - REJECT_DECAY * INJECTION_MULTIPLIER (default 15.0)
  Acceptance (clean): + ACCEPT_GAIN       (default 2.0)
  Score is clamped to [0, 100] after every update.
"""
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DEFAULT_SCORE: float = 100.0
SCORE_MIN: float = 0.0
SCORE_MAX: float = 100.0

# Points lost on a clean policy-gate rejection (non-injection)
REJECT_DECAY: float = 5.0

# Multiplier applied when the rejection reason matches an injection signature
INJECTION_MULTIPLIER: float = 3.0

# Points gained when ALL proposed items pass the gate cleanly
ACCEPT_GAIN: float = 2.0

# Autonomy tier thresholds
TIER_HIGH_MIN: float = 70.0
TIER_MEDIUM_MIN: float = 40.0


# ---------------------------------------------------------------------------
# Injection-signature reasons (subset of policy_gate.RejectionReason values)
# ---------------------------------------------------------------------------
INJECTION_SIGNATURE_REASONS: frozenset[str] = frozenset({
    "product_not_in_catalog",   # LLM invented a non-existent product ID
    "item_discount_exceeded",   # LLM tried a far-above-cap discount
    "category_not_allowed",     # Implausible category cross-sell
})


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------
class ChangeReason(StrEnum):
    """Machine-readable reason codes for trust score changes."""

    REJECTION_CLEAN = "rejection_clean"
    REJECTION_INJECTION_SIGNAL = "rejection_injection_signal"
    ACCEPTANCE_CLEAN = "acceptance_clean"
    NO_CHANGE_NEUTRAL = "no_change_neutral"


class AutonomyTier(StrEnum):
    """Autonomy tier derived from the current trust score."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass(frozen=True)
class ProposalRecord:
    """
    Minimal view of one completed proposal event.

    gate_result:      "accepted" | "rejected" | "partial"
    rejected_reasons: list of rejection reason strings (or RejectionReason enums)
    """

    gate_result: str
    rejected_reasons: list[Any] = field(default_factory=list)


@dataclass(frozen=True)
class TrustScoreResult:
    """Output of compute_trust_score -- fully serialisable, no DB objects."""

    old_score: float
    new_score: float
    delta: float
    reason: ChangeReason
    detail: str
    autonomy_tier: AutonomyTier
    max_proposals_override: int | None  # None = use policy gate default; 1 = LOW tier

    @property
    def score(self) -> float:
        """Convenience alias for new_score."""
        return self.new_score

    @property
    def updated_score(self) -> float:
        """Convenience alias for new_score."""
        return self.new_score

    @property
    def requires_confirmation(self) -> bool:
        """True if the tier requires explicit user review/confirmation step."""
        return self.autonomy_tier in (AutonomyTier.MEDIUM, AutonomyTier.LOW)

    def __float__(self) -> float:
        return float(self.new_score)

    def __int__(self) -> int:
        return int(self.new_score)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _autonomy_tier(score: float) -> AutonomyTier:
    """Determine autonomy tier from numeric score."""
    if score >= TIER_HIGH_MIN:
        return AutonomyTier.HIGH
    if score >= TIER_MEDIUM_MIN:
        return AutonomyTier.MEDIUM
    return AutonomyTier.LOW


def get_autonomy_tier(score: float | int | Decimal) -> AutonomyTier:
    """Public helper to get the autonomy tier for any score."""
    return _autonomy_tier(float(score))


def _extract_reason_str(r: Any) -> str:
    """Extract string value from str, Enum, dict, or object."""
    if isinstance(r, str):
        return r
    if hasattr(r, "value"):
        return str(r.value)
    if isinstance(r, dict):
        return str(r.get("reason", ""))
    if hasattr(r, "reason"):
        val = r.reason
        return str(val.value if hasattr(val, "value") else val)
    return str(r)


def _is_injection_signal(rejected_reasons: Sequence[Any]) -> bool:
    """Return True if any rejection reason matches an injection signature."""
    return any(_extract_reason_str(r) in INJECTION_SIGNATURE_REASONS for r in rejected_reasons)


def _clamp(value: float) -> float:
    return max(SCORE_MIN, min(SCORE_MAX, value))


def _to_proposal_record(obj: Any) -> ProposalRecord:
    """Convert a dict, ProposalRecord, or compatible object into a ProposalRecord."""
    if isinstance(obj, ProposalRecord):
        return obj
    if isinstance(obj, dict):
        gate_result = obj.get("gate_result", "")
        extracted_from_items = [
            item.get("reason")
            for item in obj.get("rejected_items", [])
            if isinstance(item, dict)
        ]
        reasons = (
            obj.get("rejected_reasons")
            or obj.get("rejection_reasons")
            or extracted_from_items
            or []
        )
        return ProposalRecord(gate_result=str(gate_result), rejected_reasons=list(reasons))
    # Object with attributes
    gate_result = getattr(obj, "gate_result", "")
    reasons = (
        getattr(obj, "rejected_reasons", None)
        or getattr(obj, "rejection_reasons", None)
        or []
    )
    return ProposalRecord(gate_result=str(gate_result), rejected_reasons=list(reasons))


# ---------------------------------------------------------------------------
# Core single-step update
# ---------------------------------------------------------------------------
def _apply_single_proposal(
    current_score: float,
    proposal: ProposalRecord,
) -> tuple[float, float, ChangeReason, str]:
    """Calculate single proposal delta, reason, and detail."""
    old_score = _clamp(current_score)
    gate_result = str(proposal.gate_result).lower()
    rejected_reasons = [_extract_reason_str(r) for r in proposal.rejected_reasons]

    if gate_result == "accepted":
        # All items passed -- gradual trust recovery
        new_score = _clamp(old_score + ACCEPT_GAIN)
        delta = new_score - old_score
        reason = ChangeReason.ACCEPTANCE_CLEAN
        detail = (
            f"All proposed items passed the policy gate cleanly. "
            f"Score increased by {delta:.1f}."
        )
    elif gate_result in ("rejected", "partial"):
        injection = _is_injection_signal(rejected_reasons)
        if injection:
            decay = REJECT_DECAY * INJECTION_MULTIPLIER
            reason = ChangeReason.REJECTION_INJECTION_SIGNAL
            matched = [r for r in rejected_reasons if r in INJECTION_SIGNATURE_REASONS]
            detail = (
                f"Policy gate detected injection-signature rejection(s): "
                f"{matched}. Score decreased by {decay:.1f} "
                f"(amplified {INJECTION_MULTIPLIER}x)."
            )
        else:
            decay = REJECT_DECAY
            reason = ChangeReason.REJECTION_CLEAN
            detail = (
                f"Policy gate rejected proposal (result='{gate_result}'). "
                f"Score decreased by {decay:.1f}."
            )
        new_score = _clamp(old_score - decay)
        delta = new_score - old_score
    else:
        new_score = old_score
        delta = 0.0
        reason = ChangeReason.NO_CHANGE_NEUTRAL
        detail = f"Neutral gate result='{gate_result}'; score unchanged."

    return old_score, new_score, reason, detail


# ---------------------------------------------------------------------------
# Public function
# ---------------------------------------------------------------------------
def compute_trust_score(
    proposal_history: Any = None,
    current_score: float | int | Decimal = DEFAULT_SCORE,
    latest_proposal: Any = None,
) -> TrustScoreResult:
    """
    Compute an updated trust score based on proposal history and current score.

    Flexible invocation signatures supported:
      1. compute_trust_score(proposal_history, current_score)
      2. compute_trust_score(current_score, latest_proposal)
      3. compute_trust_score(latest_proposal)  # assumes current_score=100
      4. compute_trust_score(current_score=85.0, latest_proposal=record)

    Args:
        proposal_history: A single ProposalRecord/dict or a sequence of them,
                          OR the current_score float if passed positionally first.
        current_score:    The session current trust score (0-100, default 100).
        latest_proposal:  A single proposal if using keyword arguments.

    Returns:
        TrustScoreResult with old_score, new_score, delta, reason, detail,
        autonomy_tier, max_proposals_override, and requires_confirmation.
    """
    # Disambiguate arguments if called as compute_trust_score(85.0, proposal)
    if isinstance(proposal_history, (int, float, Decimal)):
        actual_score = float(proposal_history)
        proposals_input = current_score if latest_proposal is None else latest_proposal
    else:
        actual_score = float(current_score)
        proposals_input = latest_proposal if proposal_history is None else proposal_history

    # If nothing provided at all
    if proposals_input is None:
        clamped = _clamp(actual_score)
        tier = _autonomy_tier(clamped)
        return TrustScoreResult(
            old_score=clamped,
            new_score=clamped,
            delta=0.0,
            reason=ChangeReason.NO_CHANGE_NEUTRAL,
            detail="No proposal data provided; score unchanged.",
            autonomy_tier=tier,
            max_proposals_override=1 if tier == AutonomyTier.LOW else None,
        )

    # If a list/sequence of proposals is provided:
    if isinstance(proposals_input, (list, tuple)):
        if not proposals_input:
            clamped = _clamp(actual_score)
            tier = _autonomy_tier(clamped)
            return TrustScoreResult(
                old_score=clamped,
                new_score=clamped,
                delta=0.0,
                reason=ChangeReason.NO_CHANGE_NEUTRAL,
                detail="Empty proposal history; score unchanged.",
                autonomy_tier=tier,
                max_proposals_override=1 if tier == AutonomyTier.LOW else None,
            )

        running_score = actual_score
        initial_score = _clamp(running_score)
        last_reason = ChangeReason.NO_CHANGE_NEUTRAL
        last_detail = ""

        for item in proposals_input:
            rec = _to_proposal_record(item)
            _, running_score, last_reason, last_detail = _apply_single_proposal(running_score, rec)

        final_score = running_score
        tier = _autonomy_tier(final_score)
        return TrustScoreResult(
            old_score=initial_score,
            new_score=final_score,
            delta=final_score - initial_score,
            reason=last_reason,
            detail=last_detail,
            autonomy_tier=tier,
            max_proposals_override=1 if tier == AutonomyTier.LOW else None,
        )

    # Single proposal
    rec = _to_proposal_record(proposals_input)
    old_s, new_s, reason, detail = _apply_single_proposal(actual_score, rec)
    tier = _autonomy_tier(new_s)

    return TrustScoreResult(
        old_score=old_s,
        new_score=new_s,
        delta=new_s - old_s,
        reason=reason,
        detail=detail,
        autonomy_tier=tier,
        max_proposals_override=1 if tier == AutonomyTier.LOW else None,
    )


# Aliases for convenience
update_trust_score = compute_trust_score
calculate_trust_score = compute_trust_score
