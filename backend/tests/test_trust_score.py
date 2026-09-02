"""
Unit tests for the Trust Score engine (app/services/trust_score.py).

Pure Python: zero DB, zero network, zero mocks needed.
Safety-critical testing standard mirroring test_policy_gate.py.

Coverage targets:
  - All 3 autonomy tiers (HIGH, MEDIUM, LOW) and boundary conditions (70, 40, 0, 100)
  - Clean acceptance (+2.0 points, clamping at 100)
  - Clean rejection (-5.0 points)
  - Injection-pattern rejections (-15.0 points with 3x multiplier)
    - product_not_in_catalog
    - item_discount_exceeded
    - category_not_allowed
  - Partial gate results (treated as rejections)
  - Clamping at 0 and 100
  - Sequential proposal history simulation
  - Flexible invocation signatures (history first, score first, dicts, enums)
  - Autonomy tier properties (requires_confirmation, max_proposals_override)
"""
from decimal import Decimal

import pytest

from app.services.policy_gate import RejectionReason
from app.services.trust_score import (
    ACCEPT_GAIN,
    DEFAULT_SCORE,
    INJECTION_MULTIPLIER,
    REJECT_DECAY,
    TIER_HIGH_MIN,
    TIER_MEDIUM_MIN,
    AutonomyTier,
    ChangeReason,
    ProposalRecord,
    TrustScoreResult,
    compute_trust_score,
    get_autonomy_tier,
)


# ===========================================================================
# 1. Autonomy tiers and boundaries
# ===========================================================================
class TestAutonomyTiers:
    def test_default_score_is_high_tier(self):
        result = compute_trust_score(current_score=DEFAULT_SCORE)
        assert isinstance(result, TrustScoreResult)
        assert result.new_score == DEFAULT_SCORE
        assert result.autonomy_tier == AutonomyTier.HIGH
        assert result.requires_confirmation is False
        assert result.max_proposals_override is None

    def test_high_tier_exact_boundary(self):
        assert get_autonomy_tier(TIER_HIGH_MIN) == AutonomyTier.HIGH
        assert get_autonomy_tier(int(TIER_HIGH_MIN)) == AutonomyTier.HIGH
        assert get_autonomy_tier(Decimal(str(TIER_HIGH_MIN))) == AutonomyTier.HIGH

        res = compute_trust_score(current_score=TIER_HIGH_MIN)
        assert res.autonomy_tier == AutonomyTier.HIGH
        assert res.requires_confirmation is False
        assert res.max_proposals_override is None

    def test_medium_tier_boundaries(self):
        # Just below high tier is Medium
        just_below_high = TIER_HIGH_MIN - 0.1
        assert get_autonomy_tier(just_below_high) == AutonomyTier.MEDIUM
        res_69 = compute_trust_score(current_score=just_below_high)
        assert res_69.autonomy_tier == AutonomyTier.MEDIUM
        assert res_69.requires_confirmation is True
        assert res_69.max_proposals_override is None

        # Exact medium boundary
        assert get_autonomy_tier(TIER_MEDIUM_MIN) == AutonomyTier.MEDIUM
        res_40 = compute_trust_score(current_score=TIER_MEDIUM_MIN)
        assert res_40.autonomy_tier == AutonomyTier.MEDIUM
        assert res_40.requires_confirmation is True
        assert res_40.max_proposals_override is None

    def test_low_tier_boundaries(self):
        # Just below medium tier is Low
        just_below_med = TIER_MEDIUM_MIN - 0.1
        assert get_autonomy_tier(just_below_med) == AutonomyTier.LOW
        res_39 = compute_trust_score(current_score=just_below_med)
        assert res_39.autonomy_tier == AutonomyTier.LOW
        assert res_39.requires_confirmation is True
        assert res_39.max_proposals_override == 1

        # 0.0 is Low
        assert get_autonomy_tier(0.0) == AutonomyTier.LOW
        res_0 = compute_trust_score(current_score=0.0)
        assert res_0.autonomy_tier == AutonomyTier.LOW
        assert res_0.requires_confirmation is True
        assert res_0.max_proposals_override == 1


