// Shimmer placeholders with the final layout of the Portfolio cards and the Activity rows, so
// nothing jumps when the data arrives. Pure markup: the shimmer is CSS and is switched off by
// prefers-reduced-motion. A status line tells a screen reader what is loading.
function block(el, cls) {
  const n = el("span", "sk " + cls);
  n.setAttribute("aria-hidden", "true");
  return n;
}

function busy(el, tag, cls, label) {
  const n = el(tag, cls);
  n.setAttribute("aria-busy", "true");
  n.append(el("span", "sr-only", label));
  return n;
}

export function portfolioSkeleton(el) {
  const wrap = busy(el, "div", "page-grid skeleton-block", "Loading balances…");
  const top = el("div", "grid-2");
  const total = el("div", "card");
  total.append(block(el, "sk-label"), block(el, "sk-total"), block(el, "sk-bar"), block(el, "sk-line short"));
  const earn = el("div", "card");
  earn.append(block(el, "sk-label"), block(el, "sk-big"), block(el, "sk-line"));
  top.append(total, earn);
  const hold = el("div", "card holdings");
  hold.append(el("header", "", ""));
  for (let i = 0; i < 3; i++) {
    const r = el("div", "hold-row");
    r.append(block(el, "sk-line"), block(el, "sk-line short"), block(el, "sk-line short"), block(el, "sk-btn"));
    hold.append(r);
  }
  const two = el("div", "grid-eq");
  for (let i = 0; i < 2; i++) {
    const c = el("div", "card");
    c.append(block(el, "sk-label"), block(el, "sk-line"), block(el, "sk-line"), block(el, "sk-line short"));
    two.append(c);
  }
  wrap.append(top, hold, two);
  return wrap;
}

export function activitySkeleton(el, rows = 3) {
  const out = [];
  for (let i = 0; i < rows; i++) {
    const li = el("li", "step-card skeleton-block");
    li.setAttribute("aria-hidden", "true");
    const body = el("div", "step-body");
    body.append(block(el, "sk-line"), block(el, "sk-line short"));
    li.append(block(el, "sk-icon"), body);
    out.push(li);
  }
  const status = el("li", "sr-only", "Loading decisions…");
  status.setAttribute("role", "status");
  return [status, ...out];
}
