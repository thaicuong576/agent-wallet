"""Check candidate order amounts against owner instructions."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any

_AMOUNT_PAIR_RE = re.compile(
    r"(?:[\$]\s*(\d[\d,]*(?:\.\d+)?)(?:\s*(USDC|USDT|WETH|WBTC|ETH|CBETH|USDBC|dollars?|usd)\b)?)"
    r"|"
    r"(?:(\d[\d,]*(?:\.\d+)?)\s*(USDC|USDT|WETH|WBTC|ETH|CBETH|USDBC|dollars?|usd)\b)",
    re.IGNORECASE,
)


def _fmt(d: Decimal) -> str:
    s = f"{d:f}"
    return s.rstrip("0").rstrip(".") if "." in s else s


def _token_matches(extracted_tok: str, order_tok: str) -> bool:
    e = extracted_tok.upper()
    o = order_tok.upper()
    if e == o:
        return True
    if e in ("USD", "DOLLAR", "DOLLARS") and o in ("USDC", "USDT", "USDBC"):
        return True
    if e in ("ETH", "WETH") and o in ("ETH", "WETH"):
        return True
    if e in ("BTC", "WBTC") and o in ("BTC", "WBTC"):
        return True
    return False


def check_amounts_match_instruction(instruction: str, orders: list[dict[str, Any]]) -> str | None:
    """Verify that proposed orders carry amounts matching the owner's instruction.

    Returns an error sentence on mismatch or None if valid or no amounts extracted.
    """
    if not instruction or not orders:
        return None

    pairs: list[tuple[Decimal, str]] = []
    for m in _AMOUNT_PAIR_RE.finditer(instruction):
        if m.group(1) is not None:
            num_str = m.group(1).replace(",", "")
            tok = m.group(2) or "USD"
        else:
            num_str = m.group(3).replace(",", "")
            tok = m.group(4) or ""
        try:
            pairs.append((Decimal(num_str), tok.upper()))
        except InvalidOperation:
            continue

    if not pairs:
        return None

    for order in orders:
        amount = order.get("amount")
        if not isinstance(amount, dict):
            continue
        value = amount.get("value")
        decimals = amount.get("decimals")
        if value is None or decimals is None:
            continue
        try:
            whole = Decimal(str(value)) / (Decimal(10) ** int(decimals))
        except (InvalidOperation, TypeError, ValueError):
            continue

        token_sym = str(order.get("token", "")).strip()
        matching_numbers = [num for num, tok in pairs if _token_matches(tok, token_sym)]
        candidate_numbers = matching_numbers if matching_numbers else [num for num, _ in pairs]

        if whole not in candidate_numbers:
            expected = candidate_numbers[0]
            tok_display = token_sym or pairs[0][1]
            return (
                f"The proposed amount ({_fmt(whole)} {tok_display}) does not match the "
                f"amount in your message ({_fmt(expected)} {tok_display}); nothing was proposed. "
                "Please repeat the request."
            )

    return None
