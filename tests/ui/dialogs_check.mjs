// Funds and withdraw dialogs against a fake DOM and a fake ctx. Prints one JSON object of named
// results; tests/ui/test_dialogs.py asserts them. No network, no browser.
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");

// The views import each other by relative path, so the whole ui tree is copied to a temp folder
// with a module marker. Nothing is written inside the repo.
const COPY = fs.mkdtempSync(path.join(os.tmpdir(), "aw-ui-dialogs-"));
fs.cpSync(path.join(ROOT, "ui"), COPY, { recursive: true });
fs.writeFileSync(path.join(COPY, "package.json"), '{"type":"module"}');
const load = (rel) => import(pathToFileURL(path.join(COPY, rel)).href);
const { openFunds } = await load("views/funds.js");
const { maxWithdraw, lentNote, openWithdraw } = await load("views/withdraw.js");

const ME = "0x" + "1".repeat(40);
const AGENT_ADDR = "0x" + "2".repeat(40);
const TX = "0x" + "a".repeat(64);
const JOB = "b".repeat(32);
const FOOTER = "Base Sepolia test tokens only. Nothing here has real value.";
const agent = {
  address: AGENT_ADDR,
  eth_wei: "20000000000000000",
  gas_floor_wei: "5000000000000000",
  suggested_topup_wei: "100000000000000000",
  usdc: { value: "98000000" },
};
const portfolio = {
  positions: [
    { token: "USDC", location: "wallet", value_usd: "98.00" },
    { token: "USDC", location: "aave-v3", value_usd: "2.00" },
  ],
};

// A fake DOM: only what the dialog code uses. innerHTML throws on read and write.
class FakeEl {
  constructor(tag, cls, text) {
    this.tag = tag;
    this.className = cls || "";
    this.children = [];
    this.attrs = {};
    this.textContent = text === undefined || text === null ? "" : String(text);
    this.hidden = false;
    this.disabled = false;
    this.parentNode = null;
    this.handlers = {};
    this.id = "";
    this.value = "";
    this.type = "";
    this.inputMode = "";
  }
  get innerHTML() {
    throw new Error("innerHTML is not allowed");
  }
  set innerHTML(value) {
    throw new Error("innerHTML is not allowed: " + value);
  }
  append(...kids) {
    for (const k of kids) {
      const node = typeof k === "string" ? new FakeEl("#text", "", k) : k;
      node.parentNode = this;
      this.children.push(node);
    }
  }
  replaceChildren(...kids) {
    for (const c of this.children) c.parentNode = null;
    this.children = [];
    this.append(...kids);
  }
  remove() {
    const p = this.parentNode;
    if (p) p.children = p.children.filter((c) => c !== this);
    this.parentNode = null;
  }
  setAttribute(k, v) {
    this.attrs[k] = String(v);
  }
  getAttribute(k) {
    return k in this.attrs ? this.attrs[k] : null;
  }
  addEventListener(type, fn) {
    (this.handlers[type] ||= []).push(fn);
  }
  focus() {
    doc.activeElement = this;
  }
  querySelectorAll(sel) {
    const parts = sel.split(",").map((s) => s.trim());
    return walk(this).slice(1).filter((n) => parts.some((p) => matchSel(n, p)));
  }
}

function matchSel(node, part) {
  const m = /^([a-z0-9]+)(?:\[([a-z]+)\])?$/.exec(part);
  if (!m || node.tag !== m[1]) return false;
  return m[2] ? node.attrs[m[2]] !== undefined : true;
}
function walk(node) {
  return [node, ...node.children.flatMap(walk)];
}
const all = (root, pred) => walk(root).filter(pred);
const byId = (root, id) => all(root, (n) => n.id === id)[0];
const withClass = (root, cls) => all(root, (n) => n.className.split(" ").includes(cls));
const tabsOf = (root) => all(root, (n) => n.attrs.role === "tab");
const statusOf = (root) => all(root, (n) => n.attrs.role === "status")[0];
const buttonNamed = (root, text) => all(root, (n) => n.tag === "button" && n.textContent === text)[0];
const el = (tag, cls, text) => new FakeEl(tag, cls, text);

const doc = { body: el("body"), activeElement: null };
globalThis.document = doc;

