// Runs the real ui modules against a tiny fake DOM and fixture files. Prints one JSON object of
// named results; tests/ui/test_ui.py asserts them. No network, no browser.
import fs from "node:fs";
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

const imp = (rel) => import(pathToFileURL(path.join(UI, rel)).href);
const { createApi, ROUTES } = await imp("api.js");
const { makeCtx, describeError } = await imp("main.js");
const { TestWallet } = await imp("wallet/test.js");
const onboard = await imp("views/onboard.js");
const dashboard = await imp("views/dashboard.js");
const feed = await imp("views/feed.js");
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
  const presets = JSON.parse(fs.readFileSync(path.join(UI, "fixtures", "presets.json"), "utf8"));
  presets.items[0].title = HOSTILE;
  presets.items[0].summary = HOSTILE;
  const { ctx } = mk({ override: { decisions: rec, agent, presets } });
  const f = feed.render(ctx);
  const d = dashboard.render(ctx);
  await tick();
  await tick();
  const all = [f, d];
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
  };
}

// 2. the page works from fixtures
{
  const { ctx } = mk({ demo: false });
  const o = onboard.render(ctx);
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
  const d = dashboard.render(ctx);
  const f = feed.render(ctx);
  await tick();
  await tick();
  out.dashboard = d.textContent;
  out.feed = f.textContent;
  out.txLinks = find(f, (n) => n.tagName === "A").map((n) => n.attrs.href);
  // run hostile, then reload the feed
  const accepted = await ctx.api.run({ planner: "scripted_hostile" });
  const job = await ctx.pollJob(accepted.job_id);
  const f2 = feed.render(ctx);
  await tick();
  await tick();
  out.job = job.status;
  out.feedAfterHostile = f2.textContent;
}

// 3. wallet rejection is not a failure
{
  const { ctx } = mk({ demo: false, wallet: new TestWallet({ reject: true }) });
  const o = onboard.render(ctx);
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
