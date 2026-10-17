// Chat: talk to the agent. The owner types a task or a question; the agent plans, the policy
// engine decides, and what happened is shown as step cards rebuilt from the audit log.
// Every API string is written with textContent. "Agent says" text is the model's own words and
// is labelled as such; the step cards, not the prose, are the record of what happened.
import { ROUTES } from "../api.js";
import { cta, frozenCard } from "../gauges.js";
import { threadFromHash } from "../main.js";
import { fetchEventSource } from "../sse.js";
import { ACTIVE_PHASES, stepCard, uncertainCard } from "./cards.js";
import { createProgress } from "./progress.js";
import { historyRail, sidePanel } from "./side.js";

export const MESSAGE_MAX = 500;
const AGENT_SAYS = "Agent says: ";
const NEEDS_ACTIVATION = new Set(["TIER.READ_ONLY", "TIER.PROPOSE_ONLY"]);
const STOPPED = new Set(["TIER.EMERGENCY_STOP", "TIER.GLOBAL_STOP"]);

export const PROMPTS = ["What is my balance?", "Put 10 USDC to work in Aave", "Pull 5 USDC back out"];
const PLACEHOLDER = "Tell your agent what to do…";
const SUGGESTIONS_MAX = 4;
// /limits is read once per render of the log; a few seconds of cache covers a burst of renders.
const LIMITS_TTL_MS = 5000;
const TOO_LATE = "Too late to stop: this order is already sent.";
const ENDED_STATUS = new Set(["succeeded", "denied", "failed", "interrupted"]);
// Transaction hashes are shown only as BaseScan links on the step cards, not in the prose.
const TX_LIST = /\s*Transactions?: 0x[0-9a-f]{64}(?:, 0x[0-9a-f]{64})*\.?/g;