async function click(node) {
  if (node.disabled) return;
  await Promise.all((node.handlers.click || []).map((f) => f({ target: node, preventDefault() {} })));
}
function inputEvent(node) {
  for (const f of node.handlers.input || []) f({ target: node });
}
function key(node, k, extra = {}) {
  for (const f of node.handlers.keydown || []) f({ key: k, preventDefault() {}, ...extra });
}

function makeCtx() {
  const calls = { ensure: 0, sent: [], registered: [], mint: [], withdraw: [], stepUp: [], changed: 0, go: [] };
  const ctx = {
    calls,
    nextJob: { status: "succeeded", records: [], kind: "withdraw" },
    el,
    state: { me: { address: ME } },
    wait: async () => {},
    describeError: (e) => (e && e.message) || "Something went wrong.",
    changed() {
      calls.changed += 1;
    },
    go(hash) {
      calls.go.push(hash);
    },
    wallet: {
      async ensureBaseSepolia() {
        calls.ensure += 1;
      },
      async sendTransaction(tx) {
        calls.sent.push(tx);
        return TX;
      },
    },
    api: {
      async mint(value) {
        calls.mint.push(value);
        return { job_id: JOB };
      },
      async withdraw(token, amount, header) {
        calls.withdraw.push([token, amount, header]);
        return { job_id: JOB };
      },
      async registerTopup(hash) {
        calls.registered.push(hash);
        return {};
      },
      async topupStatus() {
        return { status: "confirmed" };
      },
    },
    async stepUp(action, params) {
      calls.stepUp.push([action, params]);
      return "HDR";
    },
    async pollJob(id, cb) {
      cb({ ...ctx.nextJob, status: "running", phase: "sent" });
      return ctx.nextJob;
    },
  };
  return ctx;
}

const out = {};

// (1) maxWithdraw
out.maxUsdc = maxWithdraw({ usdc: { value: "98000000" } }, "USDC");
out.maxEth = maxWithdraw({ eth_wei: "20000000000000000", gas_floor_wei: "5000000000000000" }, "ETH");
out.maxGarbage = maxWithdraw({ usdc: { value: "x" } }, "USDC");
out.maxNull = maxWithdraw(null, "ETH");

// (2) lentNote
out.lent = lentNote(portfolio);
out.lentWalletOnly = lentNote({ positions: [{ token: "USDC", location: "wallet", value_usd: "98.00" }] });
out.lentNull = lentNote(null);

// (3) openFunds: title, tabs, default and explicit tab
{
  const ctx = makeCtx();
  doc.activeElement = el("button", "", "Add funds");
  const dlg = openFunds(ctx, { agent });
  out.fundsTitle = withClass(dlg.card, "modal-title")[0].textContent;
  out.fundsRole = dlg.card.attrs.role;
  out.fundsTabs = tabsOf(dlg.card).map((t) => t.textContent);
  out.fundsSelected = tabsOf(dlg.card).map((t) => t.attrs["aria-selected"]);
  out.fundsUsdcPanelHidden = byId(dlg.card, "funds-panel-usdc").hidden;
  dlg.close();
  const dlg2 = openFunds(ctx, { agent, tab: "usdc" });
  out.usdcSelected = tabsOf(dlg2.card).map((t) => t.attrs["aria-selected"]);
  dlg2.close();
}

// (4) gas tab: send, then an invalid amount
{
  const ctx = makeCtx();
  const dlg = openFunds(ctx, { agent });
  const panel = byId(dlg.card, "funds-panel-gas");
  const input = byId(panel, "funds-eth");
  out.gasDefault = input.value;
  input.value = "0.01";
  inputEvent(input);
  const send = withClass(panel, "primary")[0];
  out.sendLabel = send.textContent;
  await click(send);
  const tx = ctx.calls.sent[0] || {};
  out.gasTx = { to: tx.to, from: tx.from, valueWei: tx.valueWei };
  out.gasStatus = statusOf(panel).textContent;
  out.gasStatusClass = statusOf(panel).className;
  const link = withClass(panel, "dlg-link")[0].children[0];
  out.gasLinkHref = link ? link.attrs.href : null;
  out.gasChanged = ctx.calls.changed;
  out.gasEnabledAgain = !send.disabled;
  input.value = "abc";
  const before = ctx.calls.sent.length;
  await click(send);
  out.badAmount = statusOf(panel).textContent;
  out.badAmountCalls = ctx.calls.sent.length - before;
  dlg.close();
}