# ===========================================================================
# 2. Clean acceptance
# ===========================================================================
class TestCleanAcceptance:
    def test_clean_accept_at_max_score_stays_100(self):
        rec = ProposalRecord(gate_result="accepted")
        res = compute_trust_score(rec, current_score=DEFAULT_SCORE)
        assert res.old_score == DEFAULT_SCORE
        assert res.new_score == DEFAULT_SCORE
        assert res.delta == 0.0
        assert res.reason == ChangeReason.ACCEPTANCE_CLEAN
        assert res.autonomy_tier == AutonomyTier.HIGH

    def test_clean_accept_increases_by_gain(self):
        rec = ProposalRecord(gate_result="accepted")
        start_score = 80.0
        res = compute_trust_score(rec, current_score=start_score)
        assert res.old_score == start_score
        assert res.new_score == start_score + ACCEPT_GAIN
        assert res.delta == ACCEPT_GAIN
        assert res.reason == ChangeReason.ACCEPTANCE_CLEAN
        assert res.autonomy_tier == AutonomyTier.HIGH

    def test_clean_accept_promotes_tier(self):
        # From 69.0 (MEDIUM) + 2.0 -> 71.0 (HIGH)
        rec = ProposalRecord(gate_result="accepted")
        res = compute_trust_score(rec, current_score=69.0)
        assert res.new_score == 69.0 + ACCEPT_GAIN
        assert res.autonomy_tier == AutonomyTier.HIGH
        assert res.requires_confirmation is False

        # From 39.0 (LOW) + 2.0 -> 41.0 (MEDIUM)
        res_low = compute_trust_score(rec, current_score=39.0)
        assert res_low.new_score == 39.0 + ACCEPT_GAIN
        assert res_low.autonomy_tier == AutonomyTier.MEDIUM
        assert res_low.requires_confirmation is True
        assert res_low.max_proposals_override is None


# ===========================================================================
# 3. Clean rejections (non-injection)
# ===========================================================================
class TestCleanRejections:
    @pytest.mark.parametrize("reason", [
        "product_out_of_stock",
        "product_inactive",
        "already_in_cart",
        "session_budget_exceeded",
        "negative_discount",
        "proposal_count_exceeded",
    ])
    def test_standard_rejection_decays_by_reject_decay(self, reason):
        rec = ProposalRecord(gate_result="rejected", rejected_reasons=[reason])
        res = compute_trust_score(rec, current_score=DEFAULT_SCORE)
        assert res.old_score == DEFAULT_SCORE
        assert res.new_score == DEFAULT_SCORE - REJECT_DECAY
        assert res.delta == -REJECT_DECAY
        assert res.reason == ChangeReason.REJECTION_CLEAN

    def test_clean_rejection_using_enum(self):
        rec = ProposalRecord(
            gate_result="rejected",
            rejected_reasons=[RejectionReason.PRODUCT_OUT_OF_STOCK],
        )
        res = compute_trust_score(rec, current_score=DEFAULT_SCORE)
        assert res.new_score == DEFAULT_SCORE - REJECT_DECAY
        assert res.reason == ChangeReason.REJECTION_CLEAN

    def test_partial_gate_result_with_clean_rejection(self):
        rec = ProposalRecord(
            gate_result="partial",
            rejected_reasons=["session_budget_exceeded"],
        )
        res = compute_trust_score(rec, current_score=90.0)
        assert res.new_score == 90.0 - REJECT_DECAY
        assert res.delta == -REJECT_DECAY
        assert res.reason == ChangeReason.REJECTION_CLEAN


