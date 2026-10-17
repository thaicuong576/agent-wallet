// Step cards: the one vocabulary Chat, Activity, Portfolio and the job panel share.
// Kinds: done (green), blocked (red), pending (amber, Approve and Reject), skipped (grey), plus
// fail (red, "!") for an execution that errored. Every API string is written with textContent;
// the icon and the state word mean the kind is never colour alone.
import { explainRecord, explainRule, formatAmount, shortHash } from "../lib/explain.js";
import { relTime } from "../lib/gauges.js";

// Navigation target for a transaction link, not an asset: the page never fetches it.
const EXPLORER_TX = "https://sepolia.basescan.org/tx/";
const TX_HASH = /^0x[0-9a-f]{64}$/;

export const KINDS = {
  executed: { kind: "done", icon: "✓", word: "Executed" },
  denied: { kind: "blocked", icon: "✕", word: "Blocked" },
  pending_approval: { kind: "pending", icon: "…", word: "Waiting for you" },
  failed: { kind: "fail", icon: "!", word: "Failed" },
  skipped: { kind: "skipped", icon: "↷", word: "Skipped" },
  no_action: { kind: "skipped", icon: "–", word: "No action" },
};
export const STEP_KINDS = ["done", "blocked", "pending", "skipped", "fail"];

// Provenance chips: who produced the plan, in words.
export const PLANNER_WORDS = {
  openai: "OpenAI Planner · live model",
  scripted: "Scripted Demo · fixed input, not the LLM",
  fallback: "Offline Fallback · rule-based, not the LLM",
};

// Every planner is named in words, so a reader always knows what produced the plan.
export function plannerLabel(job) {
  if (!job) return null;
  if (job.planner_client === "openai") {
    return "Latest run: OpenAI model planner (the policy engine still decides).";
  }
  if (job.planner_client === "scripted") {
    return "Latest run: scripted planner (fixed demo input, not a live model).";
  }
  if (job.planner_client === "fallback") {
    return "Latest run: fallback planner (the live model was unavailable, so a built-in offline plan was used).";
  }
  return null;
}

// A BaseScan link for one transaction hash; null for anything that is not a hash.
export function txLink(el, hash, text) {
  if (typeof hash !== "string" || !TX_HASH.test(hash)) return null;
  const a = el("a", "", text || "BaseScan ↗");
  a.setAttribute("href", EXPLORER_TX + hash);
  a.setAttribute("target", "_blank");
  a.setAttribute("rel", "noopener noreferrer");
  a.setAttribute("title", shortHash(hash));
  return a;
}

const PAST = { approve: "Approved", supply: "Supplied", withdraw: "Withdrew", mint: "Minted", exactInputSingle: "Swapped" };
const NOUN = { approve: "Approve", supply: "Supply", withdraw: "Withdraw", mint: "Mint", exactInputSingle: "Swap" };
const AAVE = { supply: " to Aave", withdraw: " from Aave" };
const own = (o, k) => typeof k === "string" && Object.prototype.hasOwnProperty.call(o, k);

// "Supplied 10 USDC to Aave" (past) or "Supply 10 USDC to Aave" from {function, token, amount}.
export function describeOrder(order, past) {
  if (!order) return "";
  const fn = order.function;
  const word = own(past ? PAST : NOUN, fn) ? (past ? PAST : NOUN)[fn] : String(fn || "Order");
  const amt = formatAmount(order.amount, 6);
  return word + (amt ? " " + amt + " " + (order.token || "") : "") + (own(AAVE, fn) ? AAVE[fn] : "");
}

function shell(el, info, extraClass) {
  const li = el("li", "step-card " + info.kind + (extraClass ? " " + extraClass : ""));
  const icon = el("span", "step-icon", info.icon);
  icon.setAttribute("aria-hidden", "true");
  const body = el("div", "step-body");
  const side = el("div", "step-side");
  side.append(el("span", "state-word", info.word));
  li.append(icon, body, side);
  return { li, body, side };
}

function addRuleChips(el, side, codes) {
  for (const code of codes) side.append(el("span", "chip code", code));
}

// ---- live job: the cards around it ---------------------------------------------------------
// The live progress card is ui/views/progress.js. `phase` comes from GET /jobs/{id} and each
// agent message.
export const ACTIVE_PHASES = new Set(["queued", "rules_check", "sent", "confirming"]);

