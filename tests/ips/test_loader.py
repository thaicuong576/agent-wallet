"""Tests for IPS loader: schema validation, canonical JSON hash, and read-only object."""

import copy
import glob
import json
import re
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType

import pytest

from core.policy import IpsError, LoadedIps, load_ips
from core.policy.ips import canonical_ips_json, compute_ips_hash

CONTRACTS_DIR = Path(__file__).resolve().parent.parent.parent / "contracts"
VALID_IPS_PATH = CONTRACTS_DIR / "examples" / "ips.valid.json"
VALID_APPROVAL_PATH = CONTRACTS_DIR / "examples" / "ips.valid.approval-band.json"


@pytest.mark.parametrize("path", [VALID_IPS_PATH, VALID_APPROVAL_PATH])
def test_valid_examples_load_successfully(path: Path):
    loaded = load_ips(path)
    assert isinstance(loaded, LoadedIps)
    assert isinstance(loaded, Mapping)
    assert isinstance(loaded.ips, MappingProxyType)
    assert isinstance(loaded.hash, str)
    assert re.fullmatch(r"^[0-9a-f]{64}$", loaded.hash)
    assert loaded["schema_version"] == 1
    assert loaded.ips["schema_version"] == 1
    assert loaded["owner"].startswith("0x")


def test_hash_is_stable_across_runs():
    loaded_first = load_ips(VALID_IPS_PATH)
    loaded_second = load_ips(VALID_IPS_PATH)
    assert loaded_first.hash == loaded_second.hash
    assert loaded_first.hash == "5d2a59943f97d6706d28bb583c2e5f17ec02d03fff357125973260f05b21060e"

    approval_first = load_ips(VALID_APPROVAL_PATH)
    approval_second = load_ips(VALID_APPROVAL_PATH)
    assert approval_first.hash == approval_second.hash


def test_hash_changes_on_any_single_value_change(tmp_path: Path):
    with open(VALID_IPS_PATH, encoding="utf-8") as f:
        base_data = json.load(f)
    base_loaded = load_ips(VALID_IPS_PATH)

    mutations = [
        ("objective", "A completely different objective"),
        ("owner", "0x2222222222222222222222222222222222222222"),
        ("allocation.max_weight_per_asset_bps", 3000),
        ("allocation.min_stable_weight_bps", 7000),
        ("risk.max_depeg_bps", 100),
        ("limits.per_tx_usd", "250"),
        ("approval.escalate_above_usd", "500"),
        ("universe.tokens", ["USDC"]),
    ]

    for key_path, new_value in mutations:
        modified = copy.deepcopy(base_data)
        parts = key_path.split(".")
        target = modified
        for p in parts[:-1]:
            target = target[p]
        target[parts[-1]] = new_value

        file_path = tmp_path / f"ips_modified_{key_path}.json"
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(modified, f)

        loaded_mod = load_ips(file_path)
        assert loaded_mod.hash != base_loaded.hash, f"Hash did not change for {key_path}"


def test_each_invalid_contract_example_raises_ips_error():
    invalid_files = sorted(glob.glob(str(CONTRACTS_DIR / "examples" / "ips.invalid.*.json")))
    assert len(invalid_files) >= 5, "Expected at least 5 invalid examples in contracts/examples/"

    for path_str in invalid_files:
        path = Path(path_str)
        with pytest.raises(IpsError):
            load_ips(path)


@pytest.mark.parametrize(
    ("field_path", "float_val"),
    [
        ("allocation.max_weight_per_asset_bps", 4000.0),
        ("allocation.max_weight_per_asset_bps", 0.4),
        ("allocation.min_stable_weight_bps", 6000.5),
        ("risk.max_depeg_bps", 50.0),
        ("risk.max_utilization_bps", 9000.0),
    ],
)
def test_float_where_basis_point_integer_required_raises_ips_error(
    tmp_path: Path, field_path: str, float_val: float
):
    with open(VALID_IPS_PATH, encoding="utf-8") as f:
        data = json.load(f)

    parts = field_path.split(".")
    target = data
    for p in parts[:-1]:
        target = target[p]
    target[parts[-1]] = float_val

    file_path = tmp_path / "ips_float.json"
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f)

    with pytest.raises(IpsError):
        load_ips(file_path)


