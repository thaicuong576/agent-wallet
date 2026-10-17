"""Uniswap V3 protocol adapter for Base Sepolia (Chain ID 84532).

Encodes calldata for ERC-20 approve and SwapRouter02 exactInputSingle swaps.
Amount out minimum is derived from AaveOracle fair value and max_slippage_bps.
"""

from __future__ import annotations

import json
import os
import urllib.request
from collections.abc import Callable, Mapping
from decimal import Decimal
from typing import Any, Final

import eth_abi
from web3 import Web3

from core.protocols.addresses import BASE_SEPOLIA_CHAIN_ID
from core.protocols.registry import (
    BuildContext,
    Call,
    PositionLeg,
    PositionReader,
    ProtocolSpec,
    register,
)
from core.protocols.tokens import (
    PriceUnavailable,
    enable_token,
    get_token,
    unit_price_usd,
)

PROTOCOL_ID: Final[str] = "uniswap-v3"

# Verified Base Sepolia contract addresses (docs/research/testnet-facts-defi-survey.md)
FACTORY_ADDRESS: Final[str] = "0x4752ba5DBc23f44D87826276BF6Fd6b1C372aD24"
SWAP_ROUTER_02: Final[str] = "0x94cC0AaC535CCDB3C01d6787D6413C739ae12bc4"
QUOTER_V2: Final[str] = "0xC5290058841028F1614F3A6F0F5816cAd0df5E27"

# Token contracts on Base Sepolia
USDC_ADDRESS: Final[str] = "0xba50Cd2A20f6DA35D788639E581bca8d0B5d4D5f"
WETH_ADDRESS: Final[str] = "0x4200000000000000000000000000000000000006"
WBTC_ADDRESS: Final[str] = "0x54114591963CF60EF3aA63bEfD6eC263D98145a4"

# Selectors
APPROVE_SELECTOR: Final[str] = "0x095ea7b3"
EXACT_INPUT_SINGLE_SELECTOR: Final[str] = "0x04e45aaf"
QUOTE_EXACT_INPUT_SINGLE_SELECTOR: Final[str] = "0xc6a5026a"

MAX_ALLOWED_SLIPPAGE_BPS: Final[int] = 100  # 1% cap

# Fee tiers with liquidity on Base Sepolia: USDC/WETH 3000, USDC/WBTC 500
SUPPORTED_PAIRS: Final[dict[tuple[str, str], int]] = {
    ("USDC", "WETH"): 3000,
    ("WETH", "USDC"): 3000,
    ("USDC", "WBTC"): 500,
    ("WBTC", "USDC"): 500,
}

TOKEN_ADDRESSES: Final[dict[str, str]] = {
    "USDC": USDC_ADDRESS,
    "WETH": WETH_ADDRESS,
    "WBTC": WBTC_ADDRESS,
}

SUPPORTED_TOKENS: Final[frozenset[str]] = frozenset(TOKEN_ADDRESSES.keys())
SUPPORTED_FUNCTIONS: Final[frozenset[str]] = frozenset({"approve", "exactInputSingle"})
SUPPORTED_RECIPIENTS: Final[frozenset[str]] = frozenset({"self", "uniswap_router"})


class OrderValidationError(ValueError):
    """Raised when an order fails Uniswap v3 validation."""

    pass


def _default_rpc(method: str, params: list[Any]) -> Any:
    """Standard library HTTP JSON-RPC call against Base Sepolia RPC."""
    url = os.environ.get("BASE_SEPOLIA_RPC_URL", "https://sepolia.base.org")
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json", "User-Agent": "agent-wallet/0.1"},
    )
    with urllib.request.urlopen(req, timeout=15.0) as resp:
        res = json.loads(resp.read().decode())
    if "error" in res:
        raise RuntimeError(f"RPC error: {res['error']}")
    return res.get("result")


