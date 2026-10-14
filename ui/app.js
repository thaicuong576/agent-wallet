// Owner console. Plain JS, no build step. Every API string is rendered with textContent.
"use strict";

const PATHS = {
  decisions: "/decisions",
  tier: "/tier",
  stop: "/emergency-stop",
  release: "/emergency-stop/release",
  approve: "/orders/{order_id}/approve",
  reject: "/orders/{order_id}/reject",
};

const BADGE = {
  denied: "✕ DENIED",
  pending_approval: "! ESCALATED, awaiting approval",
  executed: "✓ EXECUTED",
  failed: "✕ FAILED",
  no_action: "– NO ACTION",
};

let ownerToken = ""; // memory only: never stored, never in a URL

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
}
const $ = (id) => document.getElementById(id);

async function call(method, path, owner) {
  const headers = {};
  if (owner) headers["Authorization"] = "Bearer " + ownerToken;
  const res = await fetch(path, { method, headers });
  let body = null;
  try {
    body = await res.json();
  } catch (_) {
    body = null;
  }
  return { status: res.status, body };
}

function problemText(res) {
  if (res.status === 503) return "Owner controls are not configured on the server (503).";
  if (res.status === 401) return "The owner token was rejected (401).";
  const detail = res.body && (res.body.detail || res.body.title);
  return "Request failed (" + res.status + ")" + (detail ? ": " + detail : "");
}

async function fetchAllRecords() {
  const out = [];
  let cursor = null;
  for (let page = 0; page < 100; page++) {
    const q = "?limit=200" + (cursor ? "&cursor=" + encodeURIComponent(cursor) : "");
    const res = await call("GET", PATHS.decisions + q, false);
    if (res.status !== 200) throw new Error(problemText(res));
    out.push(...res.body.items);
    cursor = res.body.next_cursor;
    if (!cursor) break;
  }
  return out;
}

function recordSummary(r) {
  const src = r.decision || r.no_action;
  return {
    codes: src && src.rules_hit ? src.rules_hit.map((x) => x.code) : [],
    reason: src ? src.reason : "",
  };
}

function decisionCard(r) {
  const li = el("li", r.outcome);
  const head = el("div");
  head.append(el("span", "badge " + r.outcome, BADGE[r.outcome] || r.outcome));
  head.append(el("span", "", "seq " + r.seq));
  li.append(head);
  const s = recordSummary(r);
  li.append(el("p", "kv", "Rules: " + (s.codes.length ? s.codes.join(", ") : "none")));
  li.append(el("p", "", s.reason));
  li.append(el("p", "kv", "Order: " + (r.order_id || "none")));
  return li;
}

function renderTokenState() {
  const has = ownerToken.length > 0;
  $("btn-stop").disabled = !has;
  $("btn-release").disabled = !has;
  $("token-note").textContent = has
    ? "Owner controls enabled."
    : "Owner controls are disabled: enter the owner token to enable Stop, Release, Approve and Reject.";
  for (const b of document.querySelectorAll("button[data-owner]")) b.disabled = !has;
}

function renderPending(records) {
  const latest = new Map();
  for (const r of records) if (r.order_id) latest.set(r.order_id, r);
  const ul = $("pending");
  ul.replaceChildren();
  const pending = [...latest.values()].filter((r) => r.outcome === "pending_approval");
  if (!pending.length) {
    ul.append(el("li", "no_action", "No orders are waiting for approval."));
    return;
  }
  for (const r of pending) {
    const li = decisionCard(r);
    const row = el("div", "row");
    for (const [label, key] of [["Approve", "approve"], ["Reject", "reject"]]) {
      const b = el("button", "", label);
      b.type = "button";
      b.dataset.owner = "1";
      b.disabled = ownerToken.length === 0;
      b.setAttribute("aria-label", label + " order " + r.order_id);
      b.addEventListener("click", () => ownerAction(PATHS[key].replace("{order_id}", r.order_id)));
      row.append(b);
    }
    li.append(row);
    ul.append(li);
  }
}

async function ownerAction(path) {
  $("owner-msg").textContent = "Sending…";
  try {
    const res = await call("POST", path, true);
    $("owner-msg").textContent = res.status === 200 ? "Done." : problemText(res);
  } catch (e) {
    $("owner-msg").textContent = "Network error: " + e.message;
  }
  await refresh();
}

async function refreshTier() {
  const res = await call("GET", PATHS.tier, false);
  const p = $("tier-state");
  if (res.status !== 200) {
    p.textContent = problemText(res);
    return;
  }
  const t = res.body;
  p.replaceChildren();
  p.append(el("span", "", "Tier " + t.tier + ". "));
  p.append(
    t.emergency_stop
      ? el("strong", "status-bad", "✕ Emergency stop is ENGAGED.")
      : el("strong", "status-ok", "✓ Emergency stop is released."),
  );
}

function renderRecords(records, verdict) {
  const ul = $("records");
  ul.replaceChildren();
  const byseq = new Map(verdict ? verdict.rows.map((x) => [x.seq, x]) : []);
  for (const r of records.slice(-10).reverse()) {
    const v = byseq.get(r.seq);
    const bad = v && !(v.linkOk && v.hashOk);
    const li = el("li", bad ? "failed" : r.outcome);
    let line = "seq " + r.seq + " · " + r.outcome;
    if (v) line += " · linkage " + (v.linkOk ? "ok" : "BROKEN") + " · hash " + (v.hashOk ? "ok" : "BROKEN");
    li.append(el("p", "", line));
    li.append(el("p", "hash", "hash " + r.hash));
    li.append(el("p", "hash", "prev " + r.prev_hash));
    ul.append(li);
  }
}

async function refresh() {
  try {
    await refreshTier();
    const records = await fetchAllRecords();
    renderPending(records);
    const ul = $("decisions");
    ul.replaceChildren();
    for (const r of records.slice(-50).reverse()) ul.append(decisionCard(r));
    if (!records.length) ul.append(el("li", "no_action", "No decisions yet."));
    renderRecords(records, null);
  } catch (e) {
    $("decisions").replaceChildren(el("li", "failed", "Could not load: " + e.message));
  }
}

async function checkChain() {
  const status = $("chain-status");
  status.className = "";
  status.textContent = "Checking…";
  try {
    if (!(window.crypto && crypto.subtle)) {
      status.className = "status-warn";
      status.textContent =
        "This browser context has no SHA-256 support (crypto.subtle needs localhost or HTTPS).";
      return;
    }
    const records = await fetchAllRecords();
    const verdict = await verifyChain(records);
    if (verdict.firstBad === null) {
      status.className = "status-ok";
      status.textContent = "✓ Linkage and hashes ok for all " + records.length + " records (browser check).";
    } else {
      const row = verdict.rows.find((x) => x.seq === verdict.firstBad);
      status.className = "status-bad";
      status.textContent =
        "✕ Chain broken at seq " + verdict.firstBad + (row && !row.linkOk ? " (linkage)" : " (hash)") + ".";
    }
    renderRecords(records, verdict);
  } catch (e) {
    status.className = "status-bad";
    status.textContent = "Could not check: " + e.message;
  }
}

$("token").addEventListener("input", (e) => {
  ownerToken = e.target.value.trim();
  renderTokenState();
});
$("btn-stop").addEventListener("click", () => ownerAction(PATHS.stop));
$("btn-release").addEventListener("click", () => ownerAction(PATHS.release));
$("btn-verify").addEventListener("click", checkChain);

renderTokenState();
refresh();
