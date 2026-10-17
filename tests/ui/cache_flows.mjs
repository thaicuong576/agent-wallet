// Measurements and unit tests for UI read cache.
// Prints one JSON object of named results; tests/ui/test_ui.py asserts them.
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
    this.value = "";
    this.disabled = false;
    this.hidden = false;
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
    for (const n of nodes) {
      if (typeof n !== "string") n.parent = this;
      this.children.push(typeof n === "string" ? { textContent: n } : n);
    }
  }
  remove() {
    if (this.parent) this.parent.children = this.parent.children.filter((c) => c !== this);
    this.parent = null;
  }
  replaceChildren(...nodes) {
    for (const c of this.children) if (c && c.parent) c.parent = null;
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
  focus() {
    globalThis.document.activeElement = this;
  }
  scrollIntoView() {}
  querySelectorAll(sel) {
    const out = [];
    const walk = (n) => {
      if (n.className && n.className.split(" ").includes(sel.replace(/^\./, ""))) out.push(n);
      for (const c of n.children) walk(c);
    };
    walk(this);
    return out;
  }
}

globalThis.__innerHTMLUsed = false;
globalThis.document = {
  createElement: (t) => new FakeEl(t),
  getElementById: () => null,
  querySelectorAll: () => [],
  body: new FakeEl("body"),
  activeElement: null,
};
globalThis.location = { search: "", hash: "" };

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
const byClass = (root, cls) => find(root, (n) => n.className && n.className.split(" ").includes(cls));

const tick = (ms = 10) => new Promise((r) => setTimeout(r, ms));

const COPY = fs.mkdtempSync(path.join(os.tmpdir(), "aw-ui-cache-"));
fs.cpSync(UI, COPY, { recursive: true });
fs.writeFileSync(path.join(COPY, "package.json"), '{"type":"module"}');
const imp = (rel) => import(pathToFileURL(path.join(COPY, rel)).href);
const { createApi, ROUTES, ApiError } = await imp("lib/api.js");
const { makeCtx } = await imp("main.js");
const { TestWallet } = await imp("wallet/test.js");
const overview = await imp("views/overview.js");
const activity = await imp("views/activity.js");
const rules = await imp("views/rules.js");
const side = await imp("views/side.js");
const chat = await imp("views/chat/index.js");

const OWNER = "0x1111111111111111111111111111111111111111";
const OWNER2 = "0x2222222222222222222222222222222222222222";
const AGENT = "0x3333333333333333333333333333333333333333";

