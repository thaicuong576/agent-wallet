// Chat: talk to the agent. The owner types a task or a question; the agent plans, the policy
// engine decides, and what happened is shown as step cards rebuilt from the audit log.
// Every API string is written with textContent. "Agent says" text is the model's own words and
// is labelled as such; the step cards, not the prose, are the record of what happened.
import { explainRule, formatAmount, OUTCOMES } from "../explain.js";
import { cta } from "../gauges.js";
import { txLink } from "./feed.js";

export const MESSAGE_MAX = 500;
const AGENT_SAYS = "Agent says: ";
const NEEDS_ACTIVATION = new Set(["TIER.READ_ONLY", "TIER.PROPOSE_ONLY"]);
const STOPPED = new Set(["TIER.EMERGENCY_STOP", "TIER.GLOBAL_STOP"]);

export const PROMPTS = ["Put 10 USDC to work in Aave", "What is my balance?"];
// Not a chat action: shown as a hint that points at the Withdraw control.
export const WITHDRAW_HINT_CHIP = "Withdraw 5 USDC to my wallet";

const STEP_OUTCOMES = {
  ...OUTCOMES,
  skipped: { icon: "↷", label: "Skipped", tone: "neutral" },
};
const FUNCTION_WORDS = {
  approve: "Approve",
  supply: "Supply to Aave",
  withdraw: "Withdraw from Aave",
  exactInputSingle: "Swap",
};

// One step card. `step` is {order_id, function, token, amount, outcome, reason, rule_codes,
// tx_hash, explanation} from GET /chat.
export function stepCard(ctx, step) {
  const { el } = ctx;
  const o = STEP_OUTCOMES[step.outcome] || STEP_OUTCOMES.no_action;
  const li = el("li", "decision step-card " + o.tone);
  const amount = formatAmount(step.amount, 6);
  const title =
    (FUNCTION_WORDS[step.function] || String(step.function || "Step")) +
    (amount ? " " + amount + " " + (step.token || "") : "");
  const head = el("div", "decision-head");
  head.append(el("strong", "", title), el("span", "badge " + o.tone, o.icon + " " + o.label));
  li.append(head);
  if (step.explanation) li.append(el("p", "headline", step.explanation));
  const codes = Array.isArray(step.rule_codes) ? step.rule_codes : [];
  if (step.outcome === "denied" || step.outcome === "pending_approval") {
    for (const code of codes) li.append(el("p", "sentence", explainRule(code, {}).sentence));
  }
  if (codes.length) {
    const chips = el("p", "chips");
    for (const code of codes) chips.append(el("span", "chip neutral code", code));
    li.append(chips);
  }
  const link = txLink(el, step.tx_hash);
  if (link) {
    const p = el("p");
    p.append(link);
    li.append(p);
  }
  return li;
}

// One message bubble, with its step cards under an agent message.
export function bubble(ctx, message) {
  const { el } = ctx;
  const mine = message.role === "user";
  const li = el("li", "bubble " + (mine ? "user" : "agent"));
  li.append(el("span", "who", mine ? "You" : "Agent"));
  const text = typeof message.text === "string" ? message.text : "";
  for (const part of text.split("\n\n")) {
    if (!part) continue;
    if (!mine && part.startsWith(AGENT_SAYS)) {
      const p = el("p", "says");
      p.append(el("span", "chip neutral", "Agent says"), " " + part.slice(AGENT_SAYS.length));
      li.append(p);
    } else {
      li.append(el("p", "", part));
    }
  }
  const steps = Array.isArray(message.steps) ? message.steps : [];
  if (steps.length) {
    const list = el("ol", "cards steps");
    for (const s of steps) list.append(stepCard(ctx, s));
    li.append(list);
    const codes = new Set(steps.flatMap((s) => (Array.isArray(s.rule_codes) ? s.rule_codes : [])));
    if ([...codes].some((c) => NEEDS_ACTIVATION.has(c))) {
      li.append(
        el("p", "note", "Your agent is not allowed to act yet."),
        cta(el, "Activate your agent", "#/dashboard"),
      );
    } else if ([...codes].some((c) => STOPPED.has(c))) {
      li.append(el("p", "note", "The agent is frozen. Release the emergency stop from the top bar."));
    }
  }
  return li;
}

