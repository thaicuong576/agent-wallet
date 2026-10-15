// The right column of Chat: wallet value, today's limit, gas, audit log line. Each card reads its
// own routes and says "unavailable" rather than showing a guess.
import { formatAmount, formatWei } from "../explain.js";
import { gasGauge, spendGauge } from "../gauges.js";
import { topUpButton } from "./topup.js";

export function sidePanel(ctx) {
  const { el, api } = ctx;
  const node = el("div", "stack side");
  let agent = null;

  const wallet = el("div", "card");
  const limit = el("div", "card");
  const gas = el("div", "card stack");
  const audit = el("div", "card");
  audit.className = "card small-line";
  node.append(wallet, limit, gas, audit);

  const top = topUpButton(ctx, { getAgent: () => agent, cls: "" });

  function paintWallet(a, p) {
    wallet.replaceChildren(el("div", "label", "Agent wallet"));
    if (!a) {
      wallet.append(el("p", "note", "Balances are not available right now."));
      return;
    }
    const idle = BigInt(a.usdc.value);
    const earn = BigInt(a.ausdc.value);
    const total = idle + earn;
    wallet.append(
      el("div", "big", p ? "$" + p.total_value_usd : formatAmount(a.usdc) + " USDC"),
      el(
        "div",
        "small-line",
        formatAmount(a.usdc) + " USDC idle · " + formatAmount(a.ausdc) + " USDC earning in Aave",
      ),
    );
    const bar = el("div", "bar");
    bar.setAttribute("role", "img");
    bar.setAttribute("aria-label", "Share of USDC idle in the wallet and earning in Aave");
    if (total > 0n) {
      const idlePct = Number((idle * 1000n) / total) / 10;
      const a1 = el("span", "seg-idle");
      a1.style.width = idlePct + "%";
      const a2 = el("span", "seg-earn");
      a2.style.width = 100 - idlePct + "%";
      bar.append(a1, a2);
    }
    wallet.append(bar);
    if (p) wallet.append(el("div", "small-line", "Reference prices (mainnet), not testnet market prices."));
  }

  function paintLimit(p, i) {
    limit.replaceChildren(el("div", "label", "Today's limit"));
    const g = p && i ? spendGauge(p.spent_24h_usd, i.ips.limits.per_day_usd) : null;
    if (!g || g.level === "unknown") {
      limit.append(el("p", "note", "The limit is not available right now. Nothing is shown rather than a guess."));
      return;
    }
    const line = el("div", "gas-line");
    line.append(el("strong", "", "$" + g.spent), " of $" + g.limit + " used");
    const fill = el("span", "");
    fill.style.width = Math.max(g.pct, g.pct > 0 ? 1 : 0) + "%";
    const bar = el("div", "bar spend " + g.level);
    bar.setAttribute("role", "progressbar");
    bar.setAttribute("aria-valuemin", "0");
    bar.setAttribute("aria-valuemax", "100");
    bar.setAttribute("aria-valuenow", String(Math.round(g.pct)));
    bar.setAttribute("aria-label", "Spent in the last 24 hours against the daily limit");
    bar.append(fill);
    limit.append(
      line,
      bar,
      el(
        "div",
        "small-line",
        "Rolling 24 hours, no fixed reset time. Orders above $" +
          i.ips.approval.escalate_above_usd +
          " wait for your approval.",
      ),
    );
  }

  function paintGas(a) {
    gas.replaceChildren(el("div", "label", "Gas"));
    if (!a) {
      gas.append(el("p", "note", "Gas level is not available right now."));
      return;
    }
    const g = gasGauge(a.eth_wei, a.gas_floor_wei);
    const line = el("div", "gas-line");
    line.append(
      formatWei(a.eth_wei) + " ETH ",
      el("span", "chip " + (g.above ? "ok" : "bad"), g.above ? "● enough" : "! low"),
    );
    gas.append(line);
    if (!g.above) {
      gas.append(el("p", "note", "The agent cannot top up its own ETH. Send some from your wallet."));
    }
    top.button.className = g.above ? "" : "primary";
    gas.append(top.button, top.message);
  }

  function paintAudit(v, err) {
    audit.replaceChildren();
    audit.append(el("strong", "", "Audit log"));
    if (v && v.ok) {
      audit.append(" · " + v.records + " records · ", el("span", "chip ok", "chain verified ✓"));
    } else if (v) {
      audit.append(" · ", el("span", "chip bad", "chain broken at record " + v.first_bad_seq + " ✕"));
    } else {
      audit.append(" · unavailable (" + err + ")");
    }
  }

  async function load() {
    const [a, p, i, v] = await Promise.allSettled([
      api.agent(),
      api.portfolio(),
      api.ips(),
      api.auditVerify(),
    ]);
    agent = a.status === "fulfilled" ? a.value : null;
    const port = p.status === "fulfilled" ? p.value : null;
    paintWallet(agent, port);
    paintLimit(port, i.status === "fulfilled" ? i.value : null);
    paintGas(agent);
    paintAudit(v.status === "fulfilled" ? v.value : null, v.status === "rejected" ? ctx.describeError(v.reason) : "");
  }

  // Skeletons first, so the column never jumps empty.
  for (const c of [wallet, limit, gas, audit]) c.append(el("p", "note", "Loading…"));
  ctx.onChange(() => {
    if (node.isConnected !== false && ctx.state.me) load();
  });
  load();
  return { node, load };
}
