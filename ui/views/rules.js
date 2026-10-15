// Rules: a read-only summary of the owner's limits, from GET /ips (per owner in multi mode).
// Nothing here changes a limit: the policy engine checks every order against these values.
import { cta, emptyState, frozenCard } from "../gauges.js";
import { shortHash } from "../explain.js";

const usd = (v) => (typeof v === "string" || typeof v === "number" ? "$" + v : "not set");

export function render(ctx) {
  const { el, api } = ctx;
  const root = el("section", "stack");
  const head = el("div");
  head.append(
    el("h1", "", "Rules"),
    el(
      "p",
      "lead",
      "The limits your agent works inside. They are read-only here: the agent cannot change them, and the policy engine checks every order against them.",
    ),
  );
  const body = el("div", "stack");
  root.append(head, body);

  function rule(term, value, small) {
    const box = el("div", "rule");
    box.append(el("dt", "", term), el("dd", small ? "small" : "", value));
    return box;
  }

  async function load() {
    body.replaceChildren(el("div", "skeleton", "Loading your rules…"));
    const me = await ctx.refreshSession();
    if (!me) {
      body.replaceChildren(
        emptyState(el, "Connect a wallet to see your rules.", null, { label: "Set up your agent", hash: "#/setup" }, "div"),
      );
      return;
    }
    const [i, t] = await Promise.allSettled([api.ips(), api.tier()]);
    const parts = [];
    if (t.status === "fulfilled" && t.value.emergency_stop) parts.push(frozenCard(el));
    if (i.status !== "fulfilled") {
      parts.push(el("p", "msg error", ctx.describeError(i.reason) + " Nothing is shown rather than a guess."));
      body.replaceChildren(...parts);
      return;
    }
    const ips = i.value.ips || {};
    const lim = ips.limits || {};
    const list = el("dl", "rules-list");
    list.append(
      rule("Per transaction", usd(lim.per_tx_usd)),
      rule("Per day (rolling 24 hours)", usd(lim.per_day_usd)),
      rule("Approval threshold", ips.approval ? "Above " + usd(ips.approval.escalate_above_usd) : "not set"),
      rule("Transactions per hour", lim.max_tx_per_hour === undefined ? "not set" : String(lim.max_tx_per_hour)),
    );
    const u = ips.universe || {};
    const names = (v) => (Array.isArray(v) && v.length ? v.join(", ") : "none");
    list.append(
      rule("Allowed protocols", names(u.protocols), true),
      rule("Allowed tokens", names(u.tokens), true),
      rule("Allowed actions", names(u.functions), true),
    );
    parts.push(list);
    if (ips.objective) parts.push(el("p", "note", "Objective: " + ips.objective));
    const foot = el("p", "note");
    foot.append("Orders above the approval threshold wait for you on ", cta(el, "Portfolio", "#/portfolio", "link"));
    if (i.value.ips_hash) foot.append(" Policy hash " + shortHash("0x" + String(i.value.ips_hash).replace(/^0x/, "")) + ".");
    parts.push(foot);
    body.replaceChildren(...parts);
  }

  ctx.onChange(() => {
    if (root.isConnected !== false) load();
  });
  load();
  return root;
}
