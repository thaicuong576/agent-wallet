"""Webapp wallet and agent routes: /me, /agent, /topups, /presets, /audit/verify (design 8.1, 12.2).

The owner address comes only from the verified session (`require_session`); no route takes an
owner address as a parameter. Top-ups and their status live in the owner's `TopupStore`
(`tenant.stores.topups`), so a foreign transaction hash is simply unknown to another owner (404).
Nothing here touches a key.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

import eth_abi
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict

from api.auth import AuthProblem, AuthService, Session, require_session
from api.session import CHAIN_ID
from core.audit.verify import verify_chain
from core.gateway.tenant import CustodyUnavailable, Tenant, TenantNotFound, TenantRegistry
from core.policy.ips import compute_ips_hash
from core.protocols.addresses import AAVE_V3_A_USDC, AAVE_V3_USDC
from core.runner.portfolio import PortfolioReader
from core.storage.interfaces import TopupStore

log = logging.getLogger("agent_wallet.wallet")

EXPLORER_TX_URL = "https://sepolia.basescan.org/tx/"
GAS_UNITS_FLOOR = 500_000  # design 8.1: gas_floor_wei = 2 x base fee x 500,000 gas (an estimate)
SUGGESTED_TOPUP_FACTOR = 10  # suggested top-up = 10 x floor
_TX_HASH = re.compile(r"^0x[0-9a-f]{64}$")
RpcCall = Callable[[str, list[Any]], Any]

PRESET_IPS_FILE = (
    Path(__file__).resolve().parent.parent / "contracts" / "examples" / "ips.valid.json"
)


# ---------------------------------------------------------------- shared services and problems


@dataclass
class WebappServices:
    """Everything the webapp routes need. Set on `app.state.webapp` by the application wiring."""

    registry: TenantRegistry
    auth: AuthService
    wallet: WalletService
    jobs: Any  # api.jobs.JobService (typed loosely to avoid an import cycle)
    mode: str = "multi"


def get_webapp(request: Request) -> WebappServices:
    services = getattr(request.app.state, "webapp", None)
    if services is None:
        raise AuthProblem(
            503, "service-unavailable", "Webapp is not configured", "set AGENT_WALLET_MODE=multi"
        )
    return services


def current_tenant(
    request: Request, session: Annotated[Session, Depends(require_session)]
) -> Tenant:
    """The session owner's tenant, created on first use. No address comes from the request."""
    services = get_webapp(request)
    try:
        return services.registry.open(session.address, create=True)
    except CustodyUnavailable:
        raise AuthProblem(
            503, "custody-unavailable", "Custody is not available", "agent key custody is not ready"
        ) from None
    except TenantNotFound:
        raise AuthProblem(404, "not-found", "Not found", "no agent wallet for this owner") from None


def problem_invalid(detail: str) -> AuthProblem:
    return AuthProblem(422, "invalid-request", "Invalid request", detail)


# ---------------------------------------------------------------- wallet service


def _hex_int(value: Any) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and value.startswith(("0x", "0X")):
        return int(value, 16)
    raise ValueError("not a hex quantity")


