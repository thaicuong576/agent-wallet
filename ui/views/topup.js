// Gas top-up: the owner sends test ETH from their wallet to the agent address (the agent cannot
// top up its own ETH). Shared by Setup, Portfolio and the Chat side panel.
import { formatWei } from "../explain.js";
import { txLink } from "./cards.js";

// Send `wei` to the agent, register the hash, and wait until the server confirms it.
// `onStatus(text, hash)` reports progress. Resolves to the transaction hash.
export async function sendGas(ctx, { agent, wei, onStatus }) {
  const me = ctx.state.me;
  if (!ctx.wallet || !me) throw new Error("Connect your wallet first.");
  if (!agent || !agent.address) throw new Error("The agent address is not available yet.");
  await ctx.wallet.ensureBaseSepolia();
  const hash = await ctx.wallet.sendTransaction({
    from: me.address,
    to: agent.address,
    valueWei: wei.toString(),
  });
  if (onStatus) onStatus("Sent. Waiting for confirmation…", hash);
  // The node may not know the transaction for a few seconds after the wallet sent it.
  for (let i = 0; ; i++) {
    try {
      await ctx.api.registerTopup(hash);
      break;
    } catch (e) {
      if (!(e && e.status === 422) || i >= 6) throw e;
      await ctx.wait(3000);
    }
  }
  for (let i = 0; i < 40; i++) {
    const t = await ctx.api.topupStatus(hash);
    if (t.status === "confirmed") return hash;
    if (t.status === "failed" || t.status === "rejected") {
      throw new Error("The top-up was " + t.status + ".");
    }
    await ctx.wait(3000);
  }
  throw new Error("Still pending. Check again in a minute.");
}

// A button that sends the server's suggested top-up. `getAgent()` returns the latest /agent
// body (or null). Returns {button, message}; the caller places both.
export function topUpButton(ctx, { label = "Top up gas", getAgent, cls = "" }) {
  const { el } = ctx;
  const button = el("button", cls, label);
  button.type = "button";
  const message = el("p", "msg", "");
  message.setAttribute("role", "status");
  button.addEventListener("click", async () => {
    const agent = getAgent();
    button.disabled = true;
    message.className = "msg";
    message.textContent = "Waiting for your wallet…";
    try {
      const wei = BigInt((agent && agent.suggested_topup_wei) || "0");
      if (wei === 0n) throw new Error("The suggested amount is not available. Use Set up to choose one.");
      await sendGas(ctx, {
        agent,
        wei,
        onStatus: (text, hash) => {
          message.textContent = text + " ";
          const link = txLink(el, hash);
          if (link) message.append(link);
        },
      });
      message.className = "msg ok";
      message.textContent = "✓ Added " + formatWei(String(wei)) + " ETH of gas.";
      ctx.changed();
    } catch (e) {
      message.className = "msg error";
      message.textContent = ctx.describeError(e);
    }
    button.disabled = false;
  });
  return { button, message };
}
