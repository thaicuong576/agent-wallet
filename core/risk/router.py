"""Per-protocol on-chain risk inputs router for Base Sepolia (Chain ID 84532).

Dispatches risk input collection based on the order's protocol, token, and market:
- aave-v3: Aave V3 lending pool reserve data (USDC, USDT)
- aave-stata: Aave Stata vault underlying token reserve data
- morpho-blue: Morpho Blue whitelisted market(id) totals (USDC/WBTC, USDC/cbETH)
- uniswap-v3: Uniswap V3 factory getPool + pool ERC-20 balances valued via usd_value

Fails closed on any RPC error, contract revert, zero total supply where needed,
or unknown protocol, causing the policy engine to deny with RISK.DATA_STALE.
"""

from __future__ import annotations

import json
import pathlib
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any, Final

import eth_abi
from web3 import Web3

from core.protocols.morpho_blue import MARKET_SELECTOR, MORPHO_BLUE, WHITELISTED_MARKETS
from core.protocols.tokens import usd_value
from core.protocols.uniswap_v3 import FACTORY_ADDRESS, SUPPORTED_PAIRS, TOKEN_ADDRESSES
from core.risk.aave import RISK_INPUTS_VALIDATOR, OnchainAaveRiskProvider
from core.risk.provider import (
    ReferenceData,
    RiskDataUnavailableError,
    RpcCallError,
    StaleDataError,
    UnsupportedProtocolError,
    UnsupportedTokenError,
    format_usd,
)

ROOT_DIR: Final[pathlib.Path] = pathlib.Path(__file__).resolve().parent.parent.parent
EXAMPLES_DIR: Final[pathlib.Path] = ROOT_DIR / "contracts" / "examples"


def reference_risk_inputs(now: datetime | None = None) -> dict[str, Any]:
    """Risk inputs stamped fresh from the reference fixture file.

    Used when AGENT_WALLET_RISK_SOURCE=reference or when the risk provider is
    called without an order context (e.g. startup health checks, wiring tests).
    """
    ref_file = EXAMPLES_DIR / "risk-inputs.valid.json"
    risk = json.loads(ref_file.read_text(encoding="utf-8"))
    ts = (now or datetime.now(UTC)).strftime("%Y-%m-%dT%H:%M:%SZ")
    risk["as_of"] = ts
    risk["data_age_seconds"] = 0
    return risk


# Standard ERC-20 balanceOf(address) selector
BALANCE_OF_SELECTOR: Final[str] = "0x70a08231"

# Uniswap V3 Factory getPool(address,address,uint24) selector
GET_POOL_SELECTOR: Final[str] = "0x1698ee82"

# Morpho Blue market(bytes32) return tuple:
# (uint128 totalSupplyAssets, uint128 totalSupplyShares,
#  uint128 totalBorrowAssets, uint128 totalBorrowShares,
#  uint128 lastUpdate, uint128 fee)
MORPHO_MARKET_RETURN_TYPES: Final[list[str]] = [
    "uint128",
    "uint128",
    "uint128",
    "uint128",
    "uint128",
    "uint128",
]

# Field-level sources mapping labelling onchain vs reference values
SOURCES_MAPPING: Final[dict[str, str]] = {
    "pool_tvl_usd": "onchain",
    "utilization_bps": "onchain",
    "reward_apy_share_bps": "reference",
    "apy_deviation_bps": "reference",
    "stablecoin_depeg_bps": "reference",
    "estimated_slippage_bps": "reference",
    "estimated_cost_usd": "reference",
    "estimated_benefit_usd": "reference",
    "data_age_seconds": "onchain",
}


