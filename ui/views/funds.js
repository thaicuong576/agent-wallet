// "Add funds": one dialog with two tabs. "Send ETH for gas" sends test ETH from the owner's wallet
// to the agent (sendGas). "Get test USDC" asks the faucet (api.mint) and follows the job. Reuses
// topup.js, jobs.js and dialog.js. Every string lands in textContent.
import { explainJob, formatWei, parseUnits } from "../lib/explain.js";
import { shortAddr } from "../lib/gauges.js";
import { txLink } from "./cards.js";
import { openDialog } from "./dialog.js";
import { jobPanel, runJob } from "./jobs.js";
import { sendGas } from "./topup.js";

const FOOTER = "Base Sepolia test tokens only. Nothing here has real value.";
const TABS = [
  ["gas", "Send ETH for gas"],
  ["usdc", "Get test USDC"],
];

function flowCell(el, label, value, note) {
  const cell = el("div", "flow-cell");
  cell.append(el("p", "flow-label", label), el("p", "flow-value", value));
  if (note) cell.append(el("p", "flow-note", note));
  return cell;
}

export function openFunds(ctx, { agent, tab = "gas" }) {
  const { el, api } = ctx;
  const dlg = openDialog(ctx, { title: "Add funds" });
  const me = ctx.state.me || {};

  // ---- tabs
  const tablist = el("div", "dlg-tabs");
  tablist.setAttribute("role", "tablist");
  const tabs = {};
  const panels = {};
  function select(key) {
    for (const [k] of TABS) {
      tabs[k].setAttribute("aria-selected", k === key ? "true" : "false");
      panels[k].hidden = k !== key;
    }
  }
  for (const [key, label] of TABS) {
    const b = el("button", "", label);
    b.type = "button";
    b.id = "funds-tab-" + key;
    b.setAttribute("role", "tab");
    b.setAttribute("aria-selected", "false");
    b.addEventListener("click", () => select(key));
    tabs[key] = b;
    tablist.append(b);
  }
  for (const [key] of TABS) {
    const p = el("div", "dlg-panel");
    p.id = "funds-panel-" + key;
    p.setAttribute("role", "tabpanel");
    p.setAttribute("aria-labelledby", "funds-tab-" + key);
    panels[key] = p;
  }
  const linkOf = (hash) => {
    const a = txLink(el, hash);
    return a ? [a] : [];
  };

  // ---- gas
  const gas = panels.gas;
  const ethIn = el("input");
  ethIn.id = "funds-eth";
  ethIn.type = "text";
  ethIn.inputMode = "decimal";
  ethIn.value = formatWei((agent || {}).suggested_topup_wei) || "0.0001";
  const ethLabel = el("label", "", "ETH to send");
  ethLabel.setAttribute("for", "funds-eth");
  const send = el("button", "primary", "");
  send.type = "button";
  const sendStatus = el("p", "msg", "");
  sendStatus.setAttribute("role", "status");
  const sendLink = el("p", "dlg-link", "");
  function paintSend() {
    send.textContent = "Send " + (ethIn.value.trim() || "0") + " ETH from my wallet";
  }
  ethIn.addEventListener("input", paintSend);
  paintSend();

  send.addEventListener("click", async () => {
    const shownAmount = ethIn.value.trim();
    const wei = parseUnits(shownAmount, 18);
    if (wei === null || wei === 0n) {
      sendStatus.className = "msg error";
      sendStatus.textContent = "Enter an amount such as 0.01.";
      return;
    }
    send.disabled = true;
    sendStatus.className = "msg";
    sendStatus.textContent = "Waiting for your wallet…";
    sendLink.replaceChildren();
    try {
      const hash = await sendGas(ctx, {
        agent,
        wei,
        onStatus: (text, h) => {
          sendStatus.textContent = text;
          sendLink.replaceChildren(...linkOf(h));
        },
      });
      sendStatus.className = "msg ok";
      sendStatus.textContent = "Added " + shownAmount + " ETH of gas.";
      sendLink.replaceChildren(...linkOf(hash));
      ctx.changed();
    } catch (e) {
      sendStatus.className = "msg error";
      sendStatus.textContent = ctx.describeError(e);
    }
    send.disabled = false;
  });

  const gasFlow = el("div", "flow");
  gasFlow.append(
    flowCell(el, "From your wallet", shortAddr(me.address)),
    el("div", "flow-arrow", "→"),
    flowCell(el, "To the agent", shortAddr((agent || {}).address)),
  );
  gas.append(
    el("p", "note", "The agent needs a little ETH for network fees. It cannot top up itself."),
    gasFlow,
    ethLabel,
    ethIn,
    send,
    sendStatus,
    sendLink,
    el("p", "note", FOOTER),
  );

  // ---- USDC
  const usdc = panels.usdc;
  const usdcIn = el("input");
  usdcIn.id = "funds-usdc";
  usdcIn.type = "text";
  usdcIn.inputMode = "numeric";
  usdcIn.value = "100";
  const usdcLabel = el("label", "", "Whole USDC to get");
  usdcLabel.setAttribute("for", "funds-usdc");
  const mint = el("button", "primary", "Get test USDC");
  mint.type = "button";
  const mintStatus = el("p", "msg", "");
  mintStatus.setAttribute("role", "status");
  const panel = jobPanel(ctx);

  mint.addEventListener("click", async () => {
    const value = usdcIn.value.trim();
    mint.disabled = true;
    mintStatus.className = "msg";
    mintStatus.textContent = "Working…";
    try {
      const job = await runJob(ctx, panel, "Starting the mint…", () => api.mint(value));
      if (job.status === "succeeded") {
        mintStatus.className = "msg ok";
        mintStatus.textContent = "Test USDC added.";
        ctx.changed();
      } else {
        mintStatus.className = "msg error";
        mintStatus.textContent = explainJob(job);
      }
    } catch (e) {
      mintStatus.className = "msg error";
      mintStatus.textContent = ctx.describeError(e);
      const active = e && e.problem && e.problem.job_id;
      if (e && e.status === 409 && typeof active === "string" && /^[0-9a-f]{32}$/.test(active)) {
        mintStatus.textContent += " Following the job that is already running.";
        try {
          await panel.finish(await ctx.pollJob(active, (j) => panel.paint(j)));
        } catch (_) {
          // The message above already explains the situation.
        }
      }
    }
    mint.disabled = false;
  });

  usdc.append(
    el("p", "note", "Free Base Sepolia test USDC from the faucet. No wallet prompt. The faucet allows one mint per hour."),
    usdcLabel,
    usdcIn,
    mint,
    mintStatus,
    panel.node,
    el("p", "note", FOOTER),
  );

  const start = TABS.some(([k]) => k === tab) ? tab : "gas";
  dlg.body.append(tablist, gas, usdc);
  select(start);
  return dlg;
}
