// "Withdraw": one dialog that moves USDC or ETH from the agent back to the owner's wallet. The
// amount signed in stepUp and the amount sent to the API are the same object. Every string lands
// in textContent.
import { explainJob, formatUnits, formatWei, parseUnits } from "../explain.js";
import { shortAddr } from "../gauges.js";
import { recordCard } from "./cards.js";
import { openDialog } from "./dialog.js";
import { recordsOfJob } from "./jobs.js";
import { createProgress } from "./progress.js";

const DECIMALS = { USDC: 6, ETH: 18 };
const SETTLED = new Set(["succeeded", "denied", "failed", "interrupted"]);

// The idle amount of an asset in base units, as a BigInt. ETH keeps the gas floor. Anything
// unreadable counts as nothing idle.
function idleUnits(agent, symbol) {
  try {
    if (symbol === "USDC") {
      const v = BigInt(agent.usdc.value);
      return v > 0n ? v : 0n;
    }
    if (symbol === "ETH") {
      const spare = BigInt(agent.eth_wei) - BigInt(agent.gas_floor_wei);
      return spare > 0n ? spare : 0n;
    }
  } catch (_) {
    // Unreadable balances fall through to 0n.
  }
  return 0n;
}

// "98" for the Max link, "0" when nothing is idle.
export function maxWithdraw(agent, symbol) {
  const units = idleUnits(agent, symbol);
  if (units === 0n) return "0";
  const text = symbol === "ETH" ? formatWei(units.toString()) : formatUnits(units.toString(), 6);
  return text || "0";
}

// "98.00" -> 9800n. The API may send up to six decimals; cents keep the first two. Anything
// unreadable counts as 0n.
function centsOf(value) {
  const m = /^([0-9]{1,12})(?:\.([0-9]{1,6}))?$/.exec(String(value ?? "").trim());
  if (!m) return 0n;
  return BigInt(m[1]) * 100n + BigInt((m[2] || "").padEnd(2, "0").slice(0, 2));
}

// The note for USDC or ETH lent out or held in vaults, which a direct withdraw cannot reach.
export function lentNote(portfolio) {
  if (!portfolio || !Array.isArray(portfolio.positions)) return null;
  let cents = 0n;
  for (const p of portfolio.positions) {
    if (!p || p.location === "wallet") continue;
    cents += centsOf(p.value_usd);
  }
  if (cents === 0n) return null;
  const usd = (cents / 100n).toString() + "." + String(cents % 100n).padStart(2, "0");
  return {
    usd,
    text: "$" + usd + " is lent or in vaults and cannot be withdrawn directly. Ask the agent to pull it out first.",
  };
}

