// Holdings grouping: ui/lib/holdings.js on the real fixtures and synthetic portfolios.
// Prints one JSON object of named results; tests/ui/test_holdings.py asserts them. No network.
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");

// The module has no imports, so one copy with a module marker is enough to load it as ESM.
const COPY = fs.mkdtempSync(path.join(os.tmpdir(), "aw-ui-holdings-"));
fs.copyFileSync(path.join(ROOT, "ui", "lib", "holdings.js"), path.join(COPY, "holdings.js"));
fs.writeFileSync(path.join(COPY, "package.json"), '{"type":"module"}');
const mod = await import(pathToFileURL(path.join(COPY, "holdings.js")).href);
const { groupHoldings, allocationSegments, sumUsd } = mod;

const read = (name) => JSON.parse(fs.readFileSync(path.join(ROOT, "ui", "fixtures", name), "utf8"));
const portfolio = read("portfolio.json");
const agent = read("agent.json");

const pos = (location, token, value, decimals, usd, bps) => ({
  token,
  location,
  balance: { value, decimals },
  value_usd: usd,
  weight_bps: bps,
});

const out = {};

// (1) the real fixtures: wallet USDC and Aave-lent USDC
const real = groupHoldings(portfolio, agent);
out.real_groups = real.groups.map((g) => g.id);
out.real_group_usd = Object.fromEntries(real.groups.map((g) => [g.id, g.usd]));
out.real_total = real.totalUsd;
out.real_working = real.workingUsd;
out.real_idle = real.idleUsd;
out.real_lending_display = real.groups[1].rows[0].display;
out.real_price_source = real.priceSource;
out.real_gas_ok = real.gas.ok;

// (2) every location and token, a zero balance, an unknown location
const synth = groupHoldings(
  {
    price_source: "mainnet_reference",
    total_value_usd: "231.00",
    positions: [
      pos("wallet", "USDC", "98000000", 6, "98.00", 4242),
      pos("wallet", "USDT", "5000000", 6, "5.00", 216),
      pos("wallet", "WETH", "10000000000000000000", 18, "10.00", 433),
      pos("wallet", "WBTC", "2000000000", 8, "20.00", 866),
      pos("aave-v3", "USDC", "40000000", 6, "40.00", 1732),
      pos("aave-v3", "USDT", "1000000", 6, "1.00", 43),
      pos("aave-stata", "USDC", "20000000", 6, "20.00", 866),
      pos("morpho-blue", "USDC", "30000000", 6, "30.00", 1299),
      pos("wallet", "LINK", "0", 18, "0.00", 0),
      pos("newprotocol", "USDC", "7000000", 6, "7.00", 303),
    ],
  },
  agent,
);
out.synth_groups = synth.groups.map((g) => g.id);
out.synth_group_usd = Object.fromEntries(synth.groups.map((g) => [g.id, g.usd]));
out.synth_group_bps = Object.fromEntries(synth.groups.map((g) => [g.id, g.shareBps]));
out.synth_rows = synth.groups.flatMap((g) =>
  g.rows.map((r) => [g.id, r.key, r.display, r.protocol, r.usd, r.shareBps]),
);
out.synth_total = synth.totalUsd;
out.synth_working = synth.workingUsd;
out.synth_idle = synth.idleUsd;
out.synth_price_source = synth.priceSource;
out.synth_segments = allocationSegments(synth).map((s) => [s.id, s.bps, s.pct]);

// (2b) equal values sort by symbol; an unpriced row counts as 0.00 and has no share
const tie = groupHoldings(
  {
    positions: [
      pos("wallet", "WETH", "300", 2, "3.00", 5),
      pos("wallet", "LINK", "300", 2, "3.00", 5),
      { token: "cbETH", location: "wallet", balance: { value: "1", decimals: 18 } },
    ],
  },
  null,
);
const swapped = tie.groups[0];
out.tie_rows = swapped.rows.map((r) => [r.symbol, r.usd, r.priceKnown, r.shareBps]);
out.tie_group = [swapped.id, swapped.usd, swapped.shareBps];
out.tie_segments = allocationSegments(tie).map((s) => [s.id, s.bps, s.pct]);

// (3) sumUsd
out.sum_basic = sumUsd(["98.00", "2.5", "1", "bad"]);
out.sum_empty = sumUsd([]);
out.sum_not_array = sumUsd(null);
out.sum_round = sumUsd(["0.005", "0.004"]);

// (4) malformed input never throws
const malformedCases = [
  [null, agent],
  [undefined, undefined],
  ["x", []],
  [{ positions: "no" }, null],
  [{ positions: [null, 5, "x", { token: "USDC", location: "wallet", balance: { value: "x" } }] }, null],
];
out.malformed = malformedCases.map(([p, a]) => {
  try {
    const m = groupHoldings(p, a);
    return {
      threw: false,
      priced: m.priced,
      empty: m.empty,
      groups: m.groups.length,
      totalUsd: m.totalUsd,
      gasOk: m.gas === null ? null : m.gas.ok,
    };
  } catch {
    return { threw: true };
  }
});

// (5) gas check
const gasFor = (eth) =>
  groupHoldings(null, { eth_wei: eth, gas_floor_wei: "5000000000000000" }).gas.ok;
out.gas = {
  below: gasFor("4999999999999999"),
  zero: gasFor("0"),
  garbage: gasFor("lots"),
  equal: gasFor("5000000000000000"),
};

console.log(JSON.stringify(out));
