// Token and protocol avatars. Pure node builders: the caller passes `el` (same signature as ctx.el).
// No network, no storage, no HTML strings. Every token here is a Base Sepolia test token.

export const TOKEN_META = {
  USDC: { name: "USD Coin (test token)", file: "usdc", testnet: true },
  USDT: { name: "Tether (test token)", file: "usdt", testnet: true },
  ETH: { name: "Ether (test ETH)", file: "eth", testnet: true },
  WETH: { name: "Wrapped Ether (test token)", file: "eth", testnet: true },
  WBTC: { name: "Wrapped Bitcoin (test token)", file: "wbtc", testnet: true },
  LINK: { name: "Chainlink (test token)", file: "link", testnet: true },
  cbETH: { name: "Coinbase Wrapped Staked ETH (test token)", file: null, testnet: true },
  aUSDC: { name: "USDC supplied on Aave", file: "usdc", testnet: true },
  aUSDT: { name: "USDT supplied on Aave", file: "usdt", testnet: true },
  stataUSDC: { name: "USDC in the Aave vault", file: "usdc", testnet: true },
  stataUSDT: { name: "USDT in the Aave vault", file: "usdt", testnet: true },
};

// Dark colours, each at least 4.5:1 against white.
export const PALETTE = [
  "#1f4e79",
  "#5b2c6f",
  "#0b6e4f",
  "#8a3b12",
  "#7a1f3d",
  "#2d4a22",
  "#3d3d99",
  "#1d5c63",
];

export const PROTOCOL_META = {
  "aave-v3": { name: "Aave v3", letter: "A", color: "#5b2c6f" },
  "aave-stata": { name: "Aave vaults", letter: "A", color: "#5b2c6f" },
  "morpho-blue": { name: "Morpho Blue", letter: "M", color: "#1f4e79" },
  "uniswap-v3": { name: "Uniswap v3", letter: "U", color: "#0b6e4f" },
};

const PX = { xs: 20, sm: 22, md: 32 };

function symbolOf(symbol) {
  return typeof symbol === "string" && symbol !== "" ? symbol : "?";
}

function tokenMeta(symbol) {
  if (Object.prototype.hasOwnProperty.call(TOKEN_META, symbol)) return TOKEN_META[symbol];
  return { name: symbol, file: null, testnet: true };
}

function protocolMeta(id) {
  if (Object.prototype.hasOwnProperty.call(PROTOCOL_META, id)) return PROTOCOL_META[id];
  const key = symbolOf(id);
  return { name: key, letter: key.charAt(0).toUpperCase(), color: fallbackColor(key) };
}

function sizeOf(size) {
  return size === "xs" || size === "sm" || size === "md" ? size : "md";
}

// FNV-1a 32-bit over the symbol, so the same symbol always gets the same colour.
export function fallbackColor(symbol) {
  const s = symbolOf(symbol);
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619) >>> 0;
  }
  return PALETTE[h % PALETTE.length];
}

export function iconFile(symbol) {
  return tokenMeta(symbolOf(symbol)).file;
}

export function displayName(symbol) {
  return tokenMeta(symbolOf(symbol)).name;
}

// opts: { size: "xs"|"sm"|"md", protocol: protocol id or null, testnet: boolean (default true) }
export function tokenAvatar(el, symbol, opts) {
  const o = opts ?? {};
  const sym = symbolOf(symbol);
  const size = sizeOf(o.size);
  const meta = tokenMeta(sym);
  const span = el("span", `token-avatar ${size}`);
  span.setAttribute("aria-hidden", "true");
  span.setAttribute("data-symbol", sym);

  if (meta.file) {
    const img = el("img", "token-img");
    img.setAttribute("src", `assets/tokens/${meta.file}.svg`);
    img.setAttribute("alt", "");
    img.setAttribute("width", String(PX[size]));
    img.setAttribute("height", String(PX[size]));
    span.append(img);
  } else {
    const letter = el("span", "token-letter", sym.charAt(0).toUpperCase());
    letter.style.background = fallbackColor(sym);
    span.append(letter);
  }

  // Wrapped ether shares ETH's image, so a "W" keeps the two apart.
  if (sym === "WETH") {
    const badge = el("span", "token-letter-badge", "W");
    badge.setAttribute("title", "Wrapped");
    span.append(badge);
  }

  if (o.testnet !== false) {
    const mark = el("span", "testnet-mark", "T");
    mark.setAttribute("title", "Test token on Base Sepolia. Not the real asset.");
    mark.setAttribute("aria-hidden", "true");
    span.append(mark);
  }

  if (o.protocol) span.append(protocolBadge(el, o.protocol));
  return span;
}

// Small rounded square with the protocol's letter, drawn over a token avatar.
export function protocolBadge(el, protocolId) {
  const meta = protocolMeta(protocolId);
  const badge = el("span", "protocol-badge", meta.letter);
  badge.style.background = meta.color;
  badge.setAttribute("title", meta.name);
  return badge;
}

// Round monogram for a protocol. opts: { size: "xs"|"sm"|"md" }
export function protocolAvatar(el, protocolId, opts) {
  const size = sizeOf((opts ?? {}).size);
  const meta = protocolMeta(protocolId);
  const span = el("span", `protocol-avatar ${size}`, meta.letter);
  span.style.background = meta.color;
  span.setAttribute("aria-hidden", "true");
  return span;
}