def validate_order(order: dict[str, Any]) -> None:
    """Validate order fields against Uniswap v3 adapter constraints."""
    if not isinstance(order, dict):
        raise OrderValidationError("Order must be a dictionary")

    protocol = order.get("protocol")
    if protocol != PROTOCOL_ID:
        raise OrderValidationError(
            f"Unsupported protocol for Uniswap v3 adapter: '{protocol}'. Expected '{PROTOCOL_ID}'"
        )

    func = order.get("function")
    if func not in SUPPORTED_FUNCTIONS:
        funcs_str = sorted(SUPPORTED_FUNCTIONS)
        raise OrderValidationError(
            f"Unsupported function for Uniswap v3 adapter: '{func}'. Expected one of {funcs_str}"
        )

    token = order.get("token")
    if token not in SUPPORTED_TOKENS:
        tokens_str = sorted(SUPPORTED_TOKENS)
        raise OrderValidationError(
            f"Unsupported token for Uniswap v3 adapter: '{token}'. Expected one of {tokens_str}"
        )

    amount_obj = order.get("amount")
    if not isinstance(amount_obj, dict) or "value" not in amount_obj:
        raise OrderValidationError("Order amount is missing or invalid")
    try:
        amount_val = int(amount_obj["value"])
    except (ValueError, TypeError) as exc:
        raise OrderValidationError(f"Invalid amount value: {exc}") from exc
    if amount_val <= 0:
        raise OrderValidationError(f"Amount value must be positive, got {amount_val}")

    recipient = order.get("recipient")

    if func == "approve":
        if recipient not in SUPPORTED_RECIPIENTS:
            recipients_str = sorted(SUPPORTED_RECIPIENTS)
            raise OrderValidationError(
                f"Unsupported recipient for Uniswap v3 approve: '{recipient}'. "
                f"Expected one of {recipients_str}"
            )

    elif func == "exactInputSingle":
        if recipient != "self":
            raise OrderValidationError(
                f"Recipient for exactInputSingle must be 'self', got '{recipient}'"
            )

        token_out = order.get("token_out")
        if not token_out or token_out not in SUPPORTED_TOKENS:
            raise OrderValidationError(
                f"Unsupported or missing token_out for exactInputSingle: '{token_out}'"
            )

        if token == token_out:
            raise OrderValidationError(f"token and token_out cannot be the same: '{token}'")

        if (token, token_out) not in SUPPORTED_PAIRS:
            raise OrderValidationError(
                f"Pair ({token}, {token_out}) is not allowed on Uniswap v3 "
                "(allowed: USDC<->WETH, USDC<->WBTC)"
            )

        max_slippage = order.get("max_slippage_bps")
        if max_slippage is None:
            raise OrderValidationError("max_slippage_bps is required for exactInputSingle")
        try:
            slippage_val = int(max_slippage)
        except (ValueError, TypeError) as exc:
            raise OrderValidationError(f"Invalid max_slippage_bps: {exc}") from exc
        if slippage_val < 0:
            raise OrderValidationError(f"max_slippage_bps must be non-negative, got {slippage_val}")
        if slippage_val > MAX_ALLOWED_SLIPPAGE_BPS:
            raise OrderValidationError(
                f"max_slippage_bps {slippage_val} exceeds adapter ceiling "
                f"of {MAX_ALLOWED_SLIPPAGE_BPS} (1%)"
            )


def build_approve_calldata(spender: str, amount: int) -> str:
    """Encode ERC-20 approve(address,uint256) calldata."""
    if amount <= 0:
        raise ValueError(f"Approve amount must be positive, got {amount}")
    encoded = eth_abi.encode(
        ["address", "uint256"],
        [Web3.to_checksum_address(spender), amount],
    )
    return APPROVE_SELECTOR + encoded.hex()


def build_exact_input_single_calldata(
    token_in: str,
    token_out: str,
    fee: int,
    recipient: str,
    amount_in: int,
    amount_out_minimum: int,
    sqrt_price_limit_x96: int = 0,
) -> str:
    """Encode SwapRouter02 exactInputSingle calldata."""
    if amount_in <= 0:
        raise ValueError(f"amount_in must be positive, got {amount_in}")
    if amount_out_minimum < 0:
        raise ValueError(f"amount_out_minimum must be non-negative, got {amount_out_minimum}")
    params = (
        Web3.to_checksum_address(token_in),
        Web3.to_checksum_address(token_out),
        fee,
        Web3.to_checksum_address(recipient),
        amount_in,
        amount_out_minimum,
        sqrt_price_limit_x96,
    )
    encoded = eth_abi.encode(
        ["(address,address,uint24,address,uint256,uint256,uint160)"],
        [params],
    )
    return EXACT_INPUT_SINGLE_SELECTOR + encoded.hex()


