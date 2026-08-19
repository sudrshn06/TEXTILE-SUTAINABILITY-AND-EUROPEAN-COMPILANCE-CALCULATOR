"""
GS1 Digital Link URI Syntax Support (Release 1.7.0).

Provides standards-compliant GS1 Digital Link URI generation, GTIN normalization,
and check-digit validation using official numeric Application Identifiers (AI 01 and AI 10).

Reference:
  - GS1 Digital Link URI Syntax Release 1.7.0
  - GS1 General Specifications (Check Digit Calculation)

Positioning:
  Technical syntax readiness only; not official GS1 certification or conformant resolver.
"""

from __future__ import annotations

import logging
import os
import urllib.parse
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# Default base URL configurable via environment
DEFAULT_DIGITAL_LINK_BASE_URL = os.getenv("GS1_DIGITAL_LINK_BASE_URL", "http://localhost:8000")
SYNTAX_STANDARD = "GS1 Digital Link URI Syntax v1.7.0"
DISCLAIMER = (
    "GS1 Digital Link URI generated from supplied GS1 identifiers. "
    "Technical syntax readiness only; not official GS1 certification or conformant resolver."
)

# Official GS1 XCHAR 82-character subset from GS1 General Specifications (Table 7.11-1)
# DIGIT (10) + UPPERALPHA (26) + LOWERALPHA (26) + 20 XSYMBOLS (!"%&'()*+,-./:;<=>?_)
GS1_XCHAR_SET: frozenset[str] = frozenset(
    "0123456789"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "abcdefghijklmnopqrstuvwxyz"
    "!\"%&'()*+,-./:;<=>?_"
)


def validate_batch_lot(batch_lot: Optional[str]) -> Tuple[bool, Optional[str], Optional[str]]:
    """
    Validate an AI 10 batch/lot identifier against the GS1 XCHAR 82-character subset (1*20 XCHAR).

    Rules:
      - Optional: if None or empty string, returns (True, None, None).
      - Length: minimum 1, maximum 20 characters.
      - Characters: only characters permitted in the GS1 XCHAR (82-character) subset.
      - Disallows characters outside XCHAR (e.g. space, '#', '@', emoji, non-ASCII Unicode).

    Returns:
      (is_valid, cleaned_batch_lot, error_message)
    """
    if batch_lot is None:
        return True, None, None

    s = str(batch_lot)
    if not s:
        return True, None, None

    if len(s) > 20:
        return False, None, f"Batch/Lot length exceeds 20 characters ({len(s)} characters provided)."

    if len(s) < 1:
        return False, None, "Batch/Lot must have at least 1 character."

    for idx, ch in enumerate(s):
        if ch not in GS1_XCHAR_SET:
            return (
                False,
                None,
                f"Batch/Lot contains invalid character '{ch}' at position {idx+1} outside GS1 XCHAR 82-character subset.",
            )

    return True, s, None


def calculate_gtin_check_digit(digits_13: str) -> int:
    """
    Calculate the official GS1 check digit for a 13-digit sequence (left-padded to 13 digits).

    Alternates weights of 3 and 1 from right to left:
    pos 13: *3, pos 12: *1, pos 11: *3, ..., pos 1: *3.
    """
    if len(digits_13) != 13 or not digits_13.isascii() or not digits_13.isdigit():
        raise ValueError("Check digit calculation requires exactly 13 digits.")

    total = 0
    weights = [3, 1] * 6 + [3]  # 13 weights for positions 1..13
    for digit_char, weight in zip(digits_13, weights):
        total += int(digit_char) * weight

    remainder = total % 10
    return (10 - remainder) % 10


def normalize_gtin(gtin: str) -> str:
    """
    Normalize a GTIN (GTIN-8, GTIN-12, GTIN-13, or GTIN-14) to a 14-digit numeric string.

    Validates:
      1. Non-empty string of digits only (no spaces, no hyphens, no formatting).
      2. Valid length: 8, 12, 13, or 14 digits.
      3. Valid GS1 check digit.

    Returns 14-digit string zero-padded on the left.
    Raises ValueError if malformed, invalid characters, invalid length, or invalid check digit.
    """
    if not gtin or not isinstance(gtin, str):
        raise ValueError("GTIN must be a non-empty string.")

    if not gtin.isascii() or not gtin.isdigit():
        raise ValueError(f"GTIN must contain only numeric digits; received '{gtin}'.")

    length = len(gtin)
    if length not in (8, 12, 13, 14):
        raise ValueError(f"GTIN length must be 8, 12, 13, or 14 digits; received {length} digits.")

    # Left pad to 14 digits
    gtin_14 = gtin.zfill(14)

    # Verify check digit
    expected_check = calculate_gtin_check_digit(gtin_14[:13])
    actual_check = int(gtin_14[13])
    if actual_check != expected_check:
        raise ValueError(
            f"Invalid GS1 check digit for GTIN '{gtin}'. Expected {expected_check}, found {actual_check}."
        )

    return gtin_14


