"""Custody: fail closed, wrong secret, rotation, redaction, tenant wiring, setup helper."""

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

import pytest
from eth_account import Account

from core.gateway import custody
from core.gateway.custody import (
    CustodyUnavailable,
    FileKeystoreStore,
    KeystoreExists,
    LazySigner,
    create_agent_wallet,
    load_address,
    open_signer,
    rotate,
    secret_from_env,
)
from core.gateway.gateway import _sanitize_error_detail
from core.gateway.setup import generate
from core.gateway.setup import main as setup_main
from core.gateway.tenant import TenantRegistry, TenantSettings
from core.policy.ips import load_ips

OWNER = "0x" + "ab" * 20
OTHER = "0x" + "cd" * 20
SECRET = "s" * 64
NEW_SECRET = "n" * 64
ROOT = Path(__file__).resolve().parents[2]
IPS_PATH = ROOT / "contracts" / "examples" / "ips.valid.json"


@pytest.fixture
def store(tmp_path):
    return FileKeystoreStore(tmp_path / "owners")


@pytest.fixture
def wallet(store):
    return create_agent_wallet(store, OWNER, SECRET, kdf="pbkdf2")


@pytest.mark.parametrize("bad", [None, "", "short", "x" * 31])
def test_short_secret_rejected(store, bad):
    with pytest.raises(CustodyUnavailable):
        create_agent_wallet(store, OWNER, bad, kdf="pbkdf2")
    assert not store.exists(OWNER)


def test_unset_secret_fails_closed_from_env():
    with pytest.raises(CustodyUnavailable):
        secret_from_env({})
    with pytest.raises(CustodyUnavailable):
        secret_from_env({custody.SECRET_ENV: "x" * 10})
    assert secret_from_env({custody.SECRET_ENV: "x" * 32}) == "x" * 32


def test_kdf_from_env_default_and_validation():
    assert custody.kdf_from_env({}) == "scrypt"
    assert custody.kdf_from_env({custody.KDF_ENV: "PBKDF2"}) == "pbkdf2"
    with pytest.raises(CustodyUnavailable):
        custody.kdf_from_env({custody.KDF_ENV: "md5"})


def test_wrong_secret_fails_closed(store, wallet):
    with pytest.raises(CustodyUnavailable):
        open_signer(store, OWNER, "w" * 64)
    with pytest.raises(CustodyUnavailable):
        open_signer(store, OWNER, "short")
    assert open_signer(store, OWNER, SECRET).address == wallet.address


def test_keystore_mode_and_no_overwrite(store, wallet):
    before = store.get(OWNER)
    with pytest.raises(KeystoreExists):
        create_agent_wallet(store, OWNER, SECRET, kdf="pbkdf2")
    assert store.get(OWNER) == before


def test_scrypt_is_the_default_kdf(store):
    create_agent_wallet(store, OTHER, SECRET)
    assert json.loads(store.get(OTHER))["crypto"]["kdf"] == "scrypt"


def test_dump_without_secret_has_no_plaintext_key(store, wallet):
    blob = store.get(OWNER)
    key_hex = Account.decrypt(json.loads(blob), SECRET).hex().removeprefix("0x")
    assert key_hex not in blob.lower()
    assert SECRET not in blob
    assert load_address(store, OWNER) == wallet.address  # address readable without the secret


def test_rotate_keeps_address(store, wallet):
    create_agent_wallet(store, OTHER, SECRET, kdf="pbkdf2")
    other_address = load_address(store, OTHER)
    assert rotate(store, NEW_SECRET, SECRET) == 2
    assert load_address(store, OWNER) == wallet.address
    assert load_address(store, OTHER) == other_address
    assert open_signer(store, OWNER, NEW_SECRET).address == wallet.address
    with pytest.raises(CustodyUnavailable):
        open_signer(store, OWNER, SECRET)
    assert rotate(store, NEW_SECRET, SECRET) == 0  # rerun is a no-op


def test_rotate_rejects_wrong_previous_secret(store, wallet):
    before = store.get(OWNER)
    with pytest.raises(CustodyUnavailable):
        rotate(store, NEW_SECRET, "p" * 64)
    assert store.get(OWNER) == before


def test_rotate_cli_uses_env(tmp_path, store, wallet, capsys):
    env = {
        custody.DATA_DIR_ENV: str(tmp_path),
        custody.SECRET_ENV: NEW_SECRET,
        custody.PREVIOUS_SECRET_ENV: SECRET,
    }
    assert custody.main(["rotate"], env) == 0
    out = capsys.readouterr().out
    assert "1 keystore" in out and SECRET not in out and NEW_SECRET not in out
    assert custody.main(["rotate"], {}) == 1


