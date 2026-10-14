"""Risk provider protocols, reference data defaults, and exceptions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable


class RiskProviderError(Exception):
    """Base exception for all risk provider errors."""


class RiskDataUnavailableError(RiskProviderError):
    """Raised when risk data cannot be retrieved or parsed."""


class RpcCallError(RiskDataUnavailableError):
    """Raised when an on-chain JSON-RPC call fails or reverts."""


class StaleDataError(RiskDataUnavailableError):
    """Raised when risk data age exceeds the allowed freshness threshold."""


class UnsupportedProtocolError(RiskProviderError):
    """Raised when a requested protocol is not supported by the provider."""


class UnsupportedTokenError(RiskProviderError):
    """Raised when a requested token is not supported by the provider."""


@dataclass(frozen=True)
class ReferenceData:
    """Documented reference data for risk inputs not obtainable on testnet.

    On Base Sepolia testnet, secondary metrics (such as protocol-wide reward share,
    30-day mean APY deviation, and multi-exchange stablecoin depeg) are not tracked
    by indexers like DefiLlama. Rather than inventing arbitrary or zero numbers
    silently, these fields are populated from this explicit reference structure
    with conservative defaults and labelled with 'reference' in sources.

    Defaults are chosen to be safe and conservative:
    - reward_apy_share_bps: 0 (no unearned reward subsidy assumed)
    - apy_deviation_bps: 0 (stable APY assumed)
    - stablecoin_depeg_bps: 0 (no depeg on test USDC)
    - estimated_slippage_bps: None (Aave supply/withdraw does not suffer AMM slippage)
    - estimated_cost_usd: "0.40" (conservative Base L2 gas fee estimate)
    - estimated_benefit_usd: "3.10" (conservative net yield over holding horizon)
    - sources: ("reference",) (honest audit trail for where these values originate)
    """

    reward_apy_share_bps: int = 0
    apy_deviation_bps: int = 0
    stablecoin_depeg_bps: int | None = 0
    estimated_slippage_bps: int | None = None
    estimated_cost_usd: str = "0.40"
    estimated_benefit_usd: str = "3.10"
    sources: tuple[str, ...] = ("reference",)
    source: str = "reference"


def format_usd(raw_amount: int, decimals: int = 6) -> str:
    """Format an integer token atomic amount as a USD decimal string.

    Uses pure integer arithmetic (no floats). Complies with UsdString pattern:
    ^(0|[1-9][0-9]{0,17})(\\.[0-9]{1,6})?$
    """
    if raw_amount < 0:
        raise ValueError(f"raw_amount must be non-negative, got {raw_amount}")

    scale = 10**decimals
    whole = raw_amount // scale
    frac = raw_amount % scale

    if frac == 0:
        return f"{whole}.00"

    frac_str = f"{frac:0{decimals}d}".rstrip("0")
    if len(frac_str) < 2:
        frac_str = frac_str.ljust(2, "0")

    return f"{whole}.{frac_str}"


def compute_reserve_metrics(
    total_atoken: int,
    total_stable_debt: int,
    total_variable_debt: int,
    decimals: int = 6,
) -> tuple[int, str, int, int]:
    """Compute reserve metrics using pure integer arithmetic.

    Args:
        total_atoken: Total supply of aTokens (pool deposited liquidity).
        total_stable_debt: Total stable debt.
        total_variable_debt: Total variable debt.
        decimals: Token decimals (default 6 for USDC).

    Returns:
        tuple of (utilization_bps, pool_tvl_usd, total_debt, available_liquidity)
        - utilization_bps: integer 0..10000 (bps)
        - pool_tvl_usd: UsdString
        - total_debt: total debt in atomic units
        - available_liquidity: total_atoken - total_debt in atomic units (>= 0)
    """
    if total_atoken < 0 or total_stable_debt < 0 or total_variable_debt < 0:
        raise ValueError("Reserve amounts must be non-negative integers")

    total_debt = total_stable_debt + total_variable_debt
    available_liquidity = max(0, total_atoken - total_debt)
    pool_tvl_usd = format_usd(total_atoken, decimals=decimals)

    if total_atoken == 0:
        utilization_bps = 0
    else:
        # Basis points: ratio * 10000, integer division
        utilization_bps = (total_debt * 10000) // total_atoken
        if utilization_bps > 10000:
            utilization_bps = 10000

    return utilization_bps, pool_tvl_usd, total_debt, available_liquidity


@runtime_checkable
class RiskProvider(Protocol):
    """Protocol for risk input providers."""

    def get(
        self,
        protocol: str,
        token: str,
        order_usd_amount: Decimal | str | int | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Fetch risk inputs for a given protocol and token.

        Args:
            protocol: Protocol enum identifier ('aave-v3', 'uniswap-v3').
            token: Token enum identifier ('USDC', 'WETH').
            order_usd_amount: Optional order USD amount.
            now: Optional current timestamp (UTC).

        Returns:
            Dictionary validating against contracts/schemas/risk-inputs.schema.json.

        Raises:
            RiskProviderError: If inputs cannot be computed or validation fails.
        """
        ...
