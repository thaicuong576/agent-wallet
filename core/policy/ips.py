"""IPS loader: schema validation, canonical JSON hashing, and read-only mapping."""

from __future__ import annotations

import functools
import hashlib
import json
from collections.abc import Iterator, Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Any

import jsonschema
import jsonschema.protocols
import jsonschema.validators

ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_SCHEMA_PATH = ROOT / "contracts" / "schemas" / "ips.schema.json"


class IpsError(ValueError):
    """Raised when an IPS document fails schema validation or syntax checks."""


def _freeze(value: Any) -> Any:
    """Recursively freeze dicts into MappingProxyType and lists into tuples."""
    if isinstance(value, dict):
        return MappingProxyType({k: _freeze(v) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _to_plain_data(value: Any) -> Any:
    """Recursively convert MappingProxyType or LoadedIps back to plain dicts/lists."""
    if isinstance(value, LoadedIps):
        return _to_plain_data(value.ips)
    if isinstance(value, (dict, MappingProxyType)):
        return {k: _to_plain_data(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_plain_data(v) for v in value]
    return value


def canonical_ips_json(data: Any) -> bytes:
    """Compute canonical JSON representation of IPS data.

    Sorted keys, compact separators (',', ':'), UTF-8 encoded, ensure_ascii=False.
    """
    plain = _to_plain_data(data)
    encoded = json.dumps(plain, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return encoded.encode("utf-8")


def compute_ips_hash(data: Any) -> str:
    """Compute lowercase hex SHA-256 hash of the canonical JSON of the IPS."""
    return hashlib.sha256(canonical_ips_json(data)).hexdigest()


class LoadedIps(Mapping[str, Any]):
    """Immutable representation of an Investment Policy Statement with its SHA-256 hash.

    Exposes no write methods and no persistence mechanism.
    All item and attribute mutations raise TypeError or AttributeError.
    """

    __slots__ = ("_ips", "_hash")

    def __init__(self, ips: MappingProxyType[str, Any], hash: str) -> None:
        super().__setattr__("_ips", ips)
        super().__setattr__("_hash", hash)

    @property
    def ips(self) -> MappingProxyType[str, Any]:
        """Deeply read-only mapping of the IPS content."""
        return self._ips

    @property
    def hash(self) -> str:
        """Lowercase hex SHA-256 hash of the canonical JSON of the IPS."""
        return self._hash

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError(f"LoadedIps is immutable; cannot assign attribute {name!r}")

    def __delattr__(self, name: str) -> None:
        raise AttributeError(f"LoadedIps is immutable; cannot delete attribute {name!r}")

    def __getitem__(self, key: str) -> Any:
        return self._ips[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._ips)

    def __len__(self) -> int:
        return len(self._ips)

    def __contains__(self, key: object) -> bool:
        return key in self._ips

    def __repr__(self) -> str:
        return f"LoadedIps(ips={self._ips!r}, hash={self._hash!r})"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, LoadedIps):
            return False
        return self._ips == other._ips and self._hash == other._hash


def _create_strict_validator(schema: dict[str, Any]) -> jsonschema.protocols.Validator:
    """Create a Draft 2020-12 validator that strictly rejects floats where integers are expected."""
    type_checker = jsonschema.Draft202012Validator.TYPE_CHECKER.redefine(
        "integer",
        lambda _checker, instance: isinstance(instance, int) and not isinstance(instance, bool),
    )
    strict_cls = jsonschema.validators.extend(
        jsonschema.Draft202012Validator,
        type_checker=type_checker,
    )
    return strict_cls(schema)


@functools.lru_cache(maxsize=8)
def _get_validator(schema_path_str: str) -> jsonschema.protocols.Validator:
    schema_path = Path(schema_path_str)
    if not schema_path.is_file():
        raise FileNotFoundError(f"IPS schema file not found: {schema_path}")
    with open(schema_path, encoding="utf-8") as f:
        schema = json.load(f)
    return _create_strict_validator(schema)


def parse_ips(data: Any, schema_path: str | Path | None = None) -> LoadedIps:
    """Validate raw IPS dictionary data and return an immutable LoadedIps instance."""
    resolved_schema = str(schema_path or DEFAULT_SCHEMA_PATH)
    validator = _get_validator(resolved_schema)
    errors = sorted(validator.iter_errors(data), key=lambda e: str(e.path))
    if errors:
        first = errors[0]
        path_str = f" at '{first.json_path}'" if first.json_path else ""
        raise IpsError(f"IPS schema validation failed{path_str}: {first.message}")

    ips_hash = compute_ips_hash(data)
    frozen_ips = _freeze(data)
    return LoadedIps(ips=frozen_ips, hash=ips_hash)


def load_ips(path: str | Path, schema_path: str | Path | None = None) -> LoadedIps:
    """Load an IPS JSON file, validate against schema, and return an immutable LoadedIps."""
    file_path = Path(path)
    if not file_path.is_file():
        raise FileNotFoundError(f"IPS file not found: {file_path}")

    try:
        with open(file_path, encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        raise IpsError(f"Invalid JSON in IPS file at '{file_path}': {e}") from e

    return parse_ips(data, schema_path=schema_path)
