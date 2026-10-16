"""Risk input providers, protocols, and reference data for policy evaluation."""

from __future__ import annotations

from core.risk.aave import AAVE_V3_DATA_PROVIDER, OnchainAaveRiskProvider
from core.risk.fixture import FixtureRiskProvider
from core.risk.provider import (
    ReferenceData,
    RiskDataUnavailableError,
    RiskProvider,
    RiskProviderError,
    RpcCallError,
    StaleDataError,
    UnsupportedProtocolError,
    UnsupportedTokenError,
    compute_reserve_metrics,
    format_usd,
)
from core.risk.router import SOURCES_MAPPING, RiskRouter

__all__ = [
    "AAVE_V3_DATA_PROVIDER",
    "FixtureRiskProvider",
    "OnchainAaveRiskProvider",
    "ReferenceData",
    "RiskDataUnavailableError",
    "RiskProvider",
    "RiskProviderError",
    "RiskRouter",
    "RpcCallError",
    "SOURCES_MAPPING",
    "StaleDataError",
    "UnsupportedProtocolError",
    "UnsupportedTokenError",
    "compute_reserve_metrics",
    "format_usd",
]