# ===========================================================================
# 4. Injection-pattern rejections
# ===========================================================================
class TestInjectionPatternRejections:
    def test_out_of_catalog_triggers_amplified_decay(self):
        rec = ProposalRecord(
            gate_result="rejected",
            rejected_reasons=["product_not_in_catalog"],
        )
        expected_decay = REJECT_DECAY * INJECTION_MULTIPLIER
        res = compute_trust_score(rec, current_score=DEFAULT_SCORE)
        assert res.old_score == DEFAULT_SCORE
        assert res.new_score == DEFAULT_SCORE - expected_decay
        assert res.delta == -expected_decay
        assert res.reason == ChangeReason.REJECTION_INJECTION_SIGNAL
        assert "injection-signature" in res.detail

    def test_item_discount_exceeded_triggers_amplified_decay(self):
        rec = ProposalRecord(
            gate_result="rejected",
            rejected_reasons=[RejectionReason.ITEM_DISCOUNT_EXCEEDED],
        )
        expected_decay = REJECT_DECAY * INJECTION_MULTIPLIER
        res = compute_trust_score(rec, current_score=DEFAULT_SCORE)
        assert res.new_score == DEFAULT_SCORE - expected_decay
        assert res.delta == -expected_decay
        assert res.reason == ChangeReason.REJECTION_INJECTION_SIGNAL

    def test_category_not_allowed_triggers_amplified_decay(self):
        rec = ProposalRecord(
            gate_result="rejected",
            rejected_reasons=["category_not_allowed"],
        )
        expected_decay = REJECT_DECAY * INJECTION_MULTIPLIER
        res = compute_trust_score(rec, current_score=DEFAULT_SCORE)
        assert res.new_score == DEFAULT_SCORE - expected_decay
        assert res.delta == -expected_decay
        assert res.reason == ChangeReason.REJECTION_INJECTION_SIGNAL

    def test_multiple_reasons_with_one_injection_signal(self):
        rec = ProposalRecord(
            gate_result="rejected",
            rejected_reasons=["product_out_of_stock", "item_discount_exceeded"],
        )
        expected_decay = REJECT_DECAY * INJECTION_MULTIPLIER
        res = compute_trust_score(rec, current_score=DEFAULT_SCORE)
        assert res.new_score == DEFAULT_SCORE - expected_decay
        assert res.delta == -expected_decay
        assert res.reason == ChangeReason.REJECTION_INJECTION_SIGNAL

    def test_partial_gate_result_with_injection_signal(self):
        # 1 item accepted, 1 injected item rejected
        rec = ProposalRecord(
            gate_result="partial",
            rejected_reasons=["product_not_in_catalog"],
        )
        expected_decay = REJECT_DECAY * INJECTION_MULTIPLIER
        res = compute_trust_score(rec, current_score=90.0)
        assert res.new_score == 90.0 - expected_decay
        assert res.delta == -expected_decay
        assert res.reason == ChangeReason.REJECTION_INJECTION_SIGNAL


# ===========================================================================
# 5. Clamping and boundaries
# ===========================================================================
class TestClamping:
    def test_clamp_at_zero(self):
        rec = ProposalRecord(
            gate_result="rejected",
            rejected_reasons=["product_not_in_catalog"],
        )
        res = compute_trust_score(rec, current_score=10.0)
        assert res.old_score == 10.0
        assert res.new_score == 0.0
        assert res.delta == -10.0
        assert res.autonomy_tier == AutonomyTier.LOW

    def test_clamp_already_at_zero(self):
        rec = ProposalRecord(gate_result="rejected", rejected_reasons=["out_of_stock"])
        res = compute_trust_score(rec, current_score=0.0)
        assert res.new_score == 0.0
        assert res.delta == 0.0

    def test_clamp_at_100_on_recovery(self):
        rec = ProposalRecord(gate_result="accepted")
        res = compute_trust_score(rec, current_score=99.0)
        assert res.new_score == 100.0
        assert res.delta == 1.0

    def test_initial_score_out_of_range_is_clamped(self):
        rec = ProposalRecord(gate_result="accepted")
        res = compute_trust_score(rec, current_score=150.0)
        assert res.old_score == 100.0
        assert res.new_score == 100.0

        res_neg = compute_trust_score(rec, current_score=-20.0)
        assert res_neg.old_score == 0.0
        assert res_neg.new_score == ACCEPT_GAIN


