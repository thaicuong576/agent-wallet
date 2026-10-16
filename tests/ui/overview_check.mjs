// Overview view against a fake DOM and a fake ctx. Prints one JSON object of named results;
// tests/ui/test_overview.py asserts them. No network, no browser.
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");

// The views import each other by relative path, so the whole ui tree is copied to a temp folder
// with a module marker. Nothing is written inside the repo.
const COPY = fs.mkdtempSync(path.join(os.tmpdir(), "aw-ui-overview-"));
fs.cpSync(path.join(ROOT, "ui"), COPY, { recursive: true });
fs.writeFileSync(path.join(COPY, "package.json"), '{"type":"module"}');
const { render } = await import(pathToFileURL(path.join(COPY, "views", "overview.js")).href);

const FIXTURE = (name) => JSON.parse(fs.readFileSync(path.join(ROOT, "ui", "fixtures", name), "utf8"));
const portfolioFixture = FIXTURE("portfolio.json");
const agentFixture = FIXTURE("agent.json");
const tierFixture = FIXTURE("tier.json");
const ME = "0x" + "1".repeat(40);

function pos(token, location, value, usd, bps) {
  return {
    token,
    location,
    balance: { value, decimals: token === "WETH" ? 18 : 6 },
    price_usd: "1.00",
    value_usd: usd,
    weight_bps: bps,
  };
}
const richPortfolio = {
  schema_version: 1,
  as_of: "2026-10-14T09:00:00Z",
  price_source: "mainnet_reference",
  positions: [
    pos("USDC", "wallet", "98000000", "98.00", 4949),
    pos("WETH", "wallet", "5000000000000000", "10.00", 505),
    pos("USDC", "aave-v3", "40000000", "40.00", 2020),
    pos("USDC", "aave-stata", "20000000", "20.00", 1010),
    pos("USDC", "morpho-blue", "30000000", "30.00", 1515),
  ],
  total_value_usd: "198.00",
  spent_24h_usd: "0.00",
  tx_count_1h: 0,
};
const emptyPortfolio = {
  schema_version: 1,
  as_of: "2026-10-14T09:00:00Z",
  price_source: "mainnet_reference",
  positions: [],
  total_value_usd: "0.00",
  spent_24h_usd: "0.00",
  tx_count_1h: 0,
};

// A fake DOM: only what the overview and the dialogs use. innerHTML throws, and every touch is counted.
let innerHTMLTouches = 0;
class FakeEl {
  constructor(tag, cls, text) {
    this.tag = tag;
    this.className = cls || "";
    this.children = [];
    this.attrs = {};
    this.style = {};
    this.textContent = text === undefined || text === null ? "" : String(text);
    this.hidden = false;
    this.disabled = false;
    this.type = "";
    this.value = "";
    this.id = "";
    this.inputMode = "";
    this.parentNode = null;
    this.handlers = {};
    this.isConnected = true;
  }
  get innerHTML() {
    innerHTMLTouches += 1;
    throw new Error("innerHTML is not allowed");
  }
  set innerHTML(value) {
    innerHTMLTouches += 1;
    throw new Error("innerHTML is not allowed: " + value);
  }
  append(...kids) {
    for (const k of kids) {
      const node = typeof k === "string" ? new FakeEl("#text", "", k) : k;
      node.parentNode = this;
      this.children.push(node);
    }
  }
  replaceChildren(...kids) {
    for (const c of this.children) c.parentNode = null;
    this.children = [];
    this.append(...kids);
  }
  remove() {
    const p = this.parentNode;
    if (p) p.children = p.children.filter((c) => c !== this);
    this.parentNode = null;
  }
  setAttribute(k, v) {
    this.attrs[k] = String(v);
  }
  getAttribute(k) {
    return k in this.attrs ? this.attrs[k] : null;
  }
  removeAttribute(k) {
    delete this.attrs[k];
  }
  addEventListener(type, fn) {
    (this.handlers[type] ||= []).push(fn);
  }
  focus() {
    doc.activeElement = this;
  }
  querySelectorAll(sel) {
    return walk(this).slice(1).filter((n) => sel.split(",").some((p) => n.tag === p.trim()));
  }
}

