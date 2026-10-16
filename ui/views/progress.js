// Staged progress card for an agent job: honest stage rows with elapsed time.
// Pure UI helper. Nodes come from the `el(tag, cls, text)` the caller passes in (the same
// signature as ctx.el in ui/main.js). No network, no storage. Text is set with textContent only.

export const SLOW_POST_MS = 2000;
const SHOW_TIME_MS = 2000;
const TICK_MS = 1000;

export const LONG_NOTES = [
  { at: 10000, text: "Still working. The model can take up to 30 seconds." },
  { at: 30000, text: "This is taking longer than usual. You can stop the request." },
  { at: 60000, text: "This has taken over a minute. You can stop it, or check Activity later." },
];

const POST_SLOW_SUB = "Waking up the server. Your message is safe.";
const CANCEL_NOTE = "Stopping. Steps not sent yet will be skipped.";
const HINT = "You can stop until an order is sent.";
const WORKING_TITLE = "Working on your request";
const WORKING_NOT_SENT = "Reading your wallet, planning and checking your rules. Nothing has been sent yet.";
const WORKING_SENT_SEEN = "Reading your wallet, planning and checking your rules.";
const DONE_ICON = "✓";
const NOW_ICON = "●";

export function formatElapsed(ms) {
  const total = Math.max(0, Math.floor(ms / 1000));
  if (total < 60) return `${total} s`;
  const min = Math.floor(total / 60);
  const sec = total % 60;
  return sec === 0 ? `${min} min` : `${min} min ${sec} s`;
}

export function stageOf(phase, sentSeen) {
  if (phase === "queued") {
    return { id: "queued", title: "Waiting to start", sub: "The agent is picking up your request." };
  }
  if (phase === "sent") {
    return { id: "sent", title: "Sending to Base Sepolia", sub: "Waiting for the network to confirm." };
  }
  if (phase === "confirming") {
    return { id: "confirming", title: "Confirming on Base Sepolia", sub: "Waiting for the receipt." };
  }
  return { id: "working", title: WORKING_TITLE, sub: sentSeen ? WORKING_SENT_SEEN : WORKING_NOT_SENT };
}

function noteFor(elapsed) {
  let text = "";
  for (const note of LONG_NOTES) {
    if (elapsed >= note.at) text = note.text;
  }
  return text;
}

export function createProgress(el, opts = {}) {
  const now = opts.now || Date.now;
  const autoTick = opts.autoTick === undefined ? Boolean(globalThis.window) : opts.autoTick;
  const startedAt = now();
  const rows = [];
  let pendingRow = null;
  let sentSeen = false;
  let cancelling = false;
  let destroyed = false;
  let seenConnected = false;
  let timer = null;

  const card = el("div", "progress-card");
  card.setAttribute("role", "group");
  card.setAttribute("aria-label", "Agent progress");
  const head = el("p", "progress-head", "Working on it");
  const headTime = el("span", null, "");
  headTime.setAttribute("aria-hidden", "true");
  head.append(headTime);
  const list = el("ol", "progress-rows");
  list.setAttribute("aria-live", "polite");
  const note = el("p", "progress-note", "");
  const actions = el("div", "progress-actions");
  actions.append(el("span", "progress-hint", HINT));
  let stop = null;
  if (opts.onStop) {
    stop = el("button", "btn progress-stop", "Stop");
    stop.setAttribute("type", "button");
    stop.addEventListener("click", () => opts.onStop());
    actions.append(stop);
  } else {
    actions.hidden = true;
  }
  card.append(head, list, note, actions);

  function openRow(id, title, sub) {
    const li = el("li", "progress-row now");
    li.setAttribute("aria-current", "step");
    const icon = el("span", "progress-icon", NOW_ICON);
    icon.setAttribute("aria-hidden", "true");
    const titleEl = el("p", "progress-title", title);
    const subEl = el("p", "progress-sub", sub);
    const text = el("div", "progress-text");
    text.append(titleEl, subEl);
    const time = el("span", "progress-time", "");
    time.setAttribute("aria-hidden", "true");
    li.append(icon, text, time);
    list.append(li);
    const row = { id, start: now(), end: null, li, icon, titleEl, subEl, time };
    rows.push(row);
    return row;
  }

  function closeRow(row) {
    row.end = now();
    row.li.className = "progress-row done";
    row.li.removeAttribute("aria-current");
    row.icon.textContent = DONE_ICON;
  }

  function current() {
    const last = rows[rows.length - 1];
    return last && last.end === null ? last : null;
  }

  function refresh() {
    const t = now();
    headTime.textContent = ` · ${formatElapsed(t - startedAt)}`;
    for (const row of rows) {
      const dur = (row.end === null ? t : row.end) - row.start;
      row.time.textContent = dur >= SHOW_TIME_MS ? formatElapsed(dur) : "";
    }
    if (pendingRow && t - startedAt >= SLOW_POST_MS) pendingRow.subEl.textContent = POST_SLOW_SUB;
    note.textContent = cancelling ? CANCEL_NOTE : noteFor(t - startedAt);
    if (stop) {
      const cur = current();
      const late = cur !== null && (cur.id === "sent" || cur.id === "confirming");
      stop.disabled = cancelling || late;
    }
  }

  function setReceived() {
    if (!pendingRow) return;
    closeRow(pendingRow);
    pendingRow.titleEl.textContent = "Message received";
    pendingRow.subEl.textContent = "";
    pendingRow = null;
    refresh();
  }

  function setPhase(phase) {
    if (pendingRow) setReceived();
    const stage = stageOf(phase, sentSeen);
    const cur = current();
    if (cur && cur.id === stage.id) return;
    if (cur) closeRow(cur);
    if (stage.id === "sent" || stage.id === "confirming") sentSeen = true;
    openRow(stage.id, stage.title, stage.sub);
    refresh();
  }

  function setCancelling() {
    cancelling = true;
    refresh();
  }

  function stopTimer() {
    if (timer !== null) clearInterval(timer);
    timer = null;
  }

  function tick() {
    if (destroyed) return;
    if (card.isConnected) {
      seenConnected = true;
    } else if (seenConnected) {
      stopTimer();
      return;
    }
    refresh();
  }

  function destroy() {
    destroyed = true;
    stopTimer();
  }

  pendingRow = openRow("pending", "Sending your message", "");
  refresh();
  if (autoTick) timer = setInterval(tick, TICK_MS);

  return { node: card, setReceived, setPhase, setCancelling, tick, destroy };
}
