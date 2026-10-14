// Feed: every decision in plain language. All API strings go through textContent.
import { explainRecord, formatAmount, shortHash } from "../explain.js";

// Navigation target for a transaction link, not an asset: the page never fetches it.
const EXPLORER_TX = "https://sepolia.basescan.org/tx/";
const TX_HASH = /^0x[0-9a-f]{64}$/;

function orderLine(order) {
  if (!order) return null;
  const amt = formatAmount(order.amount, 6);
  return (
    "Order: " +
    order.function +
    (amt ? " " + amt + " " + order.token : "") +
    " on " +
    order.protocol +
    " (proposed by " +
    order.proposed_by +
    ")"
  );
}

// Scripted and fallback planner output is labelled in words; a live planner needs no label.
export function plannerLabel(job) {
  if (!job) return null;
  if (job.planner_client === "scripted") {
    return "Latest run: scripted planner (fixed demo input, not a live model).";
  }
  if (job.planner_client === "fallback") {
    return "Latest run: fallback planner (the live model was unavailable, so a built-in offline plan was used).";
  }
  return null;
}

export function recordCard(ctx, record) {
  const { el } = ctx;
  const x = explainRecord(record);
  const li = el("li", "decision " + x.tone);
  const head = el("div", "decision-head");
  head.append(
    el("span", "badge " + x.tone, x.icon + " " + x.label),
    el("span", "time", record.created_at || ""),
  );
  li.append(head);
  li.append(el("p", "headline", x.headline));
  for (const r of x.rules) li.append(el("p", "sentence", r.sentence));
  const src = record.decision || record.no_action;
  if (src && src.reason) li.append(el("p", "note", "Engine note: " + src.reason));
  const line = orderLine(record.order);
  if (line) li.append(el("p", "note", line));
  if (record.approval && record.approval.status) {
    li.append(el("p", "note", "Approval: " + record.approval.status + " by " + record.approval.by));
  }
  const chips = el("p", "chips");
  for (const r of x.rules) chips.append(el("span", "chip neutral", r.code));
  if (x.rules.length) li.append(chips);
  if (x.txHash && TX_HASH.test(x.txHash)) {
    const a = el("a", "", "View on BaseScan: " + shortHash(x.txHash));
    a.setAttribute("href", EXPLORER_TX + x.txHash);
    a.setAttribute("target", "_blank");
    a.setAttribute("rel", "noopener noreferrer");
    const p = el("p");
    p.append(a);
    li.append(p);
  }
  return li;
}

export function render(ctx) {
  const { el, api } = ctx;
  const root = el("section", "view");
  root.append(el("h1", "", "Decision feed"));
  const tools = el("div", "row");
  const refresh = el("button", "secondary", "Refresh");
  refresh.type = "button";
  const verify = el("button", "secondary", "Verify audit chain");
  verify.type = "button";
  const verdict = el("p", "status", "");
  verdict.setAttribute("role", "status");
  const browserCheck = el("button", "secondary", "Check in browser");
  browserCheck.type = "button";
  tools.append(refresh, verify, browserCheck);
  const planner = el("p", "chip neutral", "");
  planner.hidden = true;
  const list = el("ul", "cards");
  list.setAttribute("aria-live", "polite");
  root.append(tools, verdict, planner, list);

  async function load() {
    list.replaceChildren();
    for (let i = 0; i < 3; i++) list.append(el("li", "decision placeholder", "Loading…"));
    const me = await ctx.refreshSession();
    if (!me) {
      list.replaceChildren(el("li", "empty", "Connect a wallet to see decisions."));
      return;
    }
    try {
      const page = await api.decisions(50);
      const jobs = await api.jobs().catch(() => ({ items: [] }));
      const latest = (jobs.items || []).find((j) => j.kind === "run");
      const label = plannerLabel(latest);
      planner.hidden = !label;
      planner.textContent = label || "";
      list.replaceChildren();
      if (!page.items.length) {
        list.append(el("li", "empty", "No decisions yet. Press Run."));
        return;
      }
      for (const r of page.items.slice().reverse()) {
        try {
          list.append(recordCard(ctx, r));
        } catch (_) {
          list.append(el("li", "decision fail", "This record could not be shown."));
        }
      }
    } catch (e) {
      list.replaceChildren(el("li", "status error", ctx.describeError(e)));
    }
  }

  refresh.addEventListener("click", load);
  verify.addEventListener("click", async () => {
    verdict.className = "status";
    verdict.textContent = "Checking…";
    try {
      const v = await api.auditVerify();
      if (v.ok) {
        verdict.className = "status ok";
        verdict.textContent = "✓ Audit chain verified by the server: " + v.records + " records.";
      } else {
        verdict.className = "status error";
        verdict.textContent = "✕ Audit chain broken at record " + v.first_bad_seq + ".";
      }
    } catch (e) {
      verdict.className = "status error";
      verdict.textContent = ctx.describeError(e);
    }
  });
  browserCheck.addEventListener("click", async () => {
    verdict.className = "status";
    verdict.textContent = "Checking…";
    try {
      const check = globalThis.verifyChain;
      if (typeof check !== "function" || !(globalThis.crypto && globalThis.crypto.subtle)) {
        throw new Error("This browser context cannot compute SHA-256 (needs localhost or HTTPS).");
      }
      const page = await api.decisions(200);
      if (page.next_cursor || (page.items.length && page.items[0].seq !== 0)) {
        verdict.className = "status warn";
        verdict.textContent = "The chain is longer than one page. Use the server check.";
        return;
      }
      const res = await check(page.items);
      verdict.className = res.firstBad === null ? "status ok" : "status error";
      verdict.textContent =
        res.firstBad === null
          ? "✓ Linkage and hashes ok for " + page.items.length + " records (browser check)."
          : "✕ Chain broken at record " + res.firstBad + " (browser check).";
    } catch (e) {
      verdict.className = "status error";
      verdict.textContent = ctx.describeError(e);
    }
  });
  ctx.onChange(() => {
    if (root.isConnected !== false) load();
  });
  load();
  return root;
}