// A job that failed after the order was sent: the outcome is not known, so say so.
export function uncertainCard(ctx) {
  const { el } = ctx;
  const li = el("li", "step-card uncertain");
  const icon = el("span", "step-icon", "!");
  icon.setAttribute("aria-hidden", "true");
  const body = el("div", "step-body");
  body.append(
    el("p", "step-title", "Still confirming"),
    el("p", "step-sub", "It may still land. Check Activity before trying again."),
  );
  const link = el("a", "", "Open Activity");
  link.setAttribute("href", "#/activity");
  const side = el("div", "step-side");
  side.append(link);
  li.append(icon, body, side);
  return li;
}

// ---- limit line --------------------------------------------------------------------------
const money = (s) => (typeof s === "string" && /^\d+(\.\d+)?$/.test(s) ? "$" + s : null);

// USD value of a USDC step amount, two decimals; null for any other token.
function orderUsd(step) {
  const a = step && step.amount;
  if (!a || step.token !== "USDC" || !/^\d+$/.test(String(a.value)) || !Number.isInteger(a.decimals)) {
    return null;
  }
  const cents = (BigInt(a.value) * 100n) / 10n ** BigInt(a.decimals);
  return (cents / 100n).toString() + "." + String(cents % 100n).padStart(2, "0");
}

// "This order $X, per-order limit $Y, today $A of $B" from GET /limits (strings, two decimals).
// Parts that are not known are left out; null when nothing is known.
export function limitLine(step, limits) {
  if (!limits) return null;
  const parts = [];
  const order = orderUsd(step);
  if (order) parts.push("This order $" + order);
  const per = money(limits.per_order_usd);
  if (per) parts.push((parts.length ? "per-order" : "Per-order") + " limit " + per);
  const used = money(limits.used_today_usd);
  const day = money(limits.per_day_usd);
  if (used && day) parts.push("today " + used + " of " + day);
  return parts.length ? parts.join(", ") : null;
}

const PER_ORDER_CODE = "LIMIT.PER_TX";
const TRY_TEXT = {
  supply: (cap) => "Put " + cap + " USDC to work in Aave",
  withdraw: (cap) => "Pull " + cap + " USDC back out",
};
const SKIP_WORDS = { "owner-cancelled": "You stopped the run, so this step was not sent." };

// One step of an agent message. `step` is {order_id, function, token, amount, outcome, reason,
// rule_codes, tx_hash, explanation} from GET /chat. `opts` is optional:
//   limits: the GET /limits body, for the limit line on pending and blocked steps;
//   onTry(text): put text in the composer (never sends); onAsk(text): send that question.
// The two callbacks are given only for the newest agent message, so old cards stay quiet.
export function stepCard(ctx, step, opts = {}) {
  const { el } = ctx;
  const info = KINDS[step.outcome] || KINDS.no_action;
  const li = el("li", "step-card " + info.kind);
  const details = el("details", "step-details");
  const summary = el("summary", "step-summary");
  const row = el("div", "step-row");

  const icon = el("span", "step-icon", info.icon);
  icon.setAttribute("aria-hidden", "true");

  const past = info.kind === "done";
  const titleText = step.explanation || describeOrder(step, past) || info.word;
  const title = el("p", "step-title", titleText);
  row.append(icon, title);

  if (step.elapsed) {
    row.append(el("span", "step-elapsed", String(step.elapsed)));
  }

  const link = txLink(el, step.tx_hash);
  if (link) row.append(link);
  summary.append(row);

  const body = el("div", "step-body");
  const codes = Array.isArray(step.rule_codes) ? step.rule_codes : [];
  if (info.kind === "blocked" || info.kind === "pending") {
    for (const code of codes) body.append(el("p", "step-sub", explainRule(code, {}).sentence));
  } else if (info.kind === "done") {
    body.append(el("p", "step-sub", "Inside every limit of your rules"));
  } else if (info.kind === "skipped" && typeof step.reason === "string") {
    const key = Object.keys(SKIP_WORDS).find((k) => step.reason.endsWith(k));
    if (key) body.append(el("p", "step-sub", SKIP_WORDS[key]));
  }
  if (info.kind === "blocked" || info.kind === "pending") {
    const line = limitLine(step, opts.limits);
    if (line) body.append(el("p", "limit-line", line));
  }
  if (info.kind === "blocked" && (opts.onTry || opts.onAsk)) {
    const actions = el("div", "step-actions");
    const per = opts.limits && money(opts.limits.per_order_usd);
    if (opts.onTry && codes.includes(PER_ORDER_CODE) && per) {
      const cap = per.slice(1).replace(/\.00$/, "");
      const make = Object.prototype.hasOwnProperty.call(TRY_TEXT, step.function)
        ? TRY_TEXT[step.function]
        : (c) => "Try " + c + " USDC";
      const chip = el("button", "prompt-chip", "Try " + cap + " USDC");
      chip.type = "button";
      chip.addEventListener("click", () => opts.onTry(make(cap)));
      actions.append(chip);
    }
    if (opts.onAsk) {
      const chip = el("button", "prompt-chip", "Why is this blocked?");
      chip.type = "button";
      chip.addEventListener("click", () => opts.onAsk("Why is this blocked?"));
      actions.append(chip);
    }
    if (actions.children.length) body.append(actions);
  }
  const side = el("div", "step-side");
  side.append(el("span", "state-word", info.word));
  addRuleChips(el, side, codes);
  body.append(side);

  details.append(summary, body);
  li.append(details);
  return li;
}

