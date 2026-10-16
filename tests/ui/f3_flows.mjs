// F3 (ticket 131): Rules from /limits, stop confirm, skeletons, log out. Real ui modules against
// a fake DOM and a fake fetch. Prints one JSON object of named results; test_f3.py asserts them.
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const UI = path.join(ROOT, "ui");

class FakeEl {
  constructor(tag) {
    this.tagName = tag.toUpperCase();
    this.children = [];
    this.attrs = {};
    this.dataset = {};
    this.style = {};
    this.listeners = {};
    this.className = "";
    this._text = "";
    this.isConnected = true;
    this.parent = null;
  }
  set textContent(v) {
    this.children = [];
    this._text = String(v);
  }
  get textContent() {
    return this._text + this.children.map((c) => c.textContent).join("");
  }
  set innerHTML(_v) {
    throw new Error("innerHTML must not be used");
  }
  append(...nodes) {
    for (const n of nodes) {
      const node = typeof n === "string" ? { textContent: n } : n;
      if (node instanceof FakeEl) node.parent = this;
      this.children.push(node);
    }
  }
  replaceChildren(...nodes) {
    this.children = [];
    this._text = "";
    this.append(...nodes);
  }
  remove() {
    if (this.parent) this.parent.children = this.parent.children.filter((c) => c !== this);
    this.parent = null;
  }
  focus() {
    globalThis.document.activeElement = this;
  }
  setAttribute(k, v) {
    this.attrs[k] = String(v);
  }
  getAttribute(k) {
    return k in this.attrs ? this.attrs[k] : null;
  }
  removeAttribute(k) {
    delete this.attrs[k];
  }
  addEventListener(t, f) {
    (this.listeners[t] = this.listeners[t] || []).push(f);
  }
}
const body = new FakeEl("body");
globalThis.document = {
  createElement: (t) => new FakeEl(t),
  getElementById: () => null,
  querySelectorAll: () => [],
  body,
  activeElement: null,
};
globalThis.location = { search: "", hash: "#/portfolio" };

const walk = (n, fn) => {
  if (!(n instanceof FakeEl)) return;
  fn(n);
  n.children.forEach((c) => walk(c, fn));
};
const find = (root, pred) => {
  const out = [];
  walk(root, (n) => pred(n) && out.push(n));
  return out;
};
const button = (root, text) =>
  find(root, (n) => n.tagName === "BUTTON" && n.textContent.includes(text))[0];
const tick = (ms = 30) => new Promise((r) => setTimeout(r, ms));
async function fire(node, type, ev = {}) {
  for (const f of node.listeners[type] || []) await f({ target: node, ...ev });
  await tick();
}

const COPY = fs.mkdtempSync(path.join(os.tmpdir(), "aw-ui-f3-"));
fs.cpSync(UI, COPY, { recursive: true });
fs.writeFileSync(path.join(COPY, "package.json"), '{"type":"module"}');
const imp = (rel) => import(pathToFileURL(path.join(COPY, rel)).href);
const { createApi } = await imp("api.js");
const { makeCtx } = await imp("main.js");
const { TestWallet } = await imp("wallet/test.js");
const rules = await imp("views/rules.js");
const setup = await imp("views/setup.js");
const account = await imp("views/account.js");
const confirm = await imp("views/confirm.js");
const skeleton = await imp("skeleton.js");
const limits = await imp("limits.js");