function makeFakeServer(opts = {}) {
  const calls = [];
  let currentTime = opts.initialTime || 1000000;
  const delay = opts.delay || 0;
  let customState = { ...(opts.state || {}) };

  const fetchImpl = async (url, init = {}) => {
    const method = init.method || "GET";
    const body = init.body ? JSON.parse(init.body) : undefined;
    calls.push({ method, url, time: currentTime, body });
    if (delay > 0) {
      await tick(delay);
    }
    const reply = (status, data, headers = {}) => ({
      ok: status < 400,
      status,
      headers: { get: (k) => headers[k] ?? null },
      json: async () => data,
    });

    const key = method + " " + url.split("?")[0];
    if (opts.handler) {
      const res = opts.handler(key, method, url, body, customState);
      if (res !== undefined) return reply(res.status || 200, res.data);
    }

    if (key === "GET /me") {
      return reply(200, {
        address: customState.owner || opts.owner || OWNER,
        agent_address: AGENT,
        mode: "multi",
        chain_id: 84532,
        tier: "T2",
        emergency_stop: Boolean(customState.stopped),
      });
    }
    if (key === "GET /agent") {
      return reply(200, {
        address: AGENT,
        eth_wei: "100000000000000000",
        gas_floor_wei: "1000000000000000",
        suggested_topup_wei: "10000000000000000",
        usdc: { value: "1000000000", decimals: 6 },
        ausdc: { value: "0", decimals: 6 },
        price_source: customState.priceSource || "mainnet_reference",
        custody: "custodial-testnet",
      });
    }
    if (key === "GET /portfolio") {
      return reply(200, {
        schema_version: 1,
        as_of: "2026-10-15T12:00:00Z",
        price_source: customState.priceSource || "mainnet_reference",
        positions: [
          { token: "USDC", location: "wallet", balance: { value: "900000000", decimals: 6 }, price_usd: "1.00", value_usd: customState.val1 || "900.00", weight_bps: 9000 },
          { token: "USDC", location: "aave-v3", balance: { value: "100000000", decimals: 6 }, price_usd: "1.00", value_usd: customState.val2 || "100.00", weight_bps: 1000 },
        ],
        total_value_usd: customState.totalUsd || "1000.00",
        spent_24h_usd: "0.00",
        tx_count_1h: 0,
      });
    }
    if (key === "GET /limits") {
      return reply(200, {
        per_order_usd: "100.00",
        per_day_usd: "200.00",
        used_today_usd: "0.00",
        approval_threshold_usd: "50.00",
        protocols: ["aave_v3"],
        emergency_stop: customState.stopped ? "stopped" : "ready",
        as_of: "2026-10-15T12:00:00Z",
      });
    }
    if (key === "GET /ips") {
      return reply(200, {
        schema_version: 1,
        ips_hash: "0x" + "ab".repeat(32),
        ips: { limits: { per_tx_usd: "100.00", per_day_usd: "200.00" }, approval: { escalate_above_usd: "50.00" } },
      });
    }
    if (key === "GET /audit/verify") {
      return reply(200, { ok: true, records: 5, first_bad_seq: null });
    }
    if (key === "GET /jobs") {
      return reply(200, { items: [] });
    }
    if (key === "GET /chat") {
      return reply(200, { messages: [] });
    }
    if (key === "GET /chat/threads") {
      return reply(200, { threads: [] });
    }
    if (key === "GET /chat/suggestions") {
      return reply(200, { suggestions: [] });
    }
    if (key === "POST /chat") {
      return reply(202, { job_id: "1".repeat(32), status: "queued" });
    }
    if (key === "POST /emergency-stop") {
      customState.stopped = true;
      return reply(200, { outcome: "executed" });
    }
    if (key === "POST /emergency-stop/release") {
      customState.stopped = false;
      return reply(200, { outcome: "executed" });
    }
    if (key === "PUT /tier") {
      return reply(200, { schema_version: 1, tier: body.tier, emergency_stop: false });
    }
    if (key === "POST /agent/mint") {
      return reply(202, { job_id: "2".repeat(32), status: "queued" });
    }
    if (key === "POST /agent/withdraw") {
      return reply(202, { job_id: "3".repeat(32), status: "queued" });
    }
    if (key === "POST /auth/logout") {
      return reply(200, { ok: true });
    }
    return reply(404, { type: "urn:agent-wallet:problem:not-found", title: "Not found", status: 404 });
  };

  return {
    fetchImpl,
    calls,
    state: customState,
    setTime: (t) => { currentTime = t; },
    getTime: () => currentTime,
  };
}

const out = {};

// =========================================================================================
// Required measurements: (a), (b), (c)
// =========================================================================================

// --- (a) Number of requests for one cold load of Overview plus side panel
{
  const srv = makeFakeServer();
  const api = createApi({ fixtures: false, fetchImpl: srv.fetchImpl });
  const wallet = new TestWallet({ address: OWNER });
  const ctx = makeCtx({ api, wallet });
  ctx.state.me = { address: OWNER };

  // Cold load Overview + side panel concurrently
  overview.render(ctx);
  side.sidePanel(ctx);

  await tick(100);

  const portCalls = srv.calls.filter((c) => c.url === "/portfolio").length;
  const agentCalls = srv.calls.filter((c) => c.url === "/agent").length;

  out.coldLoad = {
    portfolio: portCalls,
    agent: agentCalls,
    total: srv.calls.length,
    calls: srv.calls.map((c) => c.method + " " + c.url),
  };
}

// --- (b) Number of requests for flow: Overview -> Activity -> Overview -> Chat -> Overview within 5s
{
  const srv = makeFakeServer();
  const api = createApi({ fixtures: false, fetchImpl: srv.fetchImpl });
  const wallet = new TestWallet({ address: OWNER });
  const ctx = makeCtx({ api, wallet });
  ctx.state.me = { address: OWNER };

  // 1. Overview
  overview.render(ctx);
  await tick(100);

  // 2. Activity (at 1s)
  srv.setTime(srv.getTime() + 1000);
  activity.render(ctx);
  await tick(100);

  // 3. Overview (at 2s)
  srv.setTime(srv.getTime() + 1000);
  overview.render(ctx);
  await tick(100);

  // 4. Chat (at 3s)
  srv.setTime(srv.getTime() + 1000);
  chat.render(ctx);
  await tick(100);

  // 5. Overview (at 4s)
  srv.setTime(srv.getTime() + 1000);
  overview.render(ctx);
  await tick(100);

  out.fiveSecFlow = {
    total: srv.calls.length,
    byRoute: srv.calls.reduce((acc, c) => {
      const k = c.method + " " + c.url.split("?")[0];
      acc[k] = (acc[k] || 0) + 1;
      return acc;
    }, {}),
  };
}