const doc = {
  body: new FakeEl("body", "", ""),
  activeElement: null,
  addEventListener() {},
  removeEventListener() {},
};
globalThis.document = doc;

const el = (tag, cls, text) => new FakeEl(tag, cls, text);
const walk = (node) => [node, ...node.children.flatMap(walk)];
const find = (root, pred) => walk(root).filter(pred);
const withClass = (root, cls) => find(root, (n) => n.className.split(" ").includes(cls));
const textOf = (n) => [n.textContent, ...n.children.map(textOf)].join(" ").replace(/\s+/g, " ").trim();
const buttonNamed = (root, text) => find(root, (n) => n.tag === "button" && n.textContent === text)[0];
const titles = () => find(doc.body, (n) => n.className === "modal-title").map(textOf);

async function settle() {
  for (let i = 0; i < 8; i++) await new Promise((r) => setImmediate(r));
}

async function click(node) {
  if (!node) return "missing";
  if (node.disabled) return "disabled";
  try {
    await Promise.all((node.handlers.click || []).map((f) => f({ target: node, preventDefault() {} })));
  } catch (e) {
    return "threw: " + e.message;
  }
  return "ok";
}

// api answers the real fixtures; any other call (the dialogs make some) resolves to an empty object.
// fixtures: true makes loadRecords and getLimits take their fixture branch. Records come from opts.records
// (none by default); limits from opts.limits (none by default, so the limit card says it is not available).
function makeCtx(opts) {
  const calls = { agent: 0, portfolio: 0, tier: 0 };
  const base = {
    fixtures: true,
    decisions: async () => {
      if (opts.recordsError) throw opts.recordsError;
      return { items: opts.records || [] };
    },
    fixtureFile: async (name) => {
      if (opts.limitsError) throw opts.limitsError;
      if (name !== "limits" || !opts.limits) throw new Error("no " + name + " fixture in this check");
      return { ...opts.limits };
    },
    agent: async () => {
      calls.agent += 1;
      if (opts.agentError) throw opts.agentError;
      return opts.agent === undefined ? agentFixture : opts.agent;
    },
    portfolio: async () => {
      calls.portfolio += 1;
      if (opts.portfolioError) throw opts.portfolioError;
      return opts.portfolio;
    },
    tier: async () => {
      calls.tier += 1;
      return opts.tier === undefined ? tierFixture : opts.tier;
    },
  };
  const api = new Proxy(base, { get: (t, k) => (k in t ? t[k] : async () => ({})) });
  const ctx = {
    el,
    api,
    state: { me: { address: ME } },
    wait: async () => {},
    describeError: (e) => (e && e.message) || "Something went wrong.",
    changed() {},
    go() {},
    refreshSession: async () => (opts.signedOut ? null : { address: ME }),
    onChange() {},
  };
  return { ctx, calls };
}

async function mount(opts = {}) {
  const { ctx, calls } = makeCtx(opts);
  const root = render(ctx);
  await settle();
  return { root, main: root.children[0], side: root.children[1], calls };
}

const out = {};

// (1) fixture data: the hero, the allocation bar, two groups, the lending row and the gas row.
{
  const { root } = await mount({ portfolio: portfolioFixture });
  const groups = withClass(root, "group");
  const idle = groups.find((g) => textOf(g).includes("Idle in the wallet"));
  const lending = withClass(root, "hname").find((n) => textOf(n).includes("supplied on Aave"));
  const ok = idle ? withClass(idle, "ok")[0] : undefined;
  out.fixture = {
    hero: textOf(withClass(root, "hero")[0]),
    aria: withClass(root, "alloc")[0].attrs["aria-label"],
    groups: groups.length,
    lendingRow: lending ? textOf(lending) : null,
    gasChip: ok ? textOf(ok) : null,
  };
}