def build_quote_exact_input_single_calldata(
    token_in: str,
    token_out: str,
    amount_in: int,
    fee: int,
    sqrt_price_limit_x96: int = 0,
) -> str:
    """Encode QuoterV2 quoteExactInputSingle((address,address,uint256,uint24,uint160)) calldata."""
    if amount_in <= 0:
        raise ValueError(f"amount_in must be positive, got {amount_in}")
    params = (
        Web3.to_checksum_address(token_in),
        Web3.to_checksum_address(token_out),
        amount_in,
        fee,
        sqrt_price_limit_x96,
    )
    encoded = eth_abi.encode(
        ["(address,address,uint256,uint24,uint160)"],
        [params],
    )
    return QUOTE_EXACT_INPUT_SINGLE_SELECTOR + encoded.hex()


def compute_amount_out_minimum(
    token_in: str,
    token_out: str,
    amount_in: int,
    max_slippage_bps: int,
    *,
    rpc: Callable[[str, list[Any]], Any] | None = None,
) -> int:
    """Compute minimum output amount from Aave oracle fair value and max slippage bps.

    Fair value:
        usd_in = (amount_in / 10**decimals_in) * unit_price_usd(token_in)
        whole_out = usd_in / unit_price_usd(token_out)
        raw_out = whole_out * 10**decimals_out
        amount_out_minimum = floor(raw_out * (1 - max_slippage_bps / 10000))
    """
    if max_slippage_bps > MAX_ALLOWED_SLIPPAGE_BPS:
        raise OrderValidationError(
            f"max_slippage_bps {max_slippage_bps} exceeds adapter ceiling "
            f"of {MAX_ALLOWED_SLIPPAGE_BPS} (1%)"
        )
    if max_slippage_bps < 0:
        raise OrderValidationError(f"max_slippage_bps must be non-negative, got {max_slippage_bps}")
    if amount_in <= 0:
        raise OrderValidationError(f"amount_in must be positive, got {amount_in}")

    spec_in = get_token(token_in)
    spec_out = get_token(token_out)
    if spec_in is None or spec_out is None:
        raise OrderValidationError(f"Unknown token: {token_in} or {token_out}")

    price_in = unit_price_usd(token_in, rpc=rpc)
    price_out = unit_price_usd(token_out, rpc=rpc)

    whole_in = Decimal(amount_in) / (Decimal(10) ** spec_in.decimals)
    usd_in = whole_in * price_in
    whole_out = usd_in / price_out
    raw_out = whole_out * (Decimal(10) ** spec_out.decimals)
    min_out = raw_out * Decimal(10000 - max_slippage_bps) / Decimal(10000)
    return int(min_out)


def check_pool_price_guard(
    token_in: str,
    token_out: str,
    amount_in: int,
    *,
    rpc: Callable[[str, list[Any]], Any] | None = None,
) -> dict[str, Any]:
    """Pre-trade check: compares QuoterV2 quote with Aave oracle fair value.

    Returns rule hit dict with code RISK.POOL_PRICE_DEVIATION if the pool quote
    is more than 3% worse than oracle fair value.
    """
    rpc_caller = rpc or _default_rpc
    if (token_in, token_out) not in SUPPORTED_PAIRS:
        return {
            "code": "ACCESS.MARKET_NOT_ALLOWED",
            "evidence": {"pair": f"{token_in}-{token_out}"},
        }

    fee = SUPPORTED_PAIRS[(token_in, token_out)]
    calldata = build_quote_exact_input_single_calldata(
        token_in=TOKEN_ADDRESSES[token_in],
        token_out=TOKEN_ADDRESSES[token_out],
        amount_in=amount_in,
        fee=fee,
        sqrt_price_limit_x96=0,
    )

    try:
        res = rpc_caller("eth_call", [{"to": QUOTER_V2, "data": calldata}, "latest"])
        if isinstance(res, Mapping):
            if "error" in res:
                raise RuntimeError(str(res["error"]))
            res = res.get("result")
        raw_bytes = bytes.fromhex(res[2:] if isinstance(res, str) and res.startswith("0x") else res)
        decoded = eth_abi.decode(["uint256", "uint160", "uint32", "uint256"], raw_bytes)
        pool_amount_out = decoded[0]
    except Exception as exc:
        return {
            "code": "RISK.POOL_PRICE_DEVIATION",
            "evidence": {"error": f"QuoterV2 call failed: {exc}"},
        }

    # Oracle fair value with 0% slippage
    try:
        fair_amount_out = compute_amount_out_minimum(
            token_in, token_out, amount_in, 0, rpc=rpc_caller
        )
    except PriceUnavailable as exc:
        return {
            "code": "RISK.PRICE_UNAVAILABLE",
            "evidence": {"error": str(exc)},
        }

    if fair_amount_out <= 0:
        return {}

    deviation_bps = int(
        (Decimal(fair_amount_out - pool_amount_out) / Decimal(fair_amount_out)) * 10000
    )
    # If pool returns more than 3% (300 bps) less than oracle fair value:
    if deviation_bps > 300:
        return {
            "code": "RISK.POOL_PRICE_DEVIATION",
            "evidence": {
                "token_in": token_in,
                "token_out": token_out,
                "amount_in": amount_in,
                "pool_amount_out": pool_amount_out,
                "fair_amount_out": fair_amount_out,
                "deviation_bps": deviation_bps,
            },
        }
    return {}


