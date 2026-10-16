// Chat tab flows: the real ui modules against a fake `fetch` (server) and a fake DOM.
// Response bodies copy the shapes of GET /chat, POST /chat and GET /jobs/{id} (api/chat.py).
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
globalThis.__innerHTMLUsed = false;
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
const byClass = (root, cls) => find(root, (n) => n.className.split(" ").includes(cls));
const button = (root, text) =>
  find(root, (n) => n.tagName === "BUTTON" && n.textContent.includes(text))[0];
const tick = (ms = 30) => new Promise((r) => setTimeout(r, ms));
async function fire(node, type, ev = {}) {
  for (const f of node.listeners[type] || []) await f({ preventDefault() {}, ...ev });
  await tick();
}

const COPY = fs.mkdtempSync(path.join(os.tmpdir(), "aw-ui-chat-"));
fs.cpSync(UI, COPY, { recursive: true });
fs.writeFileSync(path.join(COPY, "package.json"), '{"type":"module"}');
const imp = (rel) => import(pathToFileURL(path.join(COPY, rel)).href);
const { createApi } = await imp("api.js");
const { makeCtx } = await imp("main.js");
const { TestWallet } = await imp("wallet/test.js");
const chat = await imp("views/chat.js");

const OWNER = "0x1111111111111111111111111111111111111111";
const TX = "0x" + "cd".repeat(32);
const JOB = "a".repeat(32);
const usdc10 = { value: "10000000", decimals: 6 };
const step = (o) => ({
  order_id: null,
  token: "USDC",
  amount: usdc10,
  reason: null,
  rule_codes: [],
  tx_hash: null,
  ...o,
});
const problem = (slug, title, status, extra = {}) => ({
  status,
  body: { type: "urn:agent-wallet:problem:" + slug, title, status, ...extra },
});

// A fake server for /me, /chat, /jobs/{id}. `script.onSend(body, state)` returns the messages to
// append (user and agent) or a problem.
function server(script = {}) {
  const calls = [];
  const state = { items: [], polls: 0, tier: "T2", stopped: false, ...script.state };
  const fetchImpl = async (url, init = {}) => {
    const method = init.method || "GET";
    const body = init.body ? JSON.parse(init.body) : undefined;
    calls.push({ method, url, headers: init.headers || {}, body });
    const reply = (status, data, headers = {}) => ({
      ok: status < 400,
      status,
      headers: { get: (k) => headers[k] ?? null },
      json: async () => data,
    });
    if (method === "GET" && url === "/me") {
      return reply(200, {
        address: OWNER,
        agent_address: "0x" + "22".repeat(20),
        mode: "multi",
        chain_id: 84532,
        tier: state.tier,
        emergency_stop: state.stopped,
      });
    }
    if (method === "GET" && url.startsWith("/chat?")) {
      const tid = new URL(url, "http://x").searchParams.get("thread_id");
      return reply(200, { items: (tid && state.byThread && state.byThread[tid]) || state.items });
    }
    if (method === "GET" && url === "/limits") {
      return state.limits ? reply(200, state.limits) : reply(503, { status: 503, title: "Unavailable" });
    }
    if (method === "GET" && url === "/chat/suggestions") {
      return state.suggestions ? reply(200, { items: state.suggestions }) : reply(503, { status: 503 });
    }
    if (method === "GET" && url.startsWith("/chat/threads")) {
      return reply(200, { items: state.threads || [] });
    }
    if (method === "POST" && url === "/chat/threads") {
      return reply(201, { id: "tnew", title: "New chat" });
    }
    if (method === "POST" && url === "/jobs/" + JOB + "/cancel") {
      const c = state.cancel || { status: 202, body: { job_id: JOB, status: "cancelling" } };
      return reply(c.status, c.body);
    }
    if (method === "POST" && url === "/chat") {
      const out = script.onSend(body, state);
      if (out.problem) return reply(out.problem.status, out.problem.body, out.problem.headers);
      state.pending = out;
      state.polls = 0;
      return reply(202, { job_id: JOB, status: "queued" });
    }
    if (method === "GET" && url === "/jobs/" + JOB && state.jobScript) {
      const at = Math.min(state.polls++, state.jobScript.length - 1);
      const last = at === state.jobScript.length - 1;
      if (last && state.pending) {
        state.items.push(...state.pending.messages);
        state.pending = null;
      }
      return reply(200, {
        job_id: JOB,
        kind: "chat",
        planner_client: "openai",
        order_ids: [],
        error_code: null,
        created_at: "2026-10-15T12:00:00Z",
        finished_at: last ? "2026-10-15T12:00:03Z" : null,
        ...state.jobScript[at],
      });
    }
    if (method === "GET" && url === "/jobs/" + JOB) {
      state.polls++;
      const done = state.polls >= 3;
      if (done && state.pending) {
        state.items.push(...state.pending.messages);
        state.pending = null;
      }
      return reply(200, {
        job_id: JOB,
        kind: "chat",
        status: done ? "succeeded" : state.polls === 1 ? "queued" : "running",
        planner_client: "openai",
        order_ids: [],
        error_code: null,
        created_at: "2026-10-15T12:00:00Z",
        finished_at: done ? "2026-10-15T12:00:03Z" : null,
      });
    }
    return reply(404, { type: "urn:agent-wallet:problem:not-found", title: "Not found", status: 404 });
  };
  return { fetchImpl, calls, state };
}

