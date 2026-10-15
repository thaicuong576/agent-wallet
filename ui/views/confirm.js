// A small modal confirm: white card, hairline border, ink scrim. Focus is trapped inside, Escape
// closes it and focus goes back to the control that opened it. Resolves true only for the
// confirm button. Every string lands in textContent.
let seq = 0;

export function confirmDialog(ctx, { title, body, cancelLabel, confirmLabel, danger = true }) {
  const { el } = ctx;
  const doc = globalThis.document;
  const opener = doc.activeElement || null;
  const id = "dlg-" + ++seq;

  const scrim = el("div", "modal-scrim");
  const card = el("div", "modal");
  card.setAttribute("role", "alertdialog");
  card.setAttribute("aria-modal", "true");
  card.setAttribute("aria-labelledby", id + "-title");
  card.setAttribute("aria-describedby", id + "-body");
  const h = el("h2", "modal-title", title);
  h.id = id + "-title";
  const p = el("p", "modal-body", body);
  p.id = id + "-body";
  const keep = el("button", "", cancelLabel);
  keep.type = "button";
  const go = el("button", danger ? "solid-danger" : "primary", confirmLabel);
  go.type = "button";
  const actions = el("div", "modal-actions");
  actions.append(keep, go);
  card.append(h, p, actions);
  scrim.append(card);

  return new Promise((resolve) => {
    let done = false;
    function close(result) {
      if (done) return;
      done = true;
      if (scrim.remove) scrim.remove();
      if (opener && opener.focus) opener.focus();
      resolve(result);
    }
    scrim.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        if (e.preventDefault) e.preventDefault();
        close(false);
      } else if (e.key === "Tab") {
        const order = [keep, go];
        const at = order.indexOf(doc.activeElement);
        const next = e.shiftKey ? (at <= 0 ? order.length - 1 : at - 1) : at === order.length - 1 ? 0 : at + 1;
        if (e.preventDefault) e.preventDefault();
        order[next].focus();
      }
    });
    scrim.addEventListener("click", (e) => {
      if (e.target === scrim) close(false);
    });
    keep.addEventListener("click", () => close(false));
    go.addEventListener("click", () => close(true));
    doc.body.append(scrim);
    keep.focus(); // the safe choice is the default one
  });
}
