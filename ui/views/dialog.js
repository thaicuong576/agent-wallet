// A focused dialog: a scrim, a card with a title and a Close button, focus trapped inside. Same
// behaviour as confirm.js. Escape, a click on the scrim or Close closes it and focus goes back to
// the control that opened it. Every string lands in textContent.
const FOCUSABLE = "button, input, select, textarea, a[href]";
let seq = 0;

// True when the node and every ancestor are not hidden.
function shown(node) {
  for (let n = node; n; n = n.parentNode) {
    if (n.hidden) return false;
  }
  return true;
}

export function openDialog(ctx, { title }) {
  const { el } = ctx;
  const doc = globalThis.document;
  const opener = doc.activeElement || null;
  const id = "dlg-" + ++seq;

  const scrim = el("div", "modal-scrim dlg-scrim");
  const card = el("div", "modal dlg");
  card.setAttribute("role", "dialog");
  card.setAttribute("aria-modal", "true");
  card.setAttribute("aria-labelledby", id + "-title");
  const head = el("div", "dlg-head");
  const h = el("h2", "modal-title", title);
  h.id = id + "-title";
  const close = el("button", "dlg-close", "Close");
  close.type = "button";
  head.append(h, close);
  const body = el("div", "dlg-body");
  card.append(head, body);
  scrim.append(card);

  let done = false;
  let finish = () => {};
  const closed = new Promise((resolve) => {
    finish = resolve;
  });
  function dismiss() {
    if (done) return;
    done = true;
    if (scrim.remove) scrim.remove();
    if (opener && opener.focus) opener.focus();
    finish();
  }
  // Computed at key time, so controls that were hidden or disabled later are skipped.
  function focusables() {
    return Array.from(card.querySelectorAll(FOCUSABLE)).filter((n) => !n.disabled && shown(n));
  }

  scrim.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      if (e.preventDefault) e.preventDefault();
      dismiss();
    } else if (e.key === "Tab") {
      const order = focusables();
      if (!order.length) return;
      const at = order.indexOf(doc.activeElement);
      let next;
      if (e.shiftKey) next = at <= 0 ? order.length - 1 : at - 1;
      else next = at === -1 || at === order.length - 1 ? 0 : at + 1;
      if (e.preventDefault) e.preventDefault();
      order[next].focus();
    }
  });
  scrim.addEventListener("click", (e) => {
    if (e.target === scrim) dismiss();
  });
  close.addEventListener("click", () => dismiss());

  doc.body.append(scrim);
  const first = focusables()[0];
  if (first) first.focus();
  return { card, body, close: dismiss, closed };
}
