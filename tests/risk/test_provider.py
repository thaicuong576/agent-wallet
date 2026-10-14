"""Comprehensive tests for RiskProvider implementations, math, and fail-closed safety."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from core.policy.engine import evaluate
from core.policy.ips import load_ips
from core.risk import (
    FixtureRiskProvider,
    OnchainAaveRiskProvider,
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
from tests.risk.conftest import encode_aave_reserve_data

# ---------------------------------------------------------------------------
# 1. Schema Validity Tests
# ---------------------------------------------------------------------------


def test_onchain_aave_provider_schema_validity(
    risk_inputs_validator: jsonschema.Draft202012Validator,
) -> None:
    """Output of OnchainAaveRiskProvider validates against risk-inputs.schema.json."""
    fake_hex = encode_aave_reserve_data(
        total_atoken=100_000_000_000,
        total_stable_debt=20_000_000_000,
        total_variable_debt=54_000_000_000,
        last_update_timestamp=1728900000,
    )

    def fake_rpc(_payload: dict[str, Any]) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": 1, "result": fake_hex}

    provider = OnchainAaveRiskProvider(rpc_callable=fake_rpc)
    now = datetime(2024, 10, 14, 10, 1, 30, tzinfo=UTC)
    res = provider.get(protocol="aave-v3", token="USDC", now=now)

    assert res["schema_version"] == 1
    assert res["protocol"] == "aave-v3"
    assert res["token"] == "USDC"
    assert res["sources"] == ["rpc_aave_pool"]
    risk_inputs_validator.validate(res)


def test_onchain_aave_provider_all_fields_present_and_typed(
    risk_inputs_validator: jsonschema.Draft202012Validator,
) -> None:
    """Every required field in risk-inputs.schema.json is present and has correct type."""
    fake_hex = encode_aave_reserve_data()

    def fake_rpc(_payload: dict[str, Any]) -> dict[str, Any]:
        return {"result": fake_hex}

    provider = OnchainAaveRiskProvider(rpc_callable=fake_rpc)
    res = provider.get("aave-v3", "USDC")

    required_keys = [
        "schema_version",
        "as_of",
        "sources",
        "protocol",
        "token",
        "pool_tvl_usd",
        "reward_apy_share_bps",
        "apy_deviation_bps",
        "stablecoin_depeg_bps",
        "utilization_bps",
        "estimated_slippage_bps",
        "estimated_cost_usd",
        "estimated_benefit_usd",
        "data_age_seconds",
    ]
    for key in required_keys:
        assert key in res, f"Missing required key: {key}"

    assert isinstance(res["schema_version"], int)
    assert isinstance(res["as_of"], str)
    assert isinstance(res["sources"], list)
    assert isinstance(res["pool_tvl_usd"], str)
    assert isinstance(res["reward_apy_share_bps"], int)
    assert isinstance(res["apy_deviation_bps"], int)
    assert isinstance(res["utilization_bps"], int)
    assert isinstance(res["estimated_cost_usd"], str)
    assert isinstance(res["estimated_benefit_usd"], str)
    assert isinstance(res["data_age_seconds"], int)
    risk_inputs_validator.validate(res)


# ---------------------------------------------------------------------------
# 2. Exact Integer Utilization Math Tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "total_atoken,total_stable,total_variable,expected_bps,expected_tvl",
    [
        # 74% utilization: 74,000 / 100,000
        (100_000_000_000, 20_000_000_000, 54_000_000_000, 7400, "100000.00"),
        # Zero debt: 0 / 50,000 = 0 bps
        (50_000_000_000, 0, 0, 0, "50000.00"),
        # 100% utilization: 80,000 / 80,000 = 10000 bps
        (80_000_000_000, 30_000_000_000, 50_000_000_000, 10000, "80000.00"),
        # Zero supply: 0 / 0 -> 0 bps (no ZeroDivisionError)
        (0, 0, 0, 0, "0.00"),
        # Integer truncation (floor division): 1 / 3 = 3333 bps (not 3333.33)
        (300_000_000, 50_000_000, 50_000_000, 3333, "300.00"),
        # Fractional cents: 1.50 USDC
        (1_500_000, 750_000, 0, 5000, "1.50"),
        # Huge realistic TVL: 120M USDC with 7400 bps utilization
        (120_000_000_000_000, 10_000_000_000_000, 78_800_000_000_000, 7400, "120000000.00"),
    ],
)
def test_exact_integer_utilization_math(
    total_atoken: int,
    total_stable: int,
    total_variable: int,
    expected_bps: int,
    expected_tvl: str,
    risk_inputs_validator: jsonschema.Draft202012Validator,
) -> None:
    """Integer utilization and TVL are calculated exactly without floating-point math."""
    fake_hex = encode_aave_reserve_data(
        total_atoken=total_atoken,
        total_stable_debt=total_stable,
        total_variable_debt=total_variable,
    )

    def fake_rpc(_payload: dict[str, Any]) -> dict[str, Any]:
        return {"result": fake_hex}

    provider = OnchainAaveRiskProvider(rpc_callable=fake_rpc)
    res = provider.get("aave-v3", "USDC")

    assert res["utilization_bps"] == expected_bps
    assert res["pool_tvl_usd"] == expected_tvl
    risk_inputs_validator.validate(res)


def test_utilization_capped_at_10000_bps_if_debt_exceeds_supply() -> None:
    """Extreme testnet edge case: if accrued debt exceeds supply, utilization is capped at 10000."""
    fake_hex = encode_aave_reserve_data(
        total_atoken=100_000_000,
        total_stable_debt=60_000_000,
        total_variable_debt=60_000_000,  # 120M debt on 100M supply
    )

    def fake_rpc(_payload: dict[str, Any]) -> dict[str, Any]:
        return {"result": fake_hex}

    provider = OnchainAaveRiskProvider(rpc_callable=fake_rpc)
    res = provider.get("aave-v3", "USDC")

    assert res["utilization_bps"] == 10000


def test_compute_reserve_metrics_direct() -> None:
    """Direct unit test of compute_reserve_metrics utility."""
    util, tvl, debt, liq = compute_reserve_metrics(
        total_atoken=1000_000_000,
        total_stable_debt=200_000_000,
        total_variable_debt=300_000_000,
        decimals=6,
    )
    assert util == 5000
    assert tvl == "1000.00"
    assert debt == 500_000_000
    assert liq == 500_000_000

    with pytest.raises(ValueError):
        compute_reserve_metrics(-1, 0, 0)


def test_format_usd_edge_cases() -> None:
    """Test format_usd for integer string compliance."""
    assert format_usd(0, decimals=6) == "0.00"
    assert format_usd(1_000_000, decimals=6) == "1.00"
    assert format_usd(1_500_000, decimals=6) == "1.50"
    assert format_usd(1_000_001, decimals=6) == "1.000001"
    assert format_usd(120_000_000_000_000, decimals=6) == "120000000.00"

    with pytest.raises(ValueError):
        format_usd(-100)


# ---------------------------------------------------------------------------
# 3. Stale Detection Tests
# ---------------------------------------------------------------------------


def test_stale_detection_honest_data_age() -> None:
    """Data age is calculated honestly from the reserve's on-chain lastUpdateTimestamp."""
    last_update = 1728900000  # 2024-10-14T10:00:00Z
    now = datetime(2024, 10, 14, 10, 1, 30, tzinfo=UTC)  # 90 seconds later

    fake_hex = encode_aave_reserve_data(last_update_timestamp=last_update)

    def fake_rpc(_payload: dict[str, Any]) -> dict[str, Any]:
        return {"result": fake_hex}

    provider = OnchainAaveRiskProvider(rpc_callable=fake_rpc)
    res = provider.get("aave-v3", "USDC", now=now)

    assert res["as_of"] == "2024-10-14T10:00:00Z"
    assert res["data_age_seconds"] == 90