function make(script = {}) {
  const srv = server(script);
  const api = createApi({ fixtures: false, fetchImpl: srv.fetchImpl });
  const sleeps = [];
  const ctx = makeCtx({
    api,
    wallet: new TestWallet({ address: OWNER }),
    sleep: async (ms) => void sleeps.push(ms),
  });
  return { srv, api, ctx, sleeps };
}

const msg = (role, text, steps = []) => ({
  id: role + Math.random().toString(16).slice(2, 10),
  role,
  text,
  job_id: JOB,
  created_at: "2026-10-15T12:00:00Z",
  steps,
});
const input = (root) => find(root, (n) => n.tagName === "TEXTAREA")[0];
const bubbles = (root) => byClass(root, "bubble");
const rowTitles = (root) => byClass(root, "progress-title").map((n) => n.textContent);
const out = {};

// 1. send: Enter submits, the progress card shows before the request leaves, history is reloaded
{
  const { srv, ctx, sleeps } = make({
    onSend: (body) => ({
      messages: [
        msg("user", body.message),
        msg(
          "agent",
          "I approved 10 USDC for the Aave pool and supplied 10 USDC to Aave. Transactions: " +
            TX +
            "\n\nAgent says: Putting it to work.",
          [
            step({ function: "approve", outcome: "executed", tx_hash: TX, explanation: "Approved 10 USDC for the Aave pool." }),
            step({ function: "supply", outcome: "executed", tx_hash: TX, explanation: "Supplied 10 USDC to Aave." }),
          ],
        ),
      ],
    }),
  });
  await ctx.refreshSession();
  const root = chat.render(ctx);
  await tick();
  out.emptyText = root.textContent;
  const box = input(root);
  const seenLive = [];
  const preSend = [];
  const api = ctx.api;
  const origSend = api.chatSend;
  api.chatSend = async (...args) => {
    preSend.push(rowTitles(root)); // the card is already in the log while the POST is pending
    return origSend(...args);
  };
  const origJob = api.job;
  api.job = async (id) => {
    seenLive.push(rowTitles(root));
    return origJob(id);
  };
  box.value = "Put 10 USDC to work in Aave";
  await fire(box, "input");
  out.counterTyped = byClass(root, "counter")[0].textContent;
  // Shift+Enter is a newline: nothing is sent
  await fire(box, "keydown", { key: "Enter", shiftKey: true });
  out.postsAfterShiftEnter = srv.calls.filter((c) => c.method === "POST").length;
  await fire(box, "keydown", { key: "Enter", shiftKey: false });
  await tick(60);
  const post = srv.calls.find((c) => c.method === "POST");
  out.send = {
    body: post && post.body,
    csrf: post && post.headers["X-AW-CSRF"],
    pre: preSend,
    live: seenLive,
    composerCleared: box.value === "",
    sleeps,
    bubbles: bubbles(root).map((b) => b.className),
    text: root.textContent,
    cards: byClass(root, "step-card").length,
    links: find(root, (n) => n.tagName === "A").map((n) => n.attrs.href),
    saysChips: find(root, (n) => n.textContent === "Agent says").length,
    sendDisabled: button(root, "Send").disabled,
  };
}

