// Overview: the main column of the agent wallet. The hero shows the total, the split between working
// and idle money, the allocation bar and the Add funds and Withdraw buttons. Below it every holding is
// grouped by what the money is doing. The right column is added by a later slice.
import { allocationSegments, groupHoldings } from "../holdings.js";
import { displayName, protocolAvatar, tokenAvatar } from "../icons.js";
import { formatAmount, formatWei } from "../explain.js";
import { emptyState, frozenCard } from "../gauges.js";
import { openFunds } from "./funds.js";
import { openWithdraw } from "./withdraw.js";
import { topUpButton } from "./topup.js";

const NO_AGENT = "Balances are not available right now.";

export function render(ctx) {
  const { el, api } = ctx;
  const root = el("section", "overview");
  const main = el("div", "overview-main");
  root.append(main);

  async function load() {
    main.replaceChildren(overviewSkeleton(el));
    const me = await ctx.refreshSession();
    if (!me) {
      const box = el("div", "card");
      box.append(
        emptyState(el, "Connect a wallet to see your agent.", null, { label: "Set up your agent", hash: "#/setup" }, "div"),
      );
      main.replaceChildren(box);
      return;
    }
    const [a, p, t] = await Promise.allSettled([api.agent(), api.portfolio(), api.tier()]);
    const agent = a.status === "fulfilled" ? a.value : null;
    const tier = t.status === "fulfilled" ? t.value : null;
    const parts = [];
    if (tier && tier.emergency_stop) parts.push(frozenCard(el));
    if (p.status !== "fulfilled") {
      parts.push(failureCard(p.reason));
    } else {
      const model = groupHoldings(p.value, agent);
      parts.push(heroCard(model, agent, p.value));
      if (!model.empty) parts.push(holdingsCard(model, agent));
    }
    main.replaceChildren(...parts);
  }

  // No numbers at all when /portfolio failed: a zero would look like a real balance.
  function failureCard(reason) {
    const card = el("div", "card");
    card.append(
      el(
        "p",
        "msg error",
        "Balances could not be read from Base Sepolia. Nothing is shown rather than zeros. " + ctx.describeError(reason),
      ),
    );
    const retry = el("button", "", "Try again");
    retry.type = "button";
    retry.addEventListener("click", () => load());
    card.append(retry);
    return card;
  }

  function heroCard(model, agent, port) {
    const hero = el("div", "card hero");
    hero.append(el("p", "label", "Agent wallet value"), el("p", "big total", money(model.totalUsd)));
    hero.append(el("p", "", money(model.workingUsd) + " working for you · " + money(model.idleUsd) + " idle"));
    hero.append(el("p", "note", priceNote(model.priceSource)));
    if (model.empty) {
      hero.append(el("p", "", "Nothing here yet. Add funds, then ask the agent in Chat to put them to work."));
    } else {
      hero.append(...allocation(model));
    }
    hero.append(heroActions(model, agent, port));
    return hero;
  }

  function allocation(model) {
    const segs = allocationSegments(model);
    const bar = el("div", "alloc");
    bar.setAttribute("role", "img");
    bar.setAttribute(
      "aria-label",
      "Where the money is: " + segs.map((s) => s.label + " " + s.pct + " percent").join(", "),
    );
    for (const s of segs) {
      const seg = el("span", "seg seg-" + s.id);
      seg.style.flexGrow = String(Math.max(s.bps, 1));
      bar.append(seg);
    }
    const legend = el("ul", "legend");
    for (const s of segs) {
      const item = el("li", "");
      item.append(el("i", "swatch seg-" + s.id), s.label + " " + s.pct + "%");
      legend.append(item);
    }
    return [bar, legend];
  }

  function heroActions(model, agent, port) {
    const row = el("div", "hero-actions");
    const add = el("button", "primary", "Add funds");
    add.type = "button";
    add.addEventListener("click", () => openFunds(ctx, { agent }));
    const withdraw = el("button", "", "Withdraw");
    withdraw.type = "button";
    withdraw.addEventListener("click", () => openWithdraw(ctx, { agent, portfolio: port }));
    if (agent === null) {
      add.disabled = true;
      withdraw.disabled = true;
      add.setAttribute("title", NO_AGENT);
      withdraw.setAttribute("title", NO_AGENT);
    }
    if (model.empty) withdraw.disabled = true;
    row.append(add, withdraw);
    return row;
  }

  function holdingsCard(model, agent) {
    const card = el("div", "card holdings-card");
    card.append(el("h2", "", "What the agent holds"), el("p", "note", "Grouped by what the money is doing."));
    for (const g of model.groups) card.append(groupSection(g, model.gas, agent));
    return card;
  }

  function groupSection(g, gas, agent) {
    const sec = el("section", "group");
    const head = el("div", "ghead");
    if (g.id !== "idle") head.append(protocolAvatar(el, g.rows[0].protocol, { size: "sm" }));
    head.append(el("b", "", g.label));
    const chip = g.shareBps ? "$" + g.usd + " · " + Math.round(g.shareBps / 100) + "%" : "$" + g.usd;
    head.append(el("span", "chip", chip));
    head.append(el("span", "small ghead-note", g.note));
    sec.append(head);
    for (const row of g.rows) sec.append(holdingRow(g, row));
    if (g.id === "idle" && gas) sec.append(gasRow(gas, agent));
    return sec;
  }

  function holdingRow(g, row) {
    const line = el("div", "hrow");
    const name = el("div", "hname");
    name.append(tokenAvatar(el, row.display, { size: "md", protocol: g.id === "idle" ? null : row.protocol }));
    const text = el("div");
    text.append(el("b", "", displayName(row.display).replace(/ \(test token\)$/, "")));
    if (row.display === row.symbol) text.append(el("span", "small", row.symbol));
    const amount = formatAmount(row.amount) ?? "—";
    text.append(el("span", "small hrow-amount", amount));
    name.append(text);
    line.append(
      name,
      el("div", "num", amount),
      el("div", "num", row.priceKnown ? "$" + row.usd : "No price"),
      el("div", "num small", row.shareBps ? pct(row.shareBps) + "%" : "-"),
    );
    return line;
  }

  function gasRow(gas, agent) {
    const line = el("div", "hrow");
    const name = el("div", "hname");
    name.append(tokenAvatar(el, "ETH", { size: "md" }));
    const text = el("div");
    text.append(el("b", "", "ETH for fees"), el("span", "small", "Gas. Not counted in the value"));
    const amount = formatWei(gas.ethWei) ?? "—";
    text.append(el("span", "small hrow-amount", amount));
    if (!gas.ok) {
      const top = topUpButton(ctx, { label: "Top up gas", getAgent: () => agent });
      text.append(top.button, top.message);
    }
    name.append(text);
    const state = el("div", "num");
    state.append(gas.ok ? el("span", "chip ok", "● enough") : el("span", "chip bad", "! low"));
    line.append(name, el("div", "num", amount), state, el("div", "num small", "-"));
    return line;
  }

  ctx.onChange(() => {
    if (root.isConnected !== false) load();
  });
  load();
  return root;
}

function money(usd) {
  return usd === null || usd === undefined ? "—" : "$" + usd;
}

function priceNote(source) {
  const from = source ? " (" + source + ")" : "";
  return "Reference prices" + from + ", not testnet market prices.";
}

// Basis points as a percent with one decimal at most: 9800 -> "98", 20 -> "0.2".
function pct(bps) {
  return String(Math.round(bps / 10) / 10);
}

function block(el, cls) {
  const n = el("span", "sk " + cls);
  n.setAttribute("aria-hidden", "true");
  return n;
}

// Placeholder cards with the hero and holdings layout while the balances load.
function overviewSkeleton(el) {
  const wrap = el("div", "skeleton-block");
  wrap.setAttribute("aria-busy", "true");
  wrap.append(el("span", "sr-only", "Loading balances…"));
  const hero = el("div", "card");
  hero.append(block(el, "sk-label"), block(el, "sk-total"), block(el, "sk-line short"), block(el, "sk-bar"));
  const hold = el("div", "card");
  hold.append(block(el, "sk-label"), block(el, "sk-line"), block(el, "sk-line"), block(el, "sk-line short"));
  wrap.append(hero, hold);
  return wrap;
}
