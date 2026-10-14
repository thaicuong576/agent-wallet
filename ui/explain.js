// Plain-language explanations. One table keyed by rule code (contracts/rule-codes.json) and by
// execution or job error code. Pure functions, no DOM, so they can be tested under node.
// Evidence values come from the API: they are only ever put into strings, never into markup.

// kind: "blocked" | "waiting" | "info". detail: optional template, used only when every
// {placeholder} it names is present in the rule's evidence.
export const RULES = {
  "ACCESS.TOKEN_NOT_ALLOWED": {
    kind: "blocked",
    text: "Blocked: this order uses a token your policy does not list.",
  },
  "ACCESS.PROTOCOL_NOT_ALLOWED": {
    kind: "blocked",
    text: "Blocked: this order targets a protocol your policy does not list.",
  },
  "ACCESS.FUNCTION_NOT_ALLOWED": {
    kind: "blocked",
    text: "Blocked: this order calls an action your policy does not allow.",
  },
  "ACCESS.RECIPIENT_NOT_ALLOWED": {
    kind: "blocked",
    text: "Blocked: the funds would go to an address your policy does not list.",
  },
  "ACCESS.UNLIMITED_APPROVAL": {
    kind: "blocked",
    text: "Blocked: the order asked for an open-ended spending allowance. Only the exact amount may be approved.",
  },
  "ACCESS.OWNER_ONLY_FUNCTION": {
    kind: "blocked",
    text: "Blocked: minting and withdrawing are owner actions. The agent is never allowed to do them.",
  },
  "LIMIT.PER_TX": {
    kind: "blocked",
    text: "Blocked: this order is larger than the most you allow for one transaction.",
    detail: "It was {order_usd} USD against a limit of {per_tx_usd} USD.",
  },
  "LIMIT.PER_DAY": {
    kind: "blocked",
    text: "Blocked: this order would push today's spending over your daily limit.",
    detail: "Daily limit: {per_day_usd} USD.",
  },
  "LIMIT.TX_PER_HOUR": {
    kind: "blocked",
    text: "Blocked: the agent has already made the most transactions you allow in one hour.",
  },
  "ALLOC.MAX_WEIGHT_PER_ASSET": {
    kind: "blocked",
    text: "Blocked: after this order one asset would be a bigger share of the portfolio than you allow.",
  },
  "ALLOC.MIN_STABLE_WEIGHT": {
    kind: "blocked",
    text: "Blocked: after this order the stablecoin share would drop below your minimum.",
  },
  "ALLOC.WITHIN_REBALANCE_BAND": {
    kind: "info",
    text: "Nothing to do: the portfolio is already close enough to your targets.",
  },
  "RISK.POOL_TVL_TOO_LOW": {
    kind: "blocked",
    text: "Blocked: the lending pool holds less money than your policy requires.",
  },
  "RISK.REWARD_APY_SHARE_TOO_HIGH": {
    kind: "blocked",
    text: "Blocked: too much of the advertised yield comes from reward tokens rather than interest.",
  },
  "RISK.APY_UNSTABLE": {
    kind: "blocked",
    text: "Blocked: the yield has moved too much recently to be trusted.",
  },
  "RISK.DEPEG_TOO_WIDE": {
    kind: "blocked",
    text: "Blocked: the stablecoin price has strayed too far from one dollar.",
  },
  "RISK.UTILIZATION_TOO_HIGH": {
    kind: "blocked",
    text: "Blocked: the lending pool is too fully borrowed, so withdrawals could be slow.",
  },
  "RISK.SLIPPAGE_TOO_HIGH": {
    kind: "blocked",
    text: "Blocked: the expected price impact of the trade is above your limit.",
  },
  "RISK.COST_EXCEEDS_BENEFIT": {
    kind: "blocked",
    text: "Blocked: the fees would eat more than the order could earn.",
  },
  "RISK.DATA_STALE": {
    kind: "blocked",
    text: "Blocked: the market data was too old to rely on.",
  },
  "TIER.READ_ONLY": {
    kind: "blocked",
    text: "Blocked: the agent is set to read only, so it cannot propose or run anything.",
  },
  "TIER.PROPOSE_ONLY": {
    kind: "blocked",
    text: "Blocked: the agent may only propose. Nothing runs until you raise its tier.",
  },
  "TIER.EMERGENCY_STOP": {
    kind: "blocked",
    text: "Blocked: emergency stop is on.",
  },
  "TIER.EMERGENCY_STOP_RELEASED": {
    kind: "info",
    text: "Emergency stop was released. The agent may act inside your policy again.",
  },
  "TIER.GLOBAL_STOP": {
    kind: "blocked",
    text: "Blocked: the service operator has paused every agent. Nothing can run until they lift it.",
  },
  "TIER.POLICY_CHANGED": {
    kind: "info",
    text: "You changed the policy. New orders are checked against the new limits.",
  },
  "TIER.APPROVAL_REQUIRED": {
    kind: "waiting",
    text: "Waiting for you: this order is above your approval threshold, so it needs your approval.",
    detail: "Threshold: {escalate_above_usd} USD; this order: {order_usd} USD.",
  },
  "TIER.APPROVAL_REJECTED": {
    kind: "blocked",
    text: "Not executed: you rejected this order.",
  },
};

