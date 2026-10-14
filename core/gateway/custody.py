"""Agent key custody: generate, encrypt, store, decrypt once, rotate (design section 4).

Importable only by core.gateway modules (boundary-tested). The key, the keystore JSON and the
secret never appear in repr, str, logs or exception text: every failure raises a fixed message.
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from eth_account import Account
from eth_utils import to_checksum_address

from core.signer import LocalSigner, Signer, TxReceiptStub

SECRET_ENV = "AGENT_WALLET_KEYSTORE_SECRET"
PREVIOUS_SECRET_ENV = "AGENT_WALLET_KEYSTORE_SECRET_PREVIOUS"
KDF_ENV = "AGENT_WALLET_KEYSTORE_KDF"
DATA_DIR_ENV = "AGENT_WALLET_DATA_DIR"
DEFAULT_DATA_DIR = ".data"
MIN_SECRET_BYTES = 32
KDFS = ("scrypt", "pbkdf2")
OWNER_RE = re.compile(r"^0x[0-9a-f]{40}$")
KEYSTORE_FILE = "keystore.json"


class CustodyUnavailable(Exception):
    """Custody cannot create or use a key. Messages are fixed and never carry key material."""


class KeystoreExists(CustodyUnavailable):
    """A keystore already exists for this owner; custody never overwrites one."""


def normalize_owner(owner: Any) -> str:
    """Lowercase and validate an owner address; the only input a path or key is built from."""
    if not isinstance(owner, str):
        raise ValueError("invalid owner address")
    candidate = owner.lower()
    if not OWNER_RE.fullmatch(candidate):
        raise ValueError("invalid owner address")
    return candidate


def check_secret(secret: str | None) -> str:
    """Return the secret if it has at least 32 bytes, otherwise fail closed."""
    if not isinstance(secret, str) or len(secret.encode("utf-8")) < MIN_SECRET_BYTES:
        raise CustodyUnavailable("keystore secret is unset or shorter than 32 bytes")
    return secret


def secret_from_env(env: Mapping[str, str] | None = None, name: str = SECRET_ENV) -> str:
    source = os.environ if env is None else env
    return check_secret(source.get(name))


def kdf_from_env(env: Mapping[str, str] | None = None) -> str:
    source = os.environ if env is None else env
    kdf = (source.get(KDF_ENV) or "scrypt").strip().lower()
    if kdf not in KDFS:
        raise CustodyUnavailable("unsupported keystore KDF")
    return kdf


@dataclass(frozen=True)
class AgentWallet:
    address: str  # public


class FileKeystoreStore:
    """KeystoreStore on files: <root>/<owner>/keystore.json, mode 0600, encrypted JSON only."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    def _path(self, owner: str) -> Path:
        addr = normalize_owner(owner)
        path = self.root / addr / KEYSTORE_FILE
        if not path.resolve().is_relative_to(self.root.resolve()):
            raise ValueError("invalid owner address")
        return path

    def put(self, owner: str, blob: str, *, overwrite: bool = False) -> None:
        path = self._path(owner)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = blob.encode("utf-8")
        if not overwrite:
            # O_EXCL makes "create, never overwrite" atomic; the mode applies at creation.
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                os.write(fd, data)
            finally:
                os.close(fd)
            return
        tmp = path.with_name(f"{KEYSTORE_FILE}.{os.getpid()}.{threading.get_ident()}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, data)
        finally:
            os.close(fd)
        os.replace(tmp, path)

    def get(self, owner: str) -> str | None:
        try:
            return self._path(owner).read_text(encoding="utf-8")
        except FileNotFoundError:
            return None

    def exists(self, owner: str) -> bool:
        return self._path(owner).is_file()

    def owners(self) -> Iterator[str]:
        if not self.root.is_dir():
            return
        for child in sorted(self.root.iterdir()):
            if OWNER_RE.fullmatch(child.name) and (child / KEYSTORE_FILE).is_file():
                yield child.name


def _encrypt(private_key: Any, secret: str, kdf: str) -> str:
    try:
        return json.dumps(Account.encrypt(private_key, secret, kdf=kdf))
    except Exception:
        raise CustodyUnavailable("keystore encryption failed") from None


def _decrypt(blob: str, secret: str) -> Any:
    try:
        return Account.decrypt(json.loads(blob), secret)
    except Exception:
        # eth_account's own message is dropped; ours is fixed.
        raise CustodyUnavailable("keystore could not be decrypted") from None


def _stored_address(blob: str) -> str:
    try:
        raw = json.loads(blob)["address"]
        return str(to_checksum_address("0x" + str(raw).removeprefix("0x")))
    except Exception:
        raise CustodyUnavailable("keystore is malformed") from None


def create_agent_wallet(store: Any, owner: str, secret: str, kdf: str = "scrypt") -> AgentWallet:
    """Generate an agent key, store it encrypted, return only the address. Never overwrites."""
    owner = normalize_owner(owner)
    check_secret(secret)
    if kdf not in KDFS:
        raise CustodyUnavailable("unsupported keystore KDF")
    if store.exists(owner):
        raise KeystoreExists("a keystore already exists for this owner")
    account = Account.create()
    blob = _encrypt(account.key, secret, kdf)
    try:
        store.put(owner, blob, overwrite=False)
    except FileExistsError:
        raise KeystoreExists("a keystore already exists for this owner") from None
    return AgentWallet(address=account.address)


def load_address(store: Any, owner: str) -> str:
    """Return the agent address from the stored "address" field, without decrypting."""
    blob = store.get(normalize_owner(owner))
    if blob is None:
        raise CustodyUnavailable("no keystore for this owner")
    return _stored_address(blob)


def _local_signer(blob: str, secret: str, sender: Callable[[bytes], Any] | None) -> LocalSigner:
    check_secret(secret)
    key = _decrypt(blob, secret)
    try:
        signer = LocalSigner(sender=sender, private_key=key.hex())
    except Exception:
        raise CustodyUnavailable("keystore holds an unusable key") from None
    finally:
        del key
    if signer.address != _stored_address(blob):
        raise CustodyUnavailable("keystore address does not match its key")
    return signer


def open_signer(
    store: Any, owner: str, secret: str, *, sender: Callable[[bytes], Any] | None = None
) -> Signer:
    """Decrypt the owner's keystore and return a LocalSigner built from the key."""
    blob = store.get(normalize_owner(owner))
    if blob is None:
        raise CustodyUnavailable("no keystore for this owner")
    return _local_signer(blob, secret, sender)


class LazySigner:
    """Signer that decrypts on the first signing use and caches the LocalSigner.

    Reads (address) never decrypt, so a tenant stays readable while the secret is missing; only
    signing fails closed.
    """

    def __init__(
        self,
        store: Any,
        owner: str,
        secret: str | None,
        *,
        sender: Callable[[bytes], Any] | None = None,
    ) -> None:
        self._store = store
        self._owner = normalize_owner(owner)
        self._secret = secret
        self._sender = sender
        self._signer: Signer | None = None
        self._lock = threading.Lock()
        self._address = load_address(store, self._owner)

    @property
    def address(self) -> str:
        return self._address

    def _resolve(self) -> Signer:
        with self._lock:
            if self._signer is None:
                self._signer = open_signer(
                    self._store, self._owner, check_secret(self._secret), sender=self._sender
                )
            return self._signer

    def sign_and_send(self, tx: dict[str, Any]) -> TxReceiptStub:
        return self._resolve().sign_and_send(tx)

    def __repr__(self) -> str:
        return f"LazySigner(address={self._address!r})"

    __str__ = __repr__


def rotate(store: Any, new_secret: str, previous_secret: str) -> int:
    """Re-encrypt every keystore from previous_secret to new_secret; return how many changed.

    A keystore that already opens with the new secret is left alone, so a rerun is safe. The
    address is checked unchanged before each write. Run offline or with the global stop set.
    """
    check_secret(new_secret)
    check_secret(previous_secret)
    changed = 0
    for owner in list(store.owners()):
        blob = store.get(owner)
        if blob is None:
            continue
        try:
            _decrypt(blob, new_secret)
            continue
        except CustodyUnavailable:
            pass
        key = _decrypt(blob, previous_secret)
        kdf = str(json.loads(blob).get("crypto", {}).get("kdf", "scrypt"))
        new_blob = _encrypt(key, new_secret, kdf if kdf in KDFS else "scrypt")
        del key
        if _stored_address(new_blob) != _stored_address(blob):
            raise CustodyUnavailable("rotation would change the address")
        store.put(owner, new_blob, overwrite=True)
        changed += 1
    return changed


def _keystore_root(env: Mapping[str, str]) -> Path:
    return Path(env.get(DATA_DIR_ENV) or DEFAULT_DATA_DIR) / "owners"


def main(argv: list[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    source = os.environ if env is None else env
    if args != ["rotate"]:
        print("usage: python -m core.gateway.custody rotate")
        return 2
    try:
        count = rotate(
            FileKeystoreStore(_keystore_root(source)),
            secret_from_env(source, SECRET_ENV),
            secret_from_env(source, PREVIOUS_SECRET_ENV),
        )
    except CustodyUnavailable as err:
        print(f"rotation refused: {err}")
        return 1
    print(f"re-encrypted {count} keystore(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