def test_stale_detection_clock_skew_clamped_to_zero() -> None:
    """If lastUpdateTimestamp is slightly ahead of local clock, age is non-negative 0."""
    last_update = 1728900100
    now = datetime(2024, 10, 14, 10, 0, 0, tzinfo=UTC)  # 100 seconds earlier

    fake_hex = encode_aave_reserve_data(last_update_timestamp=last_update)

    def fake_rpc(_payload: dict[str, Any]) -> dict[str, Any]:
        return {"result": fake_hex}

    provider = OnchainAaveRiskProvider(rpc_callable=fake_rpc)
    res = provider.get("aave-v3", "USDC", now=now)

    assert res["data_age_seconds"] == 0


def test_stale_detection_provider_fails_closed_when_exceeding_threshold() -> None:
    """When max_data_age_seconds is set, provider raises StaleDataError if data is stale."""
    last_update = 1728900000
    now = datetime(2024, 10, 14, 10, 5, 0, tzinfo=UTC)  # 300 seconds later

    fake_hex = encode_aave_reserve_data(last_update_timestamp=last_update)

    def fake_rpc(_payload: dict[str, Any]) -> dict[str, Any]:
        return {"result": fake_hex}

    provider = OnchainAaveRiskProvider(
        rpc_callable=fake_rpc,
        max_data_age_seconds=180,  # 3 minutes allowed
    )

    with pytest.raises(StaleDataError, match="Aave reserve data is stale"):
        provider.get("aave-v3", "USDC", now=now)