class WalletService:
    """Live agent balances, the gas floor and top-up verification over an injected RPC callable."""

    def __init__(
        self,
        rpc: RpcCall,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        chain_id: int = CHAIN_ID,
    ) -> None:
        self._rpc = rpc
        self._clock = clock
        self._chain_id = chain_id

    def now(self) -> datetime:
        return self._clock()

    @staticmethod
    def topups(tenant: Tenant) -> TopupStore:
        return tenant.stores.topups

    # -- reads

    def eth_balance_wei(self, address: str) -> int:
        return _hex_int(self._rpc("eth_getBalance", [address, "latest"]))

    def gas_floor_wei(self) -> int:
        block = self._rpc("eth_getBlockByNumber", ["latest", False])
        if not isinstance(block, dict) or "baseFeePerGas" not in block:
            raise ValueError("latest block has no baseFeePerGas")
        return 2 * _hex_int(block["baseFeePerGas"]) * GAS_UNITS_FLOOR

    def gas_state(self, address: str) -> tuple[int, int]:
        """(balance_wei, floor_wei). Raises on any RPC failure; callers fail closed."""
        return self.eth_balance_wei(address), self.gas_floor_wei()

    def token_balance(self, token: str, address: str) -> int:
        data = "0x70a08231" + eth_abi.encode(["address"], [address]).hex()
        return _hex_int(self._rpc("eth_call", [{"to": token, "data": data}, "latest"]))

    def agent_state(self, tenant: Tenant) -> dict[str, Any]:
        balance, floor = self.gas_state(tenant.agent_address)
        return {
            "address": tenant.agent_address,
            "eth_wei": str(balance),
            "gas_floor_wei": str(floor),
            "suggested_topup_wei": str(floor * SUGGESTED_TOPUP_FACTOR),
            "usdc": {
                "value": str(self.token_balance(AAVE_V3_USDC, tenant.agent_address)),
                "decimals": 6,
            },
            "ausdc": {
                "value": str(self.token_balance(AAVE_V3_A_USDC, tenant.agent_address)),
                "decimals": 6,
            },
            "price_source": "mainnet_reference",
            "custody": "custodial-testnet",
        }

    def portfolio_state(self, tenant: Tenant) -> dict[str, Any]:
        """Live portfolio-state of the tenant: chain balances and ledger limits, reference price.

        Raises on any RPC failure; callers fail closed and never echo the error text.
        """
        reader = PortfolioReader(self._rpc, tenant.agent_address, tenant.stores.ledger)  # type: ignore[arg-type]
        return reader.read(self._clock()).state

    # -- top-ups (design 8.1)

    def register_topup(self, tenant: Tenant, tx_hash: str) -> dict[str, Any]:
        """Check sender, recipient, value and chain of an owner-signed tx, then store it pending."""
        if not isinstance(tx_hash, str) or not _TX_HASH.fullmatch(tx_hash):
            raise problem_invalid("tx_hash must be 0x plus 64 lowercase hex digits")
        store = self.topups(tenant)
        known = store.get(tx_hash)
        if known is not None:
            return self._view(known)
        try:
            tx = self._rpc("eth_getTransactionByHash", [tx_hash])
        except Exception:  # noqa: BLE001 - any RPC failure is reported without its text
            raise AuthProblem(
                503, "service-unavailable", "Chain RPC unavailable", "try again shortly"
            ) from None
        if not isinstance(tx, dict):
            raise problem_invalid("transaction not found on Base Sepolia (yet)")
        try:
            sender = str(tx["from"]).lower()
            recipient = str(tx.get("to") or "").lower()
            value = _hex_int(tx["value"])
            chain = _hex_int(tx["chainId"]) if tx.get("chainId") is not None else self._chain_id
        except (KeyError, ValueError):
            raise problem_invalid("transaction data is incomplete") from None
        if chain != self._chain_id:
            raise problem_invalid(f"transaction is not on chain {self._chain_id}")
        if sender != tenant.owner:
            raise problem_invalid("transaction was not sent from your wallet address")
        if recipient != tenant.agent_address.lower():
            raise problem_invalid("transaction was not sent to your agent address")
        if value <= 0:
            raise problem_invalid("transaction carries no ETH value")
        return self._view(store.put(tx_hash, value, self._clock()))

    def get_topup(self, tenant: Tenant, tx_hash: str) -> dict[str, Any]:
        if not _TX_HASH.fullmatch(tx_hash):
            raise AuthProblem(404, "not-found", "Not found", "unknown top-up")
        store = self.topups(tenant)
        row = store.get(tx_hash)
        if row is None:
            raise AuthProblem(404, "not-found", "Not found", "unknown top-up")
        if row["status"] == "pending":
            try:
                receipt = self._rpc("eth_getTransactionReceipt", [tx_hash])
            except Exception:  # noqa: BLE001
                receipt = None  # stays pending; the next poll tries again
            if isinstance(receipt, dict) and receipt.get("status") is not None:
                ok = _hex_int(receipt["status"]) == 1
                block = _hex_int(receipt["blockNumber"]) if receipt.get("blockNumber") else None
                store.settle(tx_hash, "confirmed" if ok else "failed", block)
                row = store.get(tx_hash) or row
        return self._view(row)

    @staticmethod
    def _view(row: dict[str, Any]) -> dict[str, Any]:
        out = {
            "tx_hash": row["tx_hash"],
            "status": row["status"],
            "amount_wei": row["amount_wei"],
            "explorer_url": EXPLORER_TX_URL + row["tx_hash"],
        }
        if row.get("block_number") is not None:
            out["block_number"] = row["block_number"]
        return out


