"""Transaction builder for Aave V3 protocol orders on Base Sepolia."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from core.protocols.aave import build_calls

RpcCall = Callable[[str, list[Any]], Any]


class TxBuildError(Exception):
    """Raised when building a transaction fails."""


class AaveTxBuilder:
    """Gateway-side transaction builder for Aave V3 orders.

    Wraps protocol call generation, estimates gas, checks node freshness and
    nonces, verifies gas funds, and produces fully-formed EIP-1559 transaction dicts.
    """

    def __init__(
        self,
        rpc: RpcCall,
        wallet: str,
        chain_id: int = 84532,
        max_head_age_s: int = 60,
        allow_estimate_fallback: bool = False,
        *,
        clock: Any = None,
        build_calls_fn: Callable[[dict[str, Any], str], list[Any]] | None = None,
    ) -> None:
        self.rpc = rpc
        self.wallet = wallet
        self.chain_id = chain_id
        self.max_head_age_s = max_head_age_s
        self.allow_estimate_fallback = allow_estimate_fallback
        self.clock = clock
        self.build_calls = build_calls_fn if build_calls_fn is not None else build_calls

    def _now_timestamp(self) -> float:
        if self.clock is None:
            return datetime.now(UTC).timestamp()
        val = self.clock() if callable(self.clock) else self.clock
        if isinstance(val, (int, float)):
            return float(val)
        if isinstance(val, str):
            dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
            return dt.timestamp()
        if isinstance(val, datetime):
            if val.tzinfo is None:
                val = val.replace(tzinfo=UTC)
            return val.timestamp()
        raise TxBuildError(f"unsupported clock value: {val!r}")

    def __call__(self, order: dict[str, Any]) -> list[dict[str, Any]]:
        # 1. Chain ID check
        try:
            chain_id_res = self.rpc("eth_chainId", [])
            chain_id = int(chain_id_res, 16) if isinstance(chain_id_res, str) else int(chain_id_res)
        except Exception as err:
            raise TxBuildError(f"eth_chainId failed: {err}") from err

        if chain_id != self.chain_id:
            raise TxBuildError(f"chain_id mismatch: expected {self.chain_id}, got {chain_id}")

        # 2. Stale node check & base fee
        try:
            block = self.rpc("eth_getBlockByNumber", ["latest", False])
        except Exception as err:
            raise TxBuildError(f"eth_getBlockByNumber failed: {err}") from err

        if not isinstance(block, dict) or "timestamp" not in block:
            raise TxBuildError("failed to get latest block or missing timestamp")

        try:
            block_ts_raw = block["timestamp"]
            block_time_s = (
                int(block_ts_raw, 16) if isinstance(block_ts_raw, str) else int(block_ts_raw)
            )
        except Exception as err:
            raise TxBuildError(f"invalid block timestamp: {err}") from err

        now_s = self._now_timestamp()
        age_s = now_s - block_time_s
        if age_s > self.max_head_age_s or age_s < -self.max_head_age_s:
            raise TxBuildError(
                f"stale head: block age {age_s:.1f}s exceeds max {self.max_head_age_s}s"
            )

        base_fee_raw = block.get("baseFeePerGas", 0)
        try:
            base_fee = int(base_fee_raw, 16) if isinstance(base_fee_raw, str) else int(base_fee_raw)
        except Exception as err:
            raise TxBuildError(f"invalid baseFeePerGas in block: {err}") from err

        tip = 1_000_000  # 0.001 gwei
        max_fee_per_gas = base_fee * 2 + tip

        # 3. Nonce check: pending vs latest
        try:
            pending_res = self.rpc("eth_getTransactionCount", [self.wallet, "pending"])
            latest_res = self.rpc("eth_getTransactionCount", [self.wallet, "latest"])
            pending_nonce = (
                int(pending_res, 16) if isinstance(pending_res, str) else int(pending_res)
            )
            latest_nonce = int(latest_res, 16) if isinstance(latest_res, str) else int(latest_res)
        except Exception as err:
            raise TxBuildError(f"failed to get transaction count: {err}") from err

        if pending_nonce < latest_nonce:
            raise TxBuildError(f"pending < latest: pending={pending_nonce}, latest={latest_nonce}")

        # 4. Protocol call translation
        try:
            calls = self.build_calls(order, self.wallet)
        except Exception as err:
            raise TxBuildError(f"build_calls failed: {err}") from err

        if not calls:
            raise TxBuildError("build_calls returned no calls")

        # 5. Build transaction dicts with estimated gas
        txs: list[dict[str, Any]] = []
        for idx, call in enumerate(calls):
            call_to = getattr(call, "to", None) or (call["to"] if isinstance(call, dict) else None)
            call_data = getattr(call, "data", None) or (
                call["data"] if isinstance(call, dict) else None
            )
            call_val = (
                getattr(call, "value", 0) if not isinstance(call, dict) else call.get("value", 0)
            )
            call_value = int(call_val) if call_val is not None else 0

            est_param = {
                "from": self.wallet,
                "to": call_to,
                "data": call_data,
                "value": hex(call_value),
            }

            try:
                est_res = self.rpc("eth_estimateGas", [est_param])
                if isinstance(est_res, dict) and "error" in est_res:
                    raise RuntimeError(str(est_res["error"]))
                gas_est = int(est_res, 16) if isinstance(est_res, str) else int(est_res)
            except Exception as err:
                if not self.allow_estimate_fallback:
                    raise TxBuildError(f"gas estimate failed: {err}") from err
                gas_est = 400_000

            tx_gas = gas_est * 13 // 10
            nonce = pending_nonce + idx

            tx = {
                "chainId": self.chain_id,
                "type": 2,
                "to": call_to,
                "data": call_data,
                "value": call_value,
                "nonce": nonce,
                "gas": tx_gas,
                "maxPriorityFeePerGas": tip,
                "maxFeePerGas": max_fee_per_gas,
            }
            txs.append(tx)

        # 6. Gas funds check
        try:
            bal_res = self.rpc("eth_getBalance", [self.wallet, "latest"])
            if isinstance(bal_res, dict) and "error" in bal_res:
                raise RuntimeError(str(bal_res["error"]))
            balance = int(bal_res, 16) if isinstance(bal_res, str) else int(bal_res)
        except Exception as err:
            raise TxBuildError(f"eth_getBalance failed: {err}") from err

        total_gas_cost = sum(t["gas"] * t["maxFeePerGas"] for t in txs)
        if balance < total_gas_cost:
            raise TxBuildError(
                f"insufficient gas funds: balance {balance} < required {total_gas_cost}"
            )

        return txs
