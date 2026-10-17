"""Job constants, error mappings, and schema paths."""

from __future__ import annotations

import re
from pathlib import Path

from core.runner.runner import SKIP_PREFIX

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
ORDER_SCHEMA = ROOT_DIR / "contracts" / "schemas" / "order.schema.json"

KINDS = ("run", "mint", "withdraw", "chat")
ACTION_KINDS = ("run", "chat_action")
RUN_LIKE = ("run", "chat")
CHAT_MESSAGE_MAX_CHARS = 500
ACTIVE = ("queued", "running")
STOP_CODES = frozenset({"TIER.EMERGENCY_STOP", "TIER.GLOBAL_STOP"})
OWNER_STOP_CODE = "TIER.EMERGENCY_STOP"
CANCEL_CODE = "OWNER_CANCELLED"
CANCEL_REASON = "owner-cancelled"
TERMINAL_PHASE = "done"
_SKIP_TAIL = re.compile(rf"^({re.escape(SKIP_PREFIX)} [^:]*): .*$", re.DOTALL)
GLOBAL_STOP_CODE = "TIER.GLOBAL_STOP"
ERROR_CODE_MAP = {"SIGNER_UNAVAILABLE": "RPC_UNAVAILABLE"}
USDC_UNIT = 10**6
