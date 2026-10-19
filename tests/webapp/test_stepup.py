"""Step-up challenges: binding, expiry and single use."""

from __future__ import annotations

from tests.webapp.conftest import HEADERS, login, step_up


def _slug(resp) -> str:
    return resp.json()["type"].rsplit(":", 1)[1]


def test_stop_session_only_release_needs_stepup(client, wallet):
    login(client, wallet)
    assert client.post("/emergency-stop", headers=HEADERS).status_code == 200
    r = client.post("/emergency-stop/release", headers=HEADERS)
    assert r.status_code == 401
    assert _slug(r) == "step-up-required"
    assert r.json()["challenge"]["action"] == "release_stop"
    header = step_up(client, wallet, "release_stop")
    ok = client.post("/emergency-stop/release", headers={**HEADERS, "X-Step-Up": header})
    assert ok.status_code == 200
    assert ok.json()["by"] == wallet.address.lower()


def test_challenge_message_names_action_and_params(client, wallet):
    login(client, wallet)
    r = client.post(
        "/auth/step-up",
        json={"action": "approve", "params": {"order_id": "o-1", "amount": "5"}},
        headers=HEADERS,
    ).json()
    msg = r["message"]
    assert "Action: approve" in msg
    assert '"amount":"5"' in msg and '"order_id":"o-1"' in msg
    assert "Parameters hash:" in msg and f"Challenge: {r['challenge_id']}" in msg
    assert "Expiration Time: 2026-10-15T12:02:00Z" in msg
    bad = client.post("/auth/step-up", json={"action": "mint"}, headers=HEADERS)
    assert bad.status_code == 422


def test_challenge_bound_to_action_and_params(client, wallet):
    login(client, wallet)
    # a release_stop challenge cannot authorise approve
    header = step_up(client, wallet, "release_stop", {"order_id": "o-1"})
    r = client.post("/orders/o-1/approve", headers={**HEADERS, "X-Step-Up": header})
    assert r.status_code == 401 and _slug(r) == "step-up-required"
    # an approve challenge for order o-1 cannot authorise order o-2
    header = step_up(client, wallet, "approve", {"order_id": "o-1"})
    r = client.post("/orders/o-2/approve", headers={**HEADERS, "X-Step-Up": header})
    assert r.status_code == 401 and _slug(r) == "step-up-required"
    # the right pair works
    header = step_up(client, wallet, "approve", {"order_id": "o-2"})
    r = client.post("/orders/o-2/approve", headers={**HEADERS, "X-Step-Up": header})
    assert r.status_code == 200 and r.json() == {"approved": "o-2"}


def test_challenge_single_use_and_expiry(client, wallet, clock):
    login(client, wallet)
    header = step_up(client, wallet, "release_stop")
    h = {**HEADERS, "X-Step-Up": header}
    assert client.post("/emergency-stop/release", headers=h).status_code == 200
    replay = client.post("/emergency-stop/release", headers=h)
    assert replay.status_code == 401 and _slug(replay) == "step-up-required"

    header = step_up(client, wallet, "release_stop")
    clock.advance(minutes=2, seconds=1)
    # the session (8 h) is still valid; only the challenge is stale
    late = client.post("/emergency-stop/release", headers={**HEADERS, "X-Step-Up": header})
    assert late.status_code == 401 and _slug(late) == "step-up-required"


def test_stepup_signature_from_other_address_rejected(client, wallet, other_wallet):
    login(client, wallet)
    body = client.post(
        "/auth/step-up", json={"action": "release_stop", "params": {}}, headers=HEADERS
    ).json()
    header = f"{body['challenge_id']}.{other_wallet.sign(body['message'])}"
    r = client.post("/emergency-stop/release", headers={**HEADERS, "X-Step-Up": header})
    assert r.status_code == 401 and _slug(r) == "signature-invalid"


def test_stepup_challenge_not_usable_by_another_session(client, wallet, other_wallet):
    login(client, wallet)
    header = step_up(client, wallet, "release_stop")
    client.cookies.clear()
    login(client, other_wallet)
    r = client.post("/emergency-stop/release", headers={**HEADERS, "X-Step-Up": header})
    assert r.status_code == 401 and _slug(r) == "step-up-required"


def test_stepup_missing_or_malformed_header(client, wallet):
    login(client, wallet)
    for value in ["", "nodot", "a.b.c", "zz." + "00" * 65, "0" * 32 + ".0x" + "00" * 65]:
        r = client.post("/emergency-stop/release", headers={**HEADERS, "X-Step-Up": value})
        assert r.status_code == 401, value


def test_stepup_requires_session_and_csrf(client, wallet):
    r = client.post("/auth/step-up", json={"action": "approve"}, headers=HEADERS)
    assert r.status_code == 401
    login(client, wallet)
    assert client.post("/auth/step-up", json={"action": "approve"}).status_code == 403
