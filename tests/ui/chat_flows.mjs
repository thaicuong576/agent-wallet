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
}
globalThis.__innerHTMLUsed = false;
globalThis.document = {
  createElement: (t) => new FakeEl(t),
  getElementById: () => null,
  querySelectorAll: () => [],
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
      if (state.notFoundThread && tid === state.notFoundThread) {
        return reply(404, { type: "urn:agent-wallet:problem:not-found", title: "Not found", status: 404 });
      }
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
      if (state.threads && state.threads.length > 0) {
        const newest = state.threads[0];
        const msgs = state.byThread ? state.byThread[newest.id] : state.items;
        const hasMessages = msgs ? msgs.length > 0 : newest.title !== "New chat";
        if (!hasMessages) {
          return reply(200, { id: newest.id, title: newest.title || "New chat" });
        }
      }
      const id =
        script.newThreadId ||
        (state.threads && state.threads.some((t) => t.id === "tnew")
          ? "tnew" + (state.threads.length + 1)
          : "tnew");
      const t = { id, title: "New chat", last_message_at: new Date().toISOString(), status: "done" };
      if (!state.threads) state.threads = [];
      state.threads.unshift(t);
      if (state.byThread && !state.byThread[id]) state.byThread[id] = [];
      return reply(201, { id, title: "New chat" });
    }
    if (method === "POST" && url === "/jobs/" + JOB + "/cancel") {
      const c = state.cancel || { status: 202, body: { job_id: JOB, status: "cancelling" } };
      return reply(c.status, c.body);
    }
    if (method === "POST" && url === "/chat") {
      if (body.thread_id && state.threads) {
        const found = state.threads.find((t) => t.id === body.thread_id);
        if (found && (found.title === "New chat" || !found.title)) {
          found.title = body.message.slice(0, 40);
        }
      }
      const out = script.onSend(body, state);
      if (out.problem) return reply(out.problem.status, out.problem.body, out.problem.headers);
      if (body.thread_id && state.byThread) {
        state.byThread[body.thread_id] = state.byThread[body.thread_id] || [];
        state.byThread[body.thread_id].push(msg("user", body.message));
      }
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

// 15. thread survival: surviving remounts (same route, route back and forth, topbar, ctx.changed) and page reload
{
  const nowIso = new Date().toISOString();
  const { ctx } = make({
    state: {
      threads: [
        { id: "t1", title: "First thread", last_message_at: nowIso, status: "done" },
        { id: "t2", title: "Second thread", last_message_at: "2026-01-01T10:00:00Z", status: "done" },
      ],
      byThread: {
        t1: [msg("user", "Hello t1"), msg("agent", "Reply t1", [])],
        t2: [msg("user", "Hello t2"), msg("agent", "Reply t2", [])],
        tnew: [msg("user", "Starting new"), msg("agent", "New thread reply", [])],
      },
    },
    onSend: (body) => ({ messages: [msg("user", body.message), msg("agent", "Okay.", [])] }),
  });
  await ctx.refreshSession();
  let root = chat.render(ctx);
  await tick();
  // Click New chat
  await fire(button(root, "+ New chat"), "click");
  const threadAfterNew = ctx.state.threadId;
  const hashAfterNew = globalThis.location.hash;

  // Send a message
  const box = input(root);
  box.value = "Starting new";
  await fire(box, "keydown", { key: "Enter", shiftKey: false });
  await tick(80);
  const titleAfterSend = (find(root, (n) => n.className.includes("chat-title"))[0] || {}).textContent;
  const activeRailAfterSend = byClass(root, "rail-row").filter((n) => n.className.includes("active")).map((n) => n.textContent);

  // Remount 1: hashchange to same route #/chat
  globalThis.location.hash = "#/chat";
  root = chat.render(ctx);
  await tick();
  const threadSameRoute = ctx.state.threadId;
  const hashSameRoute = globalThis.location.hash;

  // Remount 2: route back and forth
  ctx.state.tier = "T2";
  root = chat.render(ctx);
  await tick();
  const threadRouteBack = ctx.state.threadId;

  // Remount 3: topbar refresh
  await ctx.refreshSession();
  const threadTopbar = ctx.state.threadId;

  // Remount 4: ctx.changed()
  ctx.changed();
  const threadChanged = ctx.state.threadId;

  // Page reload with hash
  globalThis.location.hash = "#/chat/" + threadAfterNew;
  const { ctx: reloadCtx } = make({
    state: {
      threads: [
        { id: threadAfterNew, title: "Starting new", last_message_at: nowIso, status: "done" },
        { id: "t1", title: "First thread", last_message_at: nowIso, status: "done" },
      ],
      byThread: {
        [threadAfterNew]: [msg("user", "Starting new"), msg("agent", "New thread reply", [])],
      },
    },
    onSend: () => ({ messages: [] }),
  });
  await reloadCtx.refreshSession();
  const reloadRoot = chat.render(reloadCtx);
  await tick();
  const threadReload = reloadCtx.state.threadId;
  const reloadTitle = (find(reloadRoot, (n) => n.className.includes("chat-title"))[0] || {}).textContent;
  const reloadActiveRail = byClass(reloadRoot, "rail-row").filter((n) => n.className.includes("active")).map((n) => n.textContent);

  out.threadSurvival = {
    threadAfterNew,
    hashAfterNew,
    titleAfterSend,
    activeRailAfterSend,
    threadSameRoute,
    hashSameRoute,
    threadRouteBack,
    threadTopbar,
    threadChanged,
    threadReload,
    reloadTitle,
    reloadActiveRail,
  };
}

// 16. deep link, unknown thread id fallback, and 404 stored id drop
{
  const nowIso = new Date().toISOString();
  // Case A: deep link to existing thread t2
  globalThis.location.hash = "#/chat/t2";
  const { ctx: ctxA } = make({
    state: {
      threads: [
        { id: "t1", title: "Thread 1", last_message_at: nowIso, status: "done" },
        { id: "t2", title: "Thread 2", last_message_at: "2026-01-01T10:00:00Z", status: "done" },
      ],
      byThread: {
        t2: [msg("user", "Hello t2"), msg("agent", "Reply t2", [])],
      },
    },
    onSend: () => ({ messages: [] }),
  });
  await ctxA.refreshSession();
  const rootA = chat.render(ctxA);
  await tick();
  const deepLinkSelected = ctxA.state.threadId;
  const deepLinkTitle = (find(rootA, (n) => n.className.includes("chat-title"))[0] || {}).textContent;
  const deepLinkBubbles = bubbles(rootA).map((b) => b.textContent);

  // Case B: deep link to unknown thread falls back to newest thread (t1) with no error
  globalThis.location.hash = "#/chat/unknown_thread_id";
  const { ctx: ctxB } = make({
    state: {
      threads: [
        { id: "t1", title: "Thread 1", last_message_at: nowIso, status: "done" },
        { id: "t2", title: "Thread 2", last_message_at: "2026-01-01T10:00:00Z", status: "done" },
      ],
      byThread: {
        t1: [msg("user", "Hello t1"), msg("agent", "Reply t1", [])],
      },
    },
    onSend: () => ({ messages: [] }),
  });
  await ctxB.refreshSession();
  const rootB = chat.render(ctxB);
  await tick();
  const unknownFallbackSelected = ctxB.state.threadId;
  const unknownFallbackTitle = (find(rootB, (n) => n.className.includes("chat-title"))[0] || {}).textContent;
  const unknownFallbackHash = globalThis.location.hash;
  const unknownStatus = (find(rootB, (n) => n.className.includes("msg"))[0] || {}).textContent;

  // Case C: 404 response for stored id drops it and falls back to newest thread
  globalThis.location.hash = "#/chat/t_missing";
  const { ctx: ctxC } = make({
    state: {
      notFoundThread: "t_missing",
      threads: [
        { id: "t1", title: "Thread 1", last_message_at: nowIso, status: "done" },
      ],
      byThread: {
        t1: [msg("user", "Hello t1"), msg("agent", "Reply t1", [])],
      },
    },
    onSend: () => ({ messages: [] }),
  });
  await ctxC.refreshSession();
  const rootC = chat.render(ctxC);
  await tick();
  const notFoundFallbackSelected = ctxC.state.threadId;
  const notFoundStatus = (find(rootC, (n) => n.className.includes("msg"))[0] || {}).textContent;
  const notFoundHash = globalThis.location.hash;

  out.deepLinkAnd404 = {
    deepLinkSelected,
    deepLinkTitle,
    deepLinkBubbles,
    unknownFallbackSelected,
    unknownFallbackTitle,
    unknownFallbackHash,
    unknownStatus,
    notFoundFallbackSelected,
    notFoundStatus,
    notFoundHash,
  };
}

// 17. job isolation: a running job of a thread is followed only in that thread
{
  const nowIso = new Date().toISOString();
  let t1RunningLive = false;
  let t1RunningStopBtn = false;
  let t2DuringRunLive = false;
  let t2DuringRunSendBtn = false;

  const { ctx } = make({
    state: {
      threads: [
        { id: "t1", title: "Thread 1", last_message_at: nowIso, status: "done" },
        { id: "t2", title: "Thread 2", last_message_at: "2026-01-01T10:00:00Z", status: "done" },
      ],
      byThread: {
        t1: [msg("user", "Job in t1"), msg("agent", "Earlier in t1", [])],
        t2: [msg("user", "Message in t2"), msg("agent", "Earlier in t2", [])],
      },
      jobScript: [
        { status: "running", phase: "rules_check" },
        { status: "running", phase: "sent" },
        { status: "succeeded", phase: "done" },
      ],
    },
    onSend: (body) => ({
      messages: [msg("user", body.message), msg("agent", "Finished in t1", [])],
    }),
  });
  globalThis.location.hash = "#/chat/t1";
  await ctx.refreshSession();
  const root = chat.render(ctx);
  await tick();

  const origJob = ctx.api.job;
  let pollCount = 0;
  ctx.api.job = async (id) => {
    pollCount++;
    if (pollCount === 1) {
      // In t1 while job is running
      t1RunningLive = byClass(root, "live").length > 0;
      t1RunningStopBtn = Boolean(find(root, (n) => n.className === "stop")[0]);
      // Switch to t2 while t1 is running
      const rows = byClass(root, "rail-row");
      if (rows[1]) {
        await fire(rows[1], "click");
        t2DuringRunLive = byClass(root, "live").length > 0;
        t2DuringRunSendBtn = Boolean(button(root, "Send"));
      }
    }
    return origJob(id);
  };

  const box = input(root);
  box.value = "Start background run in t1";
  await fire(box, "keydown", { key: "Enter", shiftKey: false });
  await tick(100);

  // While in t2, t1's completed message was NOT appended to t2
  const t2TextAfterJobDone = bubbles(root).map((b) => b.textContent);

  // Switch back to t1: completed job message is in t1
  const rows = byClass(root, "rail-row");
  if (rows[0]) await fire(rows[0], "click");
  await tick(50);
  const t1TextFinal = bubbles(root).map((b) => b.textContent);

  out.jobIsolation = {
    t1RunningLive,
    t1RunningStopBtn,
    t2DuringRunLive,
    t2DuringRunSendBtn,
    t2TextAfterJobDone,
    t1TextFinal,
  };
}

// 18. nClicksNewChat: N clicks on New chat produce 1 thread, 1 post, composer focused
{
  const nowIso = new Date().toISOString();
  const { srv, ctx } = make({
    state: {
      threads: [
        { id: "t1", title: "Thread 1", last_message_at: nowIso, status: "done" },
      ],
      byThread: {
        t1: [msg("user", "Hello t1"), msg("agent", "Reply t1", [])],
      },
    },
    onSend: () => ({ messages: [] }),
  });
  globalThis.location.hash = "#/chat/t1";
  await ctx.refreshSession();
  const root = chat.render(ctx);
  await tick();

  const newBtn = button(root, "+ New chat");
  const box = input(root);

  // Click '+ New chat' 5 times concurrently / in rapid succession
  await Promise.all([
    fire(newBtn, "click"),
    fire(newBtn, "click"),
    fire(newBtn, "click"),
    fire(newBtn, "click"),
    fire(newBtn, "click"),
  ]);
  await tick(50);

  const posts = postsTo(srv, "/chat/threads").length;
  const focused = globalThis.document.activeElement === box;
  const currentThreadId = ctx.state.threadId;

  // Another click while on empty thread does not POST
  await fire(newBtn, "click");
  await tick(50);
  const postsAfterEmptyClick = postsTo(srv, "/chat/threads").length;

  out.nClicksNewChat = {
    postCalls: posts,
    postCallsAfterEmptyClick: postsAfterEmptyClick,
    focused,
    threadId: currentThreadId,
    threadCount: srv.state.threads.length,
  };
}

// 19. staleHistoryIgnored: delayed history for t1 does not paint over t2
{
  const nowIso = new Date().toISOString();
  let resolveT1History;
  const t1Promise = new Promise((resolve) => {
    resolveT1History = resolve;
  });

  const { ctx } = make({
    state: {
      threads: [
        { id: "t1", title: "Thread 1", last_message_at: nowIso, status: "done" },
        { id: "t2", title: "Thread 2", last_message_at: "2026-01-01T10:00:00Z", status: "done" },
      ],
      byThread: {
        t1: [msg("user", "Hello from thread 1"), msg("agent", "Agent reply in thread 1", [])],
        t2: [msg("user", "Hello from thread 2"), msg("agent", "Agent reply in thread 2", [])],
      },
    },
    onSend: () => ({ messages: [] }),
  });

  const origChat = ctx.api.chat;
  let t1HistoryCalled = 0;
  ctx.api.chat = async (limit, tid) => {
    if (tid === "t1") {
      t1HistoryCalled++;
      await t1Promise;
      return origChat(limit, tid);
    }
    return origChat(limit, tid);
  };

  globalThis.location.hash = "#/chat/t1";
  await ctx.refreshSession();
  const root = chat.render(ctx);
  // Wait until t1 history is in-flight (waiting for t1Promise)
  while (t1HistoryCalled === 0) {
    await tick(10);
  }

  // Switch to t2 immediately while t1 history is still in-flight
  const rows = byClass(root, "rail-row");
  const t2Row = rows.find((r) => r.textContent.includes("Thread 2"));
  await fire(t2Row, "click");
  await tick(30);

  // Resolve delayed t1 history
  resolveT1History();
  await tick(50);

  const t2BubblesAfterT1 = bubbles(root).map((b) => b.textContent);

  out.staleHistoryIgnored = {
    t1HistoryCalled,
    stillShowsT2: t2BubblesAfterT1.some((t) => t.includes("Thread 2") || t.includes("thread 2")),
    doesNotShowT1: !t2BubblesAfterT1.some((t) => t.includes("Thread 1") || t.includes("thread 1")),
    currentThreadId: ctx.state.threadId,
  };
}

// 20. titleRefreshAfterFirstMessage: rail and header title update immediately after first message is accepted
{
  const nowIso = new Date().toISOString();
  let railTitleBeforeJobDone = null;
  let headerTitleBeforeJobDone = null;

  const { ctx } = make({
    state: {
      threads: [
        { id: "t_existing", title: "Existing chat", last_message_at: nowIso, status: "done" },
      ],
      byThread: {
        t_existing: [msg("user", "Earlier"), msg("agent", "Done", [])],
      },
      jobScript: [
        { status: "running", phase: "rules_check" },
        { status: "running", phase: "sent" },
        { status: "succeeded", phase: "done" },
      ],
    },
    onSend: (body) => ({
      messages: [msg("user", body.message), msg("agent", "Here is your balance.", [])],
    }),
  });
  globalThis.location.hash = "#/chat/t_existing";
  await ctx.refreshSession();
  const root = chat.render(ctx);
  await tick();

  // Click + New chat to create a new thread
  await fire(button(root, "+ New chat"), "click");
  await tick(30);

  const newThreadId = ctx.state.threadId;

  // Intercept api.job to inspect rail and header titles while job is still running (poll 1)
  const origJob = ctx.api.job;
  let jobPollCount = 0;
  ctx.api.job = async (id) => {
    jobPollCount++;
    if (jobPollCount === 1) {
      const activeRow = byClass(root, "rail-row").find((r) => r.className.includes("active"));
      railTitleBeforeJobDone = activeRow ? activeRow.textContent : "";
      headerTitleBeforeJobDone = (find(root, (n) => n.className.includes("chat-title"))[0] || {}).textContent;
    }
    return origJob(id);
  };

  const box = input(root);
  box.value = "What is my balance?";
  await fire(box, "keydown", { key: "Enter", shiftKey: false });
  await tick(100);

  out.titleRefreshAfterFirstMessage = {
    newThreadId,
    railTitleBeforeJobDone,
    headerTitleBeforeJobDone,
    updatedImmediately: Boolean(
      headerTitleBeforeJobDone &&
      headerTitleBeforeJobDone.includes("What is my balance") &&
      railTitleBeforeJobDone &&
      railTitleBeforeJobDone.includes("What is my balance")
    ),
  };
}

console.log(JSON.stringify(out));