// Error codes that appear on executions and jobs (not policy rules).
export const ERRORS = {
  SIMULATION_FAILED:
    "The transaction failed a dry run, so it was never sent. For a second test-USDC mint this usually means the faucet's one-hour cooldown has not passed.",
  GAS_BELOW_FLOOR: "The agent wallet has too little ETH for gas. Send some ETH to the agent address.",
  "gas-below-floor": "The agent wallet has too little ETH for gas. Send some ETH to the agent address.",
  "rate-limited": "Too many requests. Wait a little and try again.",
  "job-in-progress": "Another run is still in progress. Wait for it to finish.",
  stopped: "The agent is stopped, so nothing can run.",
  unauthenticated: "Your session ended. Connect your wallet again.",
  "signature-invalid": "That signature did not match your address. Try again.",
  "step-up-required": "This action needs a fresh signature from your wallet.",
  "owner-only-function": "That action is reserved for the owner.",
  "custody-unavailable": "The agent key is temporarily unavailable. Try again shortly.",
  csrf: "The request was refused as unsafe. Reload the page and try again.",
};

export const OUTCOMES = {
  executed: { icon: "✓", label: "Executed", tone: "ok" },
  denied: { icon: "✕", label: "Blocked", tone: "bad" },
  pending_approval: { icon: "…", label: "Waiting for you", tone: "wait" },
  failed: { icon: "!", label: "Failed", tone: "fail" },
  no_action: { icon: "–", label: "No action", tone: "neutral" },
};

const SAFE_VALUE = /^[A-Za-z0-9_.\-]{1,40}$/;

export function fillTemplate(template, evidence) {
  const ev = evidence && typeof evidence === "object" ? evidence : {};
  let complete = true;
  const out = template.replace(/\{([a-z_0-9]+)\}/g, (_, key) => {
    const v = ev[key];
    if ((typeof v === "string" || typeof v === "number") && SAFE_VALUE.test(String(v))) {
      return String(v);
    }
    complete = false;
    return "";
  });
  return complete ? out : null;
}

// Returns { kind, sentence, known, code }. Unknown codes still produce a readable sentence.
export function explainRule(code, evidence) {
  const entry = Object.prototype.hasOwnProperty.call(RULES, code) ? RULES[code] : null;
  if (!entry) {
    return {
      code: String(code),
      kind: "info",
      known: false,
      sentence: "The policy engine applied a rule this page does not have a description for.",
    };
  }
  let sentence = entry.text;
  if (entry.detail) {
    const d = fillTemplate(entry.detail, evidence);
    if (d) sentence += " " + d;
  }
  return { code, kind: entry.kind, known: true, sentence };
}

export function explainError(code) {
  if (code && Object.prototype.hasOwnProperty.call(ERRORS, code)) return ERRORS[code];
  return code ? "It failed with code " + String(code) + "." : "It failed.";
}

// One audit record -> what the feed shows. Never returns markup.
export function explainRecord(record) {
  const outcome = record && OUTCOMES[record.outcome] ? record.outcome : "no_action";
  const src = record && (record.decision || record.no_action);
  const rules = src && Array.isArray(src.rules_hit) ? src.rules_hit : [];
  const sentences = rules.map((r) => explainRule(r.code, r.evidence));
  const exec = record && record.execution;
  let headline;
  if (outcome === "executed") headline = "The agent made this move and it went through.";
  else if (outcome === "denied") headline = "The policy engine blocked this order. Nothing was sent.";
  else if (outcome === "pending_approval") headline = "This order is waiting for your approval.";
  else if (outcome === "failed") headline = explainError(exec && exec.error_code);
  else headline = sentences.length ? sentences[0].sentence : "The agent decided to do nothing.";
  return {
    outcome,
    ...OUTCOMES[outcome],
    headline,
    rules: sentences,
    txHash: exec && exec.status === "executed" ? exec.tx_hash : null,
  };
}

export function formatUnits(value, decimals, maxFrac = 4) {
  if (typeof value !== "string" || !/^[0-9]+$/.test(value)) return null;
  const d = Number.isInteger(decimals) && decimals >= 0 && decimals <= 36 ? decimals : 0;
  const s = value.padStart(d + 1, "0");
  const whole = s.slice(0, s.length - d);
  let frac = d ? s.slice(s.length - d).slice(0, maxFrac).replace(/0+$/, "") : "";
  return whole + (frac ? "." + frac : "");
}

export function formatAmount(amount, maxFrac = 2) {
  if (!amount || typeof amount !== "object") return null;
  return formatUnits(amount.value, amount.decimals, maxFrac);
}

export function formatWei(wei, maxFrac = 5) {
  return formatUnits(wei, 18, maxFrac);
}

// "0.01" -> 10000000000000000n. Strings only, never floats. Returns null when not a plain
// non-negative decimal with at most `decimals` fractional digits.
export function parseUnits(text, decimals) {
  const m = /^([0-9]{1,20})(?:\.([0-9]{1,36}))?$/.exec(String(text).trim());
  if (!m || (m[2] || "").length > decimals) return null;
  const frac = (m[2] || "").padEnd(decimals, "0");
  return BigInt(m[1] + frac);
}

export function shortHash(h) {
  return typeof h === "string" && h.length > 14 ? h.slice(0, 8) + "…" + h.slice(-6) : String(h || "");
}