// 2. a blocked step: plain words, rule chip, skipped card, Activate link, no tx link
{
  const { ctx } = make({
    state: {
      tier: "T0",
      items: [
        msg("user", "Put 10 USDC to work in Aave"),
        msg(
          "agent",
          "I did not act: your agent is in read-only mode (tier T0), so it cannot act yet.",
          [
            step({
              function: "approve",
              outcome: "denied",
              rule_codes: ["TIER.READ_ONLY"],
              reason: "Tier T0 is read-only; no order is executed.",
              explanation: "The agent could not approve 10 USDC for the Aave pool: your agent is in read-only mode (tier T0). Nothing was sent.",
            }),
            step({ function: "supply", outcome: "skipped", explanation: "Not attempted: the previous step was blocked." }),
          ],
        ),
      ],
    },
  });
  await ctx.refreshSession();
  const root = chat.render(ctx);
  await tick();
  const cards = byClass(root, "step-card");
  out.blocked = {
    text: root.textContent,
    cards: cards.map((c) => c.className),
    codes: find(root, (n) => n.className.includes("code")).map((n) => n.textContent),
    links: find(root, (n) => n.tagName === "A").map((n) => n.attrs.href),
    activateLinks: find(root, (n) => n.tagName === "A" && n.textContent.includes("Activate")).length,
  };
}

// 3. frozen: explained, composer disabled, nothing sent
{
  const { srv, ctx } = make({ state: { stopped: true } });
  await ctx.refreshSession();
  const root = chat.render(ctx);
  await tick();
  const box = input(root);
  box.value = "hello";
  await fire(button(root, "Send"), "click");
  out.frozen = {
    text: root.textContent,
    sendDisabled: button(root, "Send").disabled,
    inputDisabled: box.disabled,
    posts: srv.calls.filter((c) => c.method === "POST").length,
  };
}

// 4. rate limit: plain words, message kept in the composer, nothing added to the log
{
  const { ctx } = make({
    onSend: () => ({ problem: { ...problem("rate-limited", "Too many requests", 429), headers: { "Retry-After": "120" } } }),
  });
  await ctx.refreshSession();
  const root = chat.render(ctx);
  await tick();
  const box = input(root);
  box.value = "one more thing";
  await fire(button(root, "Send"), "click");
  await tick(60);
  out.rateLimited = {
    status: byClass(root, "msg")[0].textContent,
    kept: box.value,
    bubbles: bubbles(root).length,
    sendEnabled: button(root, "Send").disabled === false,
  };
}

// 5. 409: the active job is followed, then history reloads
{
  const { srv, ctx } = make({
    onSend: () => ({ problem: problem("job-in-progress", "A job is already running", 409, { job_id: JOB }) }),
  });
  srv.state.pending = { messages: [msg("agent", "Earlier request finished.")] };
  await ctx.refreshSession();
  const root = chat.render(ctx);
  await tick();
  const box = input(root);
  box.value = "second";
  await fire(button(root, "Send"), "click");
  await tick(80);
  out.conflict = {
    status: byClass(root, "msg")[0].textContent,
    text: root.textContent,
    kept: box.value,
  };
}

