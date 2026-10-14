"""Tests for tier permissions, emergency stop, and approval queue."""

import ast
import json
from pathlib import Path

import jsonschema
import pytest

from core.tiers.errors import (
    EmergencyStopActiveError,
    InvalidTierError,
    OrderAlreadyDecidedError,
    OrderAlreadyExistsError,
    OrderNotFoundError,
)
from core.tiers.owner import OwnerControls
from core.tiers.store import TierStore
from core.tiers.view import TierView

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = REPO_ROOT / "contracts" / "schemas" / "tier-state.schema.json"


@pytest.fixture
def tier_schema() -> dict:
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def store() -> TierStore:
    return TierStore(":memory:")


@pytest.fixture
def view(store: TierStore) -> TierView:
    return TierView(store)


@pytest.fixture
def owner(store: TierStore) -> OwnerControls:
    return OwnerControls(store)


def test_default_state_is_t0_and_not_stopped(store: TierStore, view: TierView):
    """Default state on first use must be T0, stopped=False, changed_by=owner."""
    assert view.current() == "T0"
    assert view.is_stopped() is False
    assert view.pending_orders() == []

    raw_state = store.get_tier_state()
    assert raw_state["schema_version"] == 1
    assert raw_state["tier"] == "T0"
    assert raw_state["emergency_stop"] is False
    assert raw_state["changed_by"] == "owner"
    assert "updated_at" in raw_state


def test_set_tier_persists_across_new_store_instance(tmp_path: Path):
    """set_tier('T2') persists across a new TierStore on the same database file."""
    db_file = tmp_path / "tiers.db"

    s1 = TierStore(db_file)
    OwnerControls(s1).set_tier("T2")
    s1.close()

    s2 = TierStore(db_file)
    v2 = TierView(s2)
    assert v2.current() == "T2"
    assert v2.is_stopped() is False
    s2.close()


def test_emergency_stop_persists_across_new_store_instance(tmp_path: Path):
    """Engaged emergency stop flag persists across new TierStore on same file."""
    db_file = tmp_path / "tiers.db"

    s1 = TierStore(db_file)
    OwnerControls(s1).engage_stop()
    s1.close()

    s2 = TierStore(db_file)
    v2 = TierView(s2)
    assert v2.is_stopped() is True
    s2.close()


def test_engage_stop_makes_is_stopped_true_and_approve_raise(owner: OwnerControls, view: TierView):
    """engage_stop() makes is_stopped() true and approve raise; release_stop() restores it."""
    order_id = "00000000-0000-0000-0000-000000000001"
    owner.request_approval(order_id)
    assert view.approval_status(order_id) == "pending"

    owner.engage_stop()
    assert view.is_stopped() is True

    # Approving while stopped must raise EmergencyStopActiveError (subclass of ValueError)
    with pytest.raises(EmergencyStopActiveError):
        owner.approve(order_id)
    with pytest.raises(ValueError):
        owner.approve(order_id)

    # Order remains pending
    assert view.approval_status(order_id) == "pending"

    # release_stop restores normal operation
    owner.release_stop()
    assert view.is_stopped() is False

    owner.approve(order_id)
    assert view.approval_status(order_id) == "approved"


def test_approval_lifecycle_pending_to_approved(
    owner: OwnerControls, view: TierView, store: TierStore
):
    """An approval request starts pending; approve makes it approved."""
    order_id = "11111111-1111-1111-1111-111111111111"
    owner.request_approval(order_id)

    assert view.approval_status(order_id) == "pending"
    assert order_id in view.pending_orders()

    owner.approve(order_id)

    assert view.approval_status(order_id) == "approved"
    assert order_id not in view.pending_orders()

    record = store.get_approval(order_id)
    assert record is not None
    assert record["status"] == "approved"
    assert record["decided_by"] == "owner"
    assert record["decided_at"] is not None


def test_approval_lifecycle_pending_to_rejected(
    owner: OwnerControls, view: TierView, store: TierStore
):
    """An approval request starts pending; reject makes it rejected."""
    order_id = "22222222-2222-2222-2222-222222222222"
    owner.request_approval(order_id)

    assert view.approval_status(order_id) == "pending"
    assert order_id in view.pending_orders()

    owner.reject(order_id)

    assert view.approval_status(order_id) == "rejected"
    assert order_id not in view.pending_orders()

    record = store.get_approval(order_id)
    assert record is not None
    assert record["status"] == "rejected"
    assert record["decided_by"] == "owner"
    assert record["decided_at"] is not None


def test_second_decision_raises(owner: OwnerControls):
    """A second decision on an already decided order must raise OrderAlreadyDecidedError."""
    order_1 = "33333333-3333-3333-3333-333333333333"
    owner.request_approval(order_1)
    owner.approve(order_1)

    with pytest.raises(OrderAlreadyDecidedError):
        owner.approve(order_1)
    with pytest.raises(OrderAlreadyDecidedError):
        owner.reject(order_1)

    order_2 = "44444444-4444-4444-4444-444444444444"
    owner.request_approval(order_2)
    owner.reject(order_2)

    with pytest.raises(OrderAlreadyDecidedError):
        owner.approve(order_2)
    with pytest.raises(OrderAlreadyDecidedError):
        owner.reject(order_2)


def test_deciding_unknown_order_raises(owner: OwnerControls):
    """Approving or rejecting an unknown order raises OrderNotFoundError."""
    unknown_id = "99999999-9999-9999-9999-999999999999"
    with pytest.raises(OrderNotFoundError):
        owner.approve(unknown_id)
    with pytest.raises(KeyError):
        owner.approve(unknown_id)

    with pytest.raises(OrderNotFoundError):
        owner.reject(unknown_id)
    with pytest.raises(KeyError):
        owner.reject(unknown_id)


