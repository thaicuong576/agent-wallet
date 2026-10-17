// Pure helpers for the gauges and small widgets: no network, no storage. Money is handled as
// BigInt from decimal strings, never as floats. API strings only ever land in textContent.
import { parseUnits } from "./explain.js";

const SCALE = 12;

// Daily spend against the daily limit. `spent` and `limit` are decimal USD strings.
// level: "ok" under 80 %, "warn" from 80 %, "full" at or over the limit, "unknown" if unreadable.
export function spendGauge(spent, limit) {
  const s = parseUnits(String(spent ?? ""), SCALE);
  const l = parseUnits(String(limit ?? ""), SCALE);
  if (s === null || l === null || l === 0n) {
    return { level: "unknown", pct: 0, spent: null, limit: null, remaining: null };
  }
  const raw = Number((s * 1000n) / l) / 10;
  const pct = Math.min(100, raw);
  const level = s >= l ? "full" : raw >= 80 ? "warn" : "ok";
  const left = s >= l ? 0n : l - s;
  const cents = (v) => {
    const c = (v * 100n) / 10n ** BigInt(SCALE);
    return (c / 100n).toString() + "." + (c % 100n).toString().padStart(2, "0");
  };
  return { level, pct, spent: cents(s), limit: cents(l), remaining: cents(left) };
}

// ETH against the gas floor, both wei strings.
export function gasGauge(ethWei, floorWei) {
  if (!/^[0-9]+$/.test(String(ethWei)) || !/^[0-9]+$/.test(String(floorWei))) {
    return { level: "unknown", above: false };
  }
  const above = BigInt(ethWei) >= BigInt(floorWei) && BigInt(ethWei) > 0n;
  return { level: above ? "ok" : "low", above };
}

// "30" or an HTTP date -> a sentence saying when the user can try again.
export function retryText(retryAfter, now = Date.now()) {
  if (retryAfter === null || retryAfter === undefined || retryAfter === "") {
    return "Wait a little and try again.";
  }
  const v = String(retryAfter).trim();
  let secs = null;
  if (/^[0-9]{1,6}$/.test(v)) secs = parseInt(v, 10);
  else {
    const t = Date.parse(v);
    if (!Number.isNaN(t)) secs = Math.max(0, Math.ceil((t - now) / 1000));
  }
  if (secs === null) return "Wait a little and try again.";
  if (secs <= 1) return "You can try again now.";
  if (secs < 90) return "You can try again in " + secs + " seconds.";
  return "You can try again in about " + Math.ceil(secs / 60) + " minutes.";
}

// A navigation link styled as a button. `hash` is one of our own routes.
export function cta(el, label, hash, cls = "primary") {
  const a = el("a", "btn " + cls, label);
  a.setAttribute("href", hash);
  return a;
}

// An empty state that says what to do next, with a button that goes there.
export function emptyState(el, text, hint, action, tag = "li") {
  const box = el(tag, "empty");
  box.append(el("p", "", text));
  if (hint) box.append(el("p", "note", hint));
  if (action) box.append(cta(el, action.label, action.hash));
  return box;
}

// "0x9D1A…373a": the owner address as a chip.
export function shortAddr(a) {
  const s = String(a || "");
  return s.length > 12 ? s.slice(0, 6) + "…" + s.slice(-4) : s;
}

// "2 min ago" from an ISO time; the date itself when it is old or unreadable.
export function relTime(iso, now = Date.now()) {
  const t = Date.parse(String(iso || ""));
  if (Number.isNaN(t)) return "";
  const s = Math.max(0, Math.round((now - t) / 1000));
  if (s < 60) return "just now";
  if (s < 3600) return Math.floor(s / 60) + " min ago";
  if (s < 86400) return Math.floor(s / 3600) + " h ago";
  if (s < 7 * 86400) return Math.floor(s / 86400) + " d ago";
  return String(iso).slice(0, 10);
}

// The in-view frozen card. Frozen has to be unmistakable without a full-width strip.
export const FROZEN_TEXT = "Emergency stop is on. All orders are blocked until you resume the agent.";
export function frozenCard(el) {
  const card = el("div", "frozen-card");
  card.setAttribute("role", "status");
  const icon = el("span", "step-icon", "✕");
  icon.className = "step-icon";
  icon.style.background = "var(--red)";
  const text = el("div");
  text.append(
    el("strong", "", "Your agent is frozen"),
    el(
      "p",
      "",
      "The emergency stop is on, so the agent will not answer or act. Use Resume in the top bar to continue; it needs one signature.",
    ),
  );
  card.append(icon, text);
  return card;
}

// Fills the header from the owner's tier state. `state` is null when nobody is connected,
// "unavailable" when the tier could not be read, else {tier, emergency_stop}.
export function applyTopbar({ pill, addrChip, stopBtn, stopMsg, masthead }, state, me) {
  const frozen = Boolean(state && state !== "unavailable" && state.emergency_stop);
  if (masthead) masthead.className = "masthead" + (frozen ? " frozen" : "");
  if (addrChip) {
    addrChip.hidden = !(state && me && me.address);
    addrChip.textContent = me && me.address ? shortAddr(me.address) : "";
    if (me && me.address) addrChip.setAttribute("title", me.address);
  }
  if (!state) {
    pill.hidden = true;
    pill.textContent = "";
    stopBtn.disabled = true;
    stopBtn.textContent = "Emergency stop";
    stopBtn.dataset.stopped = "0";
    return;
  }
  pill.hidden = false;
  stopBtn.disabled = false;
  if (state === "unavailable") {
    pill.className = "pill status warn";
    pill.textContent = "Status unavailable";
    return;
  }
  stopBtn.dataset.stopped = frozen ? "1" : "0";
  stopBtn.textContent = frozen ? "Resume (needs signature)" : "Emergency stop";
  if (frozen) {
    pill.className = "pill status bad";
    pill.textContent = "Stopped";
    pill.setAttribute("title", FROZEN_TEXT);
  } else if (state.tier === "T2") {
    pill.className = "pill status ok";
    pill.textContent = "● Agent active";
    pill.removeAttribute("title");
  } else {
    pill.className = "pill status warn";
    pill.textContent = state.tier === "T1" ? "Propose only" : "Read only";
    pill.removeAttribute("title");
  }
}
