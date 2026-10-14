"""First live transaction: supply 1 test USDC to Aave V3 on Base Sepolia, through the gateway.

Testnet only. The order goes through the full path: IPS policy, tier gate, simulation, signer,
audit record. Two orders run in sequence (approve exactly 1 USDC, then supply 1 USDC).

Default is a dry run: the real policy engine and a real RPC bundle simulation of approve + supply,
with a fake signer, nothing is broadcast. A live run needs both `--execute` and the environment
variable AGENT_WALLET_LIVE_CONFIRM=I_APPROVE_1_USDC_ON_BASE_SEPOLIA (the owner's go).

Usage:
    python scripts/live_first_tx.py                 # dry run
    python scripts/live_first_tx.py --execute       # live (needs the confirm variable)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.audit.sink import SqliteAuditSink  # noqa: E402
from core.gateway.factory import build_signer  # noqa: E402
from core.gateway.gateway import Gateway  # noqa: E402
from core.gateway.policy import EnginePolicy  # noqa: E402
from core.gateway.txbuilder import AaveTxBuilder  # noqa: E402
from core.policy.ips import parse_ips  # noqa: E402
from core.protocols.aave import AAVE_V3_POOL  # noqa: E402
from core.simulate import RpcSimulator  # noqa: E402
from core.tiers.owner import OwnerControls  # noqa: E402
from core.tiers.store import TierStore  # noqa: E402
from core.tiers.view import TierView  # noqa: E402

RPC_URL = os.environ.get("BASE_SEPOLIA_RPC_URL", "https://sepolia.base.org")
CHAIN_ID = 84532
CONFIRM_VALUE = "I_APPROVE_1_USDC_ON_BASE_SEPOLIA"
AMOUNT = "1000000"  # 1 USDC, 6 decimals
EXAMPLES = ROOT / "contracts" / "examples"
EVIDENCE = ROOT / "docs" / "evidence" / "tx_hashes.md"
HEADERS = {"User-Agent": "agent-wallet/0.1"}


def rpc(method: str, params: list[Any]) -> Any:
    resp = httpx.post(
        RPC_URL,
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
        headers=HEADERS,
        timeout=30.0,
    ).json()
    if resp.get("error"):
        raise RuntimeError(f"{method}: {resp['error']}")
    return resp["result"]


def now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def example(name: str) -> Path:
    return EXAMPLES / name


def temporary_ips() -> Any:
    """IPS fixture for this run only: USDC approve and supply to the Aave pool, at most 1 USDC."""
    ips = json.loads(example("ips.valid.json").read_text(encoding="utf-8"))
    ips["universe"] = {
        "tokens": ["USDC"],
        "protocols": ["aave-v3"],
        "functions": ["approve", "supply"],
        "recipients": ["aave_pool", "self"],
    }
    ips["allocation"] = {
        "max_weight_per_asset_bps": 10000,
        "min_stable_weight_bps": 0,
        "rebalance_band_bps": 0,
    }
    ips["limits"] = {"per_tx_usd": "1", "per_day_usd": "2", "max_tx_per_hour": 4}
    ips["approval"] = {"escalate_above_usd": "1"}
    return parse_ips(ips)


def portfolio_state(wallet: str) -> dict[str, Any]:
    state = json.loads(example("portfolio-state.valid.json").read_text(encoding="utf-8"))
    usdc = int(
        rpc(
            "eth_call",
            [{"to": USDC, "data": "0x70a08231" + wallet[2:].lower().rjust(64, "0")}, "latest"],
        ),
        16,
    )
    dollars = f"{usdc // 10**6}.{usdc % 10**6:06d}"[:-4]
    state["as_of"] = now_iso()
    state["positions"] = [
        {
            "token": "USDC",
            "location": "wallet",
            "balance": {"value": str(usdc), "decimals": 6},
            "price_usd": "1.00",
            "value_usd": dollars,
            "weight_bps": 10000,
        }
    ]
    state["total_value_usd"] = dollars
    state["spent_24h_usd"] = "0"
    state["tx_count_1h"] = 0
    return state


def fresh_risk_inputs() -> dict[str, Any]:
    risk = json.loads(example("risk-inputs.valid.json").read_text(encoding="utf-8"))
    risk["as_of"] = now_iso()
    risk["data_age_seconds"] = 0
    return risk


def make_order(function: str, recipient: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "order_id": str(uuid.uuid4()),
        "created_at": now_iso(),
        "proposed_by": "owner",
        "protocol": "aave-v3",
        "function": function,
        "token": "USDC",
        "amount": {"value": AMOUNT, "decimals": 6},
        "recipient": recipient,
    }


def send_raw(raw: bytes) -> str:
    return str(rpc("eth_sendRawTransaction", ["0x" + raw.hex().removeprefix("0x")]))


def wait_receipt(tx_hash: str, timeout: int = 90) -> dict[str, Any]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        receipt = rpc("eth_getTransactionReceipt", [tx_hash])
        if receipt:
            return receipt
        time.sleep(2)
    raise TimeoutError(f"no receipt for {tx_hash} after {timeout}s")


class StubSimulator:
    """Dry-run only: the real bundle simulation is run separately, once, for both calls."""

    def simulate(self, tx: dict[str, Any]) -> Any:
        from core.simulate import SimulationResult

        return SimulationResult(ok=True, error=None, gas_used=0, raw={"dry_run": True})


USDC = "0xba50Cd2A20f6DA35D788639E581bca8d0B5d4D5f"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true", help="broadcast for real")
    parser.add_argument(
        "--dry-run", action="store_true", help="dry run with RPC bundle simulation (default)"
    )
    args = parser.parse_args()

    live = args.execute and not args.dry_run
    if live and os.environ.get("AGENT_WALLET_LIVE_CONFIRM") != CONFIRM_VALUE:
        print("Refusing live run: the owner's confirmation variable is not set.")
        return 2
    if int(rpc("eth_chainId", []), 16) != CHAIN_ID:
        print("Refusing: RPC is not Base Sepolia (84532).")
        return 2

    ips = temporary_ips()
    if live:
        signer: Any = build_signer(True, send_raw)
        simulator: Any = RpcSimulator(RPC_URL)
    else:
        # Address only: the dry run never reads the key, it signs with a fixed fake.
        signer = build_signer(False)
        simulator = StubSimulator()
    wallet = os.environ.get("AGENT_WALLET_ADDRESS") or signer.address
    if not live:
        wallet = os.environ.get(
            "AGENT_WALLET_ADDRESS", "0x9D1Ae26890c683a52e73A504091b090f7689373a"
        )

    store = TierStore(":memory:")
    OwnerControls(store).set_tier("T2")
    audit_path = ROOT / ".data" / "live_first_tx.sqlite"
    audit_path.parent.mkdir(exist_ok=True)
    audit = SqliteAuditSink(audit_path if live else ":memory:")

    tx_builder = AaveTxBuilder(
        rpc,
        wallet,
        chain_id=CHAIN_ID,
        allow_estimate_fallback=not live,
    )

    gateway = Gateway(
        policy=EnginePolicy(ips, lambda: portfolio_state(wallet), fresh_risk_inputs),
        simulator=simulator,
        signer=signer,
        audit_sink=audit,
        tx_builder=tx_builder,
        tier_view=TierView(store),
        ips_hash=ips.hash,
    )

    print(f"mode: {'LIVE' if live else 'DRY RUN'} | wallet: {wallet} | pool: {AAVE_V3_POOL}")
    results = []
    for function, recipient in (("approve", "aave_pool"), ("supply", "self")):
        order = make_order(function, recipient)
        record = gateway.propose(order)
        execution = record.get("execution") or {}
        print(
            f"{function:8s} order={order['order_id']} outcome={record['outcome']} "
            f"rules={[h['code'] for h in record['decision']['rules_hit']]} "
            f"tx={execution.get('tx_hash')} err={execution.get('error_code')}"
        )
        if record["outcome"] != "executed":
            print("stopped: step did not execute", record["decision"].get("reason"))
            return 1
        if live:
            receipt = wait_receipt(execution["tx_hash"])
            if receipt["status"] != "0x1":
                print("receipt status is not success:", receipt["status"])
                return 1
            results.append((function, order["order_id"], execution["tx_hash"], receipt))

    if not live:
        sample = tx_builder(make_order("approve", "aave_pool")) + tx_builder(
            make_order("supply", "self")
        )
        first_nonce = int(rpc("eth_getTransactionCount", [wallet, "latest"]), 16)
        for offset, tx in enumerate(sample):
            tx["nonce"] = first_nonce + offset
            tx["from"] = wallet
        bundle = RpcSimulator(RPC_URL).simulate_bundle(sample)
        print(
            "real RPC bundle simulation (approve then supply):", "OK" if bundle.ok else bundle.error
        )
        print("dry run complete: nothing broadcast")
        return 0 if bundle.ok else 1

    EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    if not EVIDENCE.exists():
        EVIDENCE.write_text(
            "# Transaction evidence (Base Sepolia, chain 84532)\n\n"
            "| Date (UTC) | Step | Order id | Tx hash | Block | Explorer |\n"
            "| :--- | :--- | :--- | :--- | :--- | :--- |\n",
            encoding="utf-8",
        )
    with EVIDENCE.open("a", encoding="utf-8") as fh:
        for function, order_id, tx_hash, receipt in results:
            fh.write(
                f"| {now_iso()} | {function} 1 USDC | {order_id} | `{tx_hash}` | "
                f"{int(receipt['blockNumber'], 16)} | "
                f"https://sepolia.basescan.org/tx/{tx_hash} |\n"
            )
    print("evidence written to", EVIDENCE.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
