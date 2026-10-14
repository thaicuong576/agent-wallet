// Shell: hash router, shared state, top bar (tier chip and emergency stop), wallet and API wiring.
// Every API string is written with textContent. The testnet notice is static markup in
// index.html and is never touched from here.
import { createApi, ApiError } from "./api.js";
import { explainError } from "./explain.js";
import { discoverInjected } from "./wallet/injected.js";
import { isUserRejection } from "./wallet/adapter.js";
import { TestWallet } from "./wallet/test.js";
import * as onboard from "./views/onboard.js";
import * as dashboard from "./views/dashboard.js";
import * as feed from "./views/feed.js";

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
    if (err.slug) return explainError(err.slug);
    if (err.status === 401) return explainError("unauthenticated");
    if (err.status === 429) return explainError("rate-limited");
    return err.message + (err.detail ? ": " + err.detail : "");
  }
  return "Something went wrong: " + (err && err.message ? err.message : "unknown error");
}

const TERMINAL = new Set(["succeeded", "denied", "failed", "interrupted"]);

export function makeCtx({ api, wallet, delayMs = 1500 }) {
  const state = { me: null, tier: null };
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
    async pollJob(jobId) {
      let job = null;
      for (let i = 0; i < 40; i++) {
        job = await api.job(jobId);
        if (TERMINAL.has(job.status)) return job;
        await new Promise((r) => setTimeout(r, api.fixtures ? 0 : delayMs));
      }
      return job;
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

const VIEWS = { onboard, dashboard, feed };

function boot() {
  const params = new URLSearchParams(globalThis.location.search);
  const fixtures = params.get("fixtures") === "1";
  const api = createApi({
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

  let ctx = null;

  async function topbar() {
    const me = await ctx.refreshSession();
    if (!me) {
      chip.textContent = "Not connected";
      stopBtn.disabled = true;
      stopBtn.textContent = "Emergency stop";
      return;
    }
    try {
      ctx.state.tier = await api.tier();
    } catch (e) {
      ctx.state.tier = null;
      chip.textContent = "Tier unavailable";
      stopBtn.disabled = false;
      return;
    }
    const t = ctx.state.tier;
    chip.textContent = "Tier " + t.tier + (t.emergency_stop ? " · stopped" : "");
    stopBtn.disabled = false;
    stopBtn.textContent = t.emergency_stop ? "Release stop (needs signature)" : "Emergency stop";
    stopBtn.dataset.stopped = t.emergency_stop ? "1" : "0";
  }

  stopBtn.addEventListener("click", async () => {
    stopBtn.disabled = true;
    stopMsg.textContent = "Working…";
    try {
      if (stopBtn.dataset.stopped === "1") {
        await api.release(await ctx.stepUp("release_stop", {}));
        stopMsg.textContent = "Stop released.";
      } else {
        await api.stop();
        stopMsg.textContent = "Stopped. Every order is denied until you release.";
      }
    } catch (e) {
      stopMsg.textContent = describeError(e);
    }
    await topbar();
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
