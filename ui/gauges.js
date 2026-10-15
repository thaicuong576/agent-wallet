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
  const a = el("a", "cta " + cls, label);
  a.setAttribute("href", hash);
  return a;
}

// An empty state that says what to do next, with a button that goes there (A-9).
export function emptyState(el, text, hint, action) {
  const box = el("li", "empty");
  box.append(el("p", "", text));
  if (hint) box.append(el("p", "note", hint));
  if (action) box.append(cta(el, action.label, action.hash));
  return box;
}

// Fills the elements of the top bar from the owner's tier state (A-3). `state` is null when
// nobody is connected, "unavailable" when the tier could not be read, else {tier, emergency_stop}.
export const FROZEN_TEXT = "EMERGENCY STOP ACTIVE · All orders blocked until you release it.";
export function applyTopbar({ chip, stopBtn, stopMsg, masthead }, state) {
  const frozen = Boolean(state && state !== "unavailable" && state.emergency_stop);
  if (masthead) masthead.className = "masthead" + (frozen ? " frozen" : "");
  if (!state) {
    chip.textContent = "Not connected";
    stopBtn.disabled = true;
    stopBtn.textContent = "Emergency stop";
    stopBtn.dataset.stopped = "0";
    if (stopMsg.dataset.frozen === "1") {
      stopMsg.textContent = "";
      stopMsg.dataset.frozen = "0";
    }
    return;
  }
  stopBtn.disabled = false;
  if (state === "unavailable") {
    chip.textContent = "Tier unavailable";
    return;
  }
  chip.textContent = "Tier " + state.tier + (frozen ? " · FROZEN" : "");
  chip.className = "chip" + (frozen ? " bad" : "");
  stopBtn.textContent = frozen ? "Release stop (needs signature)" : "Emergency stop";
  stopBtn.dataset.stopped = frozen ? "1" : "0";
  if (frozen) {
    stopMsg.textContent = FROZEN_TEXT;
    stopMsg.dataset.frozen = "1";
  } else if (stopMsg.dataset.frozen === "1") {
    stopMsg.textContent = "";
    stopMsg.dataset.frozen = "0";
  }
}
