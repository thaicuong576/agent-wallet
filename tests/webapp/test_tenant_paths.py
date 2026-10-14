"""Owner addresses are validated and normalised before any path is built."""

import pytest

from core.gateway.custody import normalize_owner
from core.gateway.tenant import TenantNotFound, TenantRegistry, TenantSettings

A = "0x" + "ab" * 20
B = "0x" + "cd" * 20


def _registry(tmp_path):
    def ips_for(owner, preset):
        raise AssertionError("no tenant should be built in these tests")

    return TenantRegistry(TenantSettings(data_dir=tmp_path, ips_for=ips_for), rpc=lambda m, p: None)


@pytest.mark.parametrize(
    "bad",
    [
        "../../etc/passwd",
        A + "/../" + B,
        "0x" + "ab" * 19,  # too short
        "0x" + "ab" * 21,  # too long
        "0x" + "zz" * 20,  # non-hex
        "ab" * 20,  # no prefix
        "0x" + "ab" * 20 + "\n",
        " " + A,
        "",
        None,
        123,
    ],
)
def test_address_is_validated_and_normalised_rejects(bad, tmp_path):
    with pytest.raises(ValueError):
        normalize_owner(bad)
    with pytest.raises(ValueError):
        _registry(tmp_path).open(bad)
    assert list(tmp_path.iterdir()) == []


def test_address_is_validated_and_normalised_case_alias():
    mixed = "0x" + "AbCdEf" * 6 + "ABCD"
    assert normalize_owner(mixed) == mixed.lower()
    assert normalize_owner("0X" + "AB" * 20) == A


def test_path_stays_inside_owners_dir(tmp_path):
    reg = _registry(tmp_path)
    upper_b = "0x" + "CD" * 20
    pa, pb = reg._paths(A), reg._paths(upper_b)
    owners = (tmp_path / "owners").resolve()
    for p in (pa, pb):
        assert p.root.resolve().is_relative_to(owners)
        assert p.state.parent == p.root
    assert pa.root != pb.root
    assert pa.root.name == A and pb.root.name == B
    assert reg._paths("0x" + "AB" * 20) == pa  # aliasing maps to one directory


def test_unknown_owner_without_create_is_not_found(tmp_path):
    reg = _registry(tmp_path)
    assert reg.exists(A) is False
    with pytest.raises(TenantNotFound):
        reg.open(A)
    assert not (tmp_path / "owners" / A).exists()