// 6. history reload on a fresh render, a 500 character cap, the withdraw hint, chips fill the box
{
  const { srv, ctx } = make({
    state: { items: [msg("user", "What is my balance?"), msg("agent", "No transaction was made.\n\nAgent says: Wallet: 1000 USDC idle.")] },
  });
  await ctx.refreshSession();
  const root = chat.render(ctx);
  await tick();
  const box = input(root);
  out.history = {
    bubbles: bubbles(root).map((b) => b.className),
    text: root.textContent,
    getCalls: srv.calls.filter((c) => c.url.startsWith("/chat?")).map((c) => c.url),
  };
  await fire(button(root, "Put 10 USDC to work in Aave"), "click");
  out.chipFilled = box.value;
  out.prompts = byClass(root, "prompt-chip").map((n) => n.textContent);
  out.postsAfterChip = srv.calls.filter((c) => c.method === "POST").length;
  out.maxLength = box.maxLength;
}

// 7. hostile strings are inert text
{
  const hostile = '<img src=x onerror=alert(1)><script>alert(2)</script>';
  const { ctx } = make({
    state: {
      items: [
        msg("agent", hostile, [
          step({ function: hostile, token: hostile, outcome: "denied", rule_codes: [hostile], explanation: hostile, tx_hash: hostile }),
        ]),
      ],
    },
  });
  await ctx.refreshSession();
  const root = chat.render(ctx);
  await tick();
  out.hostile = {
    innerHTMLUsed: globalThis.__innerHTMLUsed,
    injected: find(root, (n) => ["IMG", "SCRIPT", "IFRAME"].includes(n.tagName)).length,
    anchors: find(root, (n) => n.tagName === "A" && (n.attrs.href || "").includes("<")).length,
    shown: root.textContent.includes(hostile),
  };
}

// 8. signed out: a prompt to connect, no chat request
{
  const srv = server({});
  const api = createApi({
    fixtures: false,
    fetchImpl: async (url, init = {}) => {
      srv.calls.push({ url, method: init.method || "GET" });
      return {
        ok: false,
        status: 401,
        headers: { get: () => null },
        json: async () => ({ type: "urn:agent-wallet:problem:unauthenticated", title: "x", status: 401 }),
      };
    },
  });
  const ctx = makeCtx({ api, wallet: new TestWallet({ address: OWNER }), sleep: async () => {} });
  const root = chat.render(ctx);
  await tick();
  out.signedOut = {
    text: root.textContent,
    chatCalls: srv.calls.filter((c) => c.url.startsWith("/chat")).length,
  };
}

// 9. fixtures mode answers the whole flow without a backend
{
  const api = createApi({
    fixtures: true,
    demo: true,
    scenario: "t0",
    fetchImpl: async (url) => ({
      ok: true,
      status: 200,
      json: async () => JSON.parse(fs.readFileSync(path.join(UI, String(url)), "utf8")),
    }),
  });
  const ctx = makeCtx({ api, wallet: new TestWallet(), sleep: async () => {} });
  await ctx.refreshSession();
  const root = chat.render(ctx);
  await tick();
  input(root).value = "Put 10 USDC to work in Aave";
  await fire(button(root, "Send"), "click");
  await tick(60);
  out.fixtures = { text: root.textContent, cards: byClass(root, "step-card").length };
}

// ---- webapp v2: phases, Stop, limit line, quick fixes, threads, suggestions -----------------
const LIMITS = {
  per_order_usd: "50.00",
  per_day_usd: "1000.00",
  used_today_usd: "10.00",
  approval_threshold_usd: "50.00",
  protocols: ["aave_v3"],
  emergency_stop: "ready",
  as_of: "2026-10-16T09:00:00Z",
};
const cardTitles = (root) => byClass(root, "step-title").map((n) => n.textContent);
const postsTo = (srv, url) => srv.calls.filter((c) => c.method === "POST" && c.url === url);