def test_unknown_fields_raise_ips_error(tmp_path: Path):
    with open(VALID_IPS_PATH, encoding="utf-8") as f:
        data = json.load(f)

    data["unknown_top_field"] = "malicious"
    file_path = tmp_path / "ips_unknown_top.json"
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f)

    with pytest.raises(IpsError):
        load_ips(file_path)


def test_missing_or_invalid_schema_version_raises_ips_error(tmp_path: Path):
    with open(VALID_IPS_PATH, encoding="utf-8") as f:
        data = json.load(f)

    del data["schema_version"]
    file_path_missing = tmp_path / "ips_missing_ver.json"
    with open(file_path_missing, "w", encoding="utf-8") as f:
        json.dump(data, f)

    with pytest.raises(IpsError):
        load_ips(file_path_missing)

    data["schema_version"] = 2
    file_path_v2 = tmp_path / "ips_v2.json"
    with open(file_path_v2, "w", encoding="utf-8") as f:
        json.dump(data, f)

    with pytest.raises(IpsError):
        load_ips(file_path_v2)


def test_loaded_ips_item_assignment_raises_type_error():
    loaded = load_ips(VALID_IPS_PATH)

    with pytest.raises(TypeError):
        loaded["owner"] = "0x2222222222222222222222222222222222222222"

    with pytest.raises(TypeError):
        loaded["new_field"] = 123

    with pytest.raises(TypeError):
        del loaded["owner"]

    with pytest.raises(TypeError):
        loaded.ips["owner"] = "0x2222222222222222222222222222222222222222"

    with pytest.raises(TypeError):
        del loaded.ips["owner"]

    with pytest.raises(TypeError):
        loaded.ips["universe"]["tokens"][0] = "DAI"

    with pytest.raises(TypeError):
        loaded.ips["allocation"]["max_weight_per_asset_bps"] = 5000

    with pytest.raises(TypeError):
        del loaded.ips["allocation"]["max_weight_per_asset_bps"]


def test_loaded_ips_attribute_assignment_raises_attribute_error():
    loaded = load_ips(VALID_IPS_PATH)

    with pytest.raises(AttributeError):
        loaded.hash = "tampered_hash_string"

    with pytest.raises(AttributeError):
        loaded.ips = {}

    with pytest.raises(AttributeError):
        loaded.owner = "0x2222222222222222222222222222222222222222"

    with pytest.raises(AttributeError):
        del loaded.hash

    with pytest.raises(AttributeError):
        del loaded.ips

    with pytest.raises(AttributeError):
        loaded.ips.owner = "0x2222222222222222222222222222222222222222"


def test_loaded_ips_exposes_no_write_or_save_methods():
    loaded = load_ips(VALID_IPS_PATH)

    for forbidden in ["save", "write", "dump", "update", "set", "modify"]:
        assert not hasattr(loaded, forbidden)
        assert not hasattr(loaded.ips, forbidden)


def test_malformed_json_raises_ips_error(tmp_path: Path):
    broken_file = tmp_path / "broken.json"
    broken_file.write_text('{"schema_version": 1, "owner": ', encoding="utf-8")

    with pytest.raises(IpsError):
        load_ips(broken_file)


def test_missing_file_raises_file_not_found(tmp_path: Path):
    missing_file = tmp_path / "nonexistent.json"
    with pytest.raises(FileNotFoundError):
        load_ips(missing_file)


def test_deep_immutability_mapping_and_tuples():
    loaded = load_ips(VALID_IPS_PATH)

    assert isinstance(loaded.ips, MappingProxyType)
    assert isinstance(loaded.ips["universe"], MappingProxyType)
    assert isinstance(loaded.ips["universe"]["tokens"], tuple)
    assert isinstance(loaded.ips["universe"]["protocols"], tuple)
    assert isinstance(loaded.ips["universe"]["functions"], tuple)
    assert isinstance(loaded.ips["universe"]["recipients"], tuple)
    assert isinstance(loaded.ips["allocation"], MappingProxyType)
    assert isinstance(loaded.ips["risk"], MappingProxyType)
    assert isinstance(loaded.ips["limits"], MappingProxyType)
    assert isinstance(loaded.ips["approval"], MappingProxyType)


def test_canonical_json_and_compute_hash():
    loaded = load_ips(VALID_IPS_PATH)
    canon_bytes = canonical_ips_json(loaded)
    assert isinstance(canon_bytes, bytes)
    assert compute_ips_hash(loaded) == loaded.hash
