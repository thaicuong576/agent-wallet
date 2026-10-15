// Shell: hash router, shared state, top bar (tier chip and emergency stop), wallet and API wiring.
// Every API string is written with textContent. The testnet notice is static markup in
// index.html and is never touched from here.
import { createApi, ApiError } from "./api.js";
import { explainError } from "./explain.js";
import { applyTopbar, retryText } from "./gauges.js";
import { discoverInjected } from "./wallet/injected.js";
import { isUserRejection } from "./wallet/adapter.js";
import { TestWallet } from "./wallet/test.js";
import * as onboard from "./views/onboard.js";
import * as dashboard from "./views/dashboard.js";
import * as feed from "./views/feed.js";
import * as approvals from "./views/approvals.js";

export function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined && text !== null) n.textContent = String(text);
  return n;
}

// Plain-language text for any failure. Wallet rejections are not failures.
export function describeError(err) {
  if (isUserRejection(err)) return "You declined in your wallet. Nothing was sent.";
  if (err instanceof ApiError) {
    if (err.slug === "rate-limited") return "Too many requests. " + retryText(err.retryAfter);
    if (err.slug) return explainError(err.slug);
    if (err.status === 401) return explainError("unauthenticated");
    if (err.status === 409) return explainError("job-in-progress");
    if (err.status === 429) return "Too many requests. " + retryText(err.retryAfter);
    if (err.status === 503) return explainError("custody-unavailable");
    return err.message + (err.detail ? ": " + err.detail : "");
  }
  return "Something went wrong: " + (err && err.message ? err.message : "unknown error");
}

const TERMINAL = new Set(["succeeded", "denied", "failed", "interrupted"]);
// Design section 10: poll every 2 s, backing off to 5 s.
export const POLL_START_MS = 2000;
export const POLL_MAX_MS = 5000;
export const pollDelay = (attempt) => Math.min(POLL_START_MS + attempt * 1000, POLL_MAX_MS);

export function makeCtx({ api, wallet, sleep }) {
  const state = { me: null, tier: null };
  const wait = sleep || ((ms) => new Promise((r) => setTimeout(r, api.fixtures ? 0 : ms)));
  const listeners = new Set();
  const ctx = {
    el,
    api,
    wallet,
    state,
    describeError,
    go: (hash) => {
      if (globalThis.location) globalThis.location.hash = hash;
    },
    onChange: (fn) => listeners.add(fn),
    changed: () => listeners.forEach((fn) => fn()),
    wait,
    // `onUpdate` sees every poll result so a view can show "queued" and "running".
    async pollJob(jobId, onUpdate) {
      let job = null;
      for (let i = 0; i < 80; i++) {
        job = await api.job(jobId);
        if (onUpdate) onUpdate(job);
        if (TERMINAL.has(job.status)) return job;
        await wait(pollDelay(i));
      }
      return job;
    },
    // A 401 from any route means the cookie is gone: drop the signed-in state once.
    signedOut() {
      if (!state.me) return;
      state.me = null;
      state.tier = null;
      ctx.changed();
    },
    // Fresh signature for one named action (design 6.2). Returns the X-Step-Up header value.
    async stepUp(action, params) {
      if (!wallet || !state.me) throw new Error("Connect your wallet first.");
      const ch = await api.stepUp(action, params);
      const sig = await wallet.signMessage(state.me.address, ch.message);
      return ch.challenge_id + "." + sig;
    },
    async refreshSession() {
      try {
        state.me = await api.me();
      } catch (e) {
        state.me = null;
      }
      return state.me;
    },
  };
  return ctx;
}

const VIEWS = { onboard, dashboard, feed, approvals };

function boot() {
  const params = new URLSearchParams(globalThis.location.search);
  const fixtures = params.get("fixtures") === "1";
  let ctx = null;
  const api = createApi({
    onUnauthorized: () => ctx && ctx.signedOut(),
    fixtures,
    demo: fixtures && params.get("demo") === "1",
    scenario: fixtures ? params.get("scenario") || "" : "",
  });
  const root = document.getElementById("view");
  const chip = document.getElementById("tier-chip");
  const stopBtn = document.getElementById("btn-stop");
  const stopMsg = document.getElementById("stop-msg");
  const modeChip = document.getElementById("mode-chip");
  if (fixtures) {
    modeChip.textContent = "Demo data (fixtures), not live";
    modeChip.hidden = false;
  }

  const bar = { chip, stopBtn, stopMsg, masthead: document.getElementById("masthead") };
  async function topbar() {
    const me = await ctx.refreshSession();
    if (!me) {
      applyTopbar(bar, null);
      return;
    }
    try {
      ctx.state.tier = await api.tier();
    } catch (e) {
      ctx.state.tier = null;
      applyTopbar(bar, "unavailable");
      return;
    }
    applyTopbar(bar, ctx.state.tier);
  }

  stopBtn.addEventListener("click", async () => {
    stopBtn.disabled = true;
    stopMsg.textContent = "Working…";
    let after = "";
    try {
      if (stopBtn.dataset.stopped === "1") {
        await api.release(await ctx.stepUp("release_stop", {}));
        after = "Stop released.";
      } else {
        await api.stop();
      }
    } catch (e) {
      after = describeError(e);
    }
    await topbar();
    // While frozen the top bar keeps its own FROZEN text; otherwise show the outcome.
    if (after && stopMsg.dataset.frozen !== "1") stopMsg.textContent = after;
    else if (after) stopMsg.textContent = after + " " + stopMsg.textContent;
    ctx.changed();
  });

  async function show() {
    const name = (globalThis.location.hash || "#/onboard").replace(/^#\//, "");
    const view = VIEWS[name] || VIEWS.onboard;
    for (const a of document.querySelectorAll("nav a")) {
      const on = a.getAttribute("href") === "#/" + (VIEWS[name] ? name : "onboard");
      if (on) a.setAttribute("aria-current", "page");
      else a.removeAttribute("aria-current");
    }
    root.replaceChildren(view.render(ctx));
  }

  (async () => {
    const wallet = fixtures ? new TestWallet() : await discoverInjected(globalThis);
    ctx = makeCtx({ api, wallet });
    ctx.onChange(topbar);
    globalThis.addEventListener("hashchange", show);
    show();
    topbar();
  })();
}

if (globalThis.document && globalThis.document.getElementById("view")) boot();
