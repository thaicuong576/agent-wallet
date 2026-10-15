// The only module that talks to the network. Every path is a design 12.2 route.
// `fixtures: true` answers from ui/fixtures/*.json and an in-memory state, with no backend.
// The session is an HttpOnly cookie set by the server: nothing is stored or read here.

const ROUTES = {
  nonce: "/auth/nonce",
  verify: "/auth/verify",
  logout: "/auth/logout",
  stepUp: "/auth/step-up",
  me: "/me",
  agent: "/agent",
  topups: "/topups",
  topup: "/topups/{tx_hash}",
  mint: "/agent/mint",
  withdraw: "/agent/withdraw",
  presets: "/presets",
  runs: "/runs",
  chat: "/chat",
  threads: "/chat/threads",
  suggestions: "/chat/suggestions",
  limits: "/limits",
  cancel: "/jobs/{job_id}/cancel",
  job: "/jobs/{job_id}",
  jobs: "/jobs",
  auditVerify: "/audit/verify",
  portfolio: "/portfolio",
  tier: "/tier",
  ips: "/ips",
  decisions: "/decisions",
  stop: "/emergency-stop",
  release: "/emergency-stop/release",
  approve: "/orders/{order_id}/approve",
  reject: "/orders/{order_id}/reject",
};

export class ApiError extends Error {
  constructor(status, slug, title, detail, extra = {}) {
    super(title || "Request failed");
    this.status = status;
    this.slug = slug || "";
    this.detail = detail || "";
    // The whole problem body is kept: a 409 carries the active job_id, a 429 a Retry-After.
    this.problem = extra.problem || null;
    this.retryAfter = extra.retryAfter || null;
  }
}

function slugOf(problem) {
  const prefix = "urn:agent-wallet:problem:";
  const t = problem && typeof problem.type === "string" ? problem.type : "";
  return t.startsWith(prefix) ? t.slice(prefix.length) : "";
}

const HEX64 = /^0x[0-9a-f]{64}$/;
const JOB_ID = /^[0-9a-f]{32}$/;
const ORDER_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

