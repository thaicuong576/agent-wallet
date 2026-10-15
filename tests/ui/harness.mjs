// Runs the real ui modules against a tiny fake DOM and fixture files. Prints one JSON object of
// named results; tests/ui/test_ui.py asserts them. No network, no browser.
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const UI = path.join(ROOT, "ui");

class TextNode {
  constructor(t) {
    this.text = t;
  }
  get textContent() {
    return this.text;
  }
}

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
  }
  set textContent(v) {
    this.children = [];
    this._text = String(v);
  }
  get textContent() {
    return this._text + this.children.map((c) => c.textContent).join("");
  }
  get lastChild() {
    return this.children[this.children.length - 1];
  }
  set innerHTML(_v) {
    globalThis.__innerHTMLUsed = true;
    throw new Error("innerHTML must not be used");
  }
  get innerHTML() {
    throw new Error("innerHTML must not be read");
  }
  append(...nodes) {
    for (const n of nodes) this.children.push(typeof n === "string" ? new TextNode(n) : n);
  }
  replaceChildren(...nodes) {
    this.children = [];
    this._text = "";
    this.append(...nodes);
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

globalThis.__innerHTMLUsed = false;
globalThis.document = {
  createElement: (t) => new FakeEl(t),
  getElementById: () => null,
  querySelectorAll: () => [],
};
globalThis.location = { search: "", hash: "" };

function walk(node, fn) {
  if (!(node instanceof FakeEl)) return;
  fn(node);
  for (const c of node.children) walk(c, fn);
}
const find = (root, pred) => {
  const out = [];
  walk(root, (n) => pred(n) && out.push(n));
  return out;
};
const byText = (root, tag, text) =>
  find(root, (n) => n.tagName === tag.toUpperCase() && n.textContent.includes(text));
const tick = () => new Promise((r) => setTimeout(r, 20));
async function click(node) {
  for (const f of node.listeners.click || []) await f({});
  await tick();
}

function fileFetch(override = {}) {
  return async (url) => {
    const name = String(url).replace(/^fixtures\//, "").replace(/\.json$/, "");
    let data;
    if (name in override) data = override[name];
    else data = JSON.parse(fs.readFileSync(path.join(UI, "fixtures", name + ".json"), "utf8"));
    return { ok: true, status: 200, json: async () => data };
  };
}

// ui/ has no package.json, so older node treats its .js files as CommonJS. Import from a copy
// that declares "type": "module" (the originals are untouched).
const COPY = fs.mkdtempSync(path.join(os.tmpdir(), "aw-ui-"));
fs.cpSync(UI, COPY, { recursive: true });
fs.writeFileSync(path.join(COPY, "package.json"), '{"type":"module"}');
const imp = (rel) => import(pathToFileURL(path.join(COPY, rel)).href);
const { createApi, ROUTES } = await imp("api.js");
const { makeCtx, describeError, routeFor } = await imp("main.js");
const { TestWallet } = await imp("wallet/test.js");
const setup = await imp("views/setup.js");
const portfolio = await imp("views/portfolio.js");
const activity = await imp("views/activity.js");
const rules = await imp("views/rules.js");
const cards = await imp("views/cards.js");
const side = await imp("views/side.js");
const gauges = await imp("gauges.js");
const explain = await imp("explain.js");

const out = {};
const HOSTILE = '<img src=x onerror=alert(1)><script>alert(2)</script>';

function mk({ demo = true, override = {}, wallet } = {}) {
  const api = createApi({ fixtures: true, demo, fetchImpl: fileFetch(override) });
  const ctx = makeCtx({ api, wallet: wallet || new TestWallet(), delayMs: 0 });
  return { api, ctx };
}

// 1. hostile strings in API fields never become markup
{
  const rec = JSON.parse(fs.readFileSync(path.join(UI, "fixtures", "decisions.json"), "utf8"));
  const r = rec.items[2];
  r.decision.reason = HOSTILE;
  r.decision.rules_hit[0].code = HOSTILE;
  r.decision.rules_hit[0].evidence = { order_usd: HOSTILE, per_tx_usd: HOSTILE };
  r.order.function = HOSTILE;
  r.order.token = HOSTILE;
  rec.items[0].execution.tx_hash = HOSTILE;
  const agent = JSON.parse(fs.readFileSync(path.join(UI, "fixtures", "agent.json"), "utf8"));
  agent.address = HOSTILE;
  agent.price_source = HOSTILE;
  const ips = JSON.parse(fs.readFileSync(path.join(UI, "fixtures", "ips.json"), "utf8"));
  ips.ips.universe.protocols = [HOSTILE];
  ips.ips.objective = HOSTILE;
  const { ctx } = mk({ override: { decisions: rec, agent, ips } });
  await ctx.refreshSession();
  const f = activity.render(ctx);
  const d = portfolio.render(ctx);
  const rulesView = rules.render(ctx);
  const s = side.sidePanel(ctx).node;
  const u = setup.render(ctx);
  await tick();
  await tick();
  const all = [f, d, rulesView, s, u];
  let anchors = 0;
  let injected = 0;
  for (const root of all) {
    walk(root, (n) => {
      if (["IMG", "SCRIPT", "IFRAME", "SVG"].includes(n.tagName)) injected++;
      if (n.tagName === "A") {
        anchors++;
        if ((n.attrs.href || "").includes("<")) injected++;
      }
    });
  }
  out.hostile = {
    innerHTMLUsed: globalThis.__innerHTMLUsed,
    injectedElements: injected,
    anchors,
    shownAsText: f.textContent.includes(HOSTILE) || d.textContent.includes(HOSTILE),
    rulesShownAsText: rulesView.textContent.includes(HOSTILE),
    setupShownAsText: u.textContent.includes(HOSTILE),
  };
}

// 2. the page works from fixtures
{
  const { ctx } = mk({ demo: false });
  const o = setup.render(ctx);
  await tick();
  out.signedOut = {
    text: o.textContent,
    hasConnectButton: byText(o, "button", "Connect wallet").length === 1,
  };
  await click(byText(o, "button", "Connect wallet")[0]);
  out.afterConnect = { me: Boolean(ctx.state.me), text: o.textContent };
}
{
  const { ctx } = mk({ demo: true });
  await ctx.refreshSession();
  const d = portfolio.render(ctx);
  const f = activity.render(ctx);
  const sp = side.sidePanel(ctx);
  const rl = rules.render(ctx);
  await tick();
  await tick();
  out.dashboard = d.textContent;
  out.side = sp.node.textContent;
  out.rules = rl.textContent;
  out.feed = f.textContent;
  out.txLinks = find(f, (n) => n.tagName === "A").map((n) => n.attrs.href);
  // run hostile, then reload the feed
  const accepted = await ctx.api.run({ planner: "scripted_hostile" });
  const job = await ctx.pollJob(accepted.job_id);
  const f2 = activity.render(ctx);
  await tick();
  await tick();
  out.job = job.status;
  out.feedAfterHostile = f2.textContent;
  out.feedBeforeAnyRun = f.textContent;
}

// planner labels: scripted and fallback are named in words, live or absent planners get none
out.plannerLabels = Object.fromEntries(
  ["scripted", "fallback", "openai", "none"].map((k) => [k, cards.plannerLabel({ planner_client: k })]),
);

// 3. wallet rejection is not a failure
{
  const { ctx } = mk({ demo: false, wallet: new TestWallet({ reject: true }) });
  const o = setup.render(ctx);
  await tick();
  await click(byText(o, "button", "Connect wallet")[0]);
  out.rejected = o.textContent;
  out.rejectedMessage = describeError({ code: 4001 });
}

// 4. explanations
{
  out.explainKeys = Object.keys(explain.RULES);
  out.explainSentencesNonEmpty = Object.values(explain.RULES).every((e) => e.text && e.text.length > 10);
  out.formatted = {
    amount: explain.formatAmount({ value: "98000000", decimals: 6 }),
    wei: explain.formatWei("20000000000000000"),
    parse: String(explain.parseUnits("0.01", 18)),
    parseBad: explain.parseUnits("1e3", 18),
  };
}

// 3b. step card kinds, rendered through the real card module
{
  const { ctx } = mk();
  const usdc = { value: "10000000", decimals: 6 };
  const base = { token: "USDC", amount: usdc, rule_codes: [], tx_hash: null };
  const mkStep = (o) => cards.stepCard(ctx, { ...base, function: "supply", ...o });
  const list = {
    done: mkStep({ outcome: "executed", explanation: "Supplied 10 USDC to Aave." }),
    blocked: mkStep({ outcome: "denied", rule_codes: ["ACCESS.RECIPIENT_NOT_ALLOWED"] }),
    pending: mkStep({ outcome: "pending_approval", rule_codes: ["TIER.APPROVAL_REQUIRED"] }),
    skipped: mkStep({ outcome: "skipped", explanation: "Not attempted: the previous step was blocked." }),
    fail: mkStep({ outcome: "failed" }),
  };
  out.stepKinds = Object.fromEntries(
    Object.entries(list).map(([k, n]) => [k, { cls: n.className, text: n.textContent }]),
  );
  out.stepKindList = cards.STEP_KINDS;
}

// 3c. routing: Setup until activated, then Chat; old hashes still land somewhere
{
  const r = (hash, signedIn, tier) => routeFor(hash, { signedIn, tier });
  out.routing = {
    signedOut: r("#/chat", false, null),
    t0: r("#/chat", true, "T0"),
    t0Portfolio: r("#/portfolio", true, "T0"),
    t2Default: r("", true, "T2"),
    t2Setup: r("#/setup", true, "T2"),
    t1Setup: r("#/setup", true, "T1"),
    t2Portfolio: r("#/portfolio", true, "T2"),
    alias: ["#/dashboard", "#/feed", "#/approvals", "#/onboard"].map((h) => r(h, true, "T2")),
    unknown: r("#/nope", true, "T2"),
    tierUnknown: r("#/rules", true, null),
  };
  const mk2 = (tag) => document.createElement(tag);
  const parts = {
    pill: mk2("span"),
    addrChip: mk2("span"),
    stopBtn: mk2("button"),
    stopMsg: mk2("p"),
    masthead: mk2("header"),
  };
  const me = { address: "0x" + "9d".repeat(20) };
  gauges.applyTopbar(parts, { tier: "T0", emergency_stop: false }, me);
  out.pills = { t0: parts.pill.textContent, addr: parts.addrChip.textContent };
  gauges.applyTopbar(parts, { tier: "T2", emergency_stop: false }, me);
  out.pills.t2 = parts.pill.textContent;
  gauges.applyTopbar(parts, { tier: "T2", emergency_stop: true }, me);
  out.pills.stopped = parts.pill.textContent;
}

// 5. API layer: only listed routes, errors become ApiError
{
  const seen = [];
  const api = createApi({
    fixtures: false,
    fetchImpl: async (url, init) => {
      seen.push([init.method, url, init.credentials, init.headers["X-AW-CSRF"]]);
      return { ok: false, status: 429, json: async () => ({ type: "urn:agent-wallet:problem:rate-limited", title: "Too many" }) };
    },
  });
  let err = null;
  try {
    await api.mint("5");
  } catch (e) {
    err = e;
  }
  out.apiError = { status: err && err.status, slug: err && err.slug, text: describeError(err), seen };
  out.routes = Object.values(ROUTES);
}

console.log(JSON.stringify(out));