def test_key_never_in_logs_or_errors(store, wallet, caplog):
    blob = store.get(OWNER)
    key_hex = Account.decrypt(json.loads(blob), SECRET).hex().removeprefix("0x")
    bad_secret = "w" * 64
    seen: list[str] = []
    with caplog.at_level(logging.DEBUG):
        signer = open_signer(store, OWNER, SECRET)
        lazy = LazySigner(store, OWNER, SECRET)
        seen += [repr(signer), str(signer), repr(lazy), str(lazy), repr(wallet), str(wallet)]
        for call in (
            lambda: open_signer(store, OWNER, bad_secret),
            lambda: open_signer(store, OWNER, "short"),
            lambda: create_agent_wallet(store, OWNER, SECRET),
            lambda: rotate(store, NEW_SECRET, bad_secret),
            lambda: LazySigner(store, OWNER, bad_secret).sign_and_send({}),
        ):
            with pytest.raises(CustodyUnavailable) as err:
                call()
            seen += [str(err.value), repr(err.value), _sanitize_error_detail(err.value)]
            assert err.value.__cause__ is None
        seen.append(caplog.text)
    joined = "\n".join(seen).lower()
    for needle in (key_hex, SECRET, bad_secret, NEW_SECRET, blob.lower()):
        assert needle.lower() not in joined


def test_lazy_signer_reads_without_secret_and_caches_signing(store, wallet):
    lazy = LazySigner(store, OWNER, None)
    assert lazy.address == wallet.address
    with pytest.raises(CustodyUnavailable):
        lazy.sign_and_send({"chainId": 84532})
    good = LazySigner(store, OWNER, SECRET, sender=lambda raw: "0x" + "11" * 32)
    tx = {
        "chainId": 84532,
        "nonce": 0,
        "to": wallet.address,
        "value": 0,
        "gas": 21000,
        "maxFeePerGas": 10**9,
        "maxPriorityFeePerGas": 10**9,
        "type": 2,
    }
    assert good.sign_and_send(tx).tx_hash == "0x" + "11" * 32
    first = good._signer
    good.sign_and_send(tx)
    assert good._signer is first  # decrypted once


def _settings(tmp_path, secret=SECRET):
    return TenantSettings(
        data_dir=tmp_path,
        ips_for=lambda owner, preset: load_ips(IPS_PATH),
        keystore_secret=secret,
        kdf="pbkdf2",
        clock=lambda: datetime(2026, 10, 14, tzinfo=UTC),
    )


def _registry(tmp_path, secret=SECRET, max_open=32):
    return TenantRegistry(_settings(tmp_path, secret), rpc=lambda m, p: None, max_open=max_open)


def test_tenant_signer_address_matches_keystore(tmp_path):
    reg = _registry(tmp_path)
    t1 = reg.open(OWNER, create=True)
    t2 = reg.open(OTHER, create=True)
    assert t1.agent_address != t2.agent_address
    assert t1.gateway.signer.address == t1.agent_address
    assert load_address(FileKeystoreStore(tmp_path / "owners"), OWNER) == t1.agent_address
    assert reg.open("0x" + "AB" * 20) is t1  # one tenant per address
    assert t1.paths.root != t2.paths.root and t1.paths.root.is_dir()
    assert t1.paths.keystore.is_file()
    assert t1.paths.audit.parent == t1.paths.root
    assert "private" not in (repr(t1) + str(t1.gateway.signer)).lower()


def test_tenant_without_secret_reads_but_cannot_create_or_sign(tmp_path):
    created = _registry(tmp_path).open(OWNER, create=True)
    fresh = _registry(tmp_path, secret=None)
    with pytest.raises(CustodyUnavailable):
        fresh.open(OTHER, create=True)
    reopened = fresh.open(OWNER)
    assert reopened.agent_address == created.agent_address
    with pytest.raises(CustodyUnavailable):
        reopened.gateway.signer.sign_and_send({"chainId": 84532})


def test_registry_evicts_least_recently_used(tmp_path):
    reg = _registry(tmp_path, max_open=1)
    first = reg.open(OWNER, create=True)
    reg.open(OTHER, create=True)
    again = reg.open(OWNER)
    assert again is not first  # rebuilt after eviction, same address
    assert again.agent_address == first.agent_address


def test_setup_generates_only_if_absent_and_never_prints(tmp_path, capsys):
    env = tmp_path / ".env"
    env.write_text("OTHER=1", encoding="utf-8")
    created, present = generate(env)
    assert len(created) == 2 and present == []
    text = env.read_text(encoding="utf-8")
    values = dict(line.split("=", 1) for line in text.splitlines())
    assert values["OTHER"] == "1"
    assert len(values["AGENT_WALLET_KEYSTORE_SECRET"]) == 64
    assert values["AGENT_WALLET_KEYSTORE_SECRET"] != values["AGENT_WALLET_SESSION_SECRET"]

    assert setup_main(["--env-file", str(env)]) == 0
    out = capsys.readouterr().out
    assert env.read_text(encoding="utf-8") == text  # refuses to overwrite
    for v in values.values():
        assert v not in out


def test_setup_keeps_an_existing_value(tmp_path):
    env = tmp_path / ".env"
    env.write_text("AGENT_WALLET_KEYSTORE_SECRET=mine\n", encoding="utf-8")
    created, _ = generate(env)
    assert created == ["AGENT_WALLET_SESSION_SECRET"]
    assert "AGENT_WALLET_KEYSTORE_SECRET=mine\n" in env.read_text(encoding="utf-8")
