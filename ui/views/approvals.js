// Orders waiting for the owner. Approve needs a fresh signature, reject does not (design 6.2).
import { recordCard } from "./feed.js";
import { loadRecords } from "./jobs.js";
import { emptyState } from "../gauges.js";
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

// The parameters of a waiting order as a list: amount, protocol, and which rule holds it (A-7).
function facts(el, r) {
  const x = explainRecord(r);
  const o = r.order || {};
  const dl = el("dl", "facts");
  const add = (k, v) => {
    if (v === null || v === undefined || v === "") return;
    const d = el("div", "fact");
    d.append(el("dt", "", k), el("dd", "", v));
    dl.append(d);
  };
  const amt = formatAmount(o.amount, 6);
  add("Amount", amt ? amt + " " + (o.token || "") : null);
  add("Action", o.function);
  add("Protocol", o.protocol);
  add("Held by", x.rules.length ? x.rules.map((q) => q.code).join(", ") : null);
  return dl;
}

export function render(ctx) {
  const { el, api } = ctx;
  const root = el("section", "view");
  root.append(el("h1", "", "Waiting for you"));
  root.append(
    el(
      "p",
      "note",
      "Orders above your approval threshold wait here. Approving asks your wallet for a signature; rejecting does not.",
    ),
  );
  const msg = el("p", "status", "");
  msg.setAttribute("role", "status");
  const list = el("ul", "cards");
  list.setAttribute("aria-live", "polite");
  root.append(msg, list);

  async function decide(orderId, buttons, kind) {
    for (const b of buttons) b.disabled = true;
    msg.className = "status";
    msg.textContent = "Working…";
    try {
      if (kind === "approve") await api.approve(orderId, await ctx.stepUp("approve", { order_id: orderId }));
      else await api.reject(orderId);
      decided.add(orderId);
      msg.className = "status ok";
      msg.textContent = kind === "approve" ? "Approved. The order was sent to the policy engine again." : "Rejected. Nothing was sent.";
      ctx.changed();
    } catch (e) {
      msg.className = "status error";
      msg.textContent = ctx.describeError(e);
      for (const b of buttons) b.disabled = false;
    }
  }

  async function load() {
    list.replaceChildren(el("li", "decision placeholder", "Loading…"));
    const me = await ctx.refreshSession();
    if (!me) {
      list.replaceChildren(
        emptyState(el, "Connect a wallet to see what is waiting.", null, { label: "Set up your agent", hash: "#/onboard" }),
      );
      return;
    }
    try {
      const pending = pendingOf(await loadRecords(ctx, 200));
      list.replaceChildren();
      if (!pending.length) {
        list.append(
          emptyState(
            el,
            "Nothing waiting for you.",
            "Orders above your approval threshold stop here until you approve or reject them.",
            { label: "Run the agent", hash: "#/dashboard" },
          ),
        );
        return;
      }
      for (const r of pending.reverse()) {
        const li = recordCard(ctx, r);
        li.append(facts(el, r));
        const row = el("div", "row");
        const approve = el("button", "primary", "Approve (needs signature)");
        const reject = el("button", "secondary", "Reject");
        approve.type = "button";
        reject.type = "button";
        const both = [approve, reject];
        approve.addEventListener("click", () => decide(r.order_id, both, "approve"));
        reject.addEventListener("click", () => decide(r.order_id, both, "reject"));
        row.append(approve, reject);
        li.append(row);
        list.append(li);
      }
    } catch (e) {
      list.replaceChildren(el("li", "status error", ctx.describeError(e)));
    }
  }

  ctx.onChange(() => {
    if (root.isConnected !== false) load();
  });
  load();
  return root;
}