// (2) rich data: five groups in order, protocol avatars on the non-idle headers, the WETH badge, five legend items.
{
  const { root } = await mount({ portfolio: richPortfolio });
  const groups = withClass(root, "group");
  const wethRow = withClass(root, "hname").find((n) => textOf(n).includes("Wrapped Ether"));
  out.rich = {
    order: groups.map((g) => textOf(find(g, (n) => n.tag === "b")[0])),
    avatars: groups.map((g) => withClass(withClass(g, "ghead")[0], "protocol-avatar").length),
    wethBadge: wethRow ? (withClass(wethRow, "token-letter-badge")[0] || {}).textContent : null,
    legend: withClass(root, "legend")[0].children.length,
  };
}

// (3) empty portfolio: the empty sentence, no holdings card, Add funds primary, Withdraw disabled.
{
  const { root } = await mount({ portfolio: emptyPortfolio });
  const add = buttonNamed(root, "Add funds");
  const wd = buttonNamed(root, "Withdraw");
  out.empty = {
    text: textOf(root).includes("Nothing here yet. Add funds, then ask the agent in Chat to put them to work."),
    holdingsCard: withClass(root, "holdings-card").length,
    addClass: add.className,
    withdrawDisabled: wd.disabled === true,
  };
  const signed = await mount({ signedOut: true });
  out.signedOut = textOf(signed.root).includes("Connect a wallet to see your agent.");
}

// (4) /portfolio rejects: the error sentence, no dollar amounts, Try again re-runs the load.
{
  const { root, calls } = await mount({ portfolioError: new Error("RPC down") });
  const retry = buttonNamed(root, "Try again");
  const before = calls.portfolio;
  await click(retry);
  await settle();
  const text = textOf(root);
  out.portfolioFails = {
    error: text.includes("Balances could not be read from Base Sepolia. Nothing is shown rather than zeros. RPC down"),
    dollarSigns: (text.match(/\$/g) || []).length,
    retryFound: retry !== undefined,
    callsBefore: before,
    callsAfter: calls.portfolio,
  };
}

// (5) /agent rejects: no gas row, both hero buttons disabled, the rest still shows.
{
  const { root } = await mount({ agentError: new Error("agent down"), portfolio: portfolioFixture });
  const add = buttonNamed(root, "Add funds");
  const wd = buttonNamed(root, "Withdraw");
  out.agentFails = {
    gasRow: textOf(root).includes("ETH for fees"),
    addDisabled: add.disabled === true,
    withdrawDisabled: wd.disabled === true,
    total: textOf(root).includes("$100.00"),
  };
}

// (6) emergency stop: the frozen card is the first child of the main column.
{
  const { main } = await mount({ tier: { ...tierFixture, emergency_stop: true }, portfolio: portfolioFixture });
  out.frozen = { firstIsFrozen: main.children[0].className.includes("frozen-card") };
}

// (7) Add funds and Withdraw open their dialogs.
{
  const { root } = await mount({ portfolio: portfolioFixture });
  doc.body.children = [];
  const r1 = await click(buttonNamed(root, "Add funds"));
  await settle();
  const r2 = await click(buttonNamed(root, "Withdraw"));
  await settle();
  out.actions = { add: r1, withdraw: r2, titles: titles() };
}

const LIMITS = FIXTURE("limits.json");
const DECISIONS = FIXTURE("decisions.json").items;
const full = () => ({ portfolio: portfolioFixture, limits: LIMITS, records: DECISIONS });
const sideTitles = (side) => side.children.map((c) => textOf(find(c, (n) => n.tag === "h2")[0]));

// (8) one pending order: the waiting note is the first child of the main column, the approvals card right after it.
{
  const { main } = await mount(full());
  const note = main.children[0];
  const approvals = main.children[1];
  const none = await mount({ portfolio: portfolioFixture, records: [] });
  out.waiting = {
    noteClass: note.className,
    noteText: textOf(note.children[0]),
    review: buttonNamed(note, "Review") !== undefined,
    afterNote: approvals ? textOf(approvals.children[0]) : null,
    noneNotes: withClass(none.root, "waiting-note").length,
    noneApprovals: textOf(none.root).includes("Waiting for your approval"),
  };
}