const fixture = (name) => JSON.parse(fs.readFileSync(path.join(UI, "fixtures", name + ".json"), "utf8"));
const nameOf = (url) => String(url).replace(/^fixtures\//, "").replace(/\.json$/, "");
const fixtureFetch = (override = {}) => async (url) => {
  const name = nameOf(url);
  if (name in override) {
    const o = override[name];
    if (o && o.fail) return { ok: false, status: 500, json: async () => ({}) };
    return { ok: true, status: 200, json: async () => o };
  }
  return { ok: true, status: 200, json: async () => fixture(name) };
};
const mkCtx = (override) =>
  makeCtx({ api: createApi({ fixtures: true, demo: true, fetchImpl: fixtureFetch(override) }), wallet: new TestWallet() });
const out = {};

// 1. Rules rows from /limits (fixtures mode, signed in)
{
  const view = rules.render(mkCtx());
  await tick();
  out.rules = {
    text: view.textContent,
    rows: find(view, (n) => n.dataset && n.dataset.rule).map((n) => n.dataset.rule),
    chips: find(view, (n) => n.className.startsWith("chip ") && !n.className.includes("proto-chip")).map((n) => n.textContent),
  };
  out.usd = ["50.00", "1000.00", "0.50", "12.30", "abc", null].map((v) => limits.usd(v));
}

// 2. Rules: stopped state, an honest error line, and the real route
{
  const rows = limits.limitRows({ ...fixture("limits"), emergency_stop: "stopped", protocols: [] });
  out.rulesStopped = rows.map((r) => r.id + ":" + r.chip.text + ":" + r.chip.tone);
  const v2 = rules.render(mkCtx({ limits: { fail: true } }));
  await tick();
  out.rulesError = { text: v2.textContent, rows: find(v2, (n) => n.dataset && n.dataset.rule).length };

  const seen = [];
  const real = createApi({
    fetchImpl: async (url, init) => {
      seen.push([init.method, url, init.credentials, init.headers["X-AW-CSRF"]]);
      return { ok: true, status: 200, json: async () => fixture("limits") };
    },
  });
  await limits.getLimits(real);
  out.realLimits = seen;
}

// 3. Stop confirm: opens, traps Tab, Escape closes and focus returns to the trigger
{
  const ctx = mkCtx();
  const trigger = new FakeEl("button");
  async function open() {
    document.activeElement = trigger;
    const p = confirm.confirmDialog(ctx, {
      title: "Stop the agent now?",
      body: "It will not move any funds until you resume.",
      cancelLabel: "Keep running",
      confirmLabel: "Stop agent",
    });
    return { p, scrim: body.children[body.children.length - 1] };
  }
  let d = await open();
  const dialog = d.scrim.children[0];
  const trapped = [];
  out.stopDialog = {
    role: dialog.attrs.role,
    modal: dialog.attrs["aria-modal"],
    title: dialog.textContent.includes("Stop the agent now?"),
    openFocus: document.activeElement.textContent,
    buttons: find(dialog, (n) => n.tagName === "BUTTON").map((n) => n.className + ":" + n.textContent),
  };
  await fire(d.scrim, "keydown", { key: "Tab" });
  trapped.push(document.activeElement.textContent);
  await fire(d.scrim, "keydown", { key: "Tab" });
  trapped.push(document.activeElement.textContent);
  await fire(d.scrim, "keydown", { key: "Tab", shiftKey: true });
  trapped.push(document.activeElement.textContent);
  out.stopDialog.tabOrder = trapped;
  await fire(d.scrim, "keydown", { key: "Escape" });
  out.stopDialog.escape = {
    result: await d.p,
    removed: body.children.length === 0,
    focusBack: document.activeElement === trigger,
  };
  d = await open();
  await fire(button(d.scrim, "Stop agent"), "click");
  out.stopDialog.confirm = { result: await d.p, focusBack: document.activeElement === trigger };
  d = await open();
  await fire(button(d.scrim, "Keep running"), "click");
  out.stopDialog.keep = await d.p;
}

// 4. Skeletons keep the final layout and say what is loading
{
  const el = (t, c, x) => {
    const n = new FakeEl(t);
    if (c) n.className = c;
    if (x !== undefined) n.textContent = x;
    return n;
  };
  const p = skeleton.portfolioSkeleton(el);
  const a = skeleton.activitySkeleton(el, 3);
  out.skeleton = {
    portfolioBusy: p.attrs["aria-busy"],
    portfolioCards: find(p, (n) => n.className.startsWith("card")).length,
    holdRows: find(p, (n) => n.className === "hold-row").length,
    portfolioText: p.textContent,
    activityRows: a.filter((n) => n.className.includes("step-card")).length,
    activityText: a.map((n) => n.textContent).join("|"),
  };
}

// 5. Log out: connect screen again, and no protected fetch is retried
{
  const calls = [];
  let signedIn = true;
  const fetchImpl = async (url, init = {}) => {
    calls.push((init.method || "GET") + " " + url);
    const reply = (status, data) => ({ ok: status < 400, status, headers: { get: () => null }, json: async () => data });
    if (url === "/auth/logout") {
      signedIn = false;
      return reply(200, {});
    }
    if (!signedIn) return reply(401, { type: "urn:agent-wallet:problem:unauthenticated", title: "Sign in required" });
    if (url === "/me") return reply(200, { ...fixture("me"), tier: "T2", emergency_stop: false });
    if (url === "/agent") return reply(200, fixture("agent"));
    return reply(404, {});
  };
  let ctx = null;
  const api = createApi({ fetchImpl, onUnauthorized: () => ctx && ctx.signedOut() });
  ctx = makeCtx({ api, wallet: new TestWallet() });
  await ctx.refreshSession();
  out.logout = { before: Boolean(ctx.state.me) };
  const control = account.renderAccountControl(ctx);
  out.logout.label = control.textContent;
  let changed = 0;
  ctx.onChange(() => changed++);
  const mark = calls.length;
  await fire(control, "click");
  out.logout.after = Boolean(ctx.state.me);
  out.logout.changed = changed;
  out.logout.hash = globalThis.location.hash;
  const setupView = setup.render(ctx);
  await tick();
  out.logout.connectScreen = setupView.textContent.includes("Connect your wallet");
  out.logout.hasConnectButton = Boolean(button(setupView, "Connect wallet"));
  await ctx.refreshSession();
  out.logout.calls = calls.slice(mark);
  ctx.sessionStarted();
  signedIn = true;
  out.logout.again = Boolean(await ctx.refreshSession());
}

console.log(JSON.stringify(out));
