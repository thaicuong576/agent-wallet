// Dashboard: agent address, balances with the gas floor, allocation, today's spend, tier.
import { formatAmount, formatWei } from "../explain.js";
import { copyButton } from "./onboard.js";
import { actionsCard } from "./actions.js";
import { chainChip } from "./jobs.js";

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
      body.replaceChildren(el("p", "empty", "Connect a wallet to see your agent."));
      return;
    }
    // GET /portfolio and /ips are not per owner yet, so outside the demo only the owner's own
    // balances (GET /agent) feed the cards: nothing is shown rather than another owner's numbers.
    const none = Promise.reject(new Error("not per owner"));
    none.catch(() => {});
    const [a, p, i, t] = await Promise.allSettled([
      api.agent(),
      api.fixtures ? api.portfolio() : none,
      api.fixtures ? api.ips() : none,
      api.tier(),
    ]);
    body.replaceChildren();
    const chain = chainChip(ctx);
    body.append(identity(a, t, chain.node), balances(a));
    if (api.fixtures) body.append(allocation(p), spend(p, i));
    else body.append(walletSplit(a));
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
        el("span", "chip " + (t.value.emergency_stop ? "bad" : "ok"), t.value.emergency_stop ? "✕ Emergency stop on" : "✓ Running inside policy"),
      );
    } else {
      tier.append(el("span", "status error", "Tier unavailable."));
    }
    card.append(tier, chainNode);
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
    const floor = BigInt(v.gas_floor_wei);
    const empty = eth === 0n && v.usdc.value === "0" && v.ausdc.value === "0";
    if (empty) card.append(el("p", "empty", "No funds yet. Fund the agent."));
    const grid = el("dl", "stats");
    const add = (k, val, extra) => {
      const d = el("div", "stat");
      d.append(el("dt", "", k), el("dd", "", val));
      if (extra) d.append(extra);
      grid.append(d);
    };
    const low = eth < floor;
    add(
      "ETH (gas)",
      formatWei(v.eth_wei) + " ETH",
      el("span", "chip " + (low ? "bad" : "ok"), (low ? "! Below" : "✓ Above") + " gas floor " + formatWei(v.gas_floor_wei) + " ETH"),
    );
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
    card.append(bar, list, el("p", "note", "Split by token amount. Spend limits and prices arrive per owner in a later release."));
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
      card.append(el("p", "status error", "Spend unavailable."));
      return card;
    }
    const spent = parseFloat(p.value.spent_24h_usd);
    const lim = i.value.ips.limits;
    const day = parseFloat(lim.per_day_usd);
    const meter = el("progress");
    meter.max = day > 0 ? day : 1;
    meter.value = Math.min(spent, meter.max);
    meter.setAttribute("aria-label", "Spent in the last 24 hours against the daily limit");
    card.append(
      meter,
      el("p", "", p.value.spent_24h_usd + " USD of " + lim.per_day_usd + " USD daily limit (last 24 hours)."),
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
