"""On-chain Aave V3 risk inputs provider for Base Sepolia."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Final

import eth_abi
import jsonschema
from web3 import Web3

from core.protocols.addresses import AAVE_V3_USDC
from core.protocols.tokens import get_token
from core.risk.provider import (
    ReferenceData,
    RpcCallError,
    StaleDataError,
    UnsupportedProtocolError,
    UnsupportedTokenError,
    compute_reserve_metrics,
)

# Verified AaveProtocolDataProvider proxy on Base Sepolia (Chain ID 84532)
# Sourced from docs/research/testnet-facts-aave.md
AAVE_V3_DATA_PROVIDER: Final[str] = "0xBc9f5b7E248451CdD7cA54e717a2BFe1F32b566b"

# Function selector for IPoolDataProvider.getReserveData(address)
GET_RESERVE_DATA_SELECTOR: Final[str] = "0x35ea6a75"

# IPoolDataProvider.getReserveData return types:
# (uint256 unbacked, uint256 accruedToTreasuryScaled, uint256 totalAToken,
#  uint256 totalStableDebt, uint256 totalVariableDebt, uint256 liquidityRate,
#  uint256 variableBorrowRate, uint256 stableBorrowRate, uint256 averageStableBorrowRate,
#  uint256 liquidityIndex, uint256 variableBorrowIndex, uint40 lastUpdateTimestamp)
RESERVE_DATA_TYPES: Final[list[str]] = [
    "uint256",
    "uint256",
    "uint256",
    "uint256",
    "uint256",
    "uint256",
    "uint256",
    "uint256",
    "uint256",
    "uint256",
    "uint256",
    "uint40",
]

# JSON schema validator for risk inputs
SCHEMA_PATH: Final[Path] = (
    Path(__file__).resolve().parent.parent.parent
    / "contracts"
    / "schemas"
    / "risk-inputs.schema.json"
)

with open(SCHEMA_PATH, encoding="utf-8") as _f:
    RISK_INPUTS_SCHEMA: Final[dict[str, Any]] = json.load(_f)

RISK_INPUTS_VALIDATOR: Final[jsonschema.Draft202012Validator] = jsonschema.Draft202012Validator(
    RISK_INPUTS_SCHEMA
)


class OnchainAaveRiskProvider:
    """Reads Aave V3 Base Sepolia reserve data via JSON-RPC to compute risk inputs.

    Utilization is computed using pure integer arithmetic from total debt over
    total supply. Secondary metrics not available on testnet are taken from a
    documented ReferenceData structure with conservative defaults.
    """

    def __init__(
        self,
        rpc_callable: Callable[..., Any] | None = None,
        rpc_url: str | None = None,
        data_provider_address: str = AAVE_V3_DATA_PROVIDER,
        reference_data: ReferenceData | None = None,
        max_data_age_seconds: int | None = None,
        sources: list[str] | dict[str, Any] | None = None,
    ) -> None:
        self.rpc_callable = rpc_callable
        self.rpc_url = rpc_url
        self.data_provider_address = Web3.to_checksum_address(data_provider_address)
        self.reference_data = reference_data or ReferenceData()
        self.max_data_age_seconds = max_data_age_seconds
        if sources is not None:
            self.sources = dict(sources) if isinstance(sources, dict) else list(sources)
        else:
            self.sources = ["rpc_aave_pool"]

        usdt_spec = get_token("USDT")
        usdt_addr = (
            usdt_spec.address
            if usdt_spec and usdt_spec.address
            else "0x0a215D8ba66387DCA84B284D18c3B4ec3de6E54a"
        )
        self._token_addresses = {
            "USDC": Web3.to_checksum_address(AAVE_V3_USDC),
            "USDT": Web3.to_checksum_address(usdt_addr),
        }

    def _build_calldata(self, token_address: str) -> str:
        """Encode getReserveData(address) calldata."""
        encoded = eth_abi.encode(["address"], [token_address])
        return GET_RESERVE_DATA_SELECTOR + encoded.hex()

    def _execute_rpc(self, calldata: str) -> bytes:
        """Execute eth_call via injected callable or fallback RPC URL.

        Fails closed on any error (returns typed RpcCallError, never zeros).
        """
        payload = {
            "jsonrpc": "2.0",
            "method": "eth_call",
            "params": [
                {
                    "to": self.data_provider_address,
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
                raise RpcCallError("No RPC callable or URL provided to OnchainAaveRiskProvider")
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

        cleaned = raw_result.strip()
        if cleaned.startswith("0x") or cleaned.startswith("0X"):
            cleaned = cleaned[2:]

        if not cleaned:
            raise RpcCallError("Empty hex result returned by RPC eth_call")

        try:
            return bytes.fromhex(cleaned)
        except ValueError as exc:
            raise RpcCallError(f"Invalid hex string returned by RPC: {exc}") from exc

    def get(
        self,
        protocol: str,
        token: str,
        order_usd_amount: Decimal | str | int | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Fetch on-chain risk inputs for Aave V3 reserve."""
        if protocol not in ("aave-v3", "aave-stata"):
            raise UnsupportedProtocolError(
                f"Protocol '{protocol}' is not supported by OnchainAaveRiskProvider. "
                "Expected 'aave-v3' or 'aave-stata'"
            )

        if token not in self._token_addresses:
            supported = list(self._token_addresses.keys())
            raise UnsupportedTokenError(f"Token '{token}' is not supported. Supported: {supported}")

        token_address = self._token_addresses[token]
        calldata = self._build_calldata(token_address)
        raw_bytes = self._execute_rpc(calldata)

        try:
            vals = eth_abi.decode(RESERVE_DATA_TYPES, raw_bytes)
        except Exception as exc:
            raise RpcCallError(f"Failed to decode Aave reserve data ABI response: {exc}") from exc

        total_atoken: int = vals[2]
        total_stable_debt: int = vals[3]
        total_variable_debt: int = vals[4]
        last_update_timestamp: int = vals[11]

        tok_spec = get_token(token)
        token_decimals = tok_spec.decimals if tok_spec else 6

        (
            utilization_bps,
            pool_tvl_usd,
            _total_debt,
            _available_liquidity,
        ) = compute_reserve_metrics(
            total_atoken=total_atoken,
            total_stable_debt=total_stable_debt,
            total_variable_debt=total_variable_debt,
            decimals=token_decimals,
        )

        # Normalize now timestamp
        if now is None:
            now_dt = datetime.now(UTC)
        else:
            if now.tzinfo is None:
                now_dt = now.replace(tzinfo=UTC)
            else:
                now_dt = now.astimezone(UTC)

        if last_update_timestamp > 0:
            as_of_dt = datetime.fromtimestamp(last_update_timestamp, tz=UTC)
            as_of = as_of_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
            data_age_seconds = max(0, int((now_dt - as_of_dt).total_seconds()))
        else:
            as_of = now_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
            data_age_seconds = 0

        # Stale detection fails closed if configured
        if self.max_data_age_seconds is not None and data_age_seconds > self.max_data_age_seconds:
            raise StaleDataError(
                f"Aave reserve data is stale: age {data_age_seconds}s exceeds "
                f"max allowed {self.max_data_age_seconds}s"
            )

        result: dict[str, Any] = {
            "schema_version": 1,
            "as_of": as_of,
            "sources": self.sources,
            "protocol": protocol,
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

        # Validate strictly against JSON schema
        errors = list(RISK_INPUTS_VALIDATOR.iter_errors(result))
        if errors:
            raise RpcCallError(
                f"Generated risk inputs failed schema validation: {errors[0].message}"
            )

        return result
