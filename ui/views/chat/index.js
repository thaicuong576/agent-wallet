// Chat: talk to the agent. The owner types a task or a question; the agent plans, the policy
// engine decides, and what happened is shown as step cards rebuilt from the audit log.
// Every API string is written with textContent. "Agent says" text is the model's own words and
// is labelled as such; the step cards, not the prose, are the record of what happened.
import { ROUTES } from "../../lib/api.js";
import { threadFromHash } from "../../main.js";
import { fetchEventSource } from "../../lib/sse.js";
import { ACTIVE_PHASES, uncertainCard } from "../cards.js";
import { historyRail, sidePanel } from "../side.js";
import {
  bubble,
  buildUncertainBubble,
  chatErrorBox,
  chatSkeleton,
  createLiveBubble,
  emptyChat,
  extractClarificationOptions,
  noticeCard,
  populateAgentBubble,
  removeClarifyChips,
  renderPromptChips,
  unauthenticatedChat,
} from "./bubble.js";
import { streamJob } from "./stream.js";

export const MESSAGE_MAX = 500;
export const PROMPTS = ["What is my balance?", "Put 10 USDC to work in Aave", "Pull 5 USDC back out"];
const PLACEHOLDER = "Tell your agent what to do…";
const SUGGESTIONS_MAX = 4;
// /limits is read once per render of the log; a few seconds of cache covers a burst of renders.
const LIMITS_TTL_MS = 5000;
const TOO_LATE = "Too late to stop: this order is already sent.";
const ENDED_STATUS = new Set(["succeeded", "denied", "failed", "interrupted"]);

export { bubble, populateAgentBubble, extractClarificationOptions };

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
    renderPromptChips(el, prompts, texts, fill);
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

  function showSkeleton() {
    log.replaceChildren(chatSkeleton(el));
  }

  function showThreadError(id) {
    log.replaceChildren(chatErrorBox(el, () => selectThread(id, true)));
  }

  function showHistory(items, limits) {
    hasMessages = items.length > 0;
    const lastAgent = items.map((m) => m.role).lastIndexOf("agent");
    if (!items.length) {
      if (live) dropLive();
      log.replaceChildren(emptyChat(el));
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
    const n = noticeCard(el, tier);
    if (n) notice.append(n);
    lock();
  }

  // The live progress card of the job this view follows. It sits under a bubble of its own.
  function startLive() {
    const liveObj = createLiveBubble(el, { onStop: stop });
    log.append(liveObj.li);
    scroll.scrollTop = scroll.scrollHeight;
    return liveObj;
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
        job = await streamJob(jobId, handleUpdate, handleToken, handleReset, {
          api,
          routes: ROUTES,
          fetchEventSource,
        });
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
            log.append(buildUncertainBubble(ctx));
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
    removeClarifyChips(log);
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
    removeClarifyChips(log);
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
      root.replaceChildren(el("h1", "", "Chat with your agent"), unauthenticatedChat(el));
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
