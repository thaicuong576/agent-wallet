// Chat: talk to the agent. The owner types a task or a question; the agent plans, the policy
// engine decides, and what happened is shown as step cards rebuilt from the audit log.
// Every API string is written with textContent. "Agent says" text is the model's own words and
// is labelled as such; the step cards, not the prose, are the record of what happened.
import { cta, frozenCard } from "../gauges.js";
import { stepCard } from "./cards.js";
import { sidePanel } from "./side.js";

export const MESSAGE_MAX = 500;
const AGENT_SAYS = "Agent says: ";
const NEEDS_ACTIVATION = new Set(["TIER.READ_ONLY", "TIER.PROPOSE_ONLY"]);
const STOPPED = new Set(["TIER.EMERGENCY_STOP", "TIER.GLOBAL_STOP"]);

export const PROMPTS = ["What is my balance?", "Put 10 USDC to work in Aave", "Pull 5 USDC back out"];
const PLACEHOLDER = "Tell your agent what to do…";
// Transaction hashes are shown only as BaseScan links on the step cards, not in the prose.
const TX_LIST = /\s*Transactions?: 0x[0-9a-f]{64}(?:, 0x[0-9a-f]{64})*\.?/g;

// One message bubble, with its step cards under an agent message.
export function bubble(ctx, message) {
  const { el } = ctx;
  const mine = message.role === "user";
  const li = el("li", "bubble " + (mine ? "user" : "agent"));
  li.append(el("span", "who", mine ? "You" : "Agent"));
  const text = el("div", "text");
  const raw = typeof message.text === "string" ? message.text : "";
  for (const chunk of raw.split("\n\n")) {
    const part = chunk.replace(TX_LIST, "");
    if (!part) continue;
    if (!mine && part.startsWith(AGENT_SAYS)) {
      const p = el("p", "says");
      p.append(el("span", "chip", "Agent says"), " " + part.slice(AGENT_SAYS.length));
      text.append(p);
    } else {
      text.append(el("p", "", part));
    }
  }
  li.append(text);
  const steps = Array.isArray(message.steps) ? message.steps : [];
  if (steps.length) {
    const list = el("ol", "steps");
    for (const s of steps) list.append(stepCard(ctx, s));
    li.append(list);
    const codes = new Set(steps.flatMap((s) => (Array.isArray(s.rule_codes) ? s.rule_codes : [])));
    if ([...codes].some((c) => NEEDS_ACTIVATION.has(c))) {
      const hint = el("p", "hint", "Your agent is not allowed to act yet.");
      hint.append(cta(el, "Activate your agent", "#/setup", "primary"));
      li.append(hint);
    } else if ([...codes].some((c) => STOPPED.has(c))) {
      li.append(el("p", "hint", "The agent is frozen. Use Resume in the top bar."));
    }
  }
  return li;
}

export function render(ctx) {
  const { el, api } = ctx;
  const root = el("section", "chat-layout");
  const h1 = el("h1", "sr-only", "Chat with your agent");

  // ---- conversation card
  const card = el("div", "card chat-card");
  const head = el("header", "", "Your agent");
  const notice = el("div");
  const log = el("ol", "chat-log");
  log.setAttribute("aria-live", "polite");
  log.setAttribute("aria-label", "Conversation");
  const typing = el("p", "typing", "");
  typing.setAttribute("role", "status");
  typing.hidden = true;
  const scroll = el("div", "chat-scroll");
  scroll.append(notice, log, typing);
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
  for (const text of PROMPTS) {
    const chip = el("button", "prompt-chip", text);
    chip.type = "button";
    chip.addEventListener("click", () => {
      input.value = text;
      updateCounter();
      if (input.focus) input.focus();
    });
    prompts.append(chip);
  }
  box.append(prompts, label, composer, meta, status);
  card.append(head, scroll, box);

  const side = sidePanel(ctx);
  root.append(h1, card, side.node);

  let busy = false;
  let frozen = false;

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
  send.addEventListener("click", () => submit());

  function lock() {
    send.disabled = busy || frozen;
    input.disabled = frozen;
    input.setAttribute("placeholder", frozen ? "Frozen. Resume the agent to chat." : PLACEHOLDER);
  }

  function showHistory(items) {
    if (!items.length) {
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
    log.replaceChildren(...items.map((m) => bubble(ctx, m)));
    const last = log.lastChild;
    if (last && typeof last.scrollIntoView === "function") last.scrollIntoView({ block: "end" });
  }

  async function loadHistory() {
    try {
      const page = await api.chat(50);
      showHistory(Array.isArray(page.items) ? page.items : []);
    } catch (e) {
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

  function typingOn(text) {
    typing.hidden = false;
    typing.textContent = text;
  }

  function typingOff() {
    typing.hidden = true;
    typing.textContent = "";
  }

  function jobWords(job) {
    if (job.status === "queued") return "Agent is getting ready…";
    return "Agent is thinking…";
  }

  async function follow(jobId) {
    const job = await ctx.pollJob(jobId, (j) => typingOn(jobWords(j)));
    typingOff();
    await loadHistory();
    if (job && (job.status === "failed" || job.status === "interrupted")) {
      status.className = "msg error";
      status.textContent = "The request did not finish. See the agent's message above.";
    }
    ctx.changed();
  }

  async function submit() {
    if (busy || frozen) return;
    const text = input.value.trim();
    if (!text) return;
    if (text.length > MESSAGE_MAX) {
      status.className = "msg error";
      status.textContent = "Messages are limited to " + MESSAGE_MAX + " characters.";
      return;
    }
    busy = true;
    lock();
    status.className = "msg";
    status.textContent = "";
    // Show the owner's message at once; history is reloaded from the server afterwards.
    const mine = bubble(ctx, { role: "user", text, steps: [] });
    const placeholder = log.children.length === 1 && log.children[0].className === "empty";
    if (placeholder) log.replaceChildren();
    log.append(mine);
    input.value = "";
    updateCounter();
    typingOn("Agent is getting ready…");
    try {
      const accepted = await api.chatSend(text);
      await follow(accepted.job_id);
    } catch (e) {
      typingOff();
      status.className = "msg error";
      status.textContent = ctx.describeError(e);
      const active = e && e.problem && e.problem.job_id;
      if (e && e.status === 409 && typeof active === "string" && /^[0-9a-f]{32}$/.test(active)) {
        // One job at a time: wait for the running one, then offer the message again.
        status.textContent += " Waiting for it to finish.";
        try {
          await follow(active);
        } catch (_) {
          // The message above already explains the situation.
        }
      } else {
        await loadHistory();
      }
      input.value = text; // nothing was sent: keep what the owner typed
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
    await loadHistory();
  }

  ctx.onChange(() => {
    if (root.isConnected !== false && ctx.state.me) loadState();
  });
  start();
  return root;
}
