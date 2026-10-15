// Rules: a read-only summary of the owner's limits, from GET /limits (contract C4, board D).
// Nothing here changes a limit: the policy engine checks every order against these values.
import { emptyState, frozenCard } from "../gauges.js";
import { getLimits, limitRows } from "../limits.js";

function skeletonRows(el) {
  const box = el("div", "rules-table skeleton-block");
  box.setAttribute("aria-busy", "true");
  box.setAttribute("aria-label", "Loading your rules");
  for (let i = 0; i < 5; i++) {
    const row = el("div", "rule-row");
    const left = el("div", "rule-main");
    left.append(el("span", "sk sk-line"), el("span", "sk sk-line short"));
    row.append(left, el("span", "sk sk-value"));
    box.append(row);
  }
  return box;
}

export function render(ctx) {
  const { el, api } = ctx;
  const root = el("section", "stack rules-page");
  const head = el("div");
  head.append(
    el("h1", "", "What your agent may and may not do"),
    el("p", "lead", "Code enforces these rules before anything is signed. The assistant can only propose."),
  );
  const body = el("div", "stack");
  root.append(head, body);

  function row(r) {
    const li = el("div", "rule-row");
    li.dataset.rule = r.id;
    const main = el("div", "rule-main");
    main.append(el("div", "rule-name", r.name), el("div", "rule-text", r.text));
    if (r.used) main.append(el("div", "rule-used", r.used));
    li.append(main);
    if (r.value !== null) li.append(el("div", "rule-value", r.value));
    li.append(el("span", "chip " + r.chip.tone, r.chip.text));
    return li;
  }

  async function load() {
    body.replaceChildren(skeletonRows(el));
    const me = await ctx.refreshSession();
    if (!me) {
      body.replaceChildren(
        emptyState(el, "Connect a wallet to see your rules.", null, { label: "Set up your agent", hash: "#/setup" }, "div"),
      );
      return;
    }
    let limits;
    try {
      limits = await getLimits(api);
    } catch (e) {
      body.replaceChildren(
        el("p", "msg error", "Your limits could not be loaded: " + ctx.describeError(e) + " Nothing is shown rather than a guess."),
      );
      return;
    }
    const parts = [];
    if (limits.emergency_stop === "stopped") parts.push(frozenCard(el));
    const table = el("div", "rules-table");
    table.setAttribute("role", "list");
    for (const r of limitRows(limits)) {
      const li = row(r);
      li.setAttribute("role", "listitem");
      table.append(li);
    }
    parts.push(table, el("p", "note", "Read-only here. Changing limits will be a separate, signed step."));
    body.replaceChildren(...parts);
  }

  ctx.onChange(() => {
    if (root.isConnected !== false) load();
  });
  load();
  return root;
}
