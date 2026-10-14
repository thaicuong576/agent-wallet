"""Transaction simulator protocol and JSON-RPC implementations."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class SimulationResult:
    """Outcome of a simulated transaction execution."""

    ok: bool
    error: str | None = None
    gas_used: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class Simulator(Protocol):
    """Protocol for pre-flight transaction simulation."""

    def simulate(self, tx: dict[str, Any]) -> SimulationResult:
        """Simulate a transaction without broadcasting it."""
        ...


class RpcSimulator:
    """RPC-backed transaction simulator implementing simulate-before-sign.

    Uses `eth_simulateV1` with `blockStateCalls` and `validation: true` as the
    primary simulation method, falling back to `eth_call` if `eth_simulateV1`
    is unavailable (method-not-found).
    """

    def __init__(self, rpc_url: str, transport: Any = None):
        self.rpc_url = rpc_url
        self.transport = transport

    def _send_rpc(self, method: str, params: list[Any], req_id: int = 1) -> dict[str, Any]:
        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
            "id": req_id,
        }
        if self.transport is None:
            import httpx

            resp = httpx.post(self.rpc_url, json=payload, timeout=30.0)
            return resp.json()

        if callable(self.transport):
            try:
                res = self.transport(payload)
            except TypeError:
                res = self.transport(self.rpc_url, payload)
        elif hasattr(self.transport, "post"):
            res = self.transport.post(self.rpc_url, json=payload)
        else:
            raise TypeError(f"Unsupported transport type: {type(self.transport)}")

        if hasattr(res, "json") and callable(res.json):
            return res.json()
        return res

    def _format_tx_call(self, tx: dict[str, Any]) -> dict[str, Any]:
        call: dict[str, Any] = {}
        for k, v in tx.items():
            if k == "from_":
                field_name = "from"
            elif k == "input" and "data" not in tx:
                field_name = "data"
            else:
                field_name = k

            if isinstance(v, int):
                call[field_name] = hex(v)
            elif isinstance(v, bytes):
                call[field_name] = "0x" + v.hex()
            elif hasattr(v, "hex") and callable(v.hex):
                h = v.hex()
                call[field_name] = h if h.startswith("0x") else f"0x{h}"
            else:
                call[field_name] = v
        return call

    @staticmethod
    def _is_method_not_found(error: Any) -> bool:
        if isinstance(error, dict):
            if error.get("code") == -32601:
                return True
            msg = str(error.get("message", "")).lower()
            if "not found" in msg or "not exist" in msg or "not available" in msg:
                return True
        return False

    def simulate(self, tx: dict[str, Any]) -> SimulationResult:
        call = self._format_tx_call(tx)

        sim_v1_params = [
            {
                "blockStateCalls": [
                    {
                        "calls": [call],
                    }
                ],
                "validation": True,
                "traceTransfers": True,
            }
        ]

        try:
            resp = self._send_rpc("eth_simulateV1", sim_v1_params, req_id=1)
        except Exception as e:
            return SimulationResult(
                ok=False,
                error=str(e),
                gas_used=None,
                raw={"exception": str(e)},
            )

        # Check for method-not-found to fall back to eth_call
        if resp.get("error") and self._is_method_not_found(resp["error"]):
            return self._simulate_eth_call(call)

        # Non-fallback RPC error
        if resp.get("error"):
            err = resp["error"]
            err_msg = err.get("message") if isinstance(err, dict) else str(err)
            return SimulationResult(ok=False, error=err_msg, gas_used=None, raw=resp)

        result = resp.get("result")
        if isinstance(result, list) and len(result) > 0:
            block = result[0]
            calls = block.get("calls", []) if isinstance(block, dict) else []
            call0 = calls[0] if isinstance(calls, list) and len(calls) > 0 else {}

            gas_used_raw = call0.get("gasUsed")
            if gas_used_raw is None and isinstance(block, dict):
                gas_used_raw = block.get("gasUsed")

            gas_used: int | None = None
            if gas_used_raw is not None:
                if isinstance(gas_used_raw, str) and gas_used_raw.startswith("0x"):
                    gas_used = int(gas_used_raw, 16)
                elif isinstance(gas_used_raw, (int, float)):
                    gas_used = int(gas_used_raw)

            status = call0.get("status") if call0 else None
            if status is None and isinstance(block, dict):
                status = block.get("status")

            is_ok = status in ("0x1", 1, True)
            err_msg = None
            if not is_ok:
                call_err = call0.get("error") if call0 else None
                if call_err is None and isinstance(block, dict):
                    call_err = block.get("error")

                if isinstance(call_err, dict):
                    err_msg = call_err.get("message") or "simulation reverted"
                elif call_err:
                    err_msg = str(call_err)
                else:
                    err_msg = "simulation reverted"

            return SimulationResult(ok=is_ok, error=err_msg, gas_used=gas_used, raw=resp)

        return SimulationResult(
            ok=False,
            error="Invalid simulation response format",
            gas_used=None,
            raw=resp,
        )

    def simulate_bundle(self, txs: list[dict[str, Any]]) -> SimulationResult:
        """Simulate dependent calls (for example approve then supply) in one block (eth_simulateV1).

        Fails closed: there is no sequential eth_call fallback, because each eth_call would see the
        state before the earlier calls and a dependent call would be judged against the wrong state.
        """
        calls = [self._format_tx_call(tx) for tx in txs]
        params = [
            {
                "blockStateCalls": [{"calls": calls}],
                "validation": True,
                "traceTransfers": True,
            }
        ]
        try:
            resp = self._send_rpc("eth_simulateV1", params, req_id=3)
        except Exception as e:
            return SimulationResult(
                ok=False, error=str(e), gas_used=None, raw={"exception": str(e)}
            )

        if resp.get("error"):
            if self._is_method_not_found(resp["error"]):
                msg = "Bundle simulation needs eth_simulateV1, which this RPC does not support"
            else:
                err = resp["error"]
                msg = err.get("message") if isinstance(err, dict) else str(err)
            return SimulationResult(ok=False, error=msg, gas_used=None, raw=resp)

        result = resp.get("result")
        block = result[0] if isinstance(result, list) and result else None
        block_calls = block.get("calls") if isinstance(block, dict) else None
        if not isinstance(block_calls, list) or len(block_calls) != len(calls):
            return SimulationResult(
                ok=False, error="Invalid bundle simulation response format", gas_used=None, raw=resp
            )

        gas_total = 0
        for index, item in enumerate(block_calls):
            if item.get("status") not in ("0x1", 1, True):
                err = item.get("error")
                detail = err.get("message") if isinstance(err, dict) else str(err or "reverted")
                return SimulationResult(
                    ok=False, error=f"Call {index} failed: {detail}", gas_used=None, raw=resp
                )
            raw_gas = item.get("gasUsed")
            if isinstance(raw_gas, str) and raw_gas.startswith("0x"):
                gas_total += int(raw_gas, 16)
        return SimulationResult(ok=True, error=None, gas_used=gas_total, raw=resp)

    def _simulate_eth_call(self, call: dict[str, Any]) -> SimulationResult:
        try:
            resp = self._send_rpc("eth_call", [call, "latest"], req_id=2)
        except Exception as e:
            return SimulationResult(
                ok=False,
                error=str(e),
                gas_used=None,
                raw={"exception": str(e)},
            )

        if resp.get("error"):
            err = resp["error"]
            err_msg = err.get("message") if isinstance(err, dict) else str(err)
            return SimulationResult(ok=False, error=err_msg, gas_used=None, raw=resp)

        if "result" in resp:
            return SimulationResult(ok=True, error=None, gas_used=None, raw=resp)

        return SimulationResult(
            ok=False,
            error="Invalid eth_call response format",
            gas_used=None,
            raw=resp,
        )


__all__ = ["Simulator", "SimulationResult", "RpcSimulator"]