export function extractClarificationOptions(text) {
  if (typeof text !== "string") return [];
  const clean = text.trim();
  if (!clean) return [];

  // 1. Check for listed options (bulleted or numbered)
  const lines = clean.split("\n").map((l) => l.trim()).filter(Boolean);
  const listItems = [];
  for (const line of lines) {
    const m = line.match(/^(?:[-*•]|\d+[.)])\s+(.+)$/);
    if (m) {
      const item = m[1].replace(/[.,;]+$/, "").trim();
      if (item) listItems.push(item);
    }
  }
  if (listItems.length >= 2 && listItems.length <= 3) {
    if (listItems.every((item) => item.length > 0 && item.length <= 50)) {
      return listItems;
    }
  }

  // 2. Check for question with "A or B" or "A, B, or C"
  const questionMatch = clean.match(/([^.?!:\n]+)\?\s*$/);
  if (questionMatch) {
    let q = questionMatch[1].trim();
    if (q.includes(":")) {
      q = q.split(":").pop().trim();
    }
    const prefixMatch = q.match(/^(?:Do you want(?: to)?|Would you like(?: to)?|Should I(?: deposit| swap| supply| withdraw| send| use)?(?:\s+(?:in|into|to|from))?|Choose between|Did you mean)\s+(.+)$/i);
    const target = prefixMatch ? prefixMatch[1] : q;

    if (/\bor\b/i.test(target)) {
      const parts = target.split(/\s*,\s*or\s+|\s+or\s+|\s*,\s*/i)
        .map((p) => p.replace(/^["'`(\s]+|["'`)?.,;]+$/g, "").trim())
        .filter(Boolean);
      if (parts.length >= 2 && parts.length <= 3) {
        if (parts.every((p) => p.length > 0 && p.length <= 50)) {
          return parts;
        }
      }
    }
  }

  return [];
}

export function populateAgentBubble(ctx, li, message, opts = {}) {
  const { el } = ctx;
  li.replaceChildren();
  li.append(el("span", "who", "Agent"));

  const frame = el("div", "text");
  const raw = typeof message.text === "string" ? message.text : "";
  const rawChunks = raw.split("\n\n");
  const chunks = rawChunks.map((c) => c.replace(TX_LIST, "").trim()).filter(Boolean);

  const steps = Array.isArray(message.steps) ? message.steps : [];

  const leadIn = [];
  const outcome = [];

  for (const chunk of chunks) {
    if (chunk.startsWith(AGENT_SAYS)) {
      leadIn.push(chunk.slice(AGENT_SAYS.length).trim());
    } else {
      outcome.push(chunk);
    }
  }

  if (!leadIn.length && outcome.length > 1 && steps.length) {
    leadIn.push(outcome.shift());
  }

  if (steps.length) {
    for (const text of leadIn) {
      frame.append(el("p", "agent-lead", text));
    }
    const list = el("ol", "steps");
    for (const s of steps) list.append(stepCard(ctx, s, opts));
    frame.append(list);
    for (const text of outcome) {
      frame.append(el("p", "agent-outcome", text));
    }
    const codes = new Set(steps.flatMap((s) => (Array.isArray(s.rule_codes) ? s.rule_codes : [])));
    if ([...codes].some((c) => NEEDS_ACTIVATION.has(c))) {
      const hint = el("p", "hint", "Your agent is not allowed to act yet.");
      hint.append(cta(el, "Activate your agent", "#/setup", "primary"));
      frame.append(hint);
    } else if ([...codes].some((c) => STOPPED.has(c))) {
      frame.append(el("p", "hint", "The agent is frozen. Use Resume in the top bar."));
    }
  } else {
    for (const chunk of chunks) {
      const text = chunk.startsWith(AGENT_SAYS) ? chunk.slice(AGENT_SAYS.length).trim() : chunk;
      frame.append(el("p", "", text));
    }
  }

  li.append(frame);

  if (opts.onTry && message.intent === "clarify") {
    const options = extractClarificationOptions(raw);
    if (options.length >= 2 && options.length <= 3) {
      const chipsBox = el("div", "clarify-options");
      chipsBox.setAttribute("role", "group");
      chipsBox.setAttribute("aria-label", "Clarification options");
      for (const opt of options) {
        const btn = el("button", "prompt-chip clarify-chip", opt);
        btn.type = "button";
        btn.addEventListener("click", () => opts.onTry(opt));
        chipsBox.append(btn);
      }
      li.append(chipsBox);
    }
  }

  return li;
}

// One message bubble, with its step cards under an agent message. `opts` goes to stepCard
// ({limits, onTry, onAsk}); every field is optional.
export function bubble(ctx, message, opts = {}) {
  const { el } = ctx;
  const mine = message.role === "user";
  const li = el("li", "bubble " + (mine ? "user" : "agent"));
  if (mine) {
    li.append(el("span", "who", "You"));
    const frame = el("div", "text");
    const raw = typeof message.text === "string" ? message.text : "";
    for (const chunk of raw.split("\n\n")) {
      const part = chunk.replace(TX_LIST, "").trim();
      if (!part) continue;
      frame.append(el("p", "", part));
    }
    li.append(frame);
    return li;
  }
  return populateAgentBubble(ctx, li, message, opts);
}

export function render(ctx) {
  const { el, api } = ctx;
  const root = el("section", "chat-layout");
  const h1 = el("h1", "sr-only", "Chat with your agent");

  // ---- conversation card
  const card = el("div", "card chat-card");
  const head = el("header", "chat-head");
  const toggle = el("button", "history-toggle", "History");
  toggle.type = "button";
  toggle.setAttribute("aria-controls", "chat-history");
  toggle.setAttribute("aria-expanded", "false");
  const title = el("span", "chat-title", "Your agent");
  head.append(toggle, title);
  const notice = el("div");
  const log = el("ol", "chat-log");
  log.setAttribute("aria-live", "polite");
  log.setAttribute("aria-label", "Conversation");
  const scroll = el("div", "chat-scroll");
  scroll.append(notice, log);
  const status = el("p", "msg", "");
  status.setAttribute("role", "status");

  const box = el("div", "composer-box");
  const prompts = el("div", "suggest");
  prompts.setAttribute("role", "group");
  prompts.setAttribute("aria-label", "Suggested prompts");
  const label = el("label", "sr-only", "Message to your agent");
  label.setAttribute("for", "chat-input");
  const input = el("textarea");
  input.id = "chat-input";
  input.rows = 1;
  input.maxLength = MESSAGE_MAX;
  input.setAttribute("placeholder", PLACEHOLDER);
  const send = el("button", "primary", "Send");
  send.type = "button";
  const counter = el("span", "counter", "0 / " + MESSAGE_MAX);
  const composer = el("div", "composer");
  composer.append(input, send);
  const meta = el("div", "composer-meta");
  meta.append(el("span", "", "Enter sends, Shift+Enter adds a line."), counter);
  // The composer text is only ever filled here; nothing is sent until the owner presses Send.
  function fill(text) {
    input.value = text;
    updateCounter();
    if (input.focus) input.focus();
  }
  function showPrompts(texts) {
    prompts.replaceChildren();
    for (const text of texts) {
      const chip = el("button", "prompt-chip", text);
      chip.type = "button";
      chip.addEventListener("click", () => fill(text));
      prompts.append(chip);
    }
  }
  showPrompts(PROMPTS);
  box.append(prompts, label, composer, meta, status);
  card.append(head, scroll, box);

  const side = sidePanel(ctx);
  const rail = historyRail(ctx, { onSelect: selectThread, onNew: newChat });
  root.append(h1, rail.node, rail.backdrop, card, side.node);

  let busy = false;
  let frozen = false;
  let newThreadId = null;
  const fromHash = threadFromHash(globalThis.location ? globalThis.location.hash : "");
  let threadId = fromHash !== null ? fromHash : (ctx.state.threadId ?? null);
  if (threadId === "default") threadId = null;
  ctx.state.threadId = threadId;
  let activeJob = null; // {id, phase, threadId} while a job of this view is queued or running
  let cancelling = false;
  let limitsAt = 0;
  let limitsCache = null;
  let live = null; // {li, prog} while this view shows the progress card of a job
  let historyToken = 0;
  let threadsToken = 0;
  let selectToken = 0;
  let hasMessages = false;
  let creatingThread = false;

  function updateHash(id) {
    const clean = id === "default" || !id ? null : id;
    const target = clean ? "#/chat/" + encodeURIComponent(clean) : "#/chat";
    if (globalThis.location && globalThis.location.hash !== target) {
      globalThis.location.hash = target;
    }
  }
  updateHash(threadId);

  function onHashChange() {
    if (root.isConnected === false) return;
    const next = threadFromHash(globalThis.location ? globalThis.location.hash : "");
    const clean = next === "default" || !next ? null : next;
    if (clean !== threadId) {
      selectThread(clean);
    }
  }
  if (globalThis.addEventListener) {
    globalThis.addEventListener("hashchange", onHashChange);
  }

  function updateCounter() {
    const n = input.value.length;
    counter.textContent = n + " / " + MESSAGE_MAX;
    counter.className = "counter" + (n >= MESSAGE_MAX ? " full" : "");
  }
  input.addEventListener("input", updateCounter);
  input.addEventListener("keydown", (ev) => {
    if (ev && ev.key === "Enter" && !ev.shiftKey) {
      if (ev.preventDefault) ev.preventDefault();
      submit();
    }
  });
  send.addEventListener("click", () => (activeJob && activeJob.threadId === threadId ? stop() : submit()));

  // The Send button becomes Stop (ink fill) while a job of this thread is queued or running.
  function lock() {
    const isJobForThisThread = Boolean(activeJob && activeJob.threadId === threadId);
    const running = isJobForThisThread && !frozen;
    send.textContent = running ? "Stop" : "Send";
    send.className = running ? "stop" : "primary";
    send.disabled = running ? cancelling : busy || frozen;
    rail.setBusy(busy);
    input.disabled = frozen;
    input.setAttribute("placeholder", frozen ? "Frozen. Resume the agent to chat." : PLACEHOLDER);
  }

  async function getLimits() {
    if (Date.now() - limitsAt < LIMITS_TTL_MS) return limitsCache;
    try {
      limitsCache = await api.limits();
    } catch (_) {
      limitsCache = null; // the limit line is left out rather than guessed
    }
    limitsAt = Date.now();
    return limitsCache;
  }

  function removeClarifyChips() {
    const list = log.children ? [...log.children] : [];
    for (const li of list) {
      if (li.children) {
        for (const c of [...li.children]) {
          if (c.className && c.className.includes("clarify-options")) c.remove();
        }
      }
    }
  }

  function showSkeleton() {
    const skeleton = el("li", "chat-skeleton skeleton");
    skeleton.setAttribute("role", "status");
    skeleton.setAttribute("aria-label", "Loading chat");
    skeleton.append(
      el("div", "skeleton-bar"),
      el("div", "skeleton-bar"),
      el("div", "skeleton-bar"),
    );
    log.replaceChildren(skeleton);
  }

  function showThreadError(id) {
    const box = el("li", "empty error-box");
    const p = el("p", "", "Couldn't load this chat.");
    const retry = el("button", "btn primary", "Retry");
    retry.type = "button";
    retry.addEventListener("click", () => {
      selectThread(id, true);
    });
    box.append(p, retry);
    log.replaceChildren(box);
  }

  function showHistory(items, limits) {
    hasMessages = items.length > 0;
    const lastAgent = items.map((m) => m.role).lastIndexOf("agent");
    if (!items.length) {
      if (live) dropLive();
      const li = el("li", "empty");
      li.append(
        el("p", "", "No messages yet."),
        el(
          "p",
          "note",
          "Try “Put 10 USDC to work in Aave” or “What is my balance?”. The agent only acts inside your rules.",
        ),
      );
      log.replaceChildren(li);
      return;
    }
    // Quick fixes (Try ..., Why is this blocked?) are offered on the newest agent message only.
    const quick = { limits, onTry: fill, onAsk: (q) => submit(q) };
    const hasLiveInDom = Boolean(
      live && live.li && ((log.contains && log.contains(live.li)) || (log.children && log.children.includes(live.li)))
    );

    if (hasLiveInDom && lastAgent !== -1) {
      live.prog.destroy();
      live.li.className = "bubble agent";
      populateAgentBubble(ctx, live.li, items[lastAgent], quick);
      const liveNode = live.li;
      live = null;

      const rendered = items.map((m, i) => (i === lastAgent ? liveNode : bubble(ctx, m, { limits })));
      log.replaceChildren(...rendered);
    } else {
      if (live) dropLive();
      log.replaceChildren(
        ...items.map((m, i) => bubble(ctx, m, i === lastAgent ? quick : { limits })),
      );
    }
    const last = log.lastChild;
    if (last && typeof last.scrollIntoView === "function") last.scrollIntoView({ block: "end" });
  }

  // `resume`: a job still running on the server (the page was reloaded, or another thread was
  // opened) is followed again; the live card replaces its half-finished message.
  async function loadHistory(resume = false, selectTok = null) {
    const token = ++historyToken;
    const forId = threadId;
    try {
      const page = await api.chat(50, forId);
      if (token !== historyToken || threadId !== forId) return;
      if (selectTok !== null && selectTok !== selectToken) return;
      const all = Array.isArray(page.items) ? page.items : [];
      const running = all.filter((m) => m.role === "agent" && ACTIVE_PHASES.has(m.phase) && m.job_id);
      const items = all.filter((m) => !running.includes(m));
      const needsLimits = items.some(
        (m) => Array.isArray(m.steps) && m.steps.some((s) => s.outcome === "denied" || s.outcome === "pending")
      );
      const limits = needsLimits ? await getLimits() : limitsCache;
      if (token !== historyToken || threadId !== forId) return;
      if (selectTok !== null && selectTok !== selectToken) return;
      showHistory(items, limits);
      const last = running[running.length - 1];
      if (resume && last && (!activeJob || activeJob.threadId !== threadId) && /^[0-9a-f]{32}$/.test(last.job_id)) {
        follow(last.job_id, threadId);
      }
    } catch (e) {
      if (token !== historyToken || threadId !== forId) return;
      if (selectTok !== null && selectTok !== selectToken) return;
      if (e && e.status === 404 && threadId !== null) {
        ctx.state.threadId = null;
        threadId = null;
        updateHash(null);
        await loadThreads();
        await loadHistory(false);
        return;
      }
      if (selectTok !== null) {
        throw e;
      }
      status.className = "msg error";
      status.textContent = ctx.describeError(e);
    }
  }

  // Tier and stop state: T1 says why orders are blocked, a stop freezes the composer.
  async function loadState() {
    let tier = null;
    try {
      tier = await api.tier();
    } catch (_) {
      tier = null;
    }
    notice.replaceChildren();
    frozen = Boolean(tier && tier.emergency_stop);
    if (frozen) {
      notice.append(frozenCard(el));
    } else if (tier && (tier.tier === "T0" || tier.tier === "T1")) {
      const n = el("div", "notice-card");
      n.append(
        el(
          "p",
          "",
          tier.tier === "T0"
            ? "Your agent is read only. It can answer questions, but every order is blocked until you activate it."
            : "Your agent may only propose. Nothing runs until you activate it again.",
        ),
        cta(el, "Activate your agent", "#/setup"),
      );
      notice.append(n);
    }
    lock();
  }

  // The live progress card of the job this view follows. It sits under a bubble of its own.
  function startLive() {
    const li = el("li", "bubble agent live");
    li.append(el("span", "who", "Agent"));
    const frame = el("div", "text");
    const streamPara = el("p", "streaming-text", "");
    streamPara.hidden = true;
    const prog = createProgress(el, { onStop: stop });
    frame.append(streamPara, prog.node);
    li.append(frame);
    log.append(li);
    scroll.scrollTop = scroll.scrollHeight;
    return { li, frame, streamPara, prog };
  }

  function dropLive() {
    if (!live) return;
    live.prog.destroy();
    live.li.remove();
    live = null;
  }

  function refreshAround() {
    loadSuggestions();
    loadThreads();
  }

  async function streamJob(jobId, onUpdate, onToken, onReset) {
    if (api.fixtures || typeof globalThis.ReadableStream === "undefined") {
      throw new Error("SSE not available");
    }
    const url = ROUTES.chatStream.replace("{job_id}", jobId);
    const controller = new AbortController();
    let doneJob = null;

    await fetchEventSource(url, {
      signal: controller.signal,
      headers: {
        Accept: "text/event-stream",
        "X-AW-CSRF": "1",
      },
      onopen(res) {
        if (!res || !res.ok) {
          throw new Error(`HTTP ${res ? res.status : "error"}`);
        }
        const ct = res.headers?.get?.("content-type") || "";
        if (!ct.includes("text/event-stream")) {
          throw new Error("Not event stream");
        }
        if (!res.body || typeof res.body.getReader !== "function") {
          throw new Error("No ReadableStream");
        }
      },
      onmessage(ev) {
        if (ev.event === "token") {
          try {
            const data = JSON.parse(ev.data);
            if (data && typeof data.text === "string" && onToken) {
              onToken(data.text);
            }
          } catch (_) {}
        } else if (ev.event === "reset") {
          if (onReset) onReset();
        } else if (ev.event === "phase") {
          try {
            const data = JSON.parse(ev.data);
            if (data && typeof data.phase === "string") {
              onUpdate(data.phase, "running");
            }
          } catch (_) {}
        } else if (ev.event === "done") {
          try {
            doneJob = JSON.parse(ev.data);
          } catch (_) {}
          controller.abort();
        }
      },
      onerror(err) {
        // Fatal or client errors bail out to polling immediately
        throw err;
      },
    });

    if (!doneJob) {
      throw new Error("Stream closed without done event");
    }
    return doneJob;
  }

  async function follow(jobId, forThreadId = threadId) {
    activeJob = { id: jobId, phase: "queued", threadId: forThreadId };
    cancelling = false;
    if (!live && threadId === forThreadId) {
      // No card yet: the page was reloaded mid-run, or another thread was opened.
      live = startLive();
      live.prog.setReceived();
    }
    let sent = false;
    lock();
    let job = null;

    function handleToken(text) {
      if (threadId === forThreadId && live && live.streamPara) {
        live.streamPara.hidden = false;
        live.streamPara.textContent += text;
        const near = scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight < 80;
        if (near) scroll.scrollTop = scroll.scrollHeight;
      }
    }

    function handleReset() {
      if (threadId === forThreadId && live && live.streamPara) {
        live.streamPara.textContent = "";
        live.streamPara.hidden = true;
      }
    }

    function handleUpdate(phase, status) {
      const p = typeof phase === "string" ? phase : status === "queued" ? "queued" : "working";
      if (activeJob) activeJob.phase = p;
      if (p === "sent" || p === "confirming") sent = true;
      if (p !== "done" && (!status || !ENDED_STATUS.has(status)) && threadId === forThreadId) {
        if (!live) {
          live = startLive();
          live.prog.setReceived();
        }
        const near = scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight < 80;
        live.prog.setPhase(p);
        if (near) scroll.scrollTop = scroll.scrollHeight;
      }
    }

    try {
      try {
        job = await streamJob(jobId, handleUpdate, handleToken, handleReset);
      } catch (_) {
        job = await ctx.pollJob(jobId, (j) => handleUpdate(j.phase, j.status));
      }
    } finally {
      activeJob = null;
      cancelling = false;
    }
    if (threadId === forThreadId) {
      await loadHistory();
      if (job && (job.status === "failed" || job.status === "interrupted")) {
        if (sent) {
          // The order left the wallet and the outcome is unknown: never say "nothing was sent".
          if (live) {
            live.prog.destroy();
            live.prog.node.remove();
            live.li.className = "bubble agent";
            const list = el("ol", "steps");
            list.append(uncertainCard(ctx));
            live.frame.append(list);
            live = null;
          } else {
            const li = el("li", "bubble agent");
            const list = el("ol", "steps");
            list.append(uncertainCard(ctx));
            li.append(list);
            log.append(li);
          }
        } else {
          status.className = "msg error";
          status.textContent = "The request did not finish. See the agent's message above.";
        }
      }
      lock();
    }
    refreshAround();
    ctx.changed();
  }

  // Stop: ask the server to skip the steps not yet sent. An order already sent is never undone.
  async function stop() {
    if (!activeJob || activeJob.threadId !== threadId || cancelling) return;
    cancelling = true;
    lock();
    status.className = "msg";
    status.textContent = "";
    try {
      await api.cancelJob(activeJob.id);
      if (live) live.prog.setCancelling();
      status.textContent = "Stopping. Steps not sent yet will be skipped.";
    } catch (e) {
      cancelling = false;
      status.className = "msg error";
      status.textContent = e && e.status === 409 ? TOO_LATE : ctx.describeError(e);
    }
    lock();
  }

  let suggestionsAt = 0;
  async function loadSuggestions(force = false) {
    const now = Date.now();
    if (!force && now - suggestionsAt < 3000) return;
    suggestionsAt = now;
    let texts = PROMPTS;
    try {
      const res = await api.suggestions();
      const got = (Array.isArray(res.items) ? res.items : [])
        .map((x) => (x && typeof x.text === "string" ? x.text.trim() : ""))
        .filter((t) => t && t.length <= MESSAGE_MAX)
        .slice(0, SUGGESTIONS_MAX);
      if (got.length) texts = got;
    } catch (_) {
      texts = PROMPTS;
    }
    showPrompts(texts);
  }

  async function loadThreads() {
    const token = ++threadsToken;
    let items = [];
    let ok = true;
    try {
      const res = await api.threads();
      if (token !== threadsToken) return;
      items = Array.isArray(res.items) ? res.items : [];
    } catch (_) {
      if (token !== threadsToken) return;
      items = [];
      ok = false;
    }
    if (token !== threadsToken) return;
    if (threadId !== null && !items.some((t) => t.id === threadId || (t.id === "default" && threadId === null))) {
      const existingTitle = rail.title(threadId) || (title.textContent !== "Your agent" ? title.textContent : "") || (threadId === newThreadId ? "New chat" : "") || "New chat";
      items = [
        {
          id: threadId,
          title: existingTitle,
          last_message_at: new Date().toISOString(),
          status: "done",
        },
        ...items,
      ];
    }
    rail.setThreads(items, ok);
    rail.setActive(threadId);
    const name = threadId === null ? "" : rail.title(threadId);
    title.textContent = name || (threadId === newThreadId ? "New chat" : "Your agent");
  }

  async function selectThread(id, force = false) {
    const clean = id === "default" || !id ? null : id;
    if (!force && threadId === clean) return;
    const token = ++selectToken;
    newThreadId = null;
    if (live) dropLive();
    threadId = clean;
    ctx.state.threadId = clean;
    updateHash(clean);
    rail.setActive(clean);
    status.className = "msg";
    status.textContent = "";
    showSkeleton();
    lock();
    const name = clean === null ? "" : rail.title(clean);
    title.textContent = name || (clean === newThreadId ? "New chat" : "Your agent");
    try {
      await loadHistory(true, token);
    } catch (e) {
      if (token !== selectToken || threadId !== clean) return;
      showThreadError(clean);
    }
    if (token !== selectToken || threadId !== clean) return;
    lock();
  }

  async function newChat() {
    status.className = "msg";
    status.textContent = "";
    removeClarifyChips();
    if (live) dropLive();
    if (threadId !== null && !hasMessages) {
      if (input.focus) input.focus();
      return;
    }
    if (creatingThread) {
      if (input.focus) input.focus();
      return;
    }
    creatingThread = true;
    try {
      const t = await api.newThread();
      newThreadId = t.id;
      threadId = t.id;
      ctx.state.threadId = t.id;
      hasMessages = false;
      historyToken++;
      updateHash(t.id);
      rail.setActive(threadId);
      showHistory([], null);
      await loadThreads();
      lock();
      if (input.focus) input.focus();
    } catch (e) {
      status.className = "msg error";
      status.textContent = ctx.describeError(e);
    } finally {
      creatingThread = false;
    }
  }

  toggle.addEventListener("click", () => {
    rail.open();
    toggle.setAttribute("aria-expanded", "true");
  });
  root.addEventListener("keydown", (ev) => {
    if (ev && ev.key === "Escape" && rail.isOpen()) {
      rail.close();
      toggle.setAttribute("aria-expanded", "false");
      if (toggle.focus) toggle.focus();
    }
  });

  // `forced` is a question sent by a chip; it does not touch what the owner is typing.
  async function submit(forced) {
    if (busy || frozen || activeJob) return;
    const text = typeof forced === "string" ? forced : input.value.trim();
    if (!text) return;
    removeClarifyChips();
    if (text.length > MESSAGE_MAX) {
      status.className = "msg error";
      status.textContent = "Messages are limited to " + MESSAGE_MAX + " characters.";
      return;
    }
    busy = true;
    lock();
    status.className = "msg";
    status.textContent = "";
    hasMessages = true;
    // Show the owner's message at once; history is reloaded from the server afterwards.
    const mine = bubble(ctx, { role: "user", text, steps: [] });
    const placeholder = log.children.length === 1 && log.children[0].className === "empty";
    if (placeholder) log.replaceChildren();
    log.append(mine);
    const typed = input.value;
    if (typeof forced !== "string") {
      input.value = "";
      updateCounter();
    }
    // The progress card appears before the request leaves, so the owner sees the agent at once.
    live = startLive();
    const sentInThread = threadId === "default" || !threadId ? null : threadId;
    try {
      const accepted = await api.chatSend(text, sentInThread);
      busy = false;
      const isNew = Boolean(newThreadId);
      newThreadId = null;
      live.prog.setReceived();
      if (isNew) await loadThreads();
      if (sentInThread) rail.setActive(sentInThread);
      title.textContent = (sentInThread && rail.title(sentInThread)) || (text ? text.slice(0, 40) : "") || "Your agent";
      await follow(accepted.job_id, sentInThread);
    } catch (e) {
      dropLive();
      status.className = "msg error";
      status.textContent = ctx.describeError(e);
      const active = e && e.problem && e.problem.job_id;
      if (e && e.status === 409 && typeof active === "string" && /^[0-9a-f]{32}$/.test(active)) {
        // One job at a time: wait for the running one, then offer the message again.
        status.textContent += " Waiting for it to finish.";
        try {
          await follow(active, sentInThread);
        } catch (_) {
          // The message above already explains the situation.
        }
      } else {
        await loadHistory();
      }
      input.value = typeof forced === "string" ? typed : text; // nothing was sent: keep the text
      updateCounter();
    }
    busy = false;
    lock();
  }

  async function start() {
    const me = await ctx.refreshSession();
    if (!me) {
      const empty = el("div", "empty");
      empty.append(el("p", "", "Connect a wallet to talk to your agent."), cta(el, "Set up your agent", "#/setup"));
      root.replaceChildren(el("h1", "", "Chat with your agent"), empty);
      return;
    }
    await loadState();
    loadSuggestions();
    await loadThreads();
    await loadHistory(true);
  }

  ctx.onChange(() => {
    if (root.isConnected !== false && ctx.state.me) {
      loadState();
      loadSuggestions();
    }
  });
  start();
  return root;
}
