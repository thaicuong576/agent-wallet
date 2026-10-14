"""Signer adapter: protocols, local signer, and testing fakes.

Only `core.gateway` may import this package.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from eth_account import Account

from core.signer.guarded import GuardedSigner, SimulationFailed


class SignerError(Exception):
    """Base exception for signer failures."""


class SignerKeyError(SignerError, ValueError):
    """Raised when the private key is missing or malformed."""


@dataclass(frozen=True)
class TxReceiptStub:
    """Stub receipt returned by sign_and_send."""

    tx_hash: str


@runtime_checkable
class Signer(Protocol):
    """Protocol for transaction signers."""

    @property
    def address(self) -> str:
        """Signer's checksummed public Ethereum address."""
        ...

    def sign_and_send(self, tx: dict[str, Any]) -> TxReceiptStub:
        """Sign a transaction and broadcast it, returning a receipt stub with tx_hash."""
        ...


class LocalSigner:
    """Local signer using an Ethereum private key from AGENT_WALLET_PRIVATE_KEY."""

    ENV_VAR = "AGENT_WALLET_PRIVATE_KEY"

    def __init__(self, sender: Any = None, *, private_key: str | None = None):
        key = private_key if private_key is not None else os.environ.get(self.ENV_VAR)
        if key is None or not str(key).strip():
            raise SignerKeyError(f"Missing {self.ENV_VAR} environment variable")

        key_str = str(key).strip()
        try:
            self._account = Account.from_key(key_str)
        except Exception:
            raise SignerKeyError(
                f"Malformed {self.ENV_VAR}: key must be a valid 32-byte hex string"
            ) from None
        finally:
            del key_str
            del key

        self._sender = sender

    @property
    def address(self) -> str:
        return self._account.address

    def sign_and_send(self, tx: dict[str, Any]) -> TxReceiptStub:
        clean_tx = dict(tx)
        if "from_" in clean_tx and "from" not in clean_tx:
            clean_tx["from"] = clean_tx.pop("from_")

        try:
            signed = self._account.sign_transaction(clean_tx)
        except Exception as e:
            raise SignerError(f"Failed to sign transaction: {e}") from None

        raw_tx = signed.raw_transaction
        tx_hash: str | None = None

        if self._sender is not None:
            res = None
            if callable(self._sender):
                try:
                    res = self._sender(raw_tx)
                except TypeError:
                    res = self._sender(f"0x{raw_tx.hex()}")
            elif hasattr(self._sender, "send_raw_transaction"):
                try:
                    res = self._sender.send_raw_transaction(raw_tx)
                except TypeError:
                    res = self._sender.send_raw_transaction(f"0x{raw_tx.hex()}")
            elif hasattr(self._sender, "eth") and hasattr(self._sender.eth, "send_raw_transaction"):
                res = self._sender.eth.send_raw_transaction(raw_tx)

            if isinstance(res, str):
                tx_hash = res if res.startswith("0x") else f"0x{res}"
            elif hasattr(res, "hex") and callable(res.hex):
                h = res.hex()
                tx_hash = h if h.startswith("0x") else f"0x{h}"
            elif hasattr(res, "tx_hash"):
                tx_hash = str(res.tx_hash)
            elif isinstance(res, dict) and "tx_hash" in res:
                tx_hash = str(res["tx_hash"])
            elif isinstance(res, dict) and "transactionHash" in res:
                th = res["transactionHash"]
                tx_hash = th if isinstance(th, str) else f"0x{th.hex()}"

        if tx_hash is None:
            h = signed.hash.hex()
            tx_hash = h if h.startswith("0x") else f"0x{h}"

        return TxReceiptStub(tx_hash=tx_hash)

    def __repr__(self) -> str:
        return f"LocalSigner(address={self.address!r})"

    def __str__(self) -> str:
        return f"LocalSigner({self.address})"


class FakeSigner:
    """Deterministic fake signer for testing."""

    def __init__(
        self,
        address: str = "0x1111111111111111111111111111111111111111",
        *,
        fixed_hash: str | None = None,
    ):
        self._address = address
        self._fixed_hash = fixed_hash
        self.calls: list[dict[str, Any]] = []

    @property
    def address(self) -> str:
        return self._address

    def sign_and_send(self, tx: dict[str, Any]) -> TxReceiptStub:
        self.calls.append(dict(tx))
        if self._fixed_hash is not None:
            h = self._fixed_hash
        else:
            items = sorted((str(k), str(v)) for k, v in tx.items())
            digest = hashlib.sha256(f"call_{len(self.calls)}_{items}".encode()).hexdigest()
            h = f"0x{digest}"
        return TxReceiptStub(tx_hash=h)

    def __repr__(self) -> str:
        return f"FakeSigner(address={self.address!r})"

    def __str__(self) -> str:
        return f"FakeSigner({self.address})"


__all__ = [
    "Signer",
    "TxReceiptStub",
    "LocalSigner",
    "FakeSigner",
    "GuardedSigner",
    "SignerError",
    "SignerKeyError",
    "SimulationFailed",
]