// (5) USDC tab: the mint call
{
  const ctx = makeCtx();
  ctx.nextJob = { status: "succeeded", records: [], kind: "mint" };
  const dlg = openFunds(ctx, { agent, tab: "usdc" });
  const panel = byId(dlg.card, "funds-panel-usdc");
  const mint = withClass(panel, "primary")[0];
  out.mintLabel = mint.textContent;
  out.mintDefault = byId(panel, "funds-usdc").value;
  await click(mint);
  out.mintCalls = ctx.calls.mint;
  out.mintStatus = statusOf(panel).textContent;
  out.mintFooter = all(panel, (n) => n.textContent === FOOTER).length;
  dlg.close();
}

// (6) withdraw: signature and API call share one amount object; over idle; Open Chat
{
  const ctx = makeCtx();
  const dlg = openWithdraw(ctx, { agent, portfolio: null });
  out.withdrawTitle = withClass(dlg.card, "modal-title")[0].textContent;
  const maxBtn = () => all(dlg.card, (n) => n.tag === "button" && n.textContent.startsWith("Max"))[0];
  out.withdrawMax = maxBtn().textContent;
  const amountIn = byId(dlg.card, "withdraw-amount");
  const go = withClass(dlg.card, "primary")[0];
  amountIn.value = "25";
  await click(go);
  out.stepUp = ctx.calls.stepUp[0];
  out.withdrawArgs = ctx.calls.withdraw[0];
  out.sameObject = ctx.calls.withdraw[0][1] === ctx.calls.stepUp[0][1].amount;
  out.withdrawStatus = statusOf(dlg.card).textContent;
  out.withdrawChanged = ctx.calls.changed;
  buttonNamed(dlg.card, "ETH").handlers.click[0]({ target: null });
  out.ethMax = maxBtn().textContent;
  dlg.close();

  const ctx2 = makeCtx();
  const dlg2 = openWithdraw(ctx2, { agent, portfolio: null });
  byId(dlg2.card, "withdraw-amount").value = "500";
  await click(withClass(dlg2.card, "primary")[0]);
  out.overIdle = statusOf(dlg2.card).textContent;
  out.overIdleCalls = ctx2.calls.stepUp.length + ctx2.calls.withdraw.length;
  dlg2.close();

  const ctx3 = makeCtx();
  const dlg3 = openWithdraw(ctx3, { agent, portfolio });
  out.lentShown = all(dlg3.card, (n) => n.className === "note" && n.textContent.startsWith("$2.00 is lent")).length;
  const scrim3 = dlg3.card.parentNode;
  await click(buttonNamed(dlg3.card, "Open Chat"));
  out.chatGo = ctx3.calls.go;
  out.chatClosed = !doc.body.children.includes(scrim3);
}

// (7) Escape closes and returns focus; Tab wraps inside the dialog; Shift+Tab wraps back.
{
  const ctx = makeCtx();
  const opener = el("button", "", "Add funds");
  doc.activeElement = opener;
  const dlg = openFunds(ctx, { agent });
  const scrim = dlg.card.parentNode;
  const label = (n) => n.id || n.textContent;
  out.focusStart = label(doc.activeElement);
  const seen = [];
  for (let i = 0; i < 5; i++) {
    key(scrim, "Tab");
    seen.push(label(doc.activeElement));
  }
  out.tabOrder = seen;
  key(scrim, "Tab", { shiftKey: true });
  out.shiftTab = label(doc.activeElement);
  key(scrim, "Escape");
  out.escapeRemoved = !doc.body.children.includes(scrim);
  out.focusReturned = doc.activeElement === opener;
  out.closedResolved = await Promise.race([
    dlg.closed.then(() => true),
    new Promise((resolve) => setTimeout(() => resolve(false), 50)),
  ]);
}

out.innerHTMLGuard = "set-throws";
fs.rmSync(COPY, { recursive: true, force: true });
process.stdout.write(JSON.stringify(out) + "\n");