// 10. phase strip, Stop (202) and the Send button turning into Stop
{
  const { srv, ctx } = make({
    state: {
      limits: LIMITS,
      jobScript: [
        { status: "queued", phase: "queued" },
        { status: "running", phase: "rules_check" },
        { status: "running", phase: "sent" },
        { status: "running", phase: "confirming" },
        { status: "denied", phase: "done", error_code: "OWNER_CANCELLED" },
      ],
    },
    onSend: (body) => ({
      messages: [
        msg("user", body.message),
        msg("agent", "You cancelled the run, so the next step was not attempted.", [
          step({
            function: "supply",
            outcome: "skipped",
            reason: "skipped supply (10 USDC): owner-cancelled",
            explanation: "Not attempted: you cancelled this run.",
          }),
        ]),
      ],
    }),
  });
  await ctx.refreshSession();
  const root = chat.render(ctx);
  await tick();
  const box = input(root);
  const seen = [];
  let stopClicked = null;
  const origJob = ctx.api.job;
  ctx.api.job = async (id) => {
    const sendBtn = find(root, (n) => n.className === "stop" || n.className === "primary")[0];
    seen.push({
      rows: rowTitles(root),
      cardStop: byClass(root, "progress-stop").length,
      stop: Boolean(sendBtn && sendBtn.className === "stop" && sendBtn.textContent === "Stop"),
    });
    if (seen.length === 2) {
      const stopBtn = find(root, (n) => n.className === "btn progress-stop")[0];
      stopClicked = Promise.resolve(stopBtn.listeners.click[0]());
    }
    return origJob(id);
  };
  box.value = "Put 10 USDC to work in Aave";
  await fire(box, "keydown", { key: "Enter", shiftKey: false });
  await tick(80);
  await stopClicked;
  out.phases = {
    seen,
    cancelPosts: postsTo(srv, "/jobs/" + JOB + "/cancel").length,
    csrf: (postsTo(srv, "/jobs/" + JOB + "/cancel")[0] || { headers: {} }).headers["X-AW-CSRF"],
    sendLabelAfter: find(root, (n) => n.tagName === "BUTTON" && n.className === "primary").map((n) => n.textContent),
    skipped: byClass(root, "step-card").filter((n) => n.className.includes("skipped")).length,
    skipText: root.textContent.includes("You stopped the run, so this step was not sent."),
  };
}

// 11. Stop answered 409: the order is already sent
{
  const { ctx } = make({
    state: {
      jobScript: [
        { status: "running", phase: "sent" },
        { status: "running", phase: "sent" },
        { status: "succeeded", phase: "done" },
      ],
      cancel: { status: 409, body: { type: "urn:agent-wallet:problem:not-cancellable", title: "Job cannot be cancelled", status: 409, job_id: JOB, error: "not-cancellable" } },
    },
    onSend: (body) => ({ messages: [msg("user", body.message), msg("agent", "Done.", [])] }),
  });
  await ctx.refreshSession();
  const root = chat.render(ctx);
  await tick();
  const box = input(root);
  let status = "";
  const origJob = ctx.api.job;
  let n = 0;
  ctx.api.job = async (id) => {
    if (++n === 2) {
      const stopBtn = find(root, (x) => x.className === "stop")[0];
      await stopBtn.listeners.click[0]();
      status = byClass(root, "msg").map((x) => x.textContent).join("|");
    }
    return origJob(id);
  };
  box.value = "Put 10 USDC to work in Aave";
  await fire(box, "keydown", { key: "Enter", shiftKey: false });
  await tick(80);
  out.tooLate = status;
}

