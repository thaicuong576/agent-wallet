// Dashboard: agent address, balances with the gas floor, allocation, today's spend, tier.
import { formatAmount, formatWei } from "../explain.js";
import { copyButton } from "./onboard.js";
import { actionsCard } from "./actions.js";
import { chainChip } from "./jobs.js";
import { activateCard, activatedCard, lowerControls } from "./activate.js";
import { cta, gasGauge, spendGauge } from "../gauges.js";

export function render(ctx) {
  const { el, api } = ctx;
  const root = el("section", "view");
  root.append(el("h1", "", "Dashboard"));
  const body = el("div");
  root.append(body);

  // Built once: a reload after a job must not wipe the job's result.
  const actions = actionsCard(ctx);

  async function load() {
    body.replaceChildren(el("p", "note", "Loading balances…"));
    const me = await ctx.refreshSession();
    if (!me) {
      const box = el("div", "empty");
      box.append(el("p", "", "Connect a wallet to see your agent."), cta(el, "Set up your agent", "#/onboard"));
      body.replaceChildren(box);
      return;
    }
    // In multi mode /portfolio and /ips answer for the signed-in owner. If they fail the
    // allocation falls back to the owner's own balances and the spend card says it is unavailable.
    const [a, p, i, t] = await Promise.allSettled([
      api.agent(),
      api.portfolio(),
      api.ips(),
      api.tier(),
    ]);
    body.replaceChildren();
    const chain = chainChip(ctx);
    const tier = t.status === "fulfilled" ? t.value : null;
    if (tier && !tier.emergency_stop && (tier.tier === "T0" || tier.tier === "T1")) {
      body.append(activateCard(ctx, tier.tier));
    } else if (tier && tier.tier === "T2" && ctx.state.justActivated) {
      body.append(activatedCard(ctx, tier.tier));
    }
    body.append(identity(a, t, chain.node), balances(a));
    if (p.status === "fulfilled") body.append(allocation(p));
    else if (api.fixtures) body.append(allocation(p));
    else body.append(walletSplit(a));
    body.append(spend(p, i));
    body.append(actions);
    chain.check();
  }

  function identity(a, t, chainNode) {
    const card = el("div", "card");
    card.append(el("h2", "", "Agent wallet"));
    if (a.status === "fulfilled") {
      const row = el("p", "addr");
      row.append(el("code", "", a.value.address), copyButton(ctx, a.value.address));
      card.append(row);
      card.append(el("p", "note", "Custody: " + a.value.custody + ". The server holds this testnet key."));
    } else {
      card.append(el("p", "status error", ctx.describeError(a.reason)));
    }
    const tier = el("p", "");
    if (t.status === "fulfilled") {
      tier.append(
        el("span", "chip", "Tier " + t.value.tier),
        ...(t.value.emergency_stop
          ? [el("span", "chip bad", "✕ Emergency stop on")]
          : t.value.tier === "T2"
            ? [el("span", "chip ok", "✓ Running inside policy")]
            : [el("span", "chip bad", "! " + (t.value.tier === "T0" ? "Read only: every order is blocked" : "Propose only: nothing runs"))]),
      );
    } else {
      tier.append(el("span", "status error", "Tier unavailable."));
    }
    card.append(tier, chainNode);
    if (t.status === "fulfilled" && !t.value.emergency_stop) {
      card.append(...lowerControls(ctx, t.value.tier));
    }
    return card;
  }

  function balances(a) {
    const card = el("div", "card");
    card.append(el("h2", "", "Balances"));
    if (a.status !== "fulfilled") {
      card.append(el("p", "status error", "Balances unavailable (RPC). Nothing is shown rather than zeros."));
      return card;
    }
    const v = a.value;
    const eth = BigInt(v.eth_wei);
    const empty = eth === 0n && v.usdc.value === "0" && v.ausdc.value === "0";
    if (empty) {
      const e = el("div", "empty");
      e.append(el("p", "", "No funds yet. Fund the agent."), cta(el, "Fund the agent", "#/onboard"));
      card.append(e);
    }
    const grid = el("dl", "stats");
    const add = (k, val, extra) => {
      const d = el("div", "stat");
      d.append(el("dt", "", k), el("dd", "", val));
      if (extra) d.append(extra);
      grid.append(d);
    };
    const gas = gasGauge(v.eth_wei, v.gas_floor_wei);
    const low = !gas.above;
    const gasBox = el("span", "gas");
    gasBox.append(
      el("span", "chip " + (low ? "bad" : "ok"), (low ? "! Below" : "✓ Above") + " gas floor " + formatWei(v.gas_floor_wei) + " ETH"),
    );
    if (low) gasBox.append(cta(el, "Fund the agent", "#/onboard", "secondary"));
    add("ETH (gas)", formatWei(v.eth_wei) + " ETH", gasBox);
    add("USDC", formatAmount(v.usdc) + " USDC");
    add("aUSDC (lent on Aave)", formatAmount(v.ausdc) + " aUSDC");
    card.append(grid);
    if (low) {
      card.append(
        el("p", "status error", "Gas is below the floor. The agent cannot top up its own ETH: send test ETH to the agent address."),
      );
    }
    const label = el("span", "chip neutral", "Reference price: " + String(v.price_source).replace(/_/g, " "));
    card.append(el("p", "note", "Prices are mainnet reference values, not testnet market prices. "), label);
    return card;
  }

  function walletSplit(a) {
    const card = el("div", "card");
    card.append(el("h2", "", "Allocation"));
    if (a.status !== "fulfilled") {
      card.append(el("p", "status error", "Allocation unavailable."));
      return card;
    }
    const wallet = BigInt(a.value.usdc.value);
    const lent = BigInt(a.value.ausdc.value);
    const total = wallet + lent;
    if (total === 0n) {
      card.append(el("p", "empty", "No positions yet. Mint test USDC, then run the agent."));
      return card;
    }
    const parts = [
      ["USDC in wallet", wallet],
      ["aUSDC lent on Aave", lent],
    ].filter(([, v]) => v > 0n);
    const bar = el("div", "alloc-bar");
    bar.setAttribute("role", "img");
    bar.setAttribute("aria-label", "Split between wallet USDC and lent aUSDC, shares add to 100 percent");
    const list = el("ul", "legend");
    parts.forEach(([name, v], idx) => {
      const pct = Number((v * 1000n) / total) / 10;
      const seg = el("span", "seg seg-" + idx);
      seg.style.width = pct + "%";
      bar.append(seg);
      list.append(el("li", "", name + ": " + pct.toFixed(1) + "%, " + formatAmount({ value: v.toString(), decimals: 6 }) + " USDC"));
    });
    card.append(bar, list, el("p", "note", "Split by token amount, built from this agent's balances because the portfolio could not be read."));
    return card;
  }

  function allocation(p) {
    const card = el("div", "card");
    card.append(el("h2", "", "Allocation"));
    if (p.status !== "fulfilled") {
      card.append(el("p", "status error", "Allocation unavailable."));
      return card;
    }
    const pos = p.value.positions.filter((x) => x.weight_bps > 0);
    if (!pos.length) {
      card.append(el("p", "empty", "No positions yet. Mint test USDC, then run the agent."));
      return card;
    }
    const bar = el("div", "alloc-bar");
    bar.setAttribute("role", "img");
    const list = el("ul", "legend");
    pos.forEach((x, idx) => {
      const pct = (x.weight_bps / 100).toFixed(1) + "%";
      const seg = el("span", "seg seg-" + (idx % 4));
      seg.style.width = x.weight_bps / 100 + "%";
      bar.append(seg);
      list.append(
        el("li", "", x.token + (x.location === "aave-v3" ? " (lent on Aave)" : " (in wallet)") + ": " + pct + ", about " + x.value_usd + " USD"),
      );
    });
    bar.setAttribute("aria-label", "Allocation by asset, shares add to 100 percent");
    card.append(bar, list, el("p", "note", "Total value " + p.value.total_value_usd + " USD at reference prices."));
    return card;
  }

  function spend(p, i) {
    const card = el("div", "card");
    card.append(el("h2", "", "Today's spend"));
    if (p.status !== "fulfilled" || i.status !== "fulfilled") {
      card.append(el("p", "status error", "Spend unavailable. Nothing is shown rather than a guess."));
      return card;
    }
    const lim = i.value.ips.limits;
    const g = spendGauge(p.value.spent_24h_usd, lim.per_day_usd);
    if (g.level === "unknown") {
      card.append(el("p", "status error", "Spend unavailable: the limit or the amount could not be read."));
      return card;
    }
    const bar = el("div", "spend-bar " + g.level);
    bar.setAttribute("role", "progressbar");
    bar.setAttribute("aria-valuemin", "0");
    bar.setAttribute("aria-valuemax", "100");
    bar.setAttribute("aria-valuenow", String(Math.round(g.pct)));
    bar.setAttribute("aria-label", "Spent in the last 24 hours against the daily limit");
    const fill = el("span", "spend-fill");
    fill.style.width = g.pct + "%";
    bar.append(fill);
    const word = g.level === "full" ? "Daily limit reached" : g.level === "warn" ? "Close to the daily limit" : "Inside the daily limit";
    card.append(
      bar,
      el("p", "", g.spent + " / " + g.limit + " USD spent in the last 24 hours (" + Math.round(g.pct) + "%). " + g.remaining + " USD left."),
      el("span", "chip " + (g.level === "ok" ? "ok" : "bad"), (g.level === "ok" ? "✓ " : "! ") + word),
      el(
        "p",
        "note",
        "Rolling 24-hour window, not a calendar day: each spend counts for 24 hours after it was made, then drops off. There is no fixed reset time.",
      ),
      el("p", "note", "Per transaction limit: " + lim.per_tx_usd + " USD. Orders above " + i.value.ips.approval.escalate_above_usd + " USD wait for your approval."),
    );
    return card;
  }

  const again = el("button", "secondary", "Refresh");
  again.type = "button";
  again.addEventListener("click", load);
  root.append(again);
  ctx.onChange(() => {
    if (root.isConnected !== false) load();
  });
  load();
  return root;
}
