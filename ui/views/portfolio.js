// Portfolio: total value and split, holdings (USDC, aUSDC, ETH gas) with Withdraw and Top up,
// recent activity, the approvals card, and the mint, run and withdraw controls.
import { formatAmount, formatWei } from "../explain.js";
import { cta, emptyState, frozenCard, gasGauge } from "../gauges.js";
import { actionsCard } from "./actions.js";
import { approvalsCard } from "./approvals.js";
import { lowerControls } from "./activate.js";
import { loadRecords } from "./jobs.js";
import { recordLine } from "./cards.js";
import { topUpButton } from "./topup.js";

export function render(ctx) {
  const { el, api } = ctx;
  const root = el("section", "page-grid");
  root.append(el("h1", "sr-only", "Portfolio"));
  const body = el("div", "page-grid");
  root.append(body);

  // Built once: a reload after a job must not wipe the job's result or the approvals list.
  const actions = actionsCard(ctx);
  const approvals = approvalsCard(ctx);
  let agent = null;
  const top = topUpButton(ctx, { label: "Top up", getAgent: () => agent });

  async function load() {
    body.replaceChildren(el("div", "skeleton", "Loading balances…"));
    const me = await ctx.refreshSession();
    if (!me) {
      const box = el("div", "card");
      box.append(
        emptyState(el, "Connect a wallet to see your agent.", null, { label: "Set up your agent", hash: "#/setup" }, "div"),
      );
      body.replaceChildren(box);
      return;
    }
    // In multi mode /portfolio and /ips answer for the signed-in owner. If they fail the split
    // falls back to the owner's own balances and the USD columns say so.
    const [a, p, t, r] = await Promise.allSettled([
      api.agent(),
      api.portfolio(),
      api.tier(),
      loadRecords(ctx, 50),
    ]);
    agent = a.status === "fulfilled" ? a.value : null;
    const port = p.status === "fulfilled" ? p.value : null;
    const tier = t.status === "fulfilled" ? t.value : null;
    const parts = [];
    if (tier && tier.emergency_stop) parts.push(frozenCard(el));
    parts.push(el("div", "grid-2"));
    parts[parts.length - 1].append(totalCard(a, port), earningCard(a));
    parts.push(holdings(a, port));
    const two = el("div", "grid-eq");
    two.append(recent(r), approvals);
    parts.push(two, actions.node);
    if (tier && !tier.emergency_stop && (tier.tier === "T1" || tier.tier === "T2")) {
      const perm = el("div", "card");
      perm.append(
        el("h2", "", "Agent permission"),
        el(
          "p",
          "note",
          "The agent is " + (tier.tier === "T2" ? "active" : "propose only") + " (tier " + tier.tier + "). Lowering needs no signature.",
        ),
      );
      const row = lowerControls(ctx, tier.tier);
      if (tier.tier === "T1") row.push(cta(el, "Activate again", "#/setup", ""));
      perm.append(...row);
      parts.push(perm);
    }
    body.replaceChildren(...parts);
  }

  function valueOf(port, token, location) {
    if (!port) return null;
    const hit = port.positions.find((x) => x.token === token && x.location === location);
    return hit ? hit.value_usd : null;
  }

  function totalCard(a, port) {
    const card = el("div", "card");
    card.append(el("div", "label", "Total value"));
    if (a.status !== "fulfilled" && !port) {
      card.append(el("p", "msg error", ctx.describeError(a.reason)));
      return card;
    }
    let idle;
    let earn;
    let totalText;
    if (port) {
      const w = port.positions.filter((x) => x.location !== "aave-v3");
      const l = port.positions.filter((x) => x.location === "aave-v3");
      idle = w.reduce((s, x) => s + x.weight_bps, 0);
      earn = l.reduce((s, x) => s + x.weight_bps, 0);
      totalText = "$" + port.total_value_usd;
    } else {
      const wallet = BigInt(a.value.usdc.value);
      const lent = BigInt(a.value.ausdc.value);
      const sum = wallet + lent;
      idle = sum > 0n ? Number((wallet * 10000n) / sum) : 0;
      earn = sum > 0n ? 10000 - idle : 0;
      totalText = formatAmount(a.value.usdc) + " USDC + " + formatAmount(a.value.ausdc) + " aUSDC";
    }
    card.append(el("div", "total", totalText));
    if (idle + earn === 0) {
      card.append(
        el("p", "note", "No positions yet. Mint test USDC below, then ask the agent to put it to work."),
      );
    } else {
      const bar = el("div", "bar thick");
      bar.setAttribute("role", "img");
      bar.setAttribute("aria-label", "Allocation: idle USDC and USDC earning in Aave, shares add to 100 percent");
      const s1 = el("span", "seg-idle");
      s1.style.width = idle / 100 + "%";
      const s2 = el("span", "seg-earn");
      s2.style.width = earn / 100 + "%";
      bar.append(s1, s2);
      const legend = el("ul", "legend");
      legend.append(
        el("li", "", "● Idle USDC " + (idle / 100).toFixed(0) + "%"),
        el("li", "earn", "● Earning in Aave " + (earn / 100).toFixed(0) + "%"),
      );
      card.append(bar, legend);
    }
    const src = port ? port.price_source : a.status === "fulfilled" ? a.value.price_source : null;
    if (src) {
      card.append(
        el(
          "p",
          "note",
          "Reference price: " + String(src).replace(/_/g, " ") + ". Prices are mainnet reference values, not testnet market prices.",
        ),
      );
    }
    return card;
  }

  function earningCard(a) {
    const card = el("div", "card");
    card.append(el("div", "label", "Earning in Aave"));
    if (a.status !== "fulfilled") {
      card.append(el("p", "note", "Not available right now."));
      return card;
    }
    card.append(
      el("div", "big", formatAmount(a.value.ausdc) + " USDC"),
      el("p", "note", "Supplied to Aave v3 as aUSDC. The current rate is not shown here: the API does not expose it yet."),
    );
    return card;
  }

  function holdings(a, port) {
    const card = el("div", "card holdings");
    card.append(el("header", "", "Holdings"));
    if (a.status !== "fulfilled") {
      const r = el("div", "hold-row");
      r.append(el("span", "msg error", "Balances unavailable (RPC). Nothing is shown rather than zeros."));
      card.append(r);
      return card;
    }
    const v = a.value;
    const usd = (t, loc) => {
      const x = valueOf(port, t, loc);
      return x === null ? "reference value unavailable" : "$" + x;
    };
    const rows = [];
    const withdraw = el("button", "", "Withdraw");
    withdraw.type = "button";
    withdraw.addEventListener("click", () => actions.focusWithdraw());
    rows.push(["USDC (in wallet)", formatAmount(v.usdc), usd("USDC", "wallet"), withdraw]);
    rows.push(["aUSDC (earning in Aave)", formatAmount(v.ausdc), usd("USDC", "aave-v3"), null]);
    const g = gasGauge(v.eth_wei, v.gas_floor_wei);
    const cell = el("div", "stack");
    cell.append(top.button, top.message);
    rows.push([
      "ETH (gas)",
      formatWei(v.eth_wei),
      g.above ? "gas only" : "! below the gas floor of " + formatWei(v.gas_floor_wei) + " ETH",
      cell,
    ]);
    for (const [name, amt, value, ctl] of rows) {
      const r = el("div", "hold-row");
      const c = el("span", "");
      if (ctl) c.append(ctl);
      r.append(el("span", "name", name), el("span", "", amt), el("span", "usd", value), c);
      card.append(r);
    }
    return card;
  }

  function recent(r) {
    const card = el("div", "card");
    card.append(el("h2", "", "Recent activity"));
    const items = r.status === "fulfilled" ? r.value.slice(-3).reverse() : [];
    if (r.status !== "fulfilled") {
      card.append(el("p", "note", "Recent activity is not available right now."));
    } else if (!items.length) {
      card.append(el("p", "note", "Nothing yet. Ask the agent something in Chat."));
    } else {
      const list = el("ul", "recent");
      for (const rec of items) {
        const l = recordLine(rec);
        const li = el("li", "");
        li.append(el("span", l.tone === "blocked" || l.tone === "fail" ? "bad" : "", l.icon + " " + l.text), el("span", "when", l.when));
        list.append(li);
      }
      card.append(list);
    }
    card.append(cta(el, "See all activity", "#/activity", ""));
    return card;
  }

  ctx.onChange(() => {
    if (root.isConnected !== false) load();
  });
  load();
  return root;
}