export function openWithdraw(ctx, { agent, portfolio }) {
  const { el } = ctx;
  const dlg = openDialog(ctx, { title: "Withdraw" });
  const me = ctx.state.me || {};
  let token = "USDC";

  // ---- from the agent, to the owner
  const fromValue = el("p", "flow-value", "");
  const fromCell = el("div", "flow-cell");
  fromCell.append(el("p", "flow-label", "From the agent"), fromValue, el("p", "flow-note", "Idle in the agent wallet"));
  const toCell = el("div", "flow-cell");
  toCell.append(
    el("p", "flow-label", "To your wallet"),
    el("p", "flow-value", shortAddr(me.address)),
    el("p", "flow-note", "Always your own address"),
  );
  const flow = el("div", "flow");
  flow.append(fromCell, el("div", "flow-arrow", "→"), toCell);

  // ---- asset switch
  const switchRow = el("div", "seg-switch");
  switchRow.setAttribute("role", "group");
  switchRow.setAttribute("aria-label", "Asset");
  const assetBtn = {};
  for (const sym of ["USDC", "ETH"]) {
    const b = el("button", "", sym);
    b.type = "button";
    b.setAttribute("aria-pressed", "false");
    b.addEventListener("click", () => {
      token = sym;
      refresh();
    });
    assetBtn[sym] = b;
    switchRow.append(b);
  }

  // ---- amount
  const input = el("input");
  input.id = "withdraw-amount";
  input.type = "text";
  input.inputMode = "decimal";
  input.value = "1";
  const amountLabel = el("label", "", "Amount");
  amountLabel.setAttribute("for", "withdraw-amount");
  const maxBtn = el("button", "link", "");
  maxBtn.type = "button";
  maxBtn.addEventListener("click", () => {
    input.value = maxWithdraw(agent, token);
    refresh();
  });
  const amountRow = el("div", "amount-row");
  amountRow.append(input, maxBtn);
  input.addEventListener("input", () => refresh());

  // ---- lent note, signature line, submit, status, progress and records
  const lent = lentNote(portfolio);
  let lentBox = null;
  if (lent) {
    lentBox = el("p", "note", lent.text);
    const openChat = el("button", "link", "Open Chat");
    openChat.type = "button";
    openChat.addEventListener("click", () => {
      dlg.close();
      ctx.go("#/chat");
    });
    lentBox.append(" ", openChat);
  }
  const sign = el("p", "note", "Withdrawing needs one signature from your wallet. It does not change your rules.");
  const go = el("button", "primary", "");
  go.type = "button";
  const status = el("p", "msg", "");
  status.setAttribute("role", "status");
  const slot = el("div", "");
  const list = el("ul", "steps");

  function refresh() {
    for (const s of ["USDC", "ETH"]) {
      assetBtn[s].setAttribute("aria-pressed", s === token ? "true" : "false");
    }
    fromValue.textContent = maxWithdraw(agent, token) + " " + token;
    maxBtn.textContent = "Max " + maxWithdraw(agent, token);
    go.textContent = "Withdraw " + (input.value.trim() || "0") + " " + token;
  }

  async function submit() {
    const sym = token;
    const decimals = DECIMALS[sym];
    const units = parseUnits(input.value, decimals);
    if (units === null || units === 0n) {
      status.className = "msg error";
      status.textContent = "Enter an amount such as 1 or 0.5.";
      return;
    }
    if (units > idleUnits(agent, sym)) {
      status.className = "msg error";
      status.textContent = "That is more than the agent holds idle.";
      return;
    }
    // One object for the signature and for the request: the server checks they match.
    const amount = { value: units.toString(), decimals };
    const shownAmount = formatUnits(amount.value, decimals) + " " + sym;
    go.disabled = true;
    list.replaceChildren();
    status.className = "msg";
    status.textContent = "Waiting for your wallet…";
    let progress = null;
    let done = false;
    try {
      const header = await ctx.stepUp("withdraw", { token: sym, amount });
      status.textContent = "Withdrawing " + shownAmount + "…";
      progress = createProgress(el, {});
      slot.replaceChildren(progress.node);
      const accepted = await ctx.api.withdraw(sym, amount, header);
      progress.setReceived();
      const job = await ctx.pollJob(accepted.job_id, (j) => {
        if (!SETTLED.has(j.status) && j.phase !== "done") progress.setPhase(j.phase);
      });
      if (job.status === "succeeded") {
        done = true; // the balances above are stale now, so a second click would be a guess
        status.className = "msg ok";
        status.textContent = shownAmount + " is back in your wallet.";
        try {
          const records = await recordsOfJob(ctx, job);
          list.replaceChildren(...records.map((r) => recordCard(ctx, r)));
        } catch (e) {
          status.className = "msg error";
          status.textContent += " " + ctx.describeError(e);
        }
      } else {
        status.className = "msg error";
        status.textContent = explainJob(job);
      }
      ctx.changed();
    } catch (e) {
      status.className = "msg error";
      status.textContent = ctx.describeError(e);
    } finally {
      if (progress) progress.destroy();
      slot.replaceChildren();
      go.disabled = done;
    }
  }
  go.addEventListener("click", () => submit());

  dlg.body.append(
    flow,
    switchRow,
    amountLabel,
    amountRow,
    ...(lentBox ? [lentBox] : []),
    sign,
    go,
    status,
    slot,
    list,
  );
  refresh();
  assetBtn[token].setAttribute("aria-pressed", "true");
  return dlg;
}