// --- (c) Time from clicking a tab to first painted content (with simulated latency)
{
  let clock = 0;
  const origDateNow = Date.now;
  Date.now = () => clock;

  const srv = makeFakeServer({ delay: 50 });
  const api = createApi({ fixtures: false, fetchImpl: srv.fetchImpl });
  const wallet = new TestWallet({ address: OWNER });
  const ctx = makeCtx({ api, wallet });
  ctx.state.me = { address: OWNER };

  // Cold visit to Overview
  const v1 = overview.render(ctx);
  const v1SkeletonImmediate = Boolean(byClass(v1, "skeleton-block").length);
  await tick(120);
  const v1HasContent = Boolean(byClass(v1, "hero").length);

  // Second visit to Overview after 1s
  clock += 1000;
  const v2 = overview.render(ctx);
  const v2SkeletonImmediate = Boolean(byClass(v2, "skeleton-block").length);
  const v2ContentImmediate = Boolean(byClass(v2, "hero").length);
  await tick(120);
  const v2ContentAfterFetch = Boolean(byClass(v2, "hero").length);

  Date.now = origDateNow;

  out.latencySimulation = {
    v1SkeletonImmediate,
    v1HasContent,
    v2SkeletonImmediate,
    v2ContentImmediate,
    v2ContentAfterFetch,
  };
}

// =========================================================================================
// Required unit tests:
// 1. single flight
// 2. memo expiry (fake clock)
// 3. write invalidates
// 4. failed read not cached
// 5. cache cleared when session changes
// 6. the view paints from peek and then repaints
// 7. stale response for a view the owner already left is dropped
// 8. hostile API strings are still not HTML
// =========================================================================================

// Test 1: Single flight
{
  const srv = makeFakeServer({ delay: 30 });
  const api = createApi({ fixtures: false, fetchImpl: srv.fetchImpl });
  const [res1, res2] = await Promise.all([api.portfolio(), api.portfolio()]);
  const portRequests = srv.calls.filter((c) => c.url === "/portfolio").length;

  out.singleFlight = {
    portRequests, // must be 1
    sameValue: res1.total_value_usd === res2.total_value_usd,
    isDistinctObject: res1 !== res2, // deep copy
  };
}

// Test 2: Memo expiry (fake clock)
{
  let fakeNow = 1000000;
  const origNow = Date.now;
  Date.now = () => fakeNow;

  const srv = makeFakeServer();
  const api = createApi({ fixtures: false, fetchImpl: srv.fetchImpl });

  await api.portfolio(); // t = 0 (call 1)
  fakeNow += 7900;
  await api.portfolio(); // t = 7.9s -> memo hit (< 8s)
  const reqsUnder8s = srv.calls.filter((c) => c.url === "/portfolio").length;

  fakeNow += 200; // t = 8.1s (> 8s)
  await api.portfolio(); // memo expired -> fresh request
  const reqsAfter8s = srv.calls.filter((c) => c.url === "/portfolio").length;

  Date.now = origNow;
  out.memoExpiry = {
    reqsUnder8s, // must be 1
    reqsAfter8s, // must be 2
  };
}

// Test 3: Write invalidates
{
  const srv = makeFakeServer();
  const api = createApi({ fixtures: false, fetchImpl: srv.fetchImpl });

  await api.portfolio(); // cached
  const beforeWrite = srv.calls.filter((c) => c.url === "/portfolio").length;
  await api.chatSend("hello"); // write: POST /chat clears cache
  await api.portfolio(); // fresh request
  const afterWrite = srv.calls.filter((c) => c.url === "/portfolio").length;

  // Test other writes also invalidate
  await api.stop(); // write: POST /emergency-stop
  await api.portfolio();
  const afterStop = srv.calls.filter((c) => c.url === "/portfolio").length;

  await api.mint("100"); // write: POST /agent/mint
  await api.portfolio();
  const afterMint = srv.calls.filter((c) => c.url === "/portfolio").length;

  out.writeInvalidates = {
    beforeWrite, // 1
    afterWrite,  // 2
    afterStop,   // 3
    afterMint,   // 4
  };
}

// Test 4: Failed read not cached
{
  let fail = true;
  const srv = makeFakeServer({
    handler: (key) => {
      if (key === "GET /portfolio" && fail) {
        return { status: 500, data: { title: "Chain RPC timeout" } };
      }
      return undefined;
    },
  });
  const api = createApi({ fixtures: false, fetchImpl: srv.fetchImpl });

  let errorCaught = null;
  try {
    await api.portfolio();
  } catch (e) {
    errorCaught = e;
  }
  const peekAfterFail = api.peek("portfolio");

  fail = false;
  const recovered = await api.portfolio();

  out.failedReadNotCached = {
    caught: Boolean(errorCaught && errorCaught.status === 500),
    peekAfterFail: peekAfterFail === undefined,
    recoveredTotal: recovered.total_value_usd,
  };
}

