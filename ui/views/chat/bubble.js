// Pure bubble builders: extractClarificationOptions, populateAgentBubble, bubble,
// and related message list elements.
import { cta, frozenCard } from "../../lib/gauges.js";
import { stepCard, uncertainCard } from "../cards.js";
import { createProgress } from "../progress.js";

export const AGENT_SAYS = "Agent says: ";
export const NEEDS_ACTIVATION = new Set(["TIER.READ_ONLY", "TIER.PROPOSE_ONLY"]);
export const STOPPED = new Set(["TIER.EMERGENCY_STOP", "TIER.GLOBAL_STOP"]);
// Transaction hashes are shown only as BaseScan links on the step cards, not in the prose.
export const TX_LIST = /\s*Transactions?: 0x[0-9a-f]{64}(?:, 0x[0-9a-f]{64})*\.?/g;

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

export function emptyChat(el) {
  const li = el("li", "empty");
  li.append(
    el("p", "", "No messages yet."),
    el(
      "p",
      "note",
      "Try “Put 10 USDC to work in Aave” or “What is my balance?”. The agent only acts inside your rules.",
    ),
  );
  return li;
}

export function chatSkeleton(el) {
  const skeleton = el("li", "chat-skeleton skeleton");
  skeleton.setAttribute("role", "status");
  skeleton.setAttribute("aria-label", "Loading chat");
  skeleton.append(
    el("div", "skeleton-bar"),
    el("div", "skeleton-bar"),
    el("div", "skeleton-bar"),
  );
  return skeleton;
}

export function chatErrorBox(el, onRetry) {
  const box = el("li", "empty error-box");
  const p = el("p", "", "Couldn't load this chat.");
  const retry = el("button", "btn primary", "Retry");
  retry.type = "button";
  retry.addEventListener("click", onRetry);
  box.append(p, retry);
  return box;
}

export function removeClarifyChips(log) {
  const list = log.children ? [...log.children] : [];
  for (const li of list) {
    if (li.children) {
      for (const c of [...li.children]) {
        if (c.className && c.className.includes("clarify-options")) c.remove();
      }
    }
  }
}

export function createLiveBubble(el, { onStop }) {
  const li = el("li", "bubble agent live");
  li.append(el("span", "who", "Agent"));
  const frame = el("div", "text");
  const streamPara = el("p", "streaming-text", "");
  streamPara.hidden = true;
  const prog = createProgress(el, { onStop });
  frame.append(streamPara, prog.node);
  li.append(frame);
  return { li, frame, streamPara, prog };
}

export function renderPromptChips(el, container, texts, onSelect) {
  container.replaceChildren();
  for (const text of texts) {
    const chip = el("button", "prompt-chip", text);
    chip.type = "button";
    chip.addEventListener("click", () => onSelect(text));
    container.append(chip);
  }
}

export function noticeCard(el, tier) {
  if (tier && tier.emergency_stop) {
    return frozenCard(el);
  }
  if (tier && (tier.tier === "T0" || tier.tier === "T1")) {
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
    return n;
  }
  return null;
}

export function buildUncertainBubble(ctx) {
  const { el } = ctx;
  const li = el("li", "bubble agent");
  const list = el("ol", "steps");
  list.append(uncertainCard(ctx));
  li.append(list);
  return li;
}

export function unauthenticatedChat(el) {
  const empty = el("div", "empty");
  empty.append(el("p", "", "Connect a wallet to talk to your agent."), cta(el, "Set up your agent", "#/setup"));
  return empty;
}
