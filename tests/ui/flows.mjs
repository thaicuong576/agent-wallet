// Real-mode flows: the real ui modules against a fake `fetch` (server) and a TestWallet.
// Response bodies copy the shapes in api/wallet.py, api/jobs.py and api/auth.py.
// Prints one JSON object of named results; tests/ui/test_ui.py asserts them. No network.
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
    for (const n of nodes) this.children.push(typeof n === "string" ? { textContent: n } : n);
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
globalThis.document = {
  createElement: (t) => new FakeEl(t),
  getElementById: () => null,
  querySelectorAll: () => [],
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
const button = (root, text) =>
  find(root, (n) => n.tagName === "BUTTON" && n.textContent.includes(text))[0];
const tick = (ms = 30) => new Promise((r) => setTimeout(r, ms));
async function click(node) {
  for (const f of node.listeners.click || []) await f({});
  await tick();
}

const COPY = fs.mkdtempSync(path.join(os.tmpdir(), "aw-ui-flows-"));
fs.cpSync(UI, COPY, { recursive: true });
fs.writeFileSync(path.join(COPY, "package.json"), '{"type":"module"}');
const imp = (rel) => import(pathToFileURL(path.join(COPY, rel)).href);
const { createApi, ROUTES } = await imp("api.js");
const { makeCtx, describeError, pollDelay } = await imp("main.js");
const { TestWallet } = await imp("wallet/test.js");
const onboard = await imp("views/onboard.js");
const dashboard = await imp("views/dashboard.js");
const feed = await imp("views/feed.js");
const approvals = await imp("views/approvals.js");
const activate = await imp("views/activate.js");
const gauges = await imp("gauges.js");

const OWNER = "0x1111111111111111111111111111111111111111";
const AGENT = "0x2222222222222222222222222222222222222222";
const TX = "0x" + "cd".repeat(32);
const ORDER = "11111111-2222-4333-8444-555555555555";
const JOB = "a".repeat(32);
const problem = (slug, title, status, extra = {}) => ({
  status,
  body: { type: "urn:agent-wallet:problem:" + slug, title, status, ...extra },
});

// A fake server that records calls and answers with the shapes the API returns.
function server(script = {}) {
  const calls = [];
  const state = { signedIn: false, jobPolls: 0, stopped: false, ...script.state };
  const fetchImpl = async (url, init = {}) => {
    const method = init.method || "GET";
    const body = init.body ? JSON.parse(init.body) : undefined;
    calls.push({ method, url, credentials: init.credentials, headers: init.headers || {}, body });
    const reply = (status, data, headers = {}) => ({
      ok: status < 400,
      status,
      headers: { get: (k) => headers[k] ?? null },
      json: async () => data,
    });
    if (script.override) {
      const o = script.override(method, url, body, state);
      if (o) return reply(o.status, o.body, o.headers);
    }
    const key = method + " " + url;
    if (key === "POST /auth/nonce") {
      const nonce = "n".repeat(32);
      return reply(200, {
        nonce,
        message: "agent-wallet wants you to sign in.\nNonce: " + nonce,
        expires_at: "2026-10-15T12:05:00Z",
      });
    }
    if (key === "POST /auth/verify") {
      state.signedIn = true;
      return reply(200, { address: OWNER, expires_at: "2026-10-15T20:00:00Z" });
    }
    if (!state.signedIn) {
      const p = problem("unauthenticated", "Unauthenticated", 401);
      return reply(p.status, p.body);
    }
    if (key === "GET /me") {
      return reply(200, {
        address: OWNER,
        agent_address: AGENT,
        mode: "multi",
        chain_id: 84532,
        tier: state.tier ?? "T0",
        emergency_stop: state.stopped,
      });
    }
    if (key === "GET /agent") {
      return reply(200, {
        address: AGENT,
        eth_wei: String(state.eth ?? 10 ** 17),
        gas_floor_wei: "1000000000000000",
        suggested_topup_wei: "10000000000000000",
        usdc: { value: "1000000000", decimals: 6 },
        ausdc: { value: "0", decimals: 6 },
        price_source: "mainnet_reference",
        custody: "custodial-testnet",
      });
    }
    if (key === "POST /topups") {
      return reply(202, {
        tx_hash: body.tx_hash,
        status: "pending",
        amount_wei: "10000000000000000",
        explorer_url: "https://sepolia.basescan.org/tx/" + body.tx_hash,
      });
    }
    if (method === "GET" && url.startsWith("/topups/")) {
      return reply(200, { tx_hash: TX, status: "confirmed", amount_wei: "1", explorer_url: "x", block_number: 5 });
    }
    if (key === "GET /audit/verify") return reply(200, { ok: true, records: 3, first_bad_seq: null });
    if (key === "GET /jobs") return reply(200, { items: state.jobsList || [] });
    if (key === "POST /agent/mint") return reply(202, { job_id: JOB, status: "queued" });
    if (key === "POST /runs") {
      state.lastRun = body;
      const id = body.planner === "scripted_hostile" ? "b".repeat(32) : "c".repeat(32);
      return reply(202, { job_id: id, status: "queued" });
    }
    if (key === "POST /auth/step-up") {
      return reply(200, {
        challenge_id: "f".repeat(32),
        message: "agent-wallet asks you to authorise one action.\nAction: " + body.action,
        expires_at: "2026-10-15T12:02:00Z",
      });
    }
    if (key === "PUT /tier") {
      const raise = { T0: 0, T1: 1, T2: 2 }[body.tier] > { T0: 0, T1: 1, T2: 2 }[state.tier ?? "T0"];
      if (raise && !(init.headers || {})["X-Step-Up"]) {
        const p = problem("step-up-required", "Step-up required", 401);
        return reply(p.status, p.body);
      }
      state.tier = body.tier;
      return reply(200, {
        schema_version: 1,
        tier: body.tier,
        emergency_stop: state.stopped,
        updated_at: "2026-10-15T12:00:00Z",
        changed_by: "owner",
      });
    }
    if (key === "GET /portfolio") {
      return reply(200, {
        schema_version: 1,
        as_of: "2026-10-15T12:00:00Z",
        price_source: "mainnet_reference",
        positions: [
          { token: "USDC", location: "wallet", balance: amt("900000000"), price_usd: "1.00", value_usd: "900.00", weight_bps: 9000 },
          { token: "USDC", location: "aave-v3", balance: amt("100000000"), price_usd: "1.00", value_usd: "100.00", weight_bps: 1000 },
        ],
        total_value_usd: "1000.00",
        spent_24h_usd: state.spent ?? "40.00",
        tx_count_1h: 0,
      });
    }
    if (key === "GET /ips") {
      return reply(200, {
        schema_version: 1,
        ips_hash: "0x" + "ab".repeat(32),
        ips: { limits: { per_tx_usd: "100.00", per_day_usd: "200.00" }, approval: { escalate_above_usd: "50.00" } },
      });
    }
    if (key === "POST /emergency-stop") {
      state.stopped = true;
      return reply(200, { outcome: "executed" });
    }
    if (key === "POST /emergency-stop/release") {
      state.stopped = false;
      return reply(200, { outcome: "executed" });
    }
    if (key === "POST /agent/withdraw") return reply(202, { job_id: "e".repeat(32), status: "queued" });
    if (method === "POST" && url === "/orders/" + ORDER + "/approve") {
      return reply(200, { outcome: "executed" });
    }
    if (method === "GET" && url.startsWith("/jobs/")) {
      state.jobPolls++;
      const tmpl = script.jobs[url.slice(6)];
      if (state.jobPolls >= 3) return reply(200, tmpl.done);
      const status = state.jobPolls === 1 ? "queued" : "running";
      return reply(200, { ...tmpl.done, status, records: [], finished_at: null });
    }
    return reply(404, { type: "urn:agent-wallet:problem:not-found", title: "Not found", status: 404 });
  };
  return { fetchImpl, calls, state };
}

const jobBase = {
  planner_client: "none",
  order_ids: [],
  error_code: null,
  created_at: "2026-10-15T12:00:00Z",
  finished_at: "2026-10-15T12:00:02Z",
};
const amt = (v) => ({ value: v, decimals: 6 });
const rec = (o) => ({ tx_hash: null, rule_codes: [], reason: "ok", planner_client: "none", ...o });
const JOBS = {
  [JOB]: {
    done: {
      ...jobBase,
      job_id: JOB,
      kind: "mint",
      status: "succeeded",
      records: [rec({ seq: 0, order_id: ORDER, outcome: "executed", function: "mint", token: "USDC", amount: amt("1000000000"), tx_hash: TX })],
    },
  },
  ["c".repeat(32)]: {
    done: {
      ...jobBase,
      job_id: "c".repeat(32),
      kind: "run",
      status: "succeeded",
      planner_client: "fallback",
      records: [rec({ seq: 1, order_id: ORDER, outcome: "executed", function: "supply", token: "USDC", amount: amt("500000000"), tx_hash: TX, planner_client: "fallback" })],
    },
  },
  ["b".repeat(32)]: {
    done: {
      ...jobBase,
      job_id: "b".repeat(32),
      kind: "run",
      status: "succeeded",
      planner_client: "scripted",
      records: [rec({ seq: 2, order_id: ORDER, outcome: "denied", function: "mint", token: "USDC", amount: amt("1000000000"), rule_codes: ["ACCESS.OWNER_ONLY_FUNCTION"], reason: "<img src=x onerror=alert(1)>", planner_client: "scripted" })],
    },
  },
  ["e".repeat(32)]: {
    done: { ...jobBase, job_id: "e".repeat(32), kind: "withdraw", status: "succeeded", records: [] },
  },
};

function make(script = {}, walletOpts) {
  const srv = server({ jobs: JOBS, ...script });
  const api = createApi({ fixtures: false, fetchImpl: srv.fetchImpl, onUnauthorized: () => ctx.signedOut() });
  const sleeps = [];
  const wallet = new TestWallet({ address: OWNER, ...(walletOpts || {}) });
  const ctx = makeCtx({ api, wallet, sleep: async (ms) => void sleeps.push(ms) });
  return { srv, api, ctx, wallet, sleeps };
}

const out = {};

// 1. sign-in: nonce, personal_sign of the exact message, verify, agent address shown
{
  const { srv, ctx, wallet } = make();
  const o = onboard.render(ctx);
  await tick();
  await click(button(o, "Connect wallet"));
  const verify = srv.calls.find((c) => c.url === "/auth/verify");
  out.signIn = {
    wallet: wallet.calls,
    signedMessage: wallet.signed[0],
    verifyBody: verify && {
      address: verify.body.address,
      nonce: verify.body.nonce,
      hasSig: /^0x[0-9a-f]+$/.test(verify.body.signature),
    },
    text: o.textContent,
  };
  out.requests = srv.calls.every(
    (c) => c.credentials === "same-origin" && c.headers["X-AW-CSRF"] === "1",
  );
}

// 2. top-up: eth_sendTransaction to the agent address, POST /topups, GET /topups/{hash}
{
  const { srv, ctx, wallet } = make({ state: { signedIn: true, eth: 0 } });
  await ctx.refreshSession();
  const o = onboard.render(ctx);
  await tick();
  out.fundShows = o.textContent;
  await click(button(o, "Send gas ETH"));
  out.topup = {
    sent: wallet.sent,
    calls: srv.calls
      .filter((c) => c.url.startsWith("/topups"))
      .map((c) => c.method + " " + c.url + (c.body ? " " + JSON.stringify(c.body) : "")),
  };
}

// 3. mint, run, hostile run and withdraw are jobs; progress comes from GET /jobs/{id}
{
  const { srv, ctx, sleeps } = make({ state: { signedIn: true } });
  await ctx.refreshSession();
  const d = dashboard.render(ctx);
  await tick(60);
  out.dashboardText = d.textContent;
  srv.state.jobPolls = 0;
  await click(button(d, "Mint test USDC"));
  out.mint = {
    text: d.textContent,
    polls: srv.calls.filter((c) => c.url === "/jobs/" + JOB).length,
    sleeps: sleeps.slice(),
    links: find(d, (n) => n.tagName === "A").map((n) => n.attrs.href),
  };
  srv.state.jobPolls = 0;
  await click(button(d, "Run the agent"));
  out.run = { body: srv.state.lastRun, text: d.textContent };
  srv.state.jobPolls = 0;
  await click(button(d, "Try a hostile note"));
  out.hostile = {
    body: srv.state.lastRun,
    text: d.textContent,
    injected: find(d, (n) => ["IMG", "SCRIPT"].includes(n.tagName)).length,
  };
  srv.state.jobPolls = 0;
  await click(button(d, "Withdraw to my wallet"));
  const w = srv.calls.find((c) => c.url === "/agent/withdraw");
  const su = srv.calls.find((c) => c.url === "/auth/step-up");
  out.withdraw = {
    stepUpBody: su && su.body,
    header: w && w.headers["X-Step-Up"],
    body: w && w.body,
  };
  out.polling = [0, 1, 2, 3, 4, 9].map(pollDelay);
}

// 4. stop needs the session only; release carries the step-up header
{
  const { srv, api, ctx } = make({ state: { signedIn: true } });
  await ctx.refreshSession();
  await api.stop();
  const stop = srv.calls.find((c) => c.url === "/emergency-stop");
  out.stop = {
    stepUpHeaders: Object.keys(stop.headers).filter((h) => h.toLowerCase().includes("step")),
    tier: await api.tier(),
  };
  await api.release(await ctx.stepUp("release_stop", {}));
  const rel = srv.calls.find((c) => c.url === "/emergency-stop/release");
  out.release = { header: rel.headers["X-Step-Up"], tier: await api.tier() };
}

// 5. audit verify and the pending approvals list
{
  const pendingRec = rec({
    seq: 4,
    order_id: ORDER,
    outcome: "pending_approval",
    function: "supply",
    token: "USDC",
    amount: amt("500000000"),
    rule_codes: ["TIER.APPROVAL_REQUIRED"],
    reason: "above threshold",
    planner_client: "openai",
  });
  const cid = "c".repeat(32);
  const { srv, ctx } = make({
    state: { signedIn: true, jobsList: [{ job_id: cid, kind: "run" }] },
    jobs: {
      [cid]: {
        done: { ...jobBase, job_id: cid, kind: "run", status: "succeeded", planner_client: "openai", records: [pendingRec] },
      },
    },
  });
  srv.state.jobPolls = 5;
  await ctx.refreshSession();
  const f = feed.render(ctx);
  await tick(60);
  out.feedText = f.textContent;
  await click(button(f, "Verify audit chain"));
  out.verify = f.textContent;
  const a = approvals.render(ctx);
  await tick(60);
  out.approvalsText = a.textContent;
  await click(button(a, "Approve"));
  const ap = srv.calls.find((c) => c.url === "/orders/" + ORDER + "/approve");
  out.approve = { header: ap && ap.headers["X-Step-Up"], listAfter: a.textContent };
}

// 6. error mapping, and a 401 drops the signed-in state
{
  const cases = {
    409: problem("job-in-progress", "Conflict", 409, { job_id: JOB }),
    429: { ...problem("rate-limited", "Too many", 429), headers: { "Retry-After": "30" } },
    503: problem("custody-unavailable", "Custody", 503),
    401: problem("unauthenticated", "Unauthenticated", 401),
  };
  out.errors = {};
  for (const [status, resp] of Object.entries(cases)) {
    const { ctx, api } = make({
      state: { signedIn: true },
      override: (m, u) => (u === "/agent" ? resp : null),
    });
    ctx.state.me = { address: OWNER };
    let err = null;
    try {
      await api.agent();
    } catch (e) {
      err = e;
    }
    out.errors[status] = {
      text: describeError(err),
      retryAfter: err.retryAfter,
      jobId: err.problem && err.problem.job_id,
      meCleared: ctx.state.me === null,
    };
  }
}

// 7. a wallet rejection during sign-in is not a failure and nothing reaches the server
{
  const { srv, ctx } = make({}, { reject: true });
  const o = onboard.render(ctx);
  await tick();
  await click(button(o, "Connect wallet"));
  out.rejected = { text: o.textContent, posts: srv.calls.filter((c) => c.method === "POST").length };
}

// 8. activate agent: step-up first, then PUT /tier with X-Step-Up; the tier chip follows
{
  const { srv, ctx, wallet } = make({ state: { signedIn: true } });
  await ctx.refreshSession();
  const d = dashboard.render(ctx);
  await tick(60);
  out.t0Dashboard = d.textContent;
  const before = srv.calls.length;
  await click(button(d, "Activate agent"));
  const sent = srv.calls.slice(before).filter((c) => c.url === "/auth/step-up" || c.url === "/tier");
  const put = sent.find((c) => c.method === "PUT");
  out.activate = {
    order: sent.map((c) => c.method + " " + c.url),
    stepUpBody: sent[0] && sent[0].body,
    putBody: put && put.body,
    header: put && put.headers["X-Step-Up"],
    signedMessage: wallet.signed[wallet.signed.length - 1],
    tierState: ctx.state.tier,
    serverTier: srv.state.tier,
    text: d.textContent,
  };
  const chip = document.createElement("span");
  const parts = { chip, stopBtn: document.createElement("button"), stopMsg: document.createElement("p"), masthead: document.createElement("header") };
  gauges.applyTopbar(parts, await ctx.api.tier());
  out.activateChip = chip.textContent;
  const after = await ctx.refreshSession();
  out.afterActivateMe = after && after.tier;
  // activated: the callout is gone, and lowering needs no signature
  const d2 = dashboard.render(ctx);
  await tick(60);
  out.activatedDashboard = d2.textContent;
  const n = srv.calls.length;
  await click(button(d2, "Lower to read only"));
  const low = srv.calls.slice(n).find((c) => c.method === "PUT");
  out.lower = {
    body: low && low.body,
    stepUpHeader: (low && low.headers["X-Step-Up"]) ?? null,
    stepUpCalls: srv.calls.slice(n).filter((c) => c.url === "/auth/step-up").length,
    serverTier: srv.state.tier,
  };
}

// 9. onboarding shows the Activate step and the prompt disclosures
{
  const { ctx } = make({ state: { signedIn: true } });
  await ctx.refreshSession();
  const o = onboard.render(ctx);
  await tick(60);
  out.onboardActivate = o.textContent;
}

// 10. FROZEN state appears when the stop is engaged and clears on release
{
  const { ctx, api } = make({ state: { signedIn: true, tier: "T2" } });
  await ctx.refreshSession();
  const mk = (tag) => document.createElement(tag);
  const parts = { chip: mk("span"), stopBtn: mk("button"), stopMsg: mk("p"), masthead: mk("header") };
  gauges.applyTopbar(parts, await api.tier());
  const calm = { cls: parts.masthead.className, btn: parts.stopBtn.textContent, msg: parts.stopMsg.textContent };
  await api.stop();
  gauges.applyTopbar(parts, await api.tier());
  const frozen = {
    cls: parts.masthead.className,
    chip: parts.chip.textContent,
    btn: parts.stopBtn.textContent,
    msg: parts.stopMsg.textContent,
  };
  await api.release(await ctx.stepUp("release_stop", {}));
  gauges.applyTopbar(parts, await api.tier());
  const released = { cls: parts.masthead.className, btn: parts.stopBtn.textContent, msg: parts.stopMsg.textContent };
  out.freeze = { calm, frozen, released };
  // the dashboard hides the activate callout while stopped
}

// 11. spend bar maths, live portfolio and limit
{
  out.spendMath = {
    low: gauges.spendGauge("40.00", "200.00"),
    warn: gauges.spendGauge("170.00", "200.00"),
    full: gauges.spendGauge("250.00", "200.00"),
    bad: gauges.spendGauge("x", "200.00"),
    zero: gauges.spendGauge("1.00", "0"),
  };
  const { srv, ctx } = make({ state: { signedIn: true, tier: "T2" } });
  await ctx.refreshSession();
  const d = dashboard.render(ctx);
  await tick(60);
  const bar = find(d, (n) => n.attrs.role === "progressbar")[0];
  out.spendCard = {
    now: bar && bar.attrs["aria-valuenow"],
    width: bar && bar.children[0].style.width,
    text: d.textContent,
  };
  out.gas = [gauges.gasGauge("0", "1000"), gauges.gasGauge("2000", "1000"), gauges.gasGauge("500", "1000")];
}

// 12. 429 shows when to try again
{
  out.retry = {
    seconds: gauges.retryText("30"),
    minutes: gauges.retryText("300"),
    date: gauges.retryText("Thu, 15 Oct 2026 12:00:45 GMT", Date.parse("Thu, 15 Oct 2026 12:00:00 GMT")),
    none: gauges.retryText(null),
    shown: describeError(
      Object.assign(new (await imp("api.js")).ApiError(429, "rate-limited", "Too many", "", { retryAfter: "30" }), {}),
    ),
  };
}

// 13. fixtures mode: activating at T0 raises the tier in the demo, with the test wallet
{
  const api = createApi({
    fixtures: true,
    demo: true,
    scenario: "t0",
    fetchImpl: async (u) => {
      const name = String(u).replace(/^fixtures\//, "").replace(/\.json$/, "");
      return { ok: true, status: 200, json: async () => JSON.parse(fs.readFileSync(path.join(UI, "fixtures", name + ".json"), "utf8")) };
    },
  });
  const ctx = makeCtx({ api, wallet: new TestWallet(), sleep: async () => {} });
  await ctx.refreshSession();
  const t0 = (await api.tier()).tier;
  await activate.activateAgent(ctx);
  out.fixturesActivate = { before: t0, after: (await api.tier()).tier };
}

out.routes = Object.values(ROUTES);
console.log(JSON.stringify(out));
