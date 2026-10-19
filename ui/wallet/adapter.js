// Wallet adapter interface. Implementations: injected.js (EIP-1193), test.js (stub).
export const BASE_SEPOLIA_ID = 84532;

export class WalletAdapter {
  get id() {
    return "none";
  }
  /** @returns {Promise<string[]>} accounts, lowercase */
  async connect() {
    throw new Error("not implemented");
  }
  /** @returns {Promise<number>} */
  async chainId() {
    throw new Error("not implemented");
  }
  /** Switch or add Base Sepolia (84532); rejects with a readable error. */
  async ensureBaseSepolia() {
    throw new Error("not implemented");
  }
  /** personal_sign of a UTF-8 message; returns the 0x signature. */
  async signMessage(_address, _message) {
    throw new Error("not implemented");
  }
  /** eth_sendTransaction {to, value}; returns the tx hash. */
  async sendTransaction(_tx) {
    throw new Error("not implemented");
  }
  /** Subscribe to account and chain changes; returns an unsubscribe function. */
  onChange(_handler) {
    return () => {};
  }
}

// An adapter is chosen from what the page can see. `providers` are EIP-6963 announcements,
// `win` is the window (for window.ethereum). Returns null when no wallet is present.
export function selectAdapter({ win, providers = [], make }) {
  const first = providers.length ? providers[0].provider : null;
  const legacy = win && win.ethereum ? win.ethereum : null;
  const provider = first || legacy;
  return provider ? make(provider) : null;
}

export function isUserRejection(err) {
  return Boolean(err) && (err.code === 4001 || err.code === "ACTION_REJECTED");
}
