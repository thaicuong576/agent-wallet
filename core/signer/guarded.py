"""Guarded signer wrapper enforcing simulate-before-sign."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.signer import Signer, TxReceiptStub
    from core.simulate import SimulationResult, Simulator


class SimulationFailed(Exception):
    """Raised when pre-flight transaction simulation fails."""

    def __init__(self, message: str, result: SimulationResult | None = None):
        super().__init__(message)
        self.result = result


class GuardedSigner:
    """Signer that simulates every transaction before delegating to the inner signer."""

    def __init__(self, signer: Signer, simulator: Simulator):
        self._signer = signer
        self._simulator = simulator

    @property
    def address(self) -> str:
        return self._signer.address

    @property
    def inner_signer(self) -> Signer:
        return self._signer

    @property
    def simulator(self) -> Simulator:
        return self._simulator

    def sign_and_send(self, tx: dict[str, Any]) -> TxReceiptStub:
        sim_result = self._simulator.simulate(tx)
        if not sim_result.ok:
            err = sim_result.error or "Simulation rejected transaction"
            raise SimulationFailed(f"Transaction simulation failed: {err}", result=sim_result)
        return self._signer.sign_and_send(tx)

    def __repr__(self) -> str:
        return f"GuardedSigner(signer={self._signer!r}, simulator={self._simulator!r})"

    def __str__(self) -> str:
        return f"GuardedSigner(address={self.address})"