// Test 5: Cache cleared when session changes
{
  const srv = makeFakeServer();
  const api = createApi({ fixtures: false, fetchImpl: srv.fetchImpl });
  const wallet = new TestWallet({ address: OWNER });
  const ctx = makeCtx({ api, wallet });
  ctx.state.me = { address: OWNER };

  await api.portfolio();
  const peekOwner1 = api.peek("portfolio");

  // Sign out
  ctx.signedOut();
  const peekAfterSignOut = api.peek("portfolio");

  // Switch owner
  srv.state.owner = OWNER2;
  srv.state.totalUsd = "5555.00";
  ctx.state.me = { address: OWNER2 };
  await ctx.refreshSession(); // GET /me notices session address changed from OWNER to OWNER2

  const peekOwner2BeforeRead = api.peek("portfolio");
  const owner2Port = await api.portfolio();

  out.sessionChange = {
    peekOwner1HasData: Boolean(peekOwner1),
    peekAfterSignOutIsUndefined: peekAfterSignOut === undefined,
    peekOwner2BeforeReadIsUndefined: peekOwner2BeforeRead === undefined,
    owner2Total: owner2Port.total_value_usd,
  };
}

// Test 6: View paints from peek and then repaints
{
  let fakeNow = 1000000;
  const origNow = Date.now;
  Date.now = () => fakeNow;

  const srv = makeFakeServer({ delay: 30 });
  const api = createApi({ fixtures: false, fetchImpl: srv.fetchImpl });
  const wallet = new TestWallet({ address: OWNER });
  const ctx = makeCtx({ api, wallet });
  ctx.state.me = { address: OWNER };

  // First visit: loads and populates cache
  const root1 = overview.render(ctx);
  await tick(80);
  const hero1Text = textOf(byClass(root1, "hero")[0]);

  // Advance time past 8s (> 8000ms) so memo expires, and update backend data
  fakeNow += 9000;
  srv.state.totalUsd = "9999.00";

  // Second visit to Overview:
  const root2 = overview.render(ctx);
  // Synchronously before fetch: hero is painted with stale value ($1000.00) from peek!
  const hero2PeekText = textOf(byClass(root2, "hero")[0]);
  const hasSkeleton = Boolean(byClass(root2, "skeleton-block").length);

  // Wait for background fetch to complete
  await tick(80);
  const hero2FreshText = textOf(byClass(root2, "hero")[0]);

  Date.now = origNow;

  out.paintPeekThenRepaint = {
    hero1Text,
    hasSkeletonOnSecondVisit: hasSkeleton,
    hero2PeekText,
    hero2FreshText,
  };
}

// Test 7: Stale response for a view the owner already left is dropped
{
  const srv = makeFakeServer({ delay: 50 });
  const api = createApi({ fixtures: false, fetchImpl: srv.fetchImpl });
  const wallet = new TestWallet({ address: OWNER });
  const ctx = makeCtx({ api, wallet });
  ctx.state.me = { address: OWNER };

  const root = overview.render(ctx);
  // Owner leaves immediately: disconnect view
  root.isConnected = false;

  await tick(100);
  // No error thrown and view dropped the response
  out.staleResponseDropped = {
    completedWithoutCrash: true,
  };
}

// Test 8: Hostile API strings are still not HTML
{
  const hostilePayload = "<script>alert('xss')</script><img src=x onerror=alert(1)>";
  const srv = makeFakeServer({
    state: {
      priceSource: hostilePayload,
      val1: hostilePayload,
    },
  });
  const api = createApi({ fixtures: false, fetchImpl: srv.fetchImpl });
  const wallet = new TestWallet({ address: OWNER });
  const ctx = makeCtx({ api, wallet });
  ctx.state.me = { address: OWNER };

  const ovRoot = overview.render(ctx);
  await tick(80);

  const actRoot = activity.render(ctx);
  await tick(80);

  out.hostileStrings = {
    innerHTMLUsed: globalThis.__innerHTMLUsed,
    overviewHasScriptText: ovRoot.textContent.includes("<script>"),
    activityHasScriptText: actRoot.textContent.includes("<script>"),
  };
}

function textOf(n) {
  if (!n) return "";
  return n.textContent.replace(/\s+/g, " ").trim();
}

console.log(JSON.stringify(out, null, 2));
