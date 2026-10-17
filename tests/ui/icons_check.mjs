// Token and protocol icon checks: the real ui/lib/icons.js against a fake DOM.
// Prints one JSON object of named results; tests/ui/test_icons.py asserts them. No network.
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");

// Copy the one module into a temp folder with a module marker, as chat_flows.mjs does,
// so this check never writes into ui/.
const COPY = fs.mkdtempSync(path.join(os.tmpdir(), "aw-ui-icons-"));
fs.copyFileSync(path.join(ROOT, "ui", "lib", "icons.js"), path.join(COPY, "icons.js"));
fs.writeFileSync(path.join(COPY, "package.json"), '{"type":"module"}');
const icons = await import(pathToFileURL(path.join(COPY, "icons.js")).href);
fs.rmSync(COPY, { recursive: true, force: true });

const { PALETTE, PROTOCOL_META, fallbackColor, tokenAvatar, protocolAvatar, displayName } = icons;

// Every node the code creates, so the attribute check covers all of them.
const created = [];
class FakeNode {
  constructor(tag, cls, text) {
    this.tag = tag;
    this.className = cls || "";
    this.children = [];
    this.attrs = {};
    this.style = {};
    this.textContent = text ?? "";
    created.push(this);
  }
  append(...nodes) {
    this.children.push(...nodes);
  }
  setAttribute(name, value) {
    this.attrs[name] = String(value);
  }
}
const el = (tag, cls, text) => new FakeNode(tag, cls, text);

// Depth-first search for the first node that matches.
function find(node, pred) {
  if (pred(node)) return node;
  for (const child of node.children) {
    const hit = find(child, pred);
    if (hit) return hit;
  }
  return null;
}
const hasClass = (cls) => (n) => n.className.split(" ").includes(cls);
const imgSrc = (root) => {
  const img = find(root, (n) => n.tag === "img");
  return img ? img.attrs.src : null;
};
const textOf = (root, cls) => {
  const hit = find(root, hasClass(cls));
  return hit ? hit.textContent : null;
};

// WCAG relative luminance and contrast ratio against white.
function channel(v) {
  const c = v / 255;
  return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
}
function luminance(hex) {
  const n = parseInt(hex.slice(1), 16);
  const r = channel((n >> 16) & 255);
  const g = channel((n >> 8) & 255);
  const b = channel(n & 255);
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}
function contrastWhite(hex) {
  return 1.05 / (luminance(hex) + 0.05);
}

const colours = [...PALETTE, ...Object.values(PROTOCOL_META).map((m) => m.color)];
const minContrast = Math.min(...colours.map(contrastWhite));

const usdc = tokenAvatar(el, "USDC");
const weth = tokenAvatar(el, "WETH");
const cbeth = tokenAvatar(el, "cbETH");
const aUsdc = tokenAvatar(el, "aUSDC", { protocol: "aave-v3" });
const usdcNoTest = tokenAvatar(el, "USDC", { testnet: false });
let noThrow42 = true;
let noThrowUndef = true;
try {
  tokenAvatar(el, 42);
} catch {
  noThrow42 = false;
}
try {
  tokenAvatar(el, undefined);
} catch {
  noThrowUndef = false;
}
const morpho = protocolAvatar(el, "morpho-blue");

const httpAttr = created.some((n) =>
  Object.values(n.attrs).some((v) => typeof v === "string" && v.startsWith("http")),
);

const out = {
  fallbackStable: new Set([1, 2, 3].map(() => fallbackColor("cbETH"))).size === 1,
  fallbackInPalette: PALETTE.includes(fallbackColor("cbETH")),
  minContrast: Math.round(minContrast * 100) / 100,
  usdcImgSrc: imgSrc(usdc),
  usdcTestnetMark: find(usdc, hasClass("testnet-mark")) !== null,
  wethImgSrc: imgSrc(weth),
  wethLetterBadge: textOf(weth, "token-letter-badge"),
  cbethHasImg: find(cbeth, (n) => n.tag === "img") !== null,
  cbethLetter: textOf(cbeth, "token-letter"),
  aUsdcImgSrc: imgSrc(aUsdc),
  aUsdcProtocolBadge: textOf(aUsdc, "protocol-badge"),
  usdcNoTestnetMark: find(usdcNoTest, hasClass("testnet-mark")) !== null,
  noThrow42,
  noThrowUndef,
  httpAttr,
  displayNameAUsdc: displayName("aUSDC"),
  displayNameZzz: displayName("ZZZ"),
  morphoLetter: morpho.textContent,
};

process.stdout.write(JSON.stringify(out) + "\n");
