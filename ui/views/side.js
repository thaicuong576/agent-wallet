// The right column of Chat: wallet value, today's limit, gas, audit log line. Each card reads its
// own routes and says "unavailable" rather than showing a guess.
import { formatAmount, formatWei } from "../lib/explain.js";
import { gasGauge, relTime, spendGauge } from "../lib/gauges.js";
import { topUpButton } from "./topup.js";

export function sidePanel(ctx) {
  const { el, api } = ctx;
  const node = el("div", "stack side");
  node.id = "chat-side";
  let agent = null;
  let walletTotal = "";

  const strip = el("button", "wallet-strip");
  strip.type = "button";
  strip.setAttribute("aria-controls", node.id);
  const wallet = el("div", "card");
  const limit = el("div", "card");
  const gas = el("div", "card stack");
  const audit = el("div", "card");
  audit.className = "card small-line";
  node.append(strip, wallet, limit, gas, audit);

  // Phone only: the strip is the whole column until it is opened as a sheet.
  let isOpen = false;
  function paintStrip() {
    strip.textContent = isOpen ? "Close ▴" : "Wallet" + walletTotal + " ▾";
    strip.setAttribute("aria-expanded", String(isOpen));
  }
  function setOpen(on) {
    isOpen = on;
    node.className = on ? "stack side open" : "stack side";
    paintStrip();
  }
  strip.addEventListener("click", () => setOpen(!isOpen));
  node.addEventListener("keydown", (e) => {
    if (e.key !== "Escape" || !isOpen) return;
    setOpen(false);
    strip.focus();
  });
  paintStrip();

  const top = topUpButton(ctx, { getAgent: () => agent, cls: "" });

  function paintWallet(a, p) {
    walletTotal = p && p.total_value_usd ? " · $" + p.total_value_usd : "";
    paintStrip();
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

  function peekSafe(name) {
    if (!api || typeof api.peek !== "function") return undefined;
    const v = api.peek(name);
    if (!v || typeof v.then === "function") return undefined;
    return v;
  }

  let sideGen = 0;
  let lastPaintSig = null;
  let cachedAgent = null;
  let cachedPort = null;
  let cachedIps = null;
  let cachedAudit = null;

  function paintAll(a, p, i, v, auditErr) {
    const sig = JSON.stringify({
      a,
      p,
      i,
      v,
      auditErr: auditErr || "",
    });
    if (sig === lastPaintSig) return;
    lastPaintSig = sig;
    if (a !== undefined) agent = a;
    paintWallet(agent, p);
    paintLimit(p, i);
    paintGas(agent);
    paintAudit(v, auditErr);
  }

  async function load() {
    const myGen = ++sideGen;
    const [a, p, i, v] = await Promise.allSettled([
      api.agent(),
      api.portfolio(),
      api.ips(),
      api.auditVerify(),
    ]);
    if (node.isConnected === false || myGen !== sideGen) return;

    if (a.status === "fulfilled") cachedAgent = a.value;
    if (p.status === "fulfilled") cachedPort = p.value;
    if (i.status === "fulfilled") cachedIps = i.value;
    if (v.status === "fulfilled") cachedAudit = v.value;

    const freshAuditErr = v.status === "rejected" ? ctx.describeError(v.reason) : "";
    paintAll(
      cachedAgent,
      cachedPort,
      cachedIps,
      cachedAudit,
      freshAuditErr,
    );
  }

  // Paint from peek immediately if available; otherwise skeletons first.
  const peekAgent = peekSafe("agent");
  const peekPort = peekSafe("portfolio");
  const peekIps = peekSafe("ips");
  const peekAudit = peekSafe("auditVerify");

  if (peekAgent !== undefined || peekPort !== undefined || peekIps !== undefined || peekAudit !== undefined) {
    cachedAgent = peekAgent ?? null;
    cachedPort = peekPort ?? null;
    cachedIps = peekIps ?? null;
    cachedAudit = peekAudit ?? null;
    paintAll(cachedAgent, cachedPort, cachedIps, cachedAudit, "");
  } else {
    for (const c of [wallet, limit, gas, audit]) c.append(el("p", "note", "Loading…"));
  }

  ctx.onChange(() => {
    if (node.isConnected !== false && ctx.state.me) load();
  });
  load();
  return { node, load };
}

// ---- history rail ------------------------------------------------------------------------
const THREAD_STATUS = { running: "Running", done: "Done", blocked: "Blocked", pending: "Waiting" };
const GROUPS = ["Today", "Yesterday", "Earlier"];

// Which group a thread falls in, by the owner's local calendar day.
export function threadGroup(iso, now = new Date()) {
  const t = new Date(String(iso || ""));
  if (Number.isNaN(t.getTime())) return "Earlier";
  const day = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const age = Math.round((day(now) - day(t)) / 86400000);
  return age <= 0 ? "Today" : age === 1 ? "Yesterday" : "Earlier";
}

// The left rail of Chat: "+ New chat" and the conversations grouped Today, Yesterday, Earlier.
// Under 900 px the stylesheet turns it into a drawer that `open()` and `close()` show and hide.
// `onSelect(id)` gets the thread id, or null for the implicit default conversation.
export function historyRail(ctx, { onSelect, onNew }) {
  const { el } = ctx;
  const node = el("nav", "rail");
  node.setAttribute("aria-label", "Conversations");
  node.id = "chat-history";
  const top = el("div", "rail-top");
  const add = el("button", "primary rail-new", "+ New chat");
  add.type = "button";
  const shut = el("button", "rail-close", "Close");
  shut.type = "button";
  top.append(add, shut);
  const list = el("div", "rail-list");
  node.append(top, list);
  const backdrop = el("div", "rail-backdrop");
  backdrop.hidden = true;

  let items = [];
  let active = null;
  let busy = false;
  let failed = false;

  function paint() {
    list.replaceChildren();
    if (failed && !items.length) {
      list.append(el("p", "note", "Conversations are not available right now."));
    }
    const rows = items.map((t) => ({
      id: t.id === "default" ? null : t.id,
      title: t.title,
      at: t.last_message_at,
      status: t.status,
    }));
    // The implicit default conversation is reachable again once another one is open.
    if (active !== null && !rows.some((r) => r.id === null)) {
      rows.push({ id: null, title: "Earlier chat", at: "", status: "" });
    }
    for (const group of GROUPS) {
      const inGroup = rows.filter((r) => (r.at ? threadGroup(r.at) : "Earlier") === group);
      if (!inGroup.length) continue;
      list.append(el("div", "rail-group", group));
      for (const r of inGroup) {
        const on = r.id === active;
        const row = el("button", "rail-row" + (on ? " active" : ""));
        row.type = "button";
        row.append(el("span", "rail-title", r.title || "New chat"));
        const word = Object.prototype.hasOwnProperty.call(THREAD_STATUS, r.status) ? THREAD_STATUS[r.status] : "";
        const when = relTime(r.at);
        const line = [word, when].filter(Boolean).join(" · ");
        if (line) row.append(el("span", "rail-meta " + (r.status === "blocked" ? "bad" : ""), line));
        if (on) row.setAttribute("aria-current", "true");
        if (busy && !on) row.disabled = true;
        row.addEventListener("click", () => {
          if (busy || on) return;
          close();
          onSelect(r.id);
        });
        list.append(row);
      }
    }
    add.disabled = busy;
  }

  function open() {
    node.className = "rail open";
    backdrop.hidden = false;
  }
  function close() {
    node.className = "rail";
    backdrop.hidden = true;
  }
  add.addEventListener("click", () => {
    if (busy) return;
    close();
    onNew();
  });
  shut.addEventListener("click", close);
  backdrop.addEventListener("click", close);

  paint();
  return {
    node,
    backdrop,
    open,
    close,
    isOpen: () => node.className === "rail open",
    setThreads(next, ok = true) {
      items = Array.isArray(next) ? next : [];
      failed = !ok;
      paint();
    },
    setActive(id) {
      active = id === "default" || !id ? null : id;
      paint();
    },
    setBusy(b) {
      busy = Boolean(b);
      paint();
    },
    title: (id) => {
      const clean = id === "default" || !id ? null : id;
      return (items.find((t) => (t.id === "default" ? null : t.id) === clean) || {}).title || "";
    },
  };
}
