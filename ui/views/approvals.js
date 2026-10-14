// Orders waiting for the owner. Approve needs a fresh signature, reject does not (design 6.2).
import { recordCard } from "./feed.js";
import { loadRecords } from "./jobs.js";

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
      list.replaceChildren(el("li", "empty", "Connect a wallet to see what is waiting."));
      return;
    }
    try {
      const pending = pendingOf(await loadRecords(ctx, 200));
      list.replaceChildren();
      if (!pending.length) {
        list.append(el("li", "empty", "Nothing waiting for you."));
        return;
      }
      for (const r of pending.reverse()) {
        const li = recordCard(ctx, r);
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