class RiskRouter:
    """Routes risk inputs queries to protocol-specific on-chain providers."""

    def __init__(
        self,
        rpc_callable: Callable[..., Any] | None = None,
        rpc_url: str | None = None,
        reference_data: ReferenceData | None = None,
        max_data_age_seconds: int | None = None,
    ) -> None:
        self.rpc_callable = rpc_callable
        self.rpc_url = rpc_url
        self.reference_data = reference_data or ReferenceData()
        self.max_data_age_seconds = max_data_age_seconds

        self._aave_provider = OnchainAaveRiskProvider(
            rpc_callable=rpc_callable,
            rpc_url=rpc_url,
            reference_data=self.reference_data,
            max_data_age_seconds=max_data_age_seconds,
            sources=SOURCES_MAPPING,
        )

    def _execute_rpc(self, to: str, calldata: str) -> bytes:
        """Execute eth_call and return raw decoded bytes."""
        payload = {
            "jsonrpc": "2.0",
            "method": "eth_call",
            "params": [
                {
                    "to": Web3.to_checksum_address(to),
                    "data": calldata,
                },
                "latest",
            ],
            "id": 1,
        }

        try:
            if self.rpc_callable is not None:
                try:
                    res = self.rpc_callable(payload)
                except TypeError:
                    res = self.rpc_callable(payload["method"], payload["params"])
            elif self.rpc_url:
                from core.simulate import _post_json

                res = _post_json(self.rpc_url, payload)
            else:
                raise RpcCallError("No RPC callable or URL provided to RiskRouter")
        except Exception as exc:
            if isinstance(exc, RpcCallError):
                raise
            raise RpcCallError(f"RPC execution error: {exc}") from exc

        if hasattr(res, "json") and callable(res.json):
            res = res.json()

        if isinstance(res, dict):
            if "error" in res:
                err = res["error"]
                msg = err.get("message") if isinstance(err, dict) else str(err)
                raise RpcCallError(f"RPC returned error: {msg}")
            if "result" not in res:
                raise RpcCallError(f"RPC response missing 'result' field: {res}")
            raw_result = res["result"]
        elif isinstance(res, str):
            raw_result = res
        elif isinstance(res, bytes):
            return res
        else:
            raise RpcCallError(f"Unexpected RPC response format: {type(res)}")

        if not isinstance(raw_result, str):
            raise RpcCallError(f"Expected hex string result from eth_call, got {type(raw_result)}")

        cleaned = raw_result.strip().removeprefix("0x").removeprefix("0X")
        if not cleaned:
            raise RpcCallError("Empty hex result returned by RPC eth_call")

        try:
            return bytes.fromhex(cleaned)
        except ValueError as exc:
            raise RpcCallError(f"Invalid hex string returned by RPC: {exc}") from exc

    def _rpc_method_call(self, method: str, params: list[Any]) -> Any:
        """Adapter matching RpcCall = Callable[[str, list[Any]], Any] for tokens.usd_value."""
        if self.rpc_callable is not None:
            try:
                return self.rpc_callable(method, params)
            except TypeError:
                payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
                res = self.rpc_callable(payload)
                if isinstance(res, dict) and "result" in res:
                    return res["result"]
                return res
        elif self.rpc_url:
            from core.simulate import _post_json

            payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
            res = _post_json(self.rpc_url, payload)
            if isinstance(res, dict) and "result" in res:
                return res["result"]
            return res
        raise RpcCallError("No RPC callable or URL provided to RiskRouter")

    def get_risk_inputs(
        self,
        order: Mapping[str, Any] | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Fetch per-protocol on-chain risk inputs from a validated Order mapping.

        If order is None, returns reference risk inputs (backward compatibility
        for health checks and zero-argument wiring callers).
        """
        if order is None:
            return reference_risk_inputs(now=now)

        if not isinstance(order, Mapping):
            raise RiskDataUnavailableError(f"Expected Order mapping, got {type(order).__name__}")

        protocol = order.get("protocol")
        if not protocol:
            raise UnsupportedProtocolError("Order is missing 'protocol' field")

        token = order.get("token")
        if not token:
            raise UnsupportedTokenError("Order is missing 'token' field")

        if protocol == "aave-v3":
            return self._get_aave(order, token=str(token), protocol="aave-v3", now=now)
        elif protocol == "aave-stata":
            return self._get_aave(order, token=str(token), protocol="aave-stata", now=now)
        elif protocol == "morpho-blue":
            return self._get_morpho_blue(order, token=str(token), now=now)
        elif protocol == "uniswap-v3":
            return self._get_uniswap_v3(order, token=str(token), now=now)

        raise UnsupportedProtocolError(
            f"Protocol '{protocol}' is not supported by RiskRouter. "
            "Supported: ['aave-v3', 'aave-stata', 'morpho-blue', 'uniswap-v3']"
        )

    def __call__(
        self,
        order: Mapping[str, Any] | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        return self.get_risk_inputs(order=order, now=now)

    def _normalize_now(self, now: datetime | None) -> datetime:
        if now is None:
            return datetime.now(UTC)
        if now.tzinfo is None:
            return now.replace(tzinfo=UTC)
        return now.astimezone(UTC)

    def _get_aave(
        self,
        order: Mapping[str, Any],
        token: str,
        protocol: str,
        now: datetime | None,
    ) -> dict[str, Any]:
        """Fetch Aave reserve risk inputs for aave-v3 or aave-stata."""
        res = self._aave_provider.get(protocol="aave-v3", token=token, now=now)
        res["protocol"] = protocol
        res["sources"] = dict(SOURCES_MAPPING)
        self._validate_schema(res)
        return res

    def _get_morpho_blue(
        self,
        order: Mapping[str, Any],
        token: str,
        now: datetime | None,
    ) -> dict[str, Any]:
        """Fetch Morpho Blue whitelisted market totals and compute utilization and TVL."""
        market_key = order.get("market")
        if not market_key or market_key not in WHITELISTED_MARKETS:
            opts = sorted(WHITELISTED_MARKETS.keys())
            raise UnsupportedProtocolError(
                f"Market '{market_key}' is not a whitelisted Morpho Blue market. "
                f"Expected one of {opts}"
            )

        market_cfg = WHITELISTED_MARKETS[str(market_key)]
        market_id_hex = market_cfg.id.removeprefix("0x")
        try:
            market_bytes = bytes.fromhex(market_id_hex)
        except ValueError as exc:
            raise RpcCallError(f"Invalid market id hex: {exc}") from exc

        calldata = MARKET_SELECTOR + eth_abi.encode(["bytes32"], [market_bytes]).hex()
        raw_bytes = self._execute_rpc(MORPHO_BLUE, calldata)

        try:
            vals = eth_abi.decode(MORPHO_MARKET_RETURN_TYPES, raw_bytes)
        except Exception as exc:
            raise RpcCallError(f"Failed to decode Morpho market return: {exc}") from exc

        total_supply_assets: int = vals[0]
        total_borrow_assets: int = vals[2]
        last_update: int = vals[4]

        if total_supply_assets < 0 or total_borrow_assets < 0:
            raise RiskDataUnavailableError("Negative assets in Morpho Blue market totals")

        if total_supply_assets == 0:
            utilization_bps = 0
        else:
            utilization_bps = (total_borrow_assets * 10000) // total_supply_assets
            if utilization_bps > 10000:
                utilization_bps = 10000

        pool_tvl_usd = format_usd(total_supply_assets, decimals=6)

        now_dt = self._normalize_now(now)
        if last_update > 0:
            as_of_dt = datetime.fromtimestamp(last_update, tz=UTC)
            as_of = as_of_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
            data_age_seconds = max(0, int((now_dt - as_of_dt).total_seconds()))
        else:
            as_of = now_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
            data_age_seconds = 0

        if self.max_data_age_seconds is not None and data_age_seconds > self.max_data_age_seconds:
            raise StaleDataError(
                f"Morpho market data is stale: age {data_age_seconds}s exceeds "
                f"max allowed {self.max_data_age_seconds}s"
            )

        result: dict[str, Any] = {
            "schema_version": 1,
            "as_of": as_of,
            "sources": dict(SOURCES_MAPPING),
            "protocol": "morpho-blue",
            "token": token,
            "pool_tvl_usd": pool_tvl_usd,
            "reward_apy_share_bps": self.reference_data.reward_apy_share_bps,
            "apy_deviation_bps": self.reference_data.apy_deviation_bps,
            "stablecoin_depeg_bps": self.reference_data.stablecoin_depeg_bps,
            "utilization_bps": utilization_bps,
            "estimated_slippage_bps": self.reference_data.estimated_slippage_bps,
            "estimated_cost_usd": self.reference_data.estimated_cost_usd,
            "estimated_benefit_usd": self.reference_data.estimated_benefit_usd,
            "data_age_seconds": data_age_seconds,
        }

        self._validate_schema(result)
        return result

    def _get_uniswap_v3(
        self,
        order: Mapping[str, Any],
        token: str,
        now: datetime | None,
    ) -> dict[str, Any]:
        """Fetch Uniswap V3 pool balances for the order's pair and value TVL via usd_value."""
        token_out = order.get("token_out")
        if not token_out:
            # Fall back to matching standard pair if not specified
            for (t_in, t_out), _ in SUPPORTED_PAIRS.items():
                if t_in == token:
                    token_out = t_out
                    break
        if not token_out:
            raise UnsupportedTokenError(f"No valid Uniswap V3 pair for token '{token}'")

        pair = (token, str(token_out))
        if pair not in SUPPORTED_PAIRS:
            raise UnsupportedTokenError(f"Pair {pair} is not supported on Uniswap V3")

        fee = SUPPORTED_PAIRS[pair]
        addr_a = TOKEN_ADDRESSES.get(token)
        addr_b = TOKEN_ADDRESSES.get(str(token_out))
        if not addr_a or not addr_b:
            raise UnsupportedTokenError(f"Address not registered for {token} or {token_out}")

        calldata = (
            GET_POOL_SELECTOR
            + eth_abi.encode(
                ["address", "address", "uint24"],
                [Web3.to_checksum_address(addr_a), Web3.to_checksum_address(addr_b), fee],
            ).hex()
        )

        raw_pool = self._execute_rpc(FACTORY_ADDRESS, calldata)
        try:
            pool_address = eth_abi.decode(["address"], raw_pool)[0]
        except Exception as exc:
            raise RpcCallError(f"Failed to decode getPool return: {exc}") from exc

        if not pool_address or int(pool_address, 16) == 0:
            raise RiskDataUnavailableError(
                f"Uniswap V3 pool for pair ({token}, {token_out}, fee={fee}) does not exist"
            )

        # Query pool balances for token A and token B
        bal_calldata = (
            BALANCE_OF_SELECTOR
            + eth_abi.encode(["address"], [Web3.to_checksum_address(pool_address)]).hex()
        )

        raw_bal_a = self._execute_rpc(addr_a, bal_calldata)
        raw_bal_b = self._execute_rpc(addr_b, bal_calldata)

        try:
            bal_a = eth_abi.decode(["uint256"], raw_bal_a)[0]
            bal_b = eth_abi.decode(["uint256"], raw_bal_b)[0]
        except Exception as exc:
            raise RpcCallError(f"Failed to decode pool balanceOf: {exc}") from exc

        if bal_a == 0 and bal_b == 0:
            raise RiskDataUnavailableError("Uniswap V3 pool has zero balance/liquidity")

        # Value balances through core.protocols.tokens.usd_value
        try:
            usd_a = usd_value(token, bal_a, rpc=self._rpc_method_call)
            usd_b = usd_value(str(token_out), bal_b, rpc=self._rpc_method_call)
        except Exception as exc:
            raise RpcCallError(f"Failed calculating pool token USD values: {exc}") from exc

        total_tvl = usd_a + usd_b
        pool_tvl_usd = f"{total_tvl:.2f}"

        now_dt = self._normalize_now(now)
        as_of = now_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

        est_slippage = (
            int(order["max_slippage_bps"])
            if "max_slippage_bps" in order
            else self.reference_data.estimated_slippage_bps
        )

        result: dict[str, Any] = {
            "schema_version": 1,
            "as_of": as_of,
            "sources": dict(SOURCES_MAPPING),
            "protocol": "uniswap-v3",
            "token": token,
            "pool_tvl_usd": pool_tvl_usd,
            "reward_apy_share_bps": self.reference_data.reward_apy_share_bps,
            "apy_deviation_bps": self.reference_data.apy_deviation_bps,
            "stablecoin_depeg_bps": self.reference_data.stablecoin_depeg_bps,
            "utilization_bps": 0,
            "estimated_slippage_bps": est_slippage,
            "estimated_cost_usd": self.reference_data.estimated_cost_usd,
            "estimated_benefit_usd": self.reference_data.estimated_benefit_usd,
            "data_age_seconds": 0,
        }

        self._validate_schema(result)
        return result

    def _validate_schema(self, data: dict[str, Any]) -> None:
        errors = list(RISK_INPUTS_VALIDATOR.iter_errors(data))
        if errors:
            raise RpcCallError(
                f"Generated risk inputs failed schema validation: {errors[0].message}"
            )