// `scenario: "hostile"` (fixtures only) starts with a blocked hostile run already in the feed.
// `onUnauthorized` is told about every 401 so the shell can drop its signed-in state.
export function createApi({
  fixtures = false,
  demo = false,
  scenario = "",
  fetchImpl,
  onUnauthorized,
} = {}) {
  const doFetch = fetchImpl || ((...a) => globalThis.fetch(...a));

  async function real(method, path, { body, headers } = {}) {
    const h = { Accept: "application/json", "X-AW-CSRF": "1", ...(headers || {}) };
    const init = { method, credentials: "same-origin", headers: h };
    if (body !== undefined) {
      h["Content-Type"] = "application/json";
      init.body = JSON.stringify(body);
    }
    const res = await doFetch(path, init);
    let data = null;
    try {
      data = await res.json();
    } catch (_) {
      data = null;
    }
    if (!res.ok) {
      const retryAfter = res.headers && res.headers.get ? res.headers.get("Retry-After") : null;
      if (res.status === 401 && onUnauthorized) onUnauthorized(path);
      throw new ApiError(res.status, slugOf(data), data && data.title, data && data.detail, {
        problem: data,
        retryAfter,
      });
    }
    return data;
  }

  // ---- fixtures -----------------------------------------------------------------------
  const fx = {
    cache: new Map(),
    signedIn: demo,
    topped: demo,
    minted: demo,
    preset: demo ? "conservative" : null,
    stopped: false,
    // `scenario=t0` starts a signed-in demo at tier T0, so the activate control can be shown.
    tier: demo && scenario !== "t0" ? "T2" : "T0",
    extra: [],
    jobs: [],
    chat: [],
    chatSeq: 0,
    seeded: false,
    threads: null,
    threadSeq: 0,
  };
  // In a real browser the fixture jobs advance one phase per poll so the phase strip and Stop can
  // be seen; under node (the tests) there is no `window` and they answer at once.
  const paced = fixtures && typeof globalThis.window !== "undefined";
  const PHASES = ["queued", "rules_check", "rules_check", "sent", "confirming"];

  async function file(name) {
    if (!fx.cache.has(name)) {
      const res = await doFetch("fixtures/" + name + ".json");
      if (!res.ok) throw new ApiError(res.status, "", "Fixture " + name + " is missing");
      fx.cache.set(name, await res.json());
    }
    return JSON.parse(JSON.stringify(fx.cache.get(name)));
  }

  function needSession() {
    if (!fx.signedIn) throw new ApiError(401, "unauthenticated", "Sign in required");
  }

  async function fixtureCall(key, arg) {
    switch (key) {
      case "nonce":
        return file("nonce");
      case "verify":
        fx.signedIn = true;
        return file("me");
      case "logout":
        fx.signedIn = false;
        return {};
      case "stepUp":
        needSession();
        return file("step-up");
      case "me":
        needSession();
        return file("me");
      case "agent": {
        needSession();
        const a = await file("agent");
        if (!fx.topped) a.eth_wei = "0";
        if (!fx.minted) {
          a.usdc = { value: "0", decimals: 6 };
          a.ausdc = { value: "0", decimals: 6 };
        }
        return a;
      }
      case "topup":
        needSession();
        fx.topped = true;
        return file("topup");
      case "mint":
        needSession();
        fx.minted = true;
        return { job_id: "a".repeat(32), status: "queued" };
      case "withdraw":
        needSession();
        return { job_id: "e".repeat(32), status: "queued" };
      case "approve":
      case "reject":
        needSession();
        return { order_id: arg, status: key === "approve" ? "approved" : "rejected" };
      case "presets":
        return file("presets");
      case "policy": {
        needSession();
        const p = await file("presets");
        const item = p.items.find((x) => x.preset_id === (arg && arg.preset_id));
        if (!item) throw new ApiError(404, "", "Unknown preset");
        fx.preset = item.preset_id;
        return { schema_version: 1, preset_id: item.preset_id, ips_hash: item.ips_hash };
      }
      case "getPolicy":
        needSession();
        return { preset_id: fx.preset };
      case "run": {
        needSession();
        if (fx.stopped) throw new ApiError(409, "stopped", "Stopped");
        const hostile = arg && arg.planner === "scripted_hostile";
        const recs = await file(hostile ? "run-hostile" : "run-benign");
        fx.extra.push(...recs);
        const job = {
          job_id: (hostile ? "b" : "c").repeat(31) + String(fx.jobs.length % 10),
          kind: "run",
          status: hostile ? "denied" : "succeeded",
          planner_client: "scripted",
          order_ids: recs.map((r) => r.order_id).filter(Boolean),
          error_code: null,
          created_at: "2026-10-14T12:00:00Z",
          finished_at: "2026-10-14T12:00:02Z",
        };
        fx.jobs.push(job);
        return { job_id: job.job_id, status: "queued" };
      }
      case "chatSend": {
        needSession();
        if (fx.stopped) throw new ApiError(409, "stopped", "Stopped");
        await seedThreads();
        return chatFixture(String(arg.message || ""), arg.threadId);
      }
      case "chat": {
        needSession();
        await seedThreads();
        const want = (arg && arg.threadId) || null;
        const items = fx.chat.filter((m) => (m.thread_id || null) === want);
        return { items: items.slice(-(Number(arg && arg.limit) || 50)).map(withPhase) };
      }
      case "threads": {
        needSession();
        await seedThreads();
        const items = fx.threads.slice();
        items.sort((a, b) => b.last_message_at.localeCompare(a.last_message_at));
        return { items };
      }
      case "newThread": {
        needSession();
        await seedThreads();
        const t = { id: "t" + ++fx.threadSeq, title: "New chat", last_message_at: nowIso(), status: "done" };
        fx.threads.push(t);
        return { id: t.id, title: t.title };
      }
      case "limits": {
        needSession();
        const l = await file("limits");
        l.used_today_usd = spentToday().toFixed(2);
        l.emergency_stop = fx.stopped ? "stopped" : "ready";
        return l;
      }
      case "suggestions": {
        needSession();
        return file(fx.stopped ? "suggestions-stopped" : "suggestions");
      }
      case "cancel": {
        needSession();
        const j = fx.jobs.find((x) => x.job_id === arg);
        if (!j) throw new ApiError(404, "not-found", "Unknown job");
        if (!j.live || !["queued", "rules_check"].includes(jobPhase(j))) {
          throw new ApiError(409, "not-cancellable", "Job cannot be cancelled", "", {
            problem: { error: "not-cancellable", job_id: arg },
          });
        }
        j.cancelled = true;
        return { job_id: arg, status: "cancelling" };
      }
      case "job": {
        needSession();
        if (arg === "a".repeat(32)) {
          return {
            job_id: arg,
            kind: "mint",
            status: "succeeded",
            planner_client: "none",
            order_ids: [],
            error_code: null,
            created_at: "2026-10-14T11:59:00Z",
            finished_at: "2026-10-14T11:59:03Z",
          };
        }
        if (arg === "e".repeat(32)) {
          return {
            job_id: arg,
            kind: "withdraw",
            status: "succeeded",
            planner_client: "none",
            order_ids: [],
            error_code: null,
            created_at: "2026-10-14T12:10:00Z",
            finished_at: "2026-10-14T12:10:03Z",
          };
        }
        const j = fx.jobs.find((x) => x.job_id === arg);
        if (!j) throw new ApiError(404, "", "Unknown job");
        if (paced && j.live) await new Promise((r) => setTimeout(r, 700));
        return jobView(j);
      }
      case "jobs":
        needSession();
        return { items: fx.jobs.slice().reverse() };
      case "auditVerify":
        needSession();
        return file("audit-verify");
      case "portfolio":
        needSession();
        return file("portfolio");
      case "ips":
        needSession();
        return file("ips");
      case "tier": {
        needSession();
        const t = await file("tier");
        t.tier = fx.tier;
        t.emergency_stop = fx.stopped;
        return t;
      }
      case "setTier": {
        needSession();
        fx.tier = arg;
        const t = await file("tier");
        t.tier = fx.tier;
        t.emergency_stop = fx.stopped;
        return t;
      }
      case "decisions": {
        needSession();
        const d = await file("decisions");
        if (scenario === "hostile" && !fx.seeded) {
          fx.seeded = true;
          const recs = await file("run-hostile");
          fx.extra.push(...recs);
          fx.jobs.push({
            job_id: "d".repeat(32),
            kind: "run",
            status: "denied",
            planner_client: "scripted",
            order_ids: recs.map((r) => r.order_id),
            error_code: null,
            created_at: "2026-10-14T12:05:00Z",
            finished_at: "2026-10-14T12:05:02Z",
          });
        }
        d.items = d.items.concat(fx.extra).map((r, i) => ({ ...r, seq: i }));
        return d;
      }
      case "stop": {
        needSession();
        fx.stopped = true;
        const rec = await file("record-stop");
        fx.extra.push(rec);
        return rec;
      }
      case "release": {
        needSession();
        fx.stopped = false;
        const rec = await file("record-release");
        fx.extra.push(rec);
        return rec;
      }
      default:
        throw new ApiError(404, "", "No fixture for " + key);
    }
  }

  // Scripted conversation for the no-backend mode: a balance question, a deposit request (blocked
  // at tier T0, executed above it) and anything else. Shapes copy GET /chat.
  const FX_TX = "0x" + "cd".repeat(32);
  const usdc10 = { value: "10000000", decimals: 6 };
  const fxStep = (o) => ({
    order_id: null,
    token: "USDC",
    amount: usdc10,
    reason: null,
    rule_codes: [],
    tx_hash: null,
    ...o,
  });
  const nowIso = () => new Date().toISOString().replace(/\.\d+Z$/, "Z");
  const dollars = (a) => (a && a.value ? Number(a.value) / 10 ** (a.decimals ?? 6) : 0);
  const spentToday = () =>
    10 +
    fx.chat.reduce(
      (n, m) =>
        n +
        m.steps
          .filter((s) => s.outcome === "executed" && s.function === "supply")
          .reduce((k, s) => k + dollars(s.amount), 0),
      0,
    );

  // Threads: a few seeded ones so the rail shows every status, then the ones the owner makes.
  async function seedThreads() {
    if (fx.threads) return;
    const seed = await file("threads");
    const at = (mins) => new Date(Date.now() - mins * 60000).toISOString().replace(/\.\d+Z$/, "Z");
    fx.threads = seed.items.map(({ minutes_ago: m, ...t }) => ({ ...t, last_message_at: at(m) }));
    fx.threadSeq = fx.threads.length;
    for (const m of seed.messages) {
      const t = fx.threads.find((x) => x.id === m.thread_id);
      fx.chat.push({ ...m, created_at: t.last_message_at });
    }
  }

  // A fixture chat run walks PHASES one poll at a time, then finishes. `live` is true only while
  // that walk is in progress.
  function jobPhase(j) {
    if (!j.live) return "done";
    return PHASES[Math.min(j.polls, PHASES.length - 1)];
  }
  function jobView(j) {
    if (!j.live) return j;
    const phase = jobPhase(j);
    const finished = j.polls >= PHASES.length;
    j.polls += 1;
    if (j.cancelled) {
      finishCancelled(j);
      return { ...j, phase: "done" };
    }
    if (finished) {
      j.live = false;
      return { ...j, phase: "done" };
    }
    return { ...j, status: phase === "queued" ? "queued" : "running", phase, finished_at: null };
  }
  // A cancel records every step as skipped by the owner.
  function finishCancelled(j) {
    j.live = false;
    j.status = "denied";
    j.error_code = "OWNER_CANCELLED";
    const m = fx.chat.find((x) => x.job_id === j.job_id && x.role === "agent");
    if (!m) return;
    m.text = "You cancelled the run, so the steps were not attempted.";
    m.steps = m.steps.map((s) => ({
      ...s,
      outcome: "skipped",
      tx_hash: null,
      rule_codes: [],
      explanation: "Not attempted: you cancelled this run.",
      reason: "skipped " + s.function + " (10 USDC): owner-cancelled",
    }));
  }
  // While a fixture job is live its agent message reports the live phase.
  function withPhase(m) {
    if (m.role !== "agent") return m;
    const j = fx.jobs.find((x) => x.job_id === m.job_id);
    return { ...m, phase: j && j.live ? jobPhase(j) : "done" };
  }

  function chatFixture(text, threadId) {
    const seq = ++fx.chatSeq;
    const jobId = "d" + String(seq).padStart(31, "0");
    const now = "2026-10-14T12:" + String(seq % 60).padStart(2, "0") + ":00Z";
    let agent = "";
    let steps = [];
    let status = "succeeded";
    if (/balance|how much/i.test(text)) {
      agent = "No transaction was made.\n\nAgent says: Wallet: 1000 USDC idle, 0 USDC in Aave.";
    } else if (/put|supply|deposit|invest|aave|work/i.test(text)) {
      if (fx.tier === "T0") {
        status = "denied";
        agent =
          "I did not act: your agent is in read-only mode (tier T0), so it cannot act yet. Activate it to let it work inside your policy.";
        steps = [
          fxStep({
            function: "approve",
            outcome: "denied",
            rule_codes: ["TIER.READ_ONLY"],
            reason: "Tier T0 is read-only; no order is executed.",
            explanation:
              "The agent could not approve 10 USDC for the Aave pool: your agent is in read-only mode (tier T0). Nothing was sent.",
          }),
          fxStep({
            function: "supply",
            outcome: "skipped",
            explanation: "Not attempted: the previous step was blocked.",
          }),
        ];
      } else {
        agent =
          "I approved 10 USDC for the Aave pool and supplied 10 USDC to Aave. Transactions: " +
          FX_TX +
          ", " +
          FX_TX +
          ".";
        steps = [
          fxStep({
            function: "approve",
            outcome: "executed",
            tx_hash: FX_TX,
            explanation: "Approved 10 USDC for the Aave pool.",
          }),
          fxStep({
            function: "supply",
            outcome: "executed",
            tx_hash: FX_TX,
            explanation: "Supplied 10 USDC to Aave.",
          }),
        ];
      }
    } else {
      agent =
        "No transaction was made.\n\nAgent says: I can answer questions about your balance and put idle USDC to work in Aave.";
    }
    const thread = threadId || null;
    const user = { id: "m" + seq + "u", role: "user", text, job_id: jobId, created_at: now, steps: [] };
    fx.chat.push({ ...user, thread_id: thread });
    fx.chat.push({ ...user, id: "m" + seq + "a", role: "agent", text: agent, steps, thread_id: thread });
    const row = fx.threads && thread ? fx.threads.find((t) => t.id === thread) : null;
    if (row) {
      if (row.title === "New chat") row.title = text.slice(0, 40);
      row.last_message_at = nowIso();
      row.status = status === "denied" ? "blocked" : "done";
    }
    fx.jobs.push({
      live: paced && steps.some((s) => s.outcome === "executed"),
      polls: 0,
      job_id: jobId,
      kind: "chat",
      status,
      planner_client: "scripted",
      order_ids: [],
      error_code: status === "denied" ? "TIER.READ_ONLY" : null,
      created_at: now,
      finished_at: now,
    });
    return { job_id: jobId, status: "queued" };
  }

  function call(key, method, path, opts, arg) {
    return fixtures ? fixtureCall(key, arg) : real(method, path, opts);
  }

  // There is no policy route in contracts/openapi.yaml yet (ticket A4), so outside fixtures
  // mode these two never reach the network.
  const noPolicyRoute = () =>
    Promise.reject(new ApiError(501, "not-available", "Choosing a preset is not available yet"));

  const withStepUp = (stepUp) => (stepUp ? { "X-Step-Up": stepUp } : {});

  return {
    fixtures,
    nonce: (address) => call("nonce", "POST", ROUTES.nonce, { body: { address } }),
    verify: (address, nonce, signature) =>
      call("verify", "POST", ROUTES.verify, { body: { address, nonce, signature } }),
    logout: () => call("logout", "POST", ROUTES.logout, {}),
    stepUp: (action, params) =>
      call("stepUp", "POST", ROUTES.stepUp, { body: { action, params: params || {} } }),
    me: () => call("me", "GET", ROUTES.me),
    agent: () => call("agent", "GET", ROUTES.agent),
    registerTopup: (txHash) => {
      if (!HEX64.test(txHash)) return Promise.reject(new ApiError(0, "", "Not a transaction hash"));
      return call("topup", "POST", ROUTES.topups, { body: { tx_hash: txHash } });
    },
    topupStatus: (txHash) =>
      HEX64.test(txHash)
        ? call("topup", "GET", ROUTES.topup.replace("{tx_hash}", txHash))
        : Promise.reject(new ApiError(0, "", "Not a transaction hash")),
    mint: (amountUsdc) =>
      call("mint", "POST", ROUTES.mint, { body: { amount_usdc: String(amountUsdc) } }),
    presets: () => call("presets", "GET", ROUTES.presets),
    withdraw: (token, amount, stepUp) =>
      call("withdraw", "POST", ROUTES.withdraw, {
        body: { token, amount },
        headers: withStepUp(stepUp),
      }),
    approve: (orderId, stepUp) =>
      ORDER_ID.test(orderId)
        ? call(
            "approve",
            "POST",
            ROUTES.approve.replace("{order_id}", orderId),
            { headers: withStepUp(stepUp) },
            orderId,
          )
        : Promise.reject(new ApiError(0, "", "Not an order id")),
    reject: (orderId) =>
      ORDER_ID.test(orderId)
        ? call("reject", "POST", ROUTES.reject.replace("{order_id}", orderId), {}, orderId)
        : Promise.reject(new ApiError(0, "", "Not an order id")),
    policy: () => (fixtures ? fixtureCall("getPolicy") : noPolicyRoute()),
    setPreset: (presetId) =>
      fixtures ? fixtureCall("policy", { preset_id: presetId }) : noPolicyRoute(),
    run: (opts) => call("run", "POST", ROUTES.runs, { body: opts || {} }, opts || {}),
    // `threadId` is optional everywhere: absent means the implicit default conversation.
    chatSend: (message, threadId) =>
      call(
        "chatSend",
        "POST",
        ROUTES.chat,
        { body: threadId ? { message, thread_id: threadId } : { message } },
        { message, threadId },
      ),
    chat: (limit = 50, threadId) =>
      call(
        "chat",
        "GET",
        ROUTES.chat +
          "?limit=" +
          encodeURIComponent(limit) +
          (threadId ? "&thread_id=" + encodeURIComponent(threadId) : ""),
        {},
        { limit, threadId },
      ),
    threads: (limit = 30) =>
      call("threads", "GET", ROUTES.threads + "?limit=" + encodeURIComponent(limit)),
    newThread: () => call("newThread", "POST", ROUTES.threads, { body: {} }),
    suggestions: () => call("suggestions", "GET", ROUTES.suggestions),
    limits: () => call("limits", "GET", ROUTES.limits),
    cancelJob: (jobId) =>
      JOB_ID.test(jobId)
        ? call("cancel", "POST", ROUTES.cancel.replace("{job_id}", jobId), {}, jobId)
        : Promise.reject(new ApiError(0, "", "Not a job id")),
    job: (jobId) =>
      JOB_ID.test(jobId)
        ? call("job", "GET", ROUTES.job.replace("{job_id}", jobId), {}, jobId)
        : Promise.reject(new ApiError(0, "", "Not a job id")),
    jobs: () => call("jobs", "GET", ROUTES.jobs),
    auditVerify: () => call("auditVerify", "GET", ROUTES.auditVerify),
    portfolio: () => call("portfolio", "GET", ROUTES.portfolio),
    // GET /tier is the single-owner global state; in the webapp the tier and the stop flag of the
    // signed-in owner come with GET /me, so outside fixtures mode that is the route used.
    tier: () =>
      fixtures
        ? fixtureCall("tier")
        : real("GET", ROUTES.me).then((m) => ({
            tier: m.tier,
            emergency_stop: Boolean(m.emergency_stop),
          })),
    // Raising the tier needs `stepUp` (X-Step-Up for action tier_raise); lowering does not.
    setTier: (tier, stepUp) =>
      call(
        "setTier",
        "PUT",
        ROUTES.tier,
        { body: { schema_version: 1, tier }, headers: withStepUp(stepUp) },
        tier,
      ),
    ips: () => call("ips", "GET", ROUTES.ips),
    decisions: (limit = 50) =>
      call("decisions", "GET", ROUTES.decisions + "?limit=" + encodeURIComponent(limit)),
    stop: () => call("stop", "POST", ROUTES.stop, {}),
    release: (stepUp) => call("release", "POST", ROUTES.release, { headers: withStepUp(stepUp) }),
  };
}

export { ROUTES };
