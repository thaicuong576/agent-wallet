// Orders waiting for the owner. Approve needs a fresh signature, reject does not (design 6.2).
// Shown as the "Waiting for your approval" card on Portfolio.
import { recordCard } from "./cards.js";
import { loadRecords } from "./jobs.js";
import { explainRecord, formatAmount } from "../explain.js";

// Orders decided in this tab. The audit log is append-only, so the list also drops any order
// that has a later, non-pending record.
const decided = new Set();

export function pendingOf(records) {
  const last = new Map();
  for (const r of records) if (r.order_id) last.set(r.order_id, r);
  return [...last.values()].filter(
    (r) =>
      r.outcome === "pending_approval" &&
      !decided.has(r.order_id) &&
      !(r.approval && r.approval.status && r.approval.status !== "pending"),
  );
}

// The parameters of a waiting order as a list: amount, protocol, and which rule holds it.
function facts(el, r) {
  const x = explainRecord(r);
  const o = r.order || {};
  const dl = el("dl", "facts");
  const add = (k, v) => {
    if (v === null || v === undefined || v === "") return;
    const d = el("div", "");
    d.append(el("dt", "", k + ":"), el("dd", "", v));
    dl.append(d);
  };
  const amt = formatAmount(o.amount, 6);
  add("Amount", amt ? amt + " " + (o.token || "") : null);
  add("Action", o.function);
  add("Protocol", o.protocol);
  add("Held by", x.rules.length ? x.rules.map((q) => q.code).join(", ") : null);
  return dl;
}

export function approvalsCard(ctx) {
  const { el, api } = ctx;
  const card = el("div", "card");
  card.append(el("h2", "", "Waiting for your approval"));
  const msg = el("p", "msg", "");
  msg.setAttribute("role", "status");
  const list = el("ul", "steps");
  list.setAttribute("aria-live", "polite");
  list.style.marginTop = "12px";
  card.append(msg, list);

  async function decide(orderId, buttons, kind) {
    for (const b of buttons) b.disabled = true;
    msg.className = "msg";
    msg.textContent = "Working…";
    try {
      if (kind === "approve") await api.approve(orderId, await ctx.stepUp("approve", { order_id: orderId }));
      else await api.reject(orderId);
      decided.add(orderId);
      msg.className = "msg ok";
      msg.textContent =
        kind === "approve" ? "Approved. The order was sent to the policy engine again." : "Rejected. Nothing was sent.";
      ctx.changed();
    } catch (e) {
      msg.className = "msg error";
      msg.textContent = ctx.describeError(e);
      for (const b of buttons) b.disabled = false;
    }
  }

  function nothing() {
    const p = el("li", "note", "Nothing waiting for you. Orders above your approval threshold appear here for you to approve or reject.");
    return p;
  }

  async function load() {
    list.replaceChildren(el("li", "note", "Loading…"));
    const me = await ctx.refreshSession();
    if (!me) {
      list.replaceChildren(el("li", "note", "Connect a wallet to see what is waiting."));
      return;
    }
    try {
      const pending = pendingOf(await loadRecords(ctx, 200));
      list.replaceChildren();
      if (!pending.length) {
        list.append(nothing());
        return;
      }
      for (const r of pending.reverse()) {
        const row = el("div", "step-actions");
        const approve = el("button", "primary", "Approve (needs signature)");
        const reject = el("button", "", "Reject");
        approve.type = "button";
        reject.type = "button";
        const both = [approve, reject];
        approve.addEventListener("click", () => decide(r.order_id, both, "approve"));
        reject.addEventListener("click", () => decide(r.order_id, both, "reject"));
        row.append(approve, reject);
        list.append(recordCard(ctx, r, [facts(el, r), row]));
      }
    } catch (e) {
      list.replaceChildren(el("li", "msg error", ctx.describeError(e)));
    }
  }

  ctx.onChange(() => {
    if (card.isConnected !== false) load();
  });
  load();
  return card;
}
