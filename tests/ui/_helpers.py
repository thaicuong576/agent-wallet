"""Shared module-level helpers for UI tests."""

from __future__ import annotations

# Design section 12.2 route list, plus the changed existing routes of the same section.
DESIGN_ROUTES = {
    "/auth/nonce",
    "/auth/verify",
    "/auth/logout",
    "/auth/step-up",
    "/me",
    "/agent",
    "/topups",
    "/topups/{tx_hash}",
    "/agent/mint",
    "/agent/withdraw",
    "/presets",
    "/policy",
    "/runs",
    "/chat",
    "/jobs/{job_id}",
    "/jobs",
    "/audit/verify",
    "/portfolio",
    "/tier",
    "/ips",
    "/decisions",
    "/emergency-stop",
    "/emergency-stop/release",
    "/orders/{order_id}/approve",
    "/orders/{order_id}/reject",
    # Webapp v2 (docs/design/webapp/v2-contract.md C1 to C5)
    "/chat/threads",
    "/chat/suggestions",
    "/limits",
    "/jobs/{job_id}/cancel",
    # Chat v3 (spec PR C: SSE stream)
    "/chat/stream/{job_id}",
}
# Links that open in a new tab or are handed to the wallet; the page never fetches them.
ALLOWED_URLS = {
    "https://sepolia.basescan.org/tx/",
    "https://sepolia.base.org",
    "https://sepolia.basescan.org",
}


def _block_after(css: str, query: str) -> str:
    start = css.index(query)
    end = css.find("@media", start + len(query))
    return css[start : end if end != -1 else len(css)]
