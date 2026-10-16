"""Top-up verification, gas floor, agent state and the server-side audit verification."""

from __future__ import annotations

import pytest

from api.auth import AuthProblem
from api.wallet import GAS_UNITS_FLOOR, SUGGESTED_TOPUP_FACTOR, WalletService, list_presets
from core.audit.verify import verify_chain
from tests.webapp.test_jobs import make_env

TX = "0x" + "a1" * 32
OWNER = "0x" + "12" * 20
OTHER = "0x" + "34" * 20


@pytest.fixture
def env(tmp_path):
    return make_env(tmp_path)


@pytest.fixture
def tenant(env):
    return env.tenant(OWNER)


def tx(tenant, **overrides):
    body = {
        "from": tenant.owner,
        "to": tenant.agent_address,
        "value": hex(5 * 10**15),
        "chainId": hex(84532),
        "hash": TX,
    }
    body.update(overrides)
    return body


def expect_422(env, tenant, tx_hash=TX):
    with pytest.raises(AuthProblem) as err:
        env.wallet.register_topup(tenant, tx_hash)
    assert err.value.status == 422 and err.value.slug == "invalid-request"
    assert env.wallet.topups(tenant).get(tx_hash) is None  # nothing stored for a rejected tx
    return err.value.detail


def test_topup_wrong_sender(env, tenant):
    env.chain.txs[TX] = tx(tenant, **{"from": OTHER})
    assert "your wallet address" in expect_422(env, tenant)


def test_topup_wrong_recipient(env, tenant):
    env.chain.txs[TX] = tx(tenant, to=OTHER)
    assert "your agent address" in expect_422(env, tenant)


def test_topup_recipient_missing_contract_creation(env, tenant):
    env.chain.txs[TX] = tx(tenant, to=None)
    expect_422(env, tenant)


@pytest.mark.parametrize("value", ["0x0", "0x"])
def test_topup_zero_value(env, tenant, value):
    env.chain.txs[TX] = tx(tenant, value=value)
    expect_422(env, tenant)


def test_topup_unknown_hash(env, tenant):
    assert "not found" in expect_422(env, tenant)


def test_topup_wrong_chain(env, tenant):
    env.chain.txs[TX] = tx(tenant, chainId=hex(1))
    assert "chain 84532" in expect_422(env, tenant)


@pytest.mark.parametrize("bad", ["0x12", "0x" + "A1" * 32, "a1" * 32, "", None, 5])
def test_topup_hash_must_be_lowercase_66_chars(env, tenant, bad):
    with pytest.raises(AuthProblem) as err:
        env.wallet.register_topup(tenant, bad)
    assert err.value.status == 422
    assert "eth_getTransactionByHash" not in env.chain.calls


def test_topup_sender_compare_ignores_case(env, tenant):
    env.chain.txs[TX] = tx(tenant, **{"from": tenant.owner.upper().replace("0X", "0x")})
    assert env.wallet.register_topup(tenant, TX)["status"] == "pending"


def test_topup_pending_then_confirmed(env, tenant):
    env.chain.txs[TX] = tx(tenant)
    first = env.wallet.register_topup(tenant, TX)
    assert first["status"] == "pending" and first["amount_wei"] == str(5 * 10**15)
    assert first["explorer_url"].endswith(TX)
    assert env.wallet.get_topup(tenant, TX)["status"] == "pending"  # no receipt yet
    env.chain.receipts[TX] = {"status": "0x1", "blockNumber": hex(1234)}
    done = env.wallet.get_topup(tenant, TX)
    assert (done["status"], done["block_number"]) == ("confirmed", 1234)
    env.chain.receipts[TX] = None  # confirmed is final; a later reorg-ish blip does not flip it
    assert env.wallet.get_topup(tenant, TX)["status"] == "confirmed"


def test_topup_reverted_receipt_is_failed(env, tenant):
    env.chain.txs[TX] = tx(tenant)
    env.wallet.register_topup(tenant, TX)
    env.chain.receipts[TX] = {"status": "0x0", "blockNumber": hex(9)}
    assert env.wallet.get_topup(tenant, TX)["status"] == "failed"