// 12. blocked card: limit line and quick fixes; unknown phase degrades to "Working"
{
  const blocked = step({
    function: "supply",
    amount: { value: "5000000000", decimals: 6 },
    outcome: "denied",
    rule_codes: ["LIMIT.PER_TX"],
    explanation: "The agent could not supply 5000 USDC to Aave. Nothing was sent.",
  });
  const { srv, ctx } = make({
    state: {
      limits: LIMITS,
      items: [msg("user", "Put 5000 USDC to work in Aave"), msg("agent", "I did not act.", [blocked])],
      jobScript: [{ status: "running", phase: "mystery" }, { status: "succeeded", phase: "done" }],
    },
    onSend: (body) => ({ messages: [msg("user", body.message), msg("agent", "Because of the limit.", [])] }),
  });
  await ctx.refreshSession();
  const root = chat.render(ctx);
  await tick();
  const box = input(root);
  const tryChip = button(root, "Try 50 USDC");
  out.quick = {
    limitLine: byClass(root, "limit-line").map((n) => n.textContent),
    chips: byClass(root, "step-actions").map((n) => n.textContent),
    limitsCalls: srv.calls.filter((c) => c.url === "/limits").length,
  };
  await fire(tryChip, "click");
  out.quick.composer = box.value;
  out.quick.postsAfterTry = postsTo(srv, "/chat").length;
  const seen = [];
  const origJob = ctx.api.job;
  ctx.api.job = async (id) => {
    seen.push(rowTitles(root).slice(-1)[0] || "");
    return origJob(id);
  };
  await fire(button(root, "Why is this blocked?"), "click");
  await tick(80);
  out.quick.asked = (postsTo(srv, "/chat")[0] || {}).body;
  out.quick.unknownPhase = seen;
  out.quick.composerAfter = box.value;
}

// 13. threads: rail groups, select loads that thread, New chat posts a thread, send carries it
{
  const nowIso = new Date().toISOString();
  const { srv, ctx } = make({
    state: {
      suggestions: [
        { text: "What did you do today?", kind: "question" },
        { text: "Supply 10 USDC to Aave", kind: "action" },
      ],
      threads: [
        { id: "t1", title: "Put 10 USDC to work", last_message_at: nowIso, status: "running" },
        { id: "t2", title: "Withdraw 5 USDC", last_message_at: "2026-01-01T10:00:00Z", status: "blocked" },
      ],
      byThread: { t2: [msg("user", "Withdraw 5 USDC"), msg("agent", "Blocked thread reply.", [])] },
    },
    onSend: (body) => ({ messages: [msg("user", body.message), msg("agent", "Okay.", [])] }),
  });
  await ctx.refreshSession();
  const root = chat.render(ctx);
  await tick();
  const rows = () => byClass(root, "rail-row");
  out.rail = {
    groups: byClass(root, "rail-group").map((n) => n.textContent),
    rows: rows().map((n) => n.textContent),
    chips: byClass(root, "prompt-chip").map((n) => n.textContent),
  };
  await fire(rows()[1], "click");
  out.rail.loaded = srv.calls.filter((c) => c.method === "GET" && c.url.includes("thread_id=t2")).length;
  out.rail.active = rows().filter((n) => n.className.includes("active")).map((n) => n.textContent);
  out.rail.text = bubbles(root).map((b) => b.textContent);
  await fire(button(root, "+ New chat"), "click");
  out.rail.newPosts = postsTo(srv, "/chat/threads").length;
  const box = input(root);
  box.value = "hello";
  await fire(box, "keydown", { key: "Enter", shiftKey: false });
  await tick(60);
  out.rail.sendBody = (postsTo(srv, "/chat")[0] || {}).body;
}

// 14. suggestions fail: the static prompts come back; a job that fails after "sent" says so
{
  const { ctx } = make({
    state: {
      jobScript: [
        { status: "running", phase: "sent" },
        { status: "failed", phase: "done", error_code: "RPC_UNAVAILABLE" },
      ],
    },
    onSend: (body) => ({ messages: [msg("user", body.message), msg("agent", "It failed.", [])] }),
  });
  await ctx.refreshSession();
  const root = chat.render(ctx);
  await tick();
  out.fallback = byClass(root, "prompt-chip").map((n) => n.textContent);
  const box = input(root);
  box.value = "Put 10 USDC to work in Aave";
  await fire(box, "keydown", { key: "Enter", shiftKey: false });
  await tick(80);
  out.uncertain = {
    text: root.textContent.includes("It may still land. Check Activity before trying again."),
    link: find(root, (n) => n.tagName === "A").map((n) => n.attrs.href),
  };
}

console.log(JSON.stringify(out));