def test_stale_detection_provider_passes_when_fresh() -> None:
    """Provider allows fresh data within max_data_age_seconds."""
    last_update = 1728900000
    now = datetime(2024, 10, 14, 10, 1, 0, tzinfo=UTC)  # 60 seconds later

    fake_hex = encode_aave_reserve_data(last_update_timestamp=last_update)

    def fake_rpc(_payload: dict[str, Any]) -> dict[str, Any]:
        return {"result": fake_hex}

    provider = OnchainAaveRiskProvider(
        rpc_callable=fake_rpc,
        max_data_age_seconds=180,
    )
    res = provider.get("aave-v3", "USDC", now=now)
    assert res["data_age_seconds"] == 60


def test_stale_detection_integrates_with_policy_engine(
    valid_risk_inputs_fixture_path: Path,
) -> None:
    """Stale risk inputs produced by provider trigger RISK.DATA_STALE in the pure policy engine."""
    repo_root = Path(__file__).resolve().parent.parent.parent
    ips = load_ips(repo_root / "contracts" / "examples" / "ips.valid.json")
    with open(repo_root / "contracts" / "examples" / "portfolio-state.valid.json") as f:
        state = json.load(f)
    with open(repo_root / "contracts" / "examples" / "order.valid.json") as f:
        order = json.load(f)

    # 10,000 seconds old data (IPS max_data_age_seconds is 7200)
    last_update = 1700000000
    now = datetime(2024, 10, 14, 10, 0, 0, tzinfo=UTC)

    fake_hex = encode_aave_reserve_data(last_update_timestamp=last_update)

    def fake_rpc(_payload: dict[str, Any]) -> dict[str, Any]:
        return {"result": fake_hex}

    provider = OnchainAaveRiskProvider(rpc_callable=fake_rpc)
    risk_inputs = provider.get("aave-v3", "USDC", now=now)

    assert risk_inputs["data_age_seconds"] > 7200

    decision = evaluate(ips=ips, state=state, order=order, risk_inputs=risk_inputs, now=now)
    assert decision["decision"] == "deny"
    codes = [h["code"] for h in decision["rules_hit"]]
    assert "RISK.DATA_STALE" in codes


# ---------------------------------------------------------------------------
# 4. RPC Failure Fails Closed Tests (Typed Error, Never Zeros)
# ---------------------------------------------------------------------------


def test_rpc_failure_revert_raises_typed_error() -> None:
    """When RPC returns a revert error, RpcCallError is raised (never default zeros)."""

    def fake_rpc(_p: dict[str, Any]) -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": 1,
            "error": {"code": -32000, "message": "execution reverted: 0x4e487b71"},
        }

    provider = OnchainAaveRiskProvider(rpc_callable=fake_rpc)

    with pytest.raises(RpcCallError, match="RPC returned error"):
        provider.get("aave-v3", "USDC")


def test_rpc_failure_network_exception_raises_typed_error() -> None:
    """When RPC callable raises a network or runtime exception, it fails closed."""

    def broken_rpc(_payload: dict[str, Any]) -> dict[str, Any]:
        raise ConnectionResetError("Connection refused by testnet RPC node")

    provider = OnchainAaveRiskProvider(rpc_callable=broken_rpc)

    with pytest.raises(RpcCallError, match="RPC execution error: Connection refused"):
        provider.get("aave-v3", "USDC")


def test_rpc_failure_empty_hex_raises_typed_error() -> None:
    """Empty result hex from RPC fails closed (never zeros)."""

    def fake_rpc(_p: dict[str, Any]) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": 1, "result": "0x"}

    provider = OnchainAaveRiskProvider(rpc_callable=fake_rpc)

    with pytest.raises(RpcCallError, match="Empty hex result returned"):
        provider.get("aave-v3", "USDC")


def test_rpc_failure_malformed_hex_raises_typed_error() -> None:
    """Non-hex result fails closed with RpcCallError."""

    def fake_rpc(_p: dict[str, Any]) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": 1, "result": "0xnotvalidhex"}

    provider = OnchainAaveRiskProvider(rpc_callable=fake_rpc)

    with pytest.raises(RpcCallError, match="Invalid hex string"):
        provider.get("aave-v3", "USDC")