export function render(ctx) {
  const { el, api } = ctx;
  const root = el("section", "view chat");
  root.append(el("h1", "", "Chat with your agent"));
  root.append(
    el(
      "p",
      "note",
      "Ask a question or hand the agent a task. The agent proposes, the policy engine decides, and every step below comes from the audit log.",
    ),
  );

  const notice = el("div");
  const log = el("ol", "chat-log");
  log.setAttribute("aria-live", "polite");
  log.setAttribute("aria-label", "Conversation");
  const typing = el("p", "typing", "");
  typing.setAttribute("role", "status");
  typing.hidden = true;
  const status = el("p", "status", "");
  status.setAttribute("role", "status");

  // ---- composer
  const form = el("div", "composer");
  const label = el("label", "", "Message to your agent");
  label.setAttribute("for", "chat-input");
  const input = el("textarea");
  input.id = "chat-input";
  input.rows = 2;
  input.maxLength = MESSAGE_MAX;
  input.setAttribute("placeholder", "Put 10 USDC to work in Aave");
  const counter = el("span", "counter", "0 / " + MESSAGE_MAX);
  const send = el("button", "primary", "Send");
  send.type = "button";
  const row = el("div", "row");
  row.append(send, counter, el("span", "note", "Enter sends, Shift+Enter adds a line."));
  form.append(label, input, row);

  const prompts = el("div", "suggest");
  prompts.setAttribute("role", "group");
  prompts.setAttribute("aria-label", "Suggested prompts");
  const hint = el("p", "note withdraw-hint", "");
  hint.hidden = true;
  for (const text of PROMPTS) {
    const chip = el("button", "chip prompt-chip", text);
    chip.type = "button";
    chip.addEventListener("click", () => {
      input.value = text;
      updateCounter();
      if (input.focus) input.focus();
    });
    prompts.append(chip);
  }
  const withdrawChip = el("button", "chip prompt-chip hint-chip", WITHDRAW_HINT_CHIP);
  withdrawChip.type = "button";
  withdrawChip.addEventListener("click", () => {
    hint.hidden = false;
    hint.replaceChildren(
      "Withdrawing needs a signature from your wallet, so it is not a chat action. Use the Withdraw control on the Dashboard. ",
      cta(el, "Open the Withdraw control", "#/dashboard", "secondary"),
    );
  });
  prompts.append(withdrawChip);

  root.append(notice, log, typing, status, form, prompts, hint);

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
    const off = busy || frozen;
    send.disabled = off;
    input.disabled = frozen;
  }

  function showHistory(items) {
    if (!items.length) {
      const li = el("li", "empty");
      li.append(
        el("p", "", "No messages yet."),
        el(
          "p",
          "note",
          "Try “Put 10 USDC to work in Aave” or “What is my balance?”. The agent only acts inside your policy.",
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
      status.className = "status error";
      status.textContent = ctx.describeError(e);
    }
  }

  // Tier and stop state: T0 and T1 say why orders are blocked, a stop freezes the composer.
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
      const card = el("div", "card frozen-note");
      card.setAttribute("role", "status");
      card.append(
        el("strong", "", "! Your agent is frozen"),
        el(
          "p",
          "",
          "The emergency stop is on, so the agent will not answer or act. Release it from the top bar to continue.",
        ),
      );
      notice.append(card);
    } else if (tier && (tier.tier === "T0" || tier.tier === "T1")) {
      const card = el("div", "card activate");
      card.append(
        el(
          "p",
          "",
          tier.tier === "T0"
            ? "Your agent is read only (tier T0). It can answer questions, but every order is blocked until you activate it."
            : "Your agent may only propose (tier T1). Nothing runs until you raise its tier.",
        ),
        cta(el, "Activate your agent", "#/dashboard"),
      );
      notice.append(card);
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
      status.className = "status error";
      status.textContent = "The request did not finish. See the agent's message above.";
    }
    ctx.changed();
  }

  async function submit() {
    if (busy || frozen) return;
    const text = input.value.trim();
    if (!text) return;
    if (text.length > MESSAGE_MAX) {
      status.className = "status error";
      status.textContent = "Messages are limited to " + MESSAGE_MAX + " characters.";
      return;
    }
    busy = true;
    lock();
    status.className = "status";
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
      status.className = "status error";
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
      const box = el("div", "empty");
      box.append(el("p", "", "Connect a wallet to talk to your agent."), cta(el, "Set up your agent", "#/onboard"));
      root.replaceChildren(el("h1", "", "Chat with your agent"), box);
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