def validate_gtin(gtin: Optional[str]) -> Tuple[bool, Optional[str], Optional[str]]:
    """
    Safely validate and normalize a GTIN without raising exceptions.

    Returns:
      (is_valid, normalized_gtin_14, error_message)
    """
    if not gtin:
        return False, None, "No GTIN provided."
    try:
        norm = normalize_gtin(gtin)
        return True, norm, None
    except ValueError as exc:
        return False, None, str(exc)


def build_gs1_digital_link(
    gtin: str,
    batch_lot: Optional[str] = None,
    base_url: Optional[str] = None,
) -> str:
    """
    Construct a GS1 Digital Link URI following Release 1.7.0 syntax.

    Format:
      {base}/id/01/{gtin_14}
      {base}/id/01/{gtin_14}/10/{encoded_batch}
    """
    norm_gtin = normalize_gtin(gtin)
    base = (base_url or os.getenv("GS1_DIGITAL_LINK_BASE_URL") or DEFAULT_DIGITAL_LINK_BASE_URL).rstrip("/")

    if batch_lot is not None and str(batch_lot) != "":
        is_valid_b, clean_batch, err_b = validate_batch_lot(batch_lot)
        if not is_valid_b or clean_batch is None:
            raise ValueError(f"Invalid AI 10 Batch/Lot: {err_b}")
        # AI 10 represents batch/lot. Percent-encode reserved characters.
        encoded_batch = urllib.parse.quote(clean_batch, safe="")
        return f"{base}/id/01/{norm_gtin}/10/{encoded_batch}"

    return f"{base}/id/01/{norm_gtin}"


def get_digital_link_payload(
    gtin: Optional[str],
    batch_lot: Optional[str] = None,
    base_url: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Generate the structured Digital Link readiness payload.

    Returns a dictionary suitable for API output and DPP standards mapping.
    Never fabricates values if GTIN is absent or invalid.
    """
    if not gtin:
        return {
            "status": "unavailable",
            "reason": "no_verified_gtin_supplied",
            "message": "GS1 Digital Link unavailable - no verified GTIN supplied.",
            "syntax_standard": SYNTAX_STANDARD,
            "disclaimer": DISCLAIMER,
        }

    is_valid, norm_gtin, err = validate_gtin(gtin)
    if not is_valid or not norm_gtin:
        return {
            "status": "invalid",
            "reason": "invalid_gtin",
            "error": err,
            "syntax_standard": SYNTAX_STANDARD,
            "disclaimer": DISCLAIMER,
        }

    clean_batch = None
    if batch_lot is not None and str(batch_lot) != "":
        is_valid_b, clean_b, err_b = validate_batch_lot(batch_lot)
        if not is_valid_b or clean_b is None:
            return {
                "status": "invalid",
                "reason": "invalid_batch_lot",
                "error": err_b,
                "syntax_standard": SYNTAX_STANDARD,
                "disclaimer": DISCLAIMER,
            }
        clean_batch = clean_b

    try:
        uri = build_gs1_digital_link(norm_gtin, clean_batch, base_url=base_url)
    except ValueError as exc:
        return {
            "status": "invalid",
            "reason": "invalid_batch_lot",
            "error": str(exc),
            "syntax_standard": SYNTAX_STANDARD,
            "disclaimer": DISCLAIMER,
        }

    ai_mapping: Dict[str, str] = {"01": norm_gtin}
    if clean_batch:
        ai_mapping["10"] = clean_batch

    return {
        "status": "ready",
        "gtin": norm_gtin,
        "batch_lot": clean_batch,
        "digital_link_uri": uri,
        "syntax_standard": SYNTAX_STANDARD,
        "ai_mapping": ai_mapping,
        "disclaimer": DISCLAIMER,
    }