def test_rpc_failure_truncated_abi_data_raises_typed_error() -> None:
    """Truncated ABI payload from RPC fails closed with RpcCallError."""
    # Only 32 bytes returned instead of required 12 words (384 bytes)
    truncated_hex = "0x" + ("00" * 32)

    def fake_rpc(_p: dict[str, Any]) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": 1, "result": truncated_hex}

    provider = OnchainAaveRiskProvider(rpc_callable=fake_rpc)

    with pytest.raises(RpcCallError, match="Failed to decode Aave reserve data ABI response"):
        provider.get("aave-v3", "USDC")


def test_rpc_failure_missing_result_raises_typed_error() -> None:
    """RPC response with neither result nor error raises RpcCallError."""

    def fake_rpc(_p: dict[str, Any]) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": 1}

    provider = OnchainAaveRiskProvider(rpc_callable=fake_rpc)

    with pytest.raises(RpcCallError, match="missing 'result' field"):
        provider.get("aave-v3", "USDC")


def test_rpc_callable_no_endpoint_raises_typed_error() -> None:
    """Provider initialized with neither callable nor URL fails closed."""
    provider = OnchainAaveRiskProvider()

    with pytest.raises(RpcCallError, match="No RPC callable or URL provided"):
        provider.get("aave-v3", "USDC")


# ---------------------------------------------------------------------------
# 5. Protocol and Token Boundary Tests
# ---------------------------------------------------------------------------


def test_unsupported_protocol_raises_typed_error() -> None:
    """Requesting an unsupported protocol raises UnsupportedProtocolError."""

    def fake_rpc(_p: dict[str, Any]) -> dict[str, Any]:
        return {"result": "0x"}

    provider = OnchainAaveRiskProvider(rpc_callable=fake_rpc)
    with pytest.raises(UnsupportedProtocolError, match="Protocol 'uniswap-v3' is not supported"):
        provider.get("uniswap-v3", "USDC")


def test_unsupported_token_raises_typed_error() -> None:
    """Requesting an unsupported token raises UnsupportedTokenError."""

    def fake_rpc(_p: dict[str, Any]) -> dict[str, Any]:
        return {"result": "0x"}

    provider = OnchainAaveRiskProvider(rpc_callable=fake_rpc)
    with pytest.raises(UnsupportedTokenError, match="Token 'WETH' is not supported"):
        provider.get("aave-v3", "WETH")


# ---------------------------------------------------------------------------
# 6. ReferenceData Integrity and Customization Tests
# ---------------------------------------------------------------------------


def test_reference_data_honest_reference_labelling() -> None:
    """ReferenceData defaults are documented and labelled 'reference' in sources."""
    ref = ReferenceData()
    assert ref.sources == ("reference",)
    assert ref.source == "reference"
    assert ref.reward_apy_share_bps == 0
    assert ref.apy_deviation_bps == 0
    assert ref.stablecoin_depeg_bps == 0
    assert ref.estimated_slippage_bps is None
    assert ref.estimated_cost_usd == "0.40"
    assert ref.estimated_benefit_usd == "3.10"


def test_custom_reference_data_injection(
    risk_inputs_validator: jsonschema.Draft202012Validator,
) -> None:
    """Custom ReferenceData can be injected and is propagated honestly to output."""
    custom_ref = ReferenceData(
        reward_apy_share_bps=1200,
        apy_deviation_bps=80,
        stablecoin_depeg_bps=5,
        estimated_slippage_bps=None,
        estimated_cost_usd="0.45",
        estimated_benefit_usd="4.20",
    )
    fake_hex = encode_aave_reserve_data()

    def fake_rpc(_payload: dict[str, Any]) -> dict[str, Any]:
        return {"result": fake_hex}

    provider = OnchainAaveRiskProvider(
        rpc_callable=fake_rpc,
        reference_data=custom_ref,
    )
    res = provider.get("aave-v3", "USDC")

    assert res["reward_apy_share_bps"] == 1200
    assert res["apy_deviation_bps"] == 80
    assert res["stablecoin_depeg_bps"] == 5
    assert res["estimated_cost_usd"] == "0.45"
    assert res["estimated_benefit_usd"] == "4.20"
    risk_inputs_validator.validate(res)


# ---------------------------------------------------------------------------
# 7. Deterministic Fixture Replay Tests (The Judges' Mode)
# ---------------------------------------------------------------------------


