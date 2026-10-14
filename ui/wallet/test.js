// Deterministic stub for tests and fixtures. It never contacts a wallet or a network.
import { BASE_SEPOLIA_ID, WalletAdapter } from "./adapter.js";

export class TestWallet extends WalletAdapter {
  constructor({ address = "0x1111111111111111111111111111111111111111", reject = false } = {}) {
    super();
    this.address = address;
    this.reject = reject;
    this.calls = [];
    this.signed = [];
    this.sent = [];
  }
  get id() {
    return "test";
  }
  _maybeReject() {
    if (this.reject) {
      const e = new Error("User rejected the request.");
      e.code = 4001;
      throw e;
    }
  }
  async connect() {
    this.calls.push("connect");
    this._maybeReject();
    return [this.address];
  }
  async chainId() {
    return BASE_SEPOLIA_ID;
  }
  async ensureBaseSepolia() {
    this.calls.push("ensureBaseSepolia");
  }
  async signMessage(address, message) {
    this.calls.push("signMessage");
    this._maybeReject();
    this.signed.push(message);
    return "0x" + "ab".repeat(65);
  }
  async sendTransaction({ from, to, valueWei }) {
    this.calls.push("sendTransaction");
    this._maybeReject();
    this.sent.push({ from, to, valueWei });
    return "0x" + "cd".repeat(32);
  }
  onChange() {
    return () => {};
  }
}