def test_set_tier_rejects_values_outside_enum(owner: OwnerControls):
    """set_tier rejects values outside the enum (T0, T1, T2)."""
    for bad_value in ["T3", "T4", "T-1", "admin", "t0", "", "none"]:
        with pytest.raises(InvalidTierError):
            owner.set_tier(bad_value)
        with pytest.raises(ValueError):
            owner.set_tier(bad_value)


def test_rejected_or_unknown_order_is_never_reported_as_approved(
    owner: OwnerControls, view: TierView
):
    """A rejected or unknown order is never reported as approved."""
    unknown_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    assert view.approval_status(unknown_id) is None
    assert view.approval_status(unknown_id) != "approved"

    rejected_id = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    owner.request_approval(rejected_id)
    owner.reject(rejected_id)

    assert view.approval_status(rejected_id) == "rejected"
    assert view.approval_status(rejected_id) != "approved"


def test_duplicate_approval_request_raises(owner: OwnerControls):
    """Enqueuing a request for an already tracked order raises OrderAlreadyExistsError."""
    order_id = "cccccccc-cccc-cccc-cccc-cccccccccccc"
    owner.request_approval(order_id)
    with pytest.raises(OrderAlreadyExistsError):
        owner.request_approval(order_id)


def test_tierview_has_no_mutating_attributes():
    """TierView has no attribute that mutates state (assert on the public names)."""
    public_names = [attr for attr in dir(TierView) if not attr.startswith("_")]
    expected_read_only = {
        "approval_status",
        "current",
        "get_approval",
        "is_stopped",
        "pending_orders",
    }
    assert set(public_names) == expected_read_only

    mutating_stems = {
        "set",
        "engage",
        "release",
        "approve",
        "reject",
        "delete",
        "insert",
        "update",
        "write",
        "mutate",
        "change",
        "add",
        "remove",
    }
    for name in public_names:
        for stem in mutating_stems:
            assert stem not in name.lower(), f"Public name '{name}' contains mutating stem '{stem}'"


def imports_owner_controls(source: str) -> bool:
    """Check whether a python source file imports core.tiers.owner or OwnerControls."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "core.tiers.owner" or alias.name.startswith("core.tiers.owner."):
                    return True
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod == "core.tiers.owner" or mod.startswith("core.tiers.owner."):
                return True
            if mod == "core.tiers" and any(a.name == "OwnerControls" for a in node.names):
                return True
    return False


def test_agent_facing_modules_do_not_import_core_tiers_owner():
    """Agent-facing modules (gateway, signer, policy) must not import core.tiers.owner."""
    agent_facing_dirs = [
        REPO_ROOT / "core" / "gateway",
        REPO_ROOT / "core" / "signer",
        REPO_ROOT / "core" / "policy",
    ]
    violations = []
    for d in agent_facing_dirs:
        if not d.exists():
            continue
        for py_path in d.rglob("*.py"):
            source = py_path.read_text(encoding="utf-8")
            if imports_owner_controls(source):
                violations.append(str(py_path.relative_to(REPO_ROOT)))

    assert violations == [], f"Agent-facing modules imported OwnerControls: {violations}"


def test_structural_scanner_detects_planted_violation(tmp_path: Path):
    """Verify that imports_owner_controls correctly catches prohibited imports."""
    file1 = tmp_path / "violation1.py"
    file1.write_text("from core.tiers.owner import OwnerControls\n", encoding="utf-8")
    assert imports_owner_controls(file1.read_text(encoding="utf-8")) is True

    file2 = tmp_path / "violation2.py"
    file2.write_text("import core.tiers.owner\n", encoding="utf-8")
    assert imports_owner_controls(file2.read_text(encoding="utf-8")) is True

    file3 = tmp_path / "violation3.py"
    file3.write_text("from core.tiers import OwnerControls\n", encoding="utf-8")
    assert imports_owner_controls(file3.read_text(encoding="utf-8")) is True

    clean = tmp_path / "clean.py"
    clean.write_text("from core.tiers.view import TierView\n", encoding="utf-8")
    assert imports_owner_controls(clean.read_text(encoding="utf-8")) is False


def test_stored_tier_state_always_validates_against_schema(
    store: TierStore, owner: OwnerControls, tier_schema: dict
):
    """Stored tier state always validates against tier-state.schema.json."""
    validator = jsonschema.Draft202012Validator(tier_schema)

    # Initial state
    state = store.get_tier_state()
    validator.validate(state)
    assert not list(validator.iter_errors(state))

    # set_tier("T1")
    owner.set_tier("T1")
    state = store.get_tier_state()
    validator.validate(state)
    assert not list(validator.iter_errors(state))

    # set_tier("T2")
    owner.set_tier("T2")
    state = store.get_tier_state()
    validator.validate(state)
    assert not list(validator.iter_errors(state))

    # engage_stop
    owner.engage_stop()
    state = store.get_tier_state()
    validator.validate(state)
    assert not list(validator.iter_errors(state))

    # release_stop
    owner.release_stop()
    state = store.get_tier_state()
    validator.validate(state)
    assert not list(validator.iter_errors(state))


def test_context_manager_support(tmp_path: Path):
    """TierStore supports context manager syntax and closes cleanly."""
    db_file = tmp_path / "context.db"
    with TierStore(db_file) as s:
        OwnerControls(s).set_tier("T1")
        assert TierView(s).current() == "T1"
