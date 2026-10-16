"""Portfolio reader and replay reader for the runner.

Reads token balances over RPC and limit counters from AuditLedger to build
a portfolio state dict conforming to portfolio-state.schema.json.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any

import eth_abi
import jsonschema
from web3 import Web3

from core.protocols import registry
from core.protocols.addresses import AAVE_V3_A_USDC, AAVE_V3_USDC
from core.runner.prices import PRICE_SOURCE, PriceUnavailable, price_usd

if TYPE_CHECKING:
    from core.runner.ledger import AuditLedger
else:
    try:
        from core.runner.ledger import AuditLedger
    except ImportError:
        AuditLedger = Any

RpcCall = Callable[[str, list[Any]], Any]

_SCHEMAS_DIR = Path(__file__).resolve().parent.parent.parent / "contracts" / "schemas"
_SCHEMA_PATH = _SCHEMAS_DIR / "portfolio-state.schema.json"

with open(_SCHEMA_PATH, encoding="utf-8") as _f:
    _SCHEMA_DATA = json.load(_f)

PORTFOLIO_VALIDATOR = jsonschema.Draft202012Validator(_SCHEMA_DATA)


class PortfolioUnavailable(Exception):
    """Raised when portfolio data cannot be retrieved from RPC or fixture."""


@dataclass(frozen=True)
class WalletSnapshot:
    """Snapshot of portfolio state and native gas balance."""

    state: dict[str, Any]  # validates against portfolio-state.schema.json
    eth_wei: int  # gas balance, outside PortfolioState (schema has no ETH)


def _make_raw_position(
    token: str,
    location: str,
    balance_int: int,
    decimals: int,
) -> dict[str, Any] | None:
    """Build raw position dict if reference price is available; otherwise omit."""
    try:
        price = price_usd(token)
    except PriceUnavailable:
        # A token without a price is omitted from positions
        return None

    token_units = Decimal(balance_int) / (Decimal(10) ** decimals)
    value_usd = token_units * price

    return {
        "token": token,
        "location": location,
        "balance": {
            "value": str(balance_int),
            "decimals": decimals,
        },
        "price_usd": f"{price:.2f}",
        "value_usd": f"{value_usd:.2f}",
        "_val_dec": value_usd,
    }


def _finalize_positions(
    raw_positions: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], Decimal]:
    """Calculate weights in basis points and total portfolio value in USD."""
    total_val = sum((p["_val_dec"] for p in raw_positions), start=Decimal("0.00"))
    positions: list[dict[str, Any]] = []
    for p in raw_positions:
        val_dec = p.pop("_val_dec")
        if total_val > Decimal(0):
            weight = int(round((val_dec / total_val) * 10000))
            weight = max(0, min(10000, weight))
        else:
            weight = 0
        p["weight_bps"] = weight
        positions.append(p)
    return positions, total_val


class PortfolioReader:
    """Reads live portfolio balances over RPC and limits from the ledger."""

    def __init__(
        self,
        rpc: RpcCall,
        wallet: str,
        ledger: AuditLedger,
        *,
        usdc_address: str = AAVE_V3_USDC,
        ausdc_address: str = AAVE_V3_A_USDC,
        extra_tokens: Mapping[str, str] | None = None,
    ) -> None:
        if not wallet or not isinstance(wallet, str):
            raise ValueError(f"Valid wallet address required, got {wallet!r}")
        self.rpc = rpc
        self.wallet = Web3.to_checksum_address(wallet)
        self.ledger = ledger
        self.usdc_address = Web3.to_checksum_address(usdc_address)
        self.ausdc_address = Web3.to_checksum_address(ausdc_address)
        self.extra_tokens = dict(extra_tokens) if extra_tokens is not None else {}

    def balance_of(self, token_address: str) -> int:
        """Wallet balance of an ERC-20 (PositionReader contract for registered specs)."""
        return self._call_balance_of(token_address)

    def address_for(self, key: str, default: str) -> str:
        """Per-reader address overrides for the keys the Aave spec uses."""
        overrides = {"USDC": self.usdc_address, "aUSDC": self.ausdc_address}
        return overrides.get(key, default)

    def call(self, to: str, data: str) -> str:
        """Generic read-only call (PositionReader protocol)."""
        try:
            res = self.rpc("eth_call", [{"to": to, "data": data}, "latest"])
        except Exception as exc:
            raise PortfolioUnavailable(f"RPC eth_call failed for {to}: {exc}") from exc

        if isinstance(res, dict) and "error" in res:
            raise PortfolioUnavailable(f"RPC returned error for {to}: {res['error']}")
        if isinstance(res, dict) and "result" in res:
            res = res["result"]
        if res is None or res in ("", "0x"):
            raise PortfolioUnavailable(f"RPC returned empty response for {to}")
        return str(res)

    def eth_call(self, to: str, data: str) -> str:
        """Alias for call() used by Morpho Blue and generic contract callers."""
        return self.call(to, data)

    def convert_to_assets(self, vault_address: str, shares: int) -> int:
        """Read convertToAssets(shares) for an ERC-4626 vault."""
        calldata = "0x07a2d13a" + eth_abi.encode(["uint256"], [shares]).hex()
        raw = self.call(vault_address, calldata)
        try:
            return int(raw, 16) if isinstance(raw, str) else int(raw)
        except Exception as exc:
            raise PortfolioUnavailable(
                f"Failed to decode convertToAssets from {vault_address}: {exc}"
            ) from exc

    def _call_balance_of(self, token_address: str) -> int:
        calldata = "0x70a08231" + eth_abi.encode(["address"], [self.wallet]).hex()
        try:
            res = self.rpc("eth_call", [{"to": token_address, "data": calldata}, "latest"])
        except Exception as exc:
            raise PortfolioUnavailable(f"RPC eth_call failed for {token_address}: {exc}") from exc

        if isinstance(res, dict) and "error" in res:
            raise PortfolioUnavailable(f"RPC returned error for {token_address}: {res['error']}")
        if isinstance(res, dict) and "result" in res:
            res = res["result"]
        if res is None or res in ("", "0x"):
            raise PortfolioUnavailable(f"RPC returned empty response for {token_address}")

        try:
            return int(res, 16) if isinstance(res, str) else int(res)
        except Exception as exc:
            raise PortfolioUnavailable(
                f"Invalid balance returned for {token_address}: {exc}"
            ) from exc

    def _call_get_balance(self) -> int:
        try:
            res = self.rpc("eth_getBalance", [self.wallet, "latest"])
        except Exception as exc:
            raise PortfolioUnavailable(f"RPC eth_getBalance failed: {exc}") from exc

        if isinstance(res, dict) and "error" in res:
            raise PortfolioUnavailable(f"RPC eth_getBalance returned error: {res['error']}")
        if isinstance(res, dict) and "result" in res:
            res = res["result"]
        if res is None or res in ("", "0x"):
            raise PortfolioUnavailable("RPC eth_getBalance returned empty response")

        try:
            return int(res, 16) if isinstance(res, str) else int(res)
        except Exception as exc:
            raise PortfolioUnavailable(f"Invalid ETH balance returned: {exc}") from exc

    def read(self, now: datetime) -> WalletSnapshot:
        """Read portfolio state as of `now`. Never caches balances across calls."""
        specs = registry.all_specs()
        max_workers = min(8, 1 + len(specs) + len(self.extra_tokens))

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            eth_fut = executor.submit(self._call_get_balance)
            spec_futs = [(s, executor.submit(s.positions, self.wallet, self)) for s in specs]
            extra_futs = [
                (
                    token_symbol,
                    executor.submit(self._call_balance_of, Web3.to_checksum_address(token_addr)),
                )
                for token_symbol, token_addr in self.extra_tokens.items()
            ]

            try:
                eth_wei = eth_fut.result()
            except PortfolioUnavailable:
                raise
            except Exception as exc:
                raise PortfolioUnavailable(f"RPC eth_getBalance failed: {exc}") from exc

            raw_positions: list[dict[str, Any]] = []

            # Positions come from each registered protocol spec (wallet first, then Aave).
            for spec, fut in spec_futs:
                try:
                    legs = fut.result()
                except PortfolioUnavailable:
                    raise
                except Exception as exc:
                    raise PortfolioUnavailable(
                        f"Failed reading positions for {spec.id}: {exc}"
                    ) from exc

                for leg in legs:
                    pos = _make_raw_position(leg.token, leg.location, leg.balance, leg.decimals)
                    if pos is not None:
                        raw_positions.append(pos)

            # Optional extra tokens (e.g. WETH or test tokens)
            for token_symbol, fut in extra_futs:
                try:
                    bal = fut.result()
                except PortfolioUnavailable:
                    raise
                except Exception as exc:
                    raise PortfolioUnavailable(
                        f"Failed reading balance for extra token {token_symbol}: {exc}"
                    ) from exc

                decimals = 18 if token_symbol == "WETH" else 6
                extra_pos = _make_raw_position(token_symbol, "wallet", bal, decimals)
                if extra_pos is not None:
                    raw_positions.append(extra_pos)

        positions, total_value = _finalize_positions(raw_positions)

        now_utc = now if now.tzinfo is not None else now.replace(tzinfo=UTC)
        now_utc = now_utc.astimezone(UTC)
        since_24h = now_utc - timedelta(hours=24)
        since_1h = now_utc - timedelta(hours=1)

        try:
            spent_24h = self.ledger.spent_usd_since(since_24h)
            count_1h = self.ledger.executed_count_since(since_1h)
        except Exception as exc:
            raise PortfolioUnavailable(f"Ledger limits query failed: {exc}") from exc

        state: dict[str, Any] = {
            "schema_version": 1,
            "as_of": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "price_source": PRICE_SOURCE,
            "positions": positions,
            "total_value_usd": f"{total_value:.2f}",
            "spent_24h_usd": f"{Decimal(str(spent_24h)):.2f}",
            "tx_count_1h": int(count_1h),
        }

        PORTFOLIO_VALIDATOR.validate(state)
        return WalletSnapshot(state=state, eth_wei=eth_wei)


class ReplayPortfolioReader:
    """Loads balances from a fixture file, and fills limits from AuditLedger."""

    def __init__(self, fixture: Path | str, ledger: AuditLedger) -> None:
        self.fixture = Path(fixture)
        self.ledger = ledger

    def read(self, now: datetime) -> WalletSnapshot:
        """Read portfolio state from fixture as of `now`."""
        if not self.fixture.exists():
            raise PortfolioUnavailable(f"Fixture file not found: {self.fixture}")

        try:
            raw_data = json.loads(self.fixture.read_text(encoding="utf-8"))
        except Exception as exc:
            raise PortfolioUnavailable(f"Failed to read fixture: {exc}") from exc

        # Handle either a saved WalletSnapshot dict or a raw PortfolioState dict
        state_data = raw_data.get("state", raw_data)
        eth_wei = int(raw_data.get("eth_wei", 0))

        raw_positions: list[dict[str, Any]] = []
        for pos in state_data.get("positions", []):
            token = pos.get("token")
            location = pos.get("location")
            bal_obj = pos.get("balance", {})
            val_str = str(bal_obj.get("value", "0"))
            decimals = int(bal_obj.get("decimals", 0))
            bal_int = int(val_str)

            p = _make_raw_position(token, location, bal_int, decimals)
            if p is not None:
                raw_positions.append(p)

        positions, total_value = _finalize_positions(raw_positions)

        now_utc = now if now.tzinfo is not None else now.replace(tzinfo=UTC)
        now_utc = now_utc.astimezone(UTC)
        since_24h = now_utc - timedelta(hours=24)
        since_1h = now_utc - timedelta(hours=1)

        try:
            spent_24h = self.ledger.spent_usd_since(since_24h)
            count_1h = self.ledger.executed_count_since(since_1h)
        except Exception as exc:
            raise PortfolioUnavailable(f"Ledger limits query failed: {exc}") from exc

        state: dict[str, Any] = {
            "schema_version": 1,
            "as_of": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "price_source": PRICE_SOURCE,
            "positions": positions,
            "total_value_usd": f"{total_value:.2f}",
            "spent_24h_usd": f"{Decimal(str(spent_24h)):.2f}",
            "tx_count_1h": int(count_1h),
        }

        PORTFOLIO_VALIDATOR.validate(state)
        return WalletSnapshot(state=state, eth_wei=eth_wei)