# ---------------------------------------------------------------- presets (A3 placeholder)


def list_presets() -> list[dict[str, Any]]:
    """One preset built from the example IPS. Ticket A4 replaces this with the preset files."""
    ips = json.loads(PRESET_IPS_FILE.read_text(encoding="utf-8"))
    limits = ips["limits"]
    return [
        {
            "preset_id": "conservative",
            "title": "Conservative stablecoin lending",
            "summary": (
                "Keeps capital in stablecoins and lends it on Aave. Each transaction is capped at "
                f"{limits['per_tx_usd']} USD and each day at {limits['per_day_usd']} USD."
            ),
            "ips_hash": compute_ips_hash(ips),
            "limits": {k: str(v) for k, v in limits.items()},
        }
    ]


# ---------------------------------------------------------------- routes

router = APIRouter()


class TopupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tx_hash: str


@router.get("/me", tags=["auth"])
def get_me(
    request: Request,
    session: Annotated[Session, Depends(require_session)],
    tenant: Annotated[Tenant, Depends(current_tenant)],
) -> dict[str, Any]:
    services = get_webapp(request)
    return {
        "address": session.address,
        "agent_address": tenant.agent_address,
        "mode": services.mode,
        "chain_id": CHAIN_ID,
        "tier": tenant.tier_view.current(),
        "emergency_stop": tenant.tier_view.is_stopped(),
    }


@router.get("/agent", tags=["agent"])
def get_agent(
    request: Request, tenant: Annotated[Tenant, Depends(current_tenant)]
) -> dict[str, Any]:
    try:
        return get_webapp(request).wallet.agent_state(tenant)
    except AuthProblem:
        raise
    except Exception:  # noqa: BLE001 - RPC text can carry URLs; never echo it
        log.warning("agent state read failed owner=%s", tenant.owner)
        raise AuthProblem(
            503, "service-unavailable", "Chain RPC unavailable", "try again shortly"
        ) from None


@router.post("/topups", status_code=202, tags=["agent"])
def post_topup(
    body: TopupRequest, request: Request, tenant: Annotated[Tenant, Depends(current_tenant)]
) -> dict[str, Any]:
    return get_webapp(request).wallet.register_topup(tenant, body.tx_hash)


@router.get("/topups/{tx_hash}", tags=["agent"])
def get_topup(
    tx_hash: str, request: Request, tenant: Annotated[Tenant, Depends(current_tenant)]
) -> dict[str, Any]:
    return get_webapp(request).wallet.get_topup(tenant, tx_hash)


@router.get("/presets", tags=["state"])
def get_presets() -> dict[str, Any]:
    return {"schema_version": 1, "items": list_presets()}


@router.get("/audit/verify", tags=["audit"])
def get_audit_verify(tenant: Annotated[Tenant, Depends(current_tenant)]) -> dict[str, Any]:
    """Recompute the session owner's hash chain on the server."""
    report = verify_chain(tenant.gateway.audit_sink)
    return {"ok": bool(report.valid), "records": report.count, "first_bad_seq": report.bad_seq}