function orderLine(order) {
  if (!order) return null;
  const amt = formatAmount(order.amount, 6);
  return (
    "Order: " +
    order.function +
    (amt ? " " + amt + " " + order.token : "") +
    (order.protocol ? " on " + order.protocol : "") +
    (order.proposed_by ? " (proposed by " + order.proposed_by + ")" : "")
  );
}

// One audit record (DecisionRecord shape) as a step card. `extra` nodes go under the text.
export function recordCard(ctx, record, extra = []) {
  const { el } = ctx;
  const x = explainRecord(record);
  const info = KINDS[x.outcome] || KINDS.no_action;
  const { li, body, side } = shell(el, info);
  const done = info.kind === "done";
  // The plain sentence leads; rule codes are small secondary chips (A-2).
  const lead = done
    ? describeOrder(record.order, true) || x.headline
    : x.rules.length
      ? x.rules[0].sentence
      : x.headline;
  body.append(el("p", "step-title", lead));
  if (lead !== x.headline) body.append(el("p", "step-sub", x.headline));
  for (const r of x.rules.slice(done || !x.rules.length ? 0 : 1)) {
    body.append(el("p", "step-sub", r.sentence));
  }
  const src = record.decision || record.no_action;
  if (src && src.reason) body.append(el("p", "step-meta", "Engine note: " + src.reason));
  const line = orderLine(record.order);
  if (line) body.append(el("p", "step-meta", line));
  if (record.approval && record.approval.status) {
    body.append(el("p", "step-meta", "Approval: " + record.approval.status + " by " + record.approval.by));
  }
  const when = relTime(record.created_at);
  if (when) {
    const t = el("p", "step-meta", when);
    t.setAttribute("title", String(record.created_at));
    body.append(t);
  }
  for (const n of extra) body.append(n);
  addRuleChips(
    el,
    side,
    x.rules.map((r) => r.code),
  );
  if (record.planner_client && PLANNER_WORDS[record.planner_client]) {
    side.append(el("span", "chip", PLANNER_WORDS[record.planner_client]));
  }
  const link = txLink(el, x.txHash);
  if (link) side.append(link);
  return li;
}

// One short line for "Recent activity": {icon, text, tone, when}.
export function recordLine(record) {
  const x = explainRecord(record);
  const info = KINDS[x.outcome] || KINDS.no_action;
  const what = describeOrder(record.order, info.kind === "done");
  let text;
  if (info.kind === "done") text = what || "Done";
  else if (info.kind === "blocked") text = "Blocked: " + (what || "an order");
  else if (info.kind === "pending") text = "Waiting for your approval: " + (what || "an order");
  else if (info.kind === "fail") text = "Failed: " + (what || "an order");
  else text = "No action";
  return { icon: info.icon, text, tone: info.kind, when: relTime(record.created_at) };
}
