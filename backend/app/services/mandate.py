"""
Cryptographically signed spend mandate layer (AP2 Protocol inspiration).

Defines the mathematical and cryptographic bounds authorized for an AI agent
within a specific customer session.

Safety Invariant:
    No proposal can be evaluated or presented to a user without an active,
    unexpired, cryptographically verified mandate issued by the server.

Tradeoff note:
    Real-world Agent Payments Protocol (AP2) implementations utilize asymmetric
    public-key cryptography (e.g., Ed25519 or ECDSA) across independent multi-party
    issuers, merchants, and client agents. For this scoped buildathon implementation,
    we use HMAC-SHA256 with a server-held secret. This is a deterministic,
    demo-appropriate surrogate that demonstrates identical tamper-evident bounds
    and invariant enforcement without introducing PKI key-management overhead.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class SpendMandate:
    """
    Verifiable authorization token constraining agent capabilities.
    """
    session_id: str
    max_cumulative_discount_pct: Decimal
    max_items_per_proposal: int
    allowed_categories: tuple[str, ...]
    issued_at: str   # ISO 8601 UTC
    expires_at: str  # ISO 8601 UTC
    nonce: str       # Hex string preventing replay attacks


def serialize_mandate_canonical(mandate: SpendMandate | dict[str, Any]) -> bytes:
    """
    Serialize mandate into canonical JSON bytes with deterministic key ordering.
    Ensures identical byte representations regardless of dictionary insertion order.
    """
    if isinstance(mandate, SpendMandate):
        raw = {
            "session_id": str(mandate.session_id),
            "max_cumulative_discount_pct": str(mandate.max_cumulative_discount_pct),
            "max_items_per_proposal": int(mandate.max_items_per_proposal),
            "allowed_categories": sorted(mandate.allowed_categories),
            "issued_at": str(mandate.issued_at),
            "expires_at": str(mandate.expires_at),
            "nonce": str(mandate.nonce),
        }
    else:
        raw = {
            "session_id": str(mandate["session_id"]),
            "max_cumulative_discount_pct": str(mandate["max_cumulative_discount_pct"]),
            "max_items_per_proposal": int(mandate["max_items_per_proposal"]),
            "allowed_categories": sorted(mandate["allowed_categories"]),
            "issued_at": str(mandate["issued_at"]),
            "expires_at": str(mandate["expires_at"]),
            "nonce": str(mandate["nonce"]),
        }

    return json.dumps(raw, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sign_mandate(mandate: SpendMandate | dict[str, Any], secret: str) -> str:
    """
    Generate an HMAC-SHA256 hex signature over the canonical serialization.
    """
    canonical_bytes = serialize_mandate_canonical(mandate)
    secret_bytes = secret.encode("utf-8")
    return hmac.new(secret_bytes, canonical_bytes, hashlib.sha256).hexdigest()


def compute_mandate_fingerprint(mandate: SpendMandate | dict[str, Any]) -> str:
    """
    Generate a non-sensitive, truncated SHA-256 fingerprint (e.g. 'mnd_a1b2c3d4e5f60718')
    safe for public audit logs without revealing signing secrets.
    """
    canonical_bytes = serialize_mandate_canonical(mandate)
    digest = hashlib.sha256(canonical_bytes).hexdigest()
    return f"mnd_{digest[:16]}"


def create_mandate(
    session_id: str | uuid.UUID,
    secret: str,
    max_cumulative_discount_pct: Decimal = Decimal("10.0"),
    max_items_per_proposal: int = 3,
    allowed_categories: tuple[str, ...] = (
        "Electronics",
        "Accessories",
        "Footwear",
        "Clothing",
        "Books",
    ),
    ttl_minutes: int = 30,
    issued_at: datetime | None = None,
) -> tuple[SpendMandate, str]:
    """
    Issue a new SpendMandate with cryptographic HMAC-SHA256 signature.
    """
    now = issued_at or datetime.now(UTC)
    expiry = now + timedelta(minutes=ttl_minutes)

    mandate = SpendMandate(
        session_id=str(session_id),
        max_cumulative_discount_pct=max_cumulative_discount_pct,
        max_items_per_proposal=max_items_per_proposal,
        allowed_categories=tuple(sorted(allowed_categories)),
        issued_at=now.isoformat(),
        expires_at=expiry.isoformat(),
        nonce=uuid.uuid4().hex,
    )
    signature = sign_mandate(mandate, secret)
    return mandate, signature


def verify_mandate(
    mandate: SpendMandate | dict[str, Any] | None,
    signature: str | None,
    secret: str,
    now: datetime | None = None,
) -> tuple[bool, str]:
    """
    Verify cryptographic signature, temporal validity, and structural integrity.

    Returns:
        (True, "mandate_valid") if all checks pass.
        (False, reason_code) if verification fails.
    """
    if mandate is None or signature is None:
        return False, "mandate_missing"

    # 1. Verify cryptographic HMAC signature
    try:
        expected_sig = sign_mandate(mandate, secret)
    except (KeyError, TypeError, ValueError):
        return False, "mandate_malformed"

    if not hmac.compare_digest(expected_sig, signature):
        return False, "mandate_invalid"

    # 2. Verify expiry
    current_time = now or datetime.now(UTC)
    try:
        expires_at_str = (
            mandate.expires_at if isinstance(mandate, SpendMandate) else mandate["expires_at"]
        )
        expires_at = datetime.fromisoformat(expires_at_str)
    except (KeyError, ValueError, TypeError):
        return False, "mandate_malformed"

    if current_time > expires_at:
        return False, "mandate_expired"

    return True, "mandate_valid"


def mandate_to_dict(mandate: SpendMandate) -> dict[str, Any]:
    """Convert SpendMandate dataclass to JSON-serializable dictionary."""
    d = asdict(mandate)
    d["max_cumulative_discount_pct"] = str(mandate.max_cumulative_discount_pct)
    d["allowed_categories"] = list(mandate.allowed_categories)
    return d
