"""Signer construction for owner-run tooling.

The signer may only be imported from the gateway package (boundary test), so scripts that need a
signer to hand to the gateway get it from here.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from core.signer import FakeSigner, LocalSigner


def build_signer(live: bool, sender: Callable[[bytes], Any] | None = None) -> Any:
    """Return a LocalSigner (key from the environment) for live runs, a fake one otherwise."""
    if live:
        return LocalSigner(sender=sender)
    return FakeSigner()