def test_fixture_provider_schema_validity(
    valid_risk_inputs_fixture_path: Path,
    risk_inputs_validator: jsonschema.Draft202012Validator,
) -> None:
    """FixtureRiskProvider loads canonical fixture and validates against schema."""
    provider = FixtureRiskProvider(valid_risk_inputs_fixture_path)
    res = provider.get("aave-v3", "USDC")

    assert res["schema_version"] == 1
    assert res["protocol"] == "aave-v3"
    assert res["token"] == "USDC"
    risk_inputs_validator.validate(res)


def test_fixture_provider_deterministic_replay(valid_risk_inputs_fixture_path: Path) -> None:
    """Consecutive calls to FixtureRiskProvider return identical outputs."""
    provider = FixtureRiskProvider(valid_risk_inputs_fixture_path)

    first = provider.get("aave-v3", "USDC")
    for _ in range(10):
        subsequent = provider.get("aave-v3", "USDC")
        assert subsequent == first


def test_fixture_provider_restamps_nothing_stale_stays_stale(tmp_path: Path) -> None:
    """Stale fixtures are never restamped; stale fixtures stay stale."""
    stale_fixture = {
        "schema_version": 1,
        "as_of": "2020-01-01T00:00:00Z",
        "sources": ["rpc_aave_pool"],
        "protocol": "aave-v3",
        "token": "USDC",
        "pool_tvl_usd": "1000000.00",
        "reward_apy_share_bps": 0,
        "apy_deviation_bps": 0,
        "stablecoin_depeg_bps": 0,
        "utilization_bps": 5000,
        "estimated_slippage_bps": None,
        "estimated_cost_usd": "0.40",
        "estimated_benefit_usd": "3.10",
        "data_age_seconds": 999999,  # unmistakably stale
    }
    fixture_file = tmp_path / "stale.json"
    fixture_file.write_text(json.dumps(stale_fixture), encoding="utf-8")

    provider = FixtureRiskProvider(fixture_file)
    now = datetime(2026, 10, 14, 14, 0, 0, tzinfo=UTC)
    res = provider.get("aave-v3", "USDC", now=now)

    # as_of and data_age_seconds MUST NOT be restamped to now or zero
    assert res["as_of"] == "2020-01-01T00:00:00Z"
    assert res["data_age_seconds"] == 999999


def test_fixture_provider_missing_file_raises_typed_error(tmp_path: Path) -> None:
    """Non-existent fixture path raises RiskDataUnavailableError."""
    with pytest.raises(RiskDataUnavailableError, match="Fixture file not found"):
        FixtureRiskProvider(tmp_path / "does_not_exist.json")


def test_fixture_provider_invalid_schema_raises_typed_error(tmp_path: Path) -> None:
    """Invalid fixture file raises RiskProviderError on load."""
    invalid_file = tmp_path / "invalid.json"
    invalid_file.write_text(json.dumps({"schema_version": 1}), encoding="utf-8")

    with pytest.raises(RiskProviderError, match="fails risk-inputs schema"):
        FixtureRiskProvider(invalid_file)


def test_fixture_provider_protocol_mismatch_raises_typed_error(
    valid_risk_inputs_fixture_path: Path,
) -> None:
    """Requesting mismatched protocol from fixture raises UnsupportedProtocolError."""
    provider = FixtureRiskProvider(valid_risk_inputs_fixture_path)
    with pytest.raises(UnsupportedProtocolError, match="Fixture is for protocol 'aave-v3'"):
        provider.get("uniswap-v3", "USDC")


def test_fixture_provider_caller_mutation_does_not_affect_replay(
    valid_risk_inputs_fixture_path: Path,
) -> None:
    """Mutating returned dict does not corrupt provider's stored fixture state."""
    provider = FixtureRiskProvider(valid_risk_inputs_fixture_path)
    first = provider.get("aave-v3", "USDC")
    first["utilization_bps"] = 9999
    first["sources"].append("corrupted")

    second = provider.get("aave-v3", "USDC")
    assert second["utilization_bps"] != 9999
    assert "corrupted" not in second["sources"]


# ---------------------------------------------------------------------------
# 8. Protocol Runtime Checkable Conformance
# ---------------------------------------------------------------------------


def test_risk_provider_protocol_conformance(valid_risk_inputs_fixture_path: Path) -> None:
    """Both OnchainAaveRiskProvider and FixtureRiskProvider satisfy RiskProvider protocol."""

    def dummy_rpc(_p: dict[str, Any]) -> dict[str, Any]:
        return {"result": "0x"}

    onchain = OnchainAaveRiskProvider(rpc_callable=dummy_rpc)
    fixture = FixtureRiskProvider(valid_risk_inputs_fixture_path)

    assert isinstance(onchain, RiskProvider)
    assert isinstance(fixture, RiskProvider)
