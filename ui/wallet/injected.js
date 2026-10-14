// Injected EIP-1193 provider (MetaMask and friends). EIP-6963 first, window.ethereum fallback.
import { BASE_SEPOLIA_ID, WalletAdapter, selectAdapter } from "./adapter.js";

const CHAIN_HEX = "0x" + BASE_SEPOLIA_ID.toString(16);
// Used only inside wallet_addEthereumChain; the page itself never fetches these.
const CHAIN_PARAMS = {
  chainId: CHAIN_HEX,
  chainName: "Base Sepolia",
  nativeCurrency: { name: "Ether", symbol: "ETH", decimals: 18 },
  rpcUrls: ["https://sepolia.base.org"],
  blockExplorerUrls: ["https://sepolia.basescan.org"],
};

function utf8Hex(text) {
  return (
    "0x" +
    Array.from(new TextEncoder().encode(text), (b) => b.toString(16).padStart(2, "0")).join("")
  );
}

export class InjectedAdapter extends WalletAdapter {
  constructor(provider) {
    super();
    this.provider = provider;
  }
  get id() {
    return "injected";
  }
  async connect() {
    const accounts = await this.provider.request({ method: "eth_requestAccounts" });
    return accounts.map((a) => String(a).toLowerCase());
  }
  async chainId() {
    return parseInt(await this.provider.request({ method: "eth_chainId" }), 16);
  }
  async ensureBaseSepolia() {
    if ((await this.chainId()) === BASE_SEPOLIA_ID) return;
    try {
      await this.provider.request({
        method: "wallet_switchEthereumChain",
        params: [{ chainId: CHAIN_HEX }],
      });
    } catch (err) {
      if (err && err.code === 4902) {
        await this.provider.request({ method: "wallet_addEthereumChain", params: [CHAIN_PARAMS] });
      } else {
        throw err;
      }
    }
  }
  async signMessage(address, message) {
    return this.provider.request({ method: "personal_sign", params: [utf8Hex(message), address] });
  }
  async sendTransaction({ from, to, valueWei }) {
    return this.provider.request({
      method: "eth_sendTransaction",
      params: [{ from, to, value: "0x" + BigInt(valueWei).toString(16) }],
    });
  }
  onChange(handler) {
    const accounts = (a) => handler({ accounts: a.map((x) => String(x).toLowerCase()) });
    const chain = (c) => handler({ chainId: parseInt(c, 16) });
    if (!this.provider.on) return () => {};
    this.provider.on("accountsChanged", accounts);
    this.provider.on("chainChanged", chain);
    return () => {
      if (this.provider.removeListener) {
        this.provider.removeListener("accountsChanged", accounts);
        this.provider.removeListener("chainChanged", chain);
      }
    };
  }
}

// Collect EIP-6963 announcements for a short moment, then pick an adapter.
export async function discoverInjected(win, waitMs = 150) {
  const providers = [];
  const onAnnounce = (e) => {
    if (e && e.detail && e.detail.provider) providers.push(e.detail);
  };
  win.addEventListener("eip6963:announceProvider", onAnnounce);
  win.dispatchEvent(new win.Event("eip6963:requestProvider"));
  await new Promise((r) => setTimeout(r, waitMs));
  win.removeEventListener("eip6963:announceProvider", onAnnounce);
  return selectAdapter({ win, providers, make: (p) => new InjectedAdapter(p) });
}