def test_topup_registration_is_idempotent_and_skips_rpc(env, tenant):
    env.chain.txs[TX] = tx(tenant)
    env.wallet.register_topup(tenant, TX)
    before = env.chain.calls.count("eth_getTransactionByHash")
    assert env.wallet.register_topup(tenant, TX)["status"] == "pending"
    assert env.chain.calls.count("eth_getTransactionByHash") == before


def test_topup_poll_survives_rpc_blip(env, tenant):
    env.chain.txs[TX] = tx(tenant)
    env.wallet.register_topup(tenant, TX)
    env.chain.down = True
    assert env.wallet.get_topup(tenant, TX)["status"] == "pending"


def test_topup_rpc_down_at_registration_is_503_without_rpc_text(env, tenant):
    env.chain.down = True
    with pytest.raises(AuthProblem) as err:
        env.wallet.register_topup(tenant, TX)
    assert err.value.status == 503 and "node down" not in (err.value.detail or "")


def test_unknown_topup_is_404_and_foreign_topup_is_invisible(env, tenant):
    other = env.tenant(OTHER)
    env.chain.txs[TX] = tx(tenant)
    env.wallet.register_topup(tenant, TX)
    with pytest.raises(AuthProblem) as err:
        env.wallet.get_topup(other, TX)
    assert err.value.status == 404
    with pytest.raises(AuthProblem) as err:
        env.wallet.get_topup(tenant, "0x" + "00" * 32)
    assert err.value.status == 404
    # the same hash cannot be claimed by another owner either: sender must match
    with pytest.raises(AuthProblem):
        env.wallet.register_topup(other, TX)


# ---------------------------------------------------------------- gas floor and agent state


def test_gas_floor_is_twice_base_fee_times_500k(env):
    env.chain.base_fee = 7 * 10**8
    assert env.wallet.gas_floor_wei() == 2 * 7 * 10**8 * GAS_UNITS_FLOOR == env.chain.gas_floor


def test_gas_floor_needs_a_base_fee():
    with pytest.raises(ValueError):
        WalletService(lambda method, params: {"timestamp": "0x1"}).gas_floor_wei()


def test_agent_state_for_a_fresh_wallet_reports_zero_balances(env, tenant):
    env.chain.eth[tenant.agent_address.lower()] = 0
    env.chain.usdc[tenant.agent_address.lower()] = 0
    state = env.wallet.agent_state(tenant)
    assert state["address"] == tenant.agent_address
    assert state["eth_wei"] == "0"
    assert state["usdc"] == {"value": "0", "decimals": 6}
    assert state["gas_floor_wei"] == str(env.chain.gas_floor)
    assert state["suggested_topup_wei"] == str(env.chain.gas_floor * SUGGESTED_TOPUP_FACTOR)
    assert (state["price_source"], state["custody"]) == ("mainnet_reference", "custodial-testnet")
    assert set(state) == {
        "address", "eth_wei", "gas_floor_wei", "suggested_topup_wei", "usdc", "ausdc",
        "price_source", "custody",
    }  # fmt: skip


def test_agent_state_reads_the_session_owners_wallet_only(env):
    a, b = env.tenant(OWNER), env.tenant(OTHER)
    assert a.agent_address != b.agent_address
    env.chain.usdc[a.agent_address.lower()] = 7_000_000
    assert env.wallet.agent_state(a)["usdc"]["value"] == "7000000"
    assert env.wallet.agent_state(b)["usdc"]["value"] != "7000000"


# ---------------------------------------------------------------- audit verify and presets


def test_audit_chain_verifies_per_owner(env):
    a, b = env.tenant(OWNER), env.tenant(OTHER)
    for i in range(3):
        a.gateway.record_no_action(f"note {i}", "2026-10-15T12:00:00Z")
    assert verify_chain(a.gateway.audit_sink).count == 3
    assert verify_chain(b.gateway.audit_sink).count == 0  # B's chain is B's own
    assert verify_chain(b.gateway.audit_sink).valid


def test_presets_listing_is_plain_language_with_a_hash():
    presets = list_presets()
    assert len(presets) == 2
    for preset in presets:
        assert len(preset["ips_hash"]) == 64
        assert set(preset["limits"]) >= {"per_tx_usd", "per_day_usd"}
        assert all(isinstance(v, str) for v in preset["limits"].values())
        assert "USD" in preset["summary"]
    assert presets[0]["preset_id"] == "conservative"
    assert presets[1]["preset_id"] == "diversified"
