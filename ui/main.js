// Shell: hash router, shared state, header (status pill, address chip, emergency stop), wallet and
// API wiring. Every API string is written with textContent. There is no notice bar: testnet and
// custody are the header chip, the footer line and the Setup text, all static markup.
import { createApi, ApiError } from "./api.js";
import { explainError } from "./explain.js";
import { applyTopbar, retryText } from "./gauges.js";
import { discoverInjected } from "./wallet/injected.js";
import { isUserRejection } from "./wallet/adapter.js";
import { TestWallet } from "./wallet/test.js";
import * as chat from "./views/chat.js";
import * as setup from "./views/setup.js";
import * as overview from "./views/overview.js";
import * as activity from "./views/activity.js";
import * as rules from "./views/rules.js";
import { renderAccountControl } from "./views/account.js";
import { confirmDialog } from "./views/confirm.js";

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
// The owner should see a change within a second, so poll fast at first: 750 ms for the first
// seven polls, 1.5 s until about 28 s, then 3 s. The loop in pollJob stops after about five minutes.
export const pollDelay = (attempt) => (attempt < 7 ? 750 : attempt < 22 ? 1500 : 3000);

export function makeCtx({ api, wallet, sleep }) {
  const state = { me: null, tier: null, threadId: null };
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
      for (let i = 0; i < 120; i++) {
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
      state.threadId = null;
      ctx.changed();
    },
    // Log out: end the server session, drop every piece of client state and show the connect
    // screen. Until a new sign-in no protected route is called again (state.loggedOut).
    async logout() {
      try {
        await api.logout();
      } catch (e) {
        // The cookie may already be gone; the client is signed out either way.
      }
      state.me = null;
      state.tier = null;
      state.threadId = null;
      state.loggedOut = true;
      ctx.go("#/chat");
      ctx.changed();
    },
    // A fresh sign-in succeeded: protected routes may be called again.
    sessionStarted() {
      state.loggedOut = false;
    },
    // Fresh signature for one named action (design 6.2). Returns the X-Step-Up header value.
    async stepUp(action, params) {
      if (!wallet || !state.me) throw new Error("Connect your wallet first.");
      const ch = await api.stepUp(action, params);
      const sig = await wallet.signMessage(state.me.address, ch.message);
      return ch.challenge_id + "." + sig;
    },
    async refreshSession() {
      if (state.loggedOut) {
        state.me = null;
        return null;
      }
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

const VIEWS = { chat, overview, activity, rules, setup };
// Old hashes keep working.
const ALIASES = { portfolio: "overview", dashboard: "overview", approvals: "overview", feed: "activity", onboard: "setup" };

// Thread id from a hash like #/chat/<thread_id>. Returns null for #/chat or other screens.
export function threadFromHash(hash) {
  const raw = String(hash || "").replace(/^#\/?/, "");
  const [route, id] = raw.split("/");
  if (route === "chat" && id) {
    try {
      return decodeURIComponent(id);
    } catch (_) {
      return id;
    }
  }
  return null;
}

// Which screen to show. Chat is home once the agent is activated; Setup is shown until then
// (always for a signed-out visitor and for tier T0). Pure, so the tests can call it.
export function routeFor(hash, { signedIn, tier }) {
  const raw = String(hash || "").replace(/^#\/?/, "");
  const [head] = raw.split("/");
  const asked = Object.prototype.hasOwnProperty.call(ALIASES, raw)
    ? ALIASES[raw]
    : Object.prototype.hasOwnProperty.call(ALIASES, head)
      ? ALIASES[head]
      : head;
  if (!signedIn || tier === "T0") return "setup";
  if (asked === "setup") return tier === "T1" ? "setup" : "chat";
  return Object.prototype.hasOwnProperty.call(VIEWS, asked) && asked !== "setup" ? asked : "chat";
}

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
  const stopBtn = document.getElementById("btn-stop");
  const stopMsg = document.getElementById("stop-msg");
  const modeChip = document.getElementById("mode-chip");
  if (fixtures) {
    modeChip.textContent = "Demo data (fixtures), not live";
    modeChip.hidden = false;
  }

  const accountSlot = document.getElementById("account-slot");
  const bar = {
    pill: document.getElementById("status-pill"),
    addrChip: document.getElementById("addr-chip"),
    stopBtn,
    stopMsg,
    masthead: document.getElementById("masthead"),
  };
  let current = "";
  function mountAccount(me) {
    if (!accountSlot) return;
    accountSlot.replaceChildren(...(me ? [renderAccountControl(ctx)] : []));
  }
  async function topbar() {
    const me = await ctx.refreshSession();
    mountAccount(me);
    if (!me) {
      applyTopbar(bar, null, null);
      refreshRoute();
      return;
    }
    try {
      ctx.state.tier = await api.tier();
    } catch (e) {
      ctx.state.tier = null;
      applyTopbar(bar, "unavailable", me);
      refreshRoute();
      return;
    }
    applyTopbar(bar, ctx.state.tier, me);
    refreshRoute();
  }

  let toastTimer = null;
  function toast(text) {
    stopMsg.textContent = text;
    if (toastTimer) clearTimeout(toastTimer);
    if (text) toastTimer = setTimeout(() => (stopMsg.textContent = ""), 8000);
  }

  stopBtn.addEventListener("click", async () => {
    // Engaging the stop asks first; resuming goes straight to its signature.
    if (stopBtn.dataset.stopped !== "1") {
      const ok = await confirmDialog(ctx, {
        title: "Stop the agent now?",
        body: "It will not move any funds until you resume. Orders already confirmed on-chain are not undone. Resuming needs one signature from your wallet.",
        cancelLabel: "Keep running",
        confirmLabel: "Stop agent",
      });
      if (!ok) return;
    }
    stopBtn.disabled = true;
    toast("Working…");
    let after = "";
    try {
      if (stopBtn.dataset.stopped === "1") {
        await api.release(await ctx.stepUp("release_stop", {}));
        after = "The agent is running again.";
      } else {
        await api.stop();
        after = "Stopped. Nothing can run until you resume.";
      }
    } catch (e) {
      after = describeError(e);
    }
    await topbar();
    toast(after);
    ctx.changed();
  });

  function showRoute(force) {
    const me = ctx.state.me;
    const tier = ctx.state.tier && ctx.state.tier.tier;
    const name = routeFor(globalThis.location.hash, { signedIn: Boolean(me), tier });
    for (const a of document.querySelectorAll("#tabs a")) {
      if (a.getAttribute("href") === "#/" + name) a.setAttribute("aria-current", "page");
      else a.removeAttribute("aria-current");
    }
    document.body.className = "screen-" + name;
    if (!force && name === current) return;
    current = name;
    root.replaceChildren(VIEWS[name].render(ctx));
  }
  function refreshRoute() {
    if (ctx) showRoute(false);
  }

  (async () => {
    const wallet = fixtures ? new TestWallet() : await discoverInjected(globalThis);
    ctx = makeCtx({ api, wallet });
    const fromHash = threadFromHash(globalThis.location ? globalThis.location.hash : "");
    if (fromHash) ctx.state.threadId = fromHash;
    ctx.onChange(topbar);
    globalThis.addEventListener("hashchange", () => showRoute(false));
    await ctx.refreshSession(); // decides the home view before the first paint
    if (ctx.state.me) {
      try {
        ctx.state.tier = await api.tier();
      } catch (e) {
        ctx.state.tier = null;
      }
    }
    showRoute(true);
    topbar();
  })();
}

if (globalThis.document && globalThis.document.getElementById("view")) boot();