# ===========================================================================
# 6. Sequential proposals and proposal history
# ===========================================================================
class TestProposalHistorySequence:
    def test_sequential_history_list(self):
        history = [
            ProposalRecord(gate_result="accepted"),
            ProposalRecord(gate_result="rejected", rejected_reasons=["out_of_stock"]),
            ProposalRecord(gate_result="rejected", rejected_reasons=["category_not_allowed"]),
            ProposalRecord(gate_result="accepted"),
        ]
        res = compute_trust_score(proposal_history=history, current_score=DEFAULT_SCORE)
        assert res.old_score == DEFAULT_SCORE
        assert res.new_score == 82.0
        assert res.delta == -18.0
        assert res.autonomy_tier == AutonomyTier.HIGH

    def test_history_drops_into_medium_then_low(self):
        history = [
            ProposalRecord(gate_result="rejected", rejected_reasons=["product_not_in_catalog"]),
            ProposalRecord(gate_result="rejected", rejected_reasons=["item_discount_exceeded"]),
            ProposalRecord(gate_result="rejected", rejected_reasons=["item_discount_exceeded"]),
            ProposalRecord(gate_result="rejected", rejected_reasons=["category_not_allowed"]),
            ProposalRecord(gate_result="rejected", rejected_reasons=["item_discount_exceeded"]),
        ]
        res = compute_trust_score(history, current_score=DEFAULT_SCORE)
        assert res.new_score == 25.0
        assert res.autonomy_tier == AutonomyTier.LOW
        assert res.max_proposals_override == 1
        assert res.requires_confirmation is True

    def test_empty_history(self):
        res = compute_trust_score(proposal_history=[], current_score=85.0)
        assert res.new_score == 85.0
        assert res.delta == 0.0
        assert res.reason == ChangeReason.NO_CHANGE_NEUTRAL

    def test_none_proposals(self):
        res = compute_trust_score(None, current_score=75.0)
        assert res.new_score == 75.0
        assert res.delta == 0.0


# ===========================================================================
# 7. Flexible calling signatures and dictionary inputs
# ===========================================================================
class TestFlexibleInputs:
    def test_dict_proposal_input(self):
        rec_dict = {
            "gate_result": "rejected",
            "rejected_reasons": ["item_discount_exceeded"],
        }
        res = compute_trust_score(rec_dict, DEFAULT_SCORE)
        assert res.new_score == DEFAULT_SCORE - (REJECT_DECAY * INJECTION_MULTIPLIER)
        assert res.reason == ChangeReason.REJECTION_INJECTION_SIGNAL

    def test_dict_with_rejected_items_structure(self):
        rec_dict = {
            "gate_result": "rejected",
            "rejected_items": [{"product_id": 99, "reason": "product_not_in_catalog"}],
        }
        res = compute_trust_score(rec_dict, DEFAULT_SCORE)
        assert res.new_score == DEFAULT_SCORE - (REJECT_DECAY * INJECTION_MULTIPLIER)

    def test_score_first_positional_signature(self):
        rec = ProposalRecord(gate_result="accepted")
        res = compute_trust_score(80.0, rec)
        assert res.new_score == 80.0 + ACCEPT_GAIN

    def test_type_conversions(self):
        rec = ProposalRecord(gate_result="accepted")
        res = compute_trust_score(rec, 90.0)
        expected = 90.0 + ACCEPT_GAIN
        assert float(res) == expected
        assert int(res) == int(expected)
        assert res.score == expected
        assert res.updated_score == expected

    def test_unknown_gate_result_is_neutral(self):
        rec = ProposalRecord(gate_result="unknown_status")
        res = compute_trust_score(rec, 80.0)
        assert res.new_score == 80.0
        assert res.delta == 0.0
        assert res.reason == ChangeReason.NO_CHANGE_NEUTRAL

    def test_custom_object_with_attributes(self):
        class DummyObj:
            gate_result = "rejected"
            rejected_reasons = ["product_not_in_catalog"]

        res = compute_trust_score(DummyObj(), DEFAULT_SCORE)
        assert res.new_score == DEFAULT_SCORE - (REJECT_DECAY * INJECTION_MULTIPLIER)

    def test_custom_object_with_rejection_reasons(self):
        class DummyObj2:
            gate_result = "rejected"
            rejection_reasons = ["category_not_allowed"]

        res = compute_trust_score(DummyObj2(), DEFAULT_SCORE)
        assert res.new_score == DEFAULT_SCORE - (REJECT_DECAY * INJECTION_MULTIPLIER)

    def test_extract_reason_str_from_nested_object(self):
        class ReasonObj:
            value = "item_discount_exceeded"

        class ItemObj:
            reason = ReasonObj()

        rec = ProposalRecord(gate_result="rejected", rejected_reasons=[ItemObj()])
        res = compute_trust_score(rec, DEFAULT_SCORE)
        assert res.new_score == DEFAULT_SCORE - (REJECT_DECAY * INJECTION_MULTIPLIER)