def build_calls(
    order: Mapping[str, Any],
    ctx: BuildContext,
    *,
    rpc: Callable[[str, list[Any]], Any] | None = None,
) -> list[Call]:
    """Turn a validated Uniswap v3 Order into Call(to, data, value) objects."""
    order_dict = dict(order)
    validate_order(order_dict)

    func = order_dict["function"]
    token = order_dict["token"]
    amount = int(order_dict["amount"]["value"])

    if func == "approve":
        # Target is the ERC-20 token being approved; spender is SwapRouter02
        data = build_approve_calldata(SWAP_ROUTER_02, amount)
        return [Call(to=TOKEN_ADDRESSES[token], data=data, value=0)]

    elif func == "exactInputSingle":
        token_out = order_dict["token_out"]
        fee = SUPPORTED_PAIRS[(token, token_out)]
        recipient = ctx.wallet
        max_slippage = int(order_dict["max_slippage_bps"])

        rpc_caller = rpc or getattr(ctx, "rpc", None) or _default_rpc

        amount_out_min = compute_amount_out_minimum(
            token_in=token,
            token_out=token_out,
            amount_in=amount,
            max_slippage_bps=max_slippage,
            rpc=rpc_caller,
        )

        data = build_exact_input_single_calldata(
            token_in=TOKEN_ADDRESSES[token],
            token_out=TOKEN_ADDRESSES[token_out],
            fee=fee,
            recipient=recipient,
            amount_in=amount,
            amount_out_minimum=amount_out_min,
            sqrt_price_limit_x96=0,
        )
        return [Call(to=SWAP_ROUTER_02, data=data, value=0)]

    raise OrderValidationError(f"Unhandled function: {func}")


def _build(order: Mapping[str, Any], ctx: BuildContext) -> list[Call]:
    return build_calls(order, ctx)


def _uniswap_positions(wallet: str, reader: PositionReader) -> list[PositionLeg]:
    """List idle WETH and WBTC wallet balances acquired via Uniswap swaps."""
    weth_addr = reader.address_for("WETH", WETH_ADDRESS)
    wbtc_addr = reader.address_for("WBTC", WBTC_ADDRESS)
    return [
        PositionLeg("WETH", "wallet", reader.balance_of(weth_addr), 18),
        PositionLeg("WBTC", "wallet", reader.balance_of(wbtc_addr), 8),
    ]


def _describe_uniswap(order: Mapping[str, Any]) -> str:
    func = str(order.get("function"))
    if func == "approve":
        return f"Approve Uniswap v3 SwapRouter02 to spend {order.get('token')}"
    if func == "exactInputSingle":
        return f"Swap {order.get('token')} for {order.get('token_out')} on Uniswap v3"
    return f"{func} on Uniswap v3"


# Enable WETH and WBTC in the token registry on module load
enable_token("WETH")
enable_token("WBTC")

register(
    ProtocolSpec(
        id=PROTOCOL_ID,
        name="Uniswap v3",
        category="dex",
        chain_id=BASE_SEPOLIA_CHAIN_ID,
        functions=SUPPORTED_FUNCTIONS,
        tokens=SUPPORTED_TOKENS,
        spenders=frozenset({SWAP_ROUTER_02}),
        build_calls=_build,
        positions=_uniswap_positions,
        describe=_describe_uniswap,
        recipients=SUPPORTED_RECIPIENTS,
        summary="Swap USDC, WETH and WBTC on Uniswap v3 (Base Sepolia)",
    )
)
