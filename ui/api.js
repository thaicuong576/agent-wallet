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
    extra: [],
    jobs: [],
    seeded: false,
  };

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
        return j;
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
    ips: () => call("ips", "GET", ROUTES.ips),
    decisions: (limit = 50) =>
      call("decisions", "GET", ROUTES.decisions + "?limit=" + encodeURIComponent(limit)),
    stop: () => call("stop", "POST", ROUTES.stop, {}),
    release: (stepUp) => call("release", "POST", ROUTES.release, { headers: withStepUp(stepUp) }),
  };
}

export { ROUTES };
