// Holdings grouped by what the money is doing. Pure functions: no DOM, no network, no storage.
// Money is decimal strings and BigInt cents only. Never floats.

export const GROUPS = [
  { id: "idle", label: "Idle in the wallet", note: "Not earning. Ready to use or withdraw." },
  { id: "lending", label: "Lent on Aave v3", note: "Earning interest." },
  { id: "vaults", label: "Aave vaults", note: "Vault shares that earn interest." },
  { id: "morpho", label: "Morpho Blue", note: "Lending markets." },
  { id: "swapped", label: "Swapped assets", note: "Bought through Uniswap v3. The price moves." },
];

const IDLE_TOKENS = ["USDC", "USDT", "USDbC"];
const UNIT = /^[0-9]+$/;
const DECIMAL = /^([0-9]{1,18})(?:\.([0-9]{1,18}))?$/;

function isObject(v) {
  return v !== null && typeof v === "object" && !Array.isArray(v);
}

// "98.00" -> 9800n. Rounds half up at the third decimal. Null when not a plain decimal.
function toCents(text) {
  if (typeof text !== "string") return null;
  const m = DECIMAL.exec(text);
  if (!m) return null;
  const frac = (m[2] || "") + "00";
  let cents = BigInt(m[1]) * 100n + BigInt(frac.slice(0, 2));
  if (frac.charAt(2) >= "5") cents += 1n;
  return cents;
}

function formatCents(cents) {
  const rest = cents % 100n;
  return (cents / 100n).toString() + "." + (rest < 10n ? "0" : "") + rest.toString();
}

export function sumUsd(strings) {
  if (!Array.isArray(strings)) return "0.00";
  let total = 0n;
  for (const item of strings) {
    const cents = toCents(item);
    if (cents !== null) total += cents;
  }
  return formatCents(total);
}

// Where a position belongs. An unknown location goes to idle, so it is never dropped.
function classify(location, token) {
  if (location === "aave-v3") return { id: "lending", protocol: "aave-v3", display: "a" + token };
  if (location === "aave-stata") return { id: "vaults", protocol: "aave-stata", display: "stata" + token };
  if (location === "morpho-blue") return { id: "morpho", protocol: "morpho-blue", display: token };
  if (location === "wallet" && !IDLE_TOKENS.includes(token)) {
    return { id: "swapped", protocol: "uniswap-v3", display: token };
  }
  return { id: "idle", protocol: null, display: token };
}

// A position with a zero or unreadable balance is left out.
function entryFor(position) {
  if (!isObject(position) || typeof position.token !== "string" || position.token === "") return null;
  const bal = position.balance;
  if (!isObject(bal) || typeof bal.value !== "string" || !UNIT.test(bal.value)) return null;
  if (BigInt(bal.value) === 0n) return null;
  const { id, protocol, display } = classify(position.location, position.token);
  const cents = toCents(position.value_usd);
  const shareBps =
    Number.isInteger(position.weight_bps) && position.weight_bps >= 0 ? position.weight_bps : 0;
  return {
    group: id,
    cents: cents === null ? 0n : cents,
    row: {
      key: `${position.location}:${position.token}`,
      symbol: position.token,
      display,
      protocol,
      amount: { value: bal.value, decimals: bal.decimals },
      usd: formatCents(cents === null ? 0n : cents),
      shareBps,
      priceKnown: cents !== null,
    },
  };
}

// Largest value first; equal values sorted by symbol.
function byValue(a, b) {
  if (a.cents !== b.cents) return a.cents > b.cents ? -1 : 1;
  if (a.row.symbol === b.row.symbol) return 0;
  return a.row.symbol < b.row.symbol ? -1 : 1;
}

function gasFrom(agent) {
  const eth = typeof agent.eth_wei === "string" && UNIT.test(agent.eth_wei) ? agent.eth_wei : null;
  const floor =
    typeof agent.gas_floor_wei === "string" && UNIT.test(agent.gas_floor_wei)
      ? agent.gas_floor_wei
      : null;
  const ok = eth !== null && floor !== null && BigInt(eth) > 0n && BigInt(eth) >= BigInt(floor);
  return { ethWei: eth, floorWei: floor, ok };
}

export function groupHoldings(portfolio, agent) {
  const priced = isObject(portfolio) && Array.isArray(portfolio.positions);
  const buckets = {};
  if (priced) {
    for (const position of portfolio.positions) {
      const entry = entryFor(position);
      if (entry) {
        if (!buckets[entry.group]) buckets[entry.group] = [];
        buckets[entry.group].push(entry);
      }
    }
  }

  const groups = [];
  for (const def of GROUPS) {
    const entries = buckets[def.id];
    if (!entries || entries.length === 0) continue;
    entries.sort(byValue);
    const rows = entries.map((e) => e.row);
    groups.push({
      id: def.id,
      label: def.label,
      note: def.note,
      usd: sumUsd(rows.map((r) => r.usd)),
      shareBps: rows.reduce((sum, r) => sum + r.shareBps, 0),
      rows,
    });
  }

  const idle = groups.find((g) => g.id === "idle");
  const totalOk = priced && toCents(portfolio.total_value_usd) !== null;
  return {
    priced,
    totalUsd: totalOk ? portfolio.total_value_usd : null,
    workingUsd: priced ? sumUsd(groups.filter((g) => g.id !== "idle").map((g) => g.usd)) : null,
    idleUsd: priced ? (idle ? idle.usd : "0.00") : null,
    priceSource:
      priced && typeof portfolio.price_source === "string"
        ? portfolio.price_source.replace(/_/g, " ")
        : null,
    groups,
    gas: isObject(agent) ? gasFrom(agent) : null,
    empty: groups.length === 0,
  };
}

// One segment per group in the model. pct has at most one decimal.
export function allocationSegments(model) {
  if (!isObject(model) || !Array.isArray(model.groups)) return [];
  return model.groups.filter(isObject).map((g) => {
    const bps = Number.isInteger(g.shareBps) && g.shareBps >= 0 ? g.shareBps : 0;
    return { id: g.id, label: g.label, bps, pct: Math.round(bps / 10) / 10 };
  });
}
