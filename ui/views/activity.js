// Activity: every decision, newest first, in the same step-card vocabulary as Chat, with the
// verify-chain action and its result. All API strings go through textContent.
import { plannerLabel, recordCard } from "./cards.js";
import { chainChip, loadRecords } from "./jobs.js";
import { emptyState, frozenCard } from "../lib/gauges.js";
import { activitySkeleton } from "../lib/skeleton.js";

export function render(ctx) {
  const { el, api } = ctx;
  const root = el("section", "stack");
  const head = el("div");
  head.append(
    el("h1", "", "Activity"),
    el(
      "p",
      "lead",
      "Every order the agent proposed and what the policy engine decided, newest first. The audit log behind it is hash-chained.",
    ),
  );
  const tools = el("div", "row");
  const refresh = el("button", "", "Refresh");
  refresh.type = "button";
  const verify = el("button", "primary", "Verify audit chain");
  verify.type = "button";
  const browserCheck = el("button", "", "Check in browser");
  browserCheck.type = "button";
  browserCheck.hidden = !api.fixtures; // /decisions is not per owner yet, so the check waits
  const chain = chainChip(ctx);
  tools.append(verify, browserCheck, refresh, chain.node);
  const verdict = el("div", "verdict");
  verdict.setAttribute("role", "status");
  verdict.hidden = true;
  // A structured verdict, in words and an icon, not colour alone.
  function showVerdict(kind, title, detail) {
    verdict.hidden = false;
    verdict.className = "verdict " + kind;
    verdict.replaceChildren(el("strong", "", title));
    if (detail) verdict.append(el("p", "note", detail));
  }
  const frozen = el("div");
  const chips = el("div", "row");
  const planner = el("span", "chip", "");
  planner.hidden = true;
  const price = el("span", "chip", "");
  price.hidden = true;
  chips.append(planner, price);
  const list = el("ul", "steps");
  list.setAttribute("aria-live", "polite");
  root.append(head, frozen, tools, verdict, chips, list);

  function peekSafe(name) {
    if (!api || typeof api.peek !== "function") return undefined;
    const v = api.peek(name);
    if (!v || typeof v.then === "function") return undefined;
    return v;
  }

  let actGen = 0;

  async function load() {
    const myGen = ++actGen;
    list.replaceChildren(...activitySkeleton(el, 3));
    const peekTier = peekSafe("tier");
    if (peekTier && peekTier.emergency_stop) {
      frozen.replaceChildren(frozenCard(el));
    }
    const peekAgent = peekSafe("agent");
    if (peekAgent) {
      price.hidden = false;
      price.textContent =
        "Reference price: " + String(peekAgent.price_source).replace(/_/g, " ") + ". Not testnet market prices.";
    }

    const me = await ctx.refreshSession();
    if (myGen !== actGen || root.isConnected === false) return;
    if (!me) {
      list.replaceChildren(
        emptyState(el, "Connect a wallet to see decisions.", null, { label: "Set up your agent", hash: "#/setup" }),
      );
      return;
    }
    chain.check();
    api
      .tier()
      .then((t) => {
        if (myGen === actGen && root.isConnected !== false) {
          frozen.replaceChildren(...(t.emergency_stop ? [frozenCard(el)] : []));
        }
      })
      .catch(() => {
        if (myGen === actGen && root.isConnected !== false && !peekTier) {
          frozen.replaceChildren();
        }
      });
    try {
      const items = await loadRecords(ctx, 50);
      if (myGen !== actGen || root.isConnected === false) return;
      const jobs = await api.jobs().catch(() => ({ items: [] }));
      if (myGen !== actGen || root.isConnected === false) return;
      const latest = (jobs.items || []).find((j) => j.kind === "run");
      const label = plannerLabel(latest);
      planner.hidden = !label;
      planner.textContent = label || "";
      const agent = await api.agent().catch(() => null);
      if (myGen !== actGen || root.isConnected === false) return;
      const activeAgent = agent || peekAgent;
      price.hidden = !activeAgent;
      price.textContent = activeAgent
        ? "Reference price: " + String(activeAgent.price_source).replace(/_/g, " ") + ". Not testnet market prices."
        : "";
      list.replaceChildren();
      if (!items.length) {
        list.append(
          emptyState(
            el,
            "No decisions yet.",
            "Every order the agent proposes, and what the policy engine decided, appears here with the reason in plain words.",
            { label: "Ask the agent in Chat", hash: "#/chat" },
          ),
        );
        return;
      }
      for (const r of items.slice().reverse()) {
        try {
          list.append(recordCard(ctx, r));
        } catch (_) {
          list.append(el("li", "step-card fail", "This record could not be shown."));
        }
      }
    } catch (e) {
      if (myGen === actGen && root.isConnected !== false) {
        list.replaceChildren(el("li", "msg error", ctx.describeError(e)));
      }
    }
  }

  refresh.addEventListener("click", load);
  verify.addEventListener("click", async () => {
    const peekAudit = peekSafe("auditVerify");
    if (peekAudit) {
      if (peekAudit.ok) {
        showVerdict(
          "ok",
          "✓ Hash chain intact (" + peekAudit.records + " records verified)",
          "Verified by the server: every record links to the one before it.",
        );
      } else {
        showVerdict(
          "bad",
          "✕ Hash chain broken at record " + peekAudit.first_bad_seq,
          "The record with seq " + peekAudit.first_bad_seq + " does not match the hash of the record before it.",
        );
      }
    } else {
      showVerdict("warn", "Checking the audit chain…", "");
    }
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
      if (!peekAudit) {
        showVerdict("bad", "Could not check the chain", ctx.describeError(e));
      }
    }
  });
  browserCheck.addEventListener("click", async () => {
    showVerdict("warn", "Checking in this browser…", "");
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
