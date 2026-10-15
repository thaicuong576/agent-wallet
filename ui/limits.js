// GET /limits (contract C4): the owner's active limits, today's spend and the stop flag.
// Read-only. Nothing is stored; a failure is thrown to the caller, never replaced by numbers.
export const LIMITS_PATH = "/limits";

export async function getLimits(api) {
  if (!api.fixtures) return api.request("GET", LIMITS_PATH);
  await api.me(); // fixtures answer 401 for a signed-out visitor, like the server
  const data = await api.fixtureFile("limits");
  const tier = await api.tier();
  data.emergency_stop = tier.emergency_stop ? "stopped" : "ready";
  return data;
}

// "50.00" -> "$50", "1000.00" -> "$1,000", "0.50" -> "$0.50". Decimal strings only, no floats.
export function usd(v) {
  const m = /^(\d+)(?:\.(\d+))?$/.exec(String(v ?? "").trim());
  if (!m) return "not set";
  const whole = m[1].replace(/^0+(?=\d)/, "").replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  const cents = (m[2] || "").replace(/0+$/, "");
  return "$" + whole + (cents ? "." + cents.padEnd(2, "0") : "");
}

const PROTOCOL_NAMES = { aave_v3: "Aave v3", "aave-v3": "Aave v3", uniswap_v3: "Uniswap v3", "uniswap-v3": "Uniswap v3" };
export const protocolName = (p) => PROTOCOL_NAMES[p] || String(p);

// One entry per row of the Rules page, in board D order.
export function limitRows(l) {
  const stopped = l.emergency_stop === "stopped";
  const protocols = Array.isArray(l.protocols) ? l.protocols : [];
  return [
    {
      id: "per-order",
      name: "Per order",
      text: "Blocks any single order above this value.",
      value: usd(l.per_order_usd),
      chip: { text: "On", tone: "ok" },
    },
    {
      id: "per-day",
      name: "Per day",
      text: "Total the agent may move in any rolling 24 hours.",
      used: "Used today " + usd(l.used_today_usd),
      value: usd(l.per_day_usd),
      chip: { text: "On", tone: "ok" },
    },
    {
      id: "ask-first",
      name: "Ask me first above",
      text: "Orders larger than this wait for your approval.",
      value: usd(l.approval_threshold_usd),
      chip: { text: "On", tone: "ok" },
    },
    {
      id: "protocols",
      name: "Where money may go",
      text: protocols.length
        ? "Only approved protocols: " + protocols.map(protocolName).join(", ") + " on Base Sepolia. Never an outside address."
        : "No protocol is approved, so the agent cannot place any order.",
      value: protocols.length ? protocols.map(protocolName).join(", ") : "None",
      chip: { text: protocols.length ? "On" : "Off", tone: protocols.length ? "ok" : "warn" },
    },
    {
      id: "stop",
      name: "Emergency stop",
      text: "Blocks every order until you resume with a signature.",
      value: null,
      chip: stopped ? { text: "Stopped", tone: "bad" } : { text: "Ready", tone: "neutral" },
    },
  ];
}