// (9) the side column: three cards in order, each under its own heading.
{
  const { side } = await mount(full());
  out.sideTitles = sideTitles(side);
}

// (10) wallet card: the short address and the network; Add funds opens the funds dialog; no agent disables it.
{
  const { side } = await mount(full());
  const wallet = side.children[0];
  const add = buttonNamed(wallet, "Add funds");
  doc.body.children = [];
  const clicked = await click(add);
  await settle();
  const strong = find(wallet, (n) => n.tag === "strong")[0];
  out.wallet = {
    short: textOf(strong),
    network: textOf(wallet).includes("Base Sepolia"),
    dialog: clicked,
    titles: titles(),
  };
  const off = await mount({ ...full(), agentError: new Error("agent down") });
  const offAdd = buttonNamed(off.side.children[0], "Add funds");
  out.walletNoAgent = { disabled: offAdd.disabled === true, title: offAdd.getAttribute("title") || "" };
}

// (11) limit card: 10 of 300 dollars, a progress bar at 3 (3.3 percent rounded), a link to the rules; no limit data says so.
{
  const { side } = await mount(full());
  const card = side.children[1];
  const bar = withClass(card, "bar")[0];
  const rules = find(card, (n) => n.attrs.href === "#/rules")[0];
  const text = textOf(card);
  out.limit = {
    spent: text.includes("$10.00"),
    limit: text.includes("$300.00"),
    role: bar.attrs.role,
    valuenow: bar.attrs["aria-valuenow"],
    rules: rules ? [textOf(rules), rules.attrs.href] : null,
  };
  const off = await mount({ ...full(), limitsError: new Error("limits down") });
  out.limitNoData = textOf(off.side.children[1]).includes("The limit is not available right now.");
}

// (12) recent activity: the last three records, newest first, each line as recordLine renders it.
{
  const { side } = await mount(full());
  const { recordLine } = await import(pathToFileURL(path.join(COPY, "views", "cards.js")).href);
  const lines = find(side.children[2], (n) => n.tag === "li").map(textOf);
  const want = [4, 3, 2].map((i) => {
    const x = recordLine(DECISIONS[i]);
    return (x.icon + " " + x.text + " " + x.when).replace(/\s+/g, " ").trim();
  });
  out.recent = {
    count: lines.length,
    newestFirst: JSON.stringify(lines) === JSON.stringify(want),
    lines: lines.map((s) => s.slice(0, 50)),
  };
  const empty = await mount({ portfolio: portfolioFixture, records: [], limits: LIMITS });
  out.recentEmpty = textOf(empty.side.children[2]).includes("Nothing yet. Ask the agent something in Chat.");
  const bad = await mount({ ...full(), recordsError: new Error("jobs down") });
  out.recentNoData = textOf(bad.side.children[2]).includes("Recent activity is not available right now.");
  out.recentNoDataLimitOk = textOf(bad.side.children[1]).includes("$300.00");
}

// (13) each side card failing alone leaves the other two rendered.
out.isolation = {};
for (const [name, extra] of Object.entries({
  wallet: { agentError: new Error("agent down") },
  limit: { limitsError: new Error("limits down") },
  recent: { recordsError: new Error("jobs down") },
})) {
  const { side } = await mount({ ...full(), ...extra });
  const [w, l, r] = side.children;
  out.isolation[name] = {
    titles: sideTitles(side),
    walletShows: textOf(w).includes("Base Sepolia"),
    limitShows: textOf(l).includes("$300.00"),
    recentLines: find(r, (n) => n.tag === "li").length,
    fallback: textOf(side).includes("Not available right now."),
  };
}

// (14) no innerHTML was read or written while rendering all of the above.
out.innerHTMLTouches = innerHTMLTouches;

fs.rmSync(COPY, { recursive: true, force: true });
process.stdout.write(JSON.stringify(out));
