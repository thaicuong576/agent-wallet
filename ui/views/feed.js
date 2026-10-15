// Feed: every decision in plain language. All API strings go through textContent.
import { explainRecord, formatAmount, shortHash } from "../explain.js";
import { chainChip, loadRecords } from "./jobs.js";
import { emptyState } from "../gauges.js";

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
    (order.protocol ? " on " + order.protocol : "") +
    (order.proposed_by ? " (proposed by " + order.proposed_by + ")" : "")
  );
}

// Provenance chips (A-6): who produced the plan, in words.
export const PLANNER_WORDS = {
  openai: "OpenAI Planner · live model",
  scripted: "Scripted Demo · fixed input, not the LLM",
  fallback: "Offline Fallback · rule-based, not the LLM",
};

// Every planner is named in words, so a reader always knows what produced the plan.
export function plannerLabel(job) {
  if (!job) return null;
  if (job.planner_client === "openai") {
    return "Latest run: OpenAI model planner (the policy engine still decides).";
  }
  if (job.planner_client === "scripted") {
    return "Latest run: scripted planner (fixed demo input, not a live model).";
  }
  if (job.planner_client === "fallback") {
    return "Latest run: fallback planner (the live model was unavailable, so a built-in offline plan was used).";
  }
  return null;
}

// A BaseScan link for one transaction hash; null for anything that is not a hash.
export function txLink(el, hash, text) {
  if (typeof hash !== "string" || !TX_HASH.test(hash)) return null;
  const a = el("a", "", text || "View on BaseScan: " + shortHash(hash));
  a.setAttribute("href", EXPLORER_TX + hash);
  a.setAttribute("target", "_blank");
  a.setAttribute("rel", "noopener noreferrer");
  return a;
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
  // The plain sentence is the headline; rule codes are secondary chips below (A-2).
  const lead = x.tone !== "ok" && x.rules.length ? x.rules[0].sentence : x.headline;
  li.append(el("p", "headline", lead));
  if (lead !== x.headline) li.append(el("p", "note", x.headline));
  for (const r of x.rules.slice(lead === x.headline ? 0 : 1)) li.append(el("p", "sentence", r.sentence));
  const src = record.decision || record.no_action;
  if (src && src.reason) li.append(el("p", "note", "Engine note: " + src.reason));
  const line = orderLine(record.order);
  if (line) li.append(el("p", "note", line));
  if (record.approval && record.approval.status) {
    li.append(el("p", "note", "Approval: " + record.approval.status + " by " + record.approval.by));
  }
  const chips = el("p", "chips");
  for (const r of x.rules) chips.append(el("span", "chip neutral code", r.code));
  if (record.planner_client && PLANNER_WORDS[record.planner_client]) {
    chips.append(el("span", "chip neutral", PLANNER_WORDS[record.planner_client]));
  }
  if (x.rules.length || chips.children.length) li.append(chips);
  const link = txLink(el, x.txHash);
  if (link) {
    const p = el("p");
    p.append(link);
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
  const verdict = el("div", "verdict");
  verdict.setAttribute("role", "status");
  verdict.hidden = true;
  // A-8: a structured verdict, in words and an icon, not colour alone.
  function showVerdict(kind, title, detail) {
    verdict.hidden = false;
    verdict.className = "verdict " + kind;
    verdict.replaceChildren(el("strong", "", title));
    if (detail) verdict.append(el("p", "note", detail));
  }
  const browserCheck = el("button", "secondary", "Check in browser");
  browserCheck.type = "button";
  tools.append(refresh, verify, browserCheck);
  const planner = el("p", "chip neutral", "");
  planner.hidden = true;
  const list = el("ul", "cards");
  list.setAttribute("aria-live", "polite");
  const chain = chainChip(ctx);
  const price = el("p", "chip neutral", "");
  price.hidden = true;
  browserCheck.hidden = !api.fixtures; // /decisions is not per owner yet, so the check waits
  root.append(tools, chain.node, verdict, planner, price, list);

  async function load() {
    list.replaceChildren();
    for (let i = 0; i < 3; i++) list.append(el("li", "decision placeholder", "Loading…"));
    const me = await ctx.refreshSession();
    if (!me) {
      list.replaceChildren(
        emptyState(el, "Connect a wallet to see decisions.", null, { label: "Set up your agent", hash: "#/onboard" }),
      );
      return;
    }
    chain.check();
    try {
      const items = await loadRecords(ctx, 50);
      const jobs = await api.jobs().catch(() => ({ items: [] }));
      const latest = (jobs.items || []).find((j) => j.kind === "run");
      const label = plannerLabel(latest);
      planner.hidden = !label;
      planner.textContent = label || "";
      const agent = await api.agent().catch(() => null);
      price.hidden = !agent;
      price.textContent = agent
        ? "Reference price: " + String(agent.price_source).replace(/_/g, " ") + ". Not testnet market prices."
        : "";
      list.replaceChildren();
      if (!items.length) {
        list.append(
          emptyState(
            el,
            "No decisions yet.",
            "Every order the agent proposes, and what the policy engine decided, appears here with the reason in plain words.",
            { label: "Run the agent now", hash: "#/dashboard" },
          ),
        );
        return;
      }
      for (const r of items.slice().reverse()) {
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
    showVerdict("pending", "Checking the audit chain…", "");
    try {
      const v = await api.auditVerify();
      if (v.ok) {
        showVerdict(
          "ok",
          "✓ Hash chain intact (" + v.records + " records verified)",
          "Verified by the server: every record links to the one before it.",
        );
      } else {
        showVerdict(
          "bad",
          "✕ Hash chain broken at record " + v.first_bad_seq,
          "The record with seq " + v.first_bad_seq + " does not match the hash of the record before it.",
        );
      }
    } catch (e) {
      showVerdict("bad", "Could not check the chain", ctx.describeError(e));
    }
  });
  browserCheck.addEventListener("click", async () => {
    showVerdict("pending", "Checking in this browser…", "");
    try {
      const check = globalThis.verifyChain;
      if (typeof check !== "function" || !(globalThis.crypto && globalThis.crypto.subtle)) {
        throw new Error("This browser context cannot compute SHA-256 (needs localhost or HTTPS).");
      }
      const page = await api.decisions(200);
      if (page.next_cursor || (page.items.length && page.items[0].seq !== 0)) {
        showVerdict("warn", "The chain is longer than one page", "Use the server check.");
        return;
      }
      const res = await check(page.items);
      if (res.firstBad === null) {
        showVerdict("ok", "✓ Hash chain intact (" + page.items.length + " records verified)", "Linkage and hashes checked in this browser.");
      } else {
        showVerdict("bad", "✕ Hash chain broken at record " + res.firstBad, "Checked in this browser.");
      }
    } catch (e) {
      showVerdict("bad", "Could not check the chain", ctx.describeError(e));
    }
  });
  ctx.onChange(() => {
    if (root.isConnected !== false) load();
  });
  load();
  return root;
}
