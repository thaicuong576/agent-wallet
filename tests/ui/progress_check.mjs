// Staged progress card: ui/views/progress.js against a fake DOM and a fake clock.
// Prints one JSON object of named results; tests/ui/test_progress.py asserts them. No network.
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");

// The module has no imports, so one copy with a module marker is enough to load it as ESM.
const COPY = fs.mkdtempSync(path.join(os.tmpdir(), "aw-ui-progress-"));
fs.copyFileSync(path.join(ROOT, "ui", "views", "progress.js"), path.join(COPY, "progress.js"));
fs.writeFileSync(path.join(COPY, "package.json"), '{"type":"module"}');
const mod = await import(pathToFileURL(path.join(COPY, "progress.js")).href);
const { createProgress, formatElapsed, LONG_NOTES } = mod;

class FakeEl {
  constructor(tag, cls, text) {
    this.tag = tag;
    this.className = cls || "";
    this.children = [];
    this.attrs = {};
    this.textContent = text === undefined || text === null ? "" : String(text);
    this.hidden = false;
    this.disabled = false;
    this.isConnected = true;
    this.handlers = {};
  }
  append(...kids) {
    this.children.push(...kids);
  }
  setAttribute(k, v) {
    this.attrs[k] = String(v);
  }
  removeAttribute(k) {
    delete this.attrs[k];
  }
  addEventListener(type, fn) {
    this.handlers[type] = fn;
  }
  click() {
    if (!this.disabled && this.handlers.click) this.handlers.click();
  }
}

const el = (tag, cls, text) => new FakeEl(tag, cls, text);
const walk = (n, pred, out = []) => {
  if (pred(n)) out.push(n);
  for (const c of n.children) walk(c, pred, out);
  return out;
};
const hasClass = (n, cls) => n.className.split(" ").includes(cls);
const byClass = (root, cls) => walk(root, (n) => hasClass(n, cls));
const one = (root, cls) => byClass(root, cls)[0];
const rowsOf = (node) => byClass(node, "progress-row");
const titles = (node) => rowsOf(node).map((li) => one(li, "progress-title").textContent);
const subOf = (li) => one(li, "progress-sub").textContent;
const doneFlags = (node) => rowsOf(node).map((li) => hasClass(li, "done"));
const headOf = (node) => {
  const head = one(node, "progress-head");
  return head.textContent + head.children.map((c) => c.textContent).join("");
};
const noteOf = (node) => one(node, "progress-note").textContent;
const stopsOf = (node) => byClass(node, "progress-stop");

const out = {};

// (1) formatElapsed
out.fmt = [0, 7000, 59999, 60000, 65000, 120000].map(formatElapsed);

// Fake clock and fake timers.
let clock = 0;
const now = () => clock;
const realSetInterval = globalThis.setInterval;
const realClearInterval = globalThis.clearInterval;
let intervalsMade = 0;
const cleared = [];
globalThis.setInterval = () => {
  intervalsMade += 1;
  return intervalsMade;
};
globalThis.clearInterval = (id) => cleared.push(id);

try {
  // Default autoTick under node (no window): no timer is made.
  const quiet = createProgress(el, { now });
  out.intervals_default = intervalsMade;
  quiet.destroy();

  // (2) pending card
  clock = 0;
  const p = createProgress(el, { now });
  out.pending_titles = titles(p.node);
  out.pending_current = rowsOf(p.node).map((li) => hasClass(li, "now"));
  out.pending_sub_before = subOf(rowsOf(p.node)[0]);
  clock = 2500;
  p.tick();
  out.pending_sub_after = subOf(rowsOf(p.node)[0]);

  // (3) received, then the stage sequence
  clock = 0;
  const s = createProgress(el, { now });
  clock = 1000;
  s.setReceived();
  clock = 1500;
  s.setPhase("queued");
  clock = 2000;
  s.setPhase("rules_check");
  s.setPhase("rules_check");
  clock = 6000;
  s.setPhase("sent");
  out.seq_titles = titles(s.node);
  out.seq_done = doneFlags(s.node);
  out.seq_working_sub = subOf(rowsOf(s.node)[2]);

  // Same card after `sent`: a new rules_check row must not say "Nothing has been sent yet."
  clock = 0;
  const after = createProgress(el, { now });
  clock = 1000;
  after.setReceived();
  after.setPhase("sent");
  after.setPhase("rules_check");
  out.after_sent_titles = titles(after.node);
  out.after_sent_working_sub = subOf(rowsOf(after.node)[2]);

  // (4) elapsed text per row
  clock = 0;
  const e = createProgress(el, { now });
  clock = 5000;
  e.setReceived();
  clock = 5500;
  e.setPhase("queued");
  clock = 6000;
  e.tick();
  out.time_received = one(rowsOf(e.node)[0], "progress-time").textContent;
  out.time_short = one(rowsOf(e.node)[1], "progress-time").textContent;
  out.head = headOf(e.node);

  // (5) long-wait notes
  clock = 0;
  const n = createProgress(el, { now });
  const noteAt = (t) => {
    clock = t;
    n.tick();
    return noteOf(n.node);
  };
  out.notes = [noteAt(9000), noteAt(10000), noteAt(31000), noteAt(61000)];
  out.long_notes = LONG_NOTES.map((x) => x.text);

  // (6) Stop button
  clock = 0;
  const noStop = createProgress(el, { now });
  out.stop_without_handler = stopsOf(noStop.node).length;
  out.actions_hidden_without_handler = one(noStop.node, "progress-actions").hidden;

  let calls = 0;
  clock = 0;
  const w = createProgress(el, { now, onStop: () => (calls += 1) });
  out.stop_with_handler = stopsOf(w.node).length;
  out.stop_disabled_pending = stopsOf(w.node)[0].disabled;
  stopsOf(w.node)[0].click();
  out.stop_calls_after_click = calls;
  clock = 1000;
  w.setReceived();
  w.setPhase("queued");
  out.stop_disabled_queued = stopsOf(w.node)[0].disabled;
  clock = 6000;
  w.setPhase("sent");
  out.stop_disabled_sent = stopsOf(w.node)[0].disabled;
  stopsOf(w.node)[0].click();
  out.stop_calls_when_sent = calls;

  clock = 0;
  const c = createProgress(el, { now, onStop: () => {} });
  c.setReceived();
  c.setCancelling();
  out.cancel_disabled = stopsOf(c.node)[0].disabled;
  out.cancel_note = noteOf(c.node);

  // (7) unknown phase
  clock = 0;
  const u = createProgress(el, { now });
  u.setReceived();
  u.setPhase("mystery");
  out.mystery_title = titles(u.node).at(-1);

  // (8) destroy stops the timer; tick does nothing afterwards
  clock = 0;
  const d = createProgress(el, { now, autoTick: true });
  out.intervals_autotick = intervalsMade;
  clock = 4000;
  d.tick();
  const headBefore = headOf(d.node);
  d.destroy();
  out.cleared_after_destroy = cleared.length;
  clock = 9000;
  d.tick();
  out.head_before_destroy = headBefore;
  out.head_after_destroy = headOf(d.node);

  // Auto-stop: a card that was connected and is now detached stops its timer.
  clock = 0;
  const detach = createProgress(el, { now, autoTick: true });
  const before = cleared.length;
  detach.node.isConnected = true;
  detach.tick();
  detach.node.isConnected = false;
  detach.tick();
  out.cleared_after_detach = cleared.length - before;
} finally {
  globalThis.setInterval = realSetInterval;
  globalThis.clearInterval = realClearInterval;
  fs.rmSync(COPY, { recursive: true, force: true });
}

console.log(JSON.stringify(out));
