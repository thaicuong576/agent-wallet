"""KeystoreStore file adapter: round trip, no overwrite, mode 0600, no plaintext key."""

import json
import os
import stat
import sys

import pytest
from eth_account import Account

from core.gateway.custody import FileKeystoreStore, create_agent_wallet

OWNER = "0x" + "ab" * 20
SECRET = "k" * 64


def test_roundtrip_no_overwrite_no_plaintext(tmp_path):
    store = FileKeystoreStore(tmp_path)
    assert not store.exists(OWNER)
    assert store.get(OWNER) is None
    wallet = create_agent_wallet(store, OWNER, SECRET, kdf="pbkdf2")
    assert store.exists(OWNER)
    blob = store.get(OWNER)
    assert json.loads(blob)["address"].lower() == wallet.address.lower().removeprefix("0x")
    assert "private" not in blob.lower()
    with pytest.raises(FileExistsError):
        store.put(OWNER, blob)
    store.put(OWNER, blob, overwrite=True)
    assert store.get(OWNER) == blob


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes are not enforced on Windows")
def test_file_mode_is_0600(tmp_path):
    store = FileKeystoreStore(tmp_path)
    create_agent_wallet(store, OWNER, SECRET, kdf="pbkdf2")
    path = tmp_path / OWNER / "keystore.json"
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    store.put(OWNER, store.get(OWNER), overwrite=True)
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_blob_is_encrypted_with_standard_keystore(tmp_path):
    store = FileKeystoreStore(tmp_path)
    wallet = create_agent_wallet(store, OWNER, SECRET, kdf="pbkdf2")
    key = Account.decrypt(json.loads(store.get(OWNER)), SECRET)
    assert Account.from_key(key).address == wallet.address
    assert key.hex().removeprefix("0x") not in store.get(OWNER)


def test_owners_lists_only_valid_directories(tmp_path):
    store = FileKeystoreStore(tmp_path)
    create_agent_wallet(store, OWNER, SECRET, kdf="pbkdf2")
    (tmp_path / "not-an-address").mkdir()
    assert list(store.owners()) == [OWNER]


@pytest.mark.parametrize("bad", ["../etc", OWNER + "/..", "0x" + "g" * 40, "0x12", ""])
def test_store_rejects_bad_owner(tmp_path, bad):
    with pytest.raises(ValueError):
        FileKeystoreStore(tmp_path).exists(bad)
