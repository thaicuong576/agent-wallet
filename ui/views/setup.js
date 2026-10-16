// Setup: three steps (Connected, Add gas, Activate), shown until the agent is activated. After
// activation the owner lands in Chat. Test USDC comes from Add funds on Overview.
import { formatWei, parseUnits } from "../explain.js";
import { activateAgent, ACTIVATE_TIER, TIER_NAMES } from "./activate.js";
import { sendGas } from "./topup.js";
import { txLink } from "./cards.js";

const FALLBACK_TOPUP = "0.0001";

export function render(ctx) {
  const { el, api } = ctx;
  const root = el("section", "setup");
  const body = el("div", "stack");
  root.append(body);

  async function load() {
    body.replaceChildren(skeleton(el));
    const me = await ctx.refreshSession();
    const data = { me, agent: null, tier: null, agentError: null };
    if (me) {
      const [a, t] = await Promise.allSettled([api.agent(), api.tier()]);
      data.agent = a.status === "fulfilled" ? a.value : null;
      data.agentError = a.status === "rejected" ? ctx.describeError(a.reason) : null;
      data.tier = t.status === "fulfilled" ? t.value.tier : null;
    }
    draw(data);
  }

  function gasDone(data) {
    const a = data.agent;
    if (!a) return false;
    const eth = BigInt(a.eth_wei || "0");
    return eth > 0n && eth >= BigInt(a.gas_floor_wei || "0");
  }

  function draw(data) {
    const connected = Boolean(data.me);
    const gas = connected && gasDone(data);
    const active = data.tier === "T2";
    const states = [connected ? "done" : "current", !connected ? "todo" : gas ? "done" : "current", active ? "done" : connected && gas ? "current" : "todo"];
    const labels = [
      "1 · Connected" + (states[0] === "done" ? " ✓" : ""),
      "2 · Add gas" + (states[1] === "done" ? " ✓" : ""),
      "3 · Activate" + (states[2] === "done" ? " ✓" : ""),
    ];
    const head = el("div");
    head.append(
      el("h1", "", "Meet your agent"),
      el(
        "p",
        "lead",
        "Three steps, about two minutes. This is a testnet demo: the server holds the agent's key and no real funds are involved.",
      ),
    );
    const bar = el("div", "setup-steps");
    bar.setAttribute("aria-label", "Progress");
    states.forEach((s, i) => {
      const sp = el("span", s, labels[i]);
      if (s === "current") sp.setAttribute("aria-current", "step");
      bar.append(sp);
    });
    const parts = [head, bar];
    parts.push(connected ? gasCard(data, gas) : connectCard());
    parts.push(activateCard(data, connected && (gas || Boolean(data.agentError)), active));
    parts.push(
      el(
        "p",
        "note faint",
        "Next: you land in Chat and can say what you want in plain words. Get test USDC from Overview.",
      ),
    );
    body.replaceChildren(...parts);
  }

  // A button that runs `fn`, reports progress in a status line and reloads on success.
  function task(label, fn, { cls = "primary", before = [], inline = [], after = [] } = {}) {
    const btn = el("button", cls, label);
    btn.type = "button";
    const msg = el("p", "msg", "");
    msg.setAttribute("role", "status");
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      msg.className = "msg";
      msg.textContent = "Working…";
      try {
        const out = await fn(msg);
        if (out !== false) {
          await load();
          ctx.changed();
        } else {
          btn.disabled = false;
        }
      } catch (e) {
        msg.className = "msg error";
        msg.textContent = ctx.describeError(e);
        btn.disabled = false;
      }
    });
    const wrap = el("div", "stack");
    const line = el("div", "row");
    line.append(...inline, btn);
    wrap.append(...before, line, msg, ...after);
    return wrap;
  }

  function connectCard() {
    const card = el("div", "card setup-card");
    card.append(
      el("h2", "", "Connect your wallet"),
      el("p", "", "Connect the wallet that owns this agent. You sign one free login message; no transaction is sent."),
    );
    if (!ctx.wallet) {
      card.append(
        el("p", "msg error", "No browser wallet was found. Install an EIP-1193 wallet such as MetaMask, then reload."),
      );
      return card;
    }
    card.append(
      task("Connect wallet", async () => {
        const [address] = await ctx.wallet.connect();
        if (!address) throw new Error("The wallet returned no account.");
        await ctx.wallet.ensureBaseSepolia();
        const n = await api.nonce(address);
        const sig = await ctx.wallet.signMessage(address, n.message);
        await api.verify(address, n.nonce, sig);
        ctx.sessionStarted();
      }),
    );
    return card;
  }

  function gasCard(data, funded) {
    const card = el("div", "card setup-card");
    const a = data.agent;
    card.append(el("h2", "", "Your agent's wallet"));
    const box = el("div", "addr-box");
    const addr = a ? a.address : data.me.agent_address;
    box.append(el("span", "", addr || "unavailable"));
    if (addr) box.append(copyButton(ctx, addr));
    card.append(box);
    if (data.agentError) card.append(el("p", "msg error", data.agentError));
    if (a) {
      card.append(
        el(
          "p",
          "",
          funded
            ? "✓ The agent has " + formatWei(a.eth_wei) + " ETH for network fees. You can add more any time."
            : "The agent needs a little ETH for network fees. Send some from your wallet; it takes one click and one confirmation.",
        ),
        el(
          "p",
          "note",
          "Gas floor: about " +
            formatWei(a.gas_floor_wei) +
            " ETH (an estimate: 2 × the current base fee × 500,000 gas). Balance now: " +
            formatWei(a.eth_wei) +
            " ETH. The agent cannot top up its own ETH.",
        ),
      );
    }
    const sent = el("p", "msg", "");
    const input = el("input", "amount");
    input.type = "text";
    input.inputMode = "decimal";
    input.id = "fund-amount";
    input.value = (a && formatWei(a.suggested_topup_wei)) || FALLBACK_TOPUP;
    input.setAttribute("aria-label", "ETH amount to send");
    const label = el("label", "", "ETH to send (Base Sepolia test ETH)");
    label.setAttribute("for", "fund-amount");
    card.append(
      task(
        funded ? "Send more gas" : "Send gas from my wallet",
        async (msg) => {
          const wei = parseUnits(input.value, 18);
          if (wei === null || wei === 0n) throw new Error("Enter an amount such as 0.01.");
          await sendGas(ctx, {
            agent: a,
            wei,
            onStatus: (text, hash) => {
              msg.textContent = text;
              const link = txLink(el, hash);
              if (link) sent.replaceChildren(link);
            },
          });
        },
        { cls: funded ? "" : "primary", before: [label], inline: [input], after: [sent] },
      ),
    );
    card.append(el("p", "note", "Wallet prompt: send test ETH (pays gas). One confirmation."));
    return card;
  }

  function activateCard(data, ready, active) {
    const card = el("div", "card setup-card" + (ready || active ? "" : " dim"));
    card.setAttribute("role", "region");
    card.setAttribute("aria-label", "Activate agent");
    card.append(
      el("h2", "", "Activate"),
      el(
        "p",
        "",
        "Until you activate, the agent can only look. Activating lets it act inside your limits, and you can stop it any time. One free signature, no transaction.",
      ),
    );
    if (data.tier && !active) {
      card.append(
        el("p", "note", "Right now the agent is " + (TIER_NAMES[data.tier] || "inactive") + " (tier " + data.tier + ")."),
      );
    }

    let selectedPreset = "conservative";
    const presetsBox = el("div", "stack");
    const presetsTitle = el("p", "note", "Investment policy preset:");
    presetsBox.append(presetsTitle);

    const optConservative = el("div", "row");
    const radioCons = el("input");
    radioCons.type = "radio";
    radioCons.name = "preset_id";
    radioCons.id = "preset-conservative";
    radioCons.value = "conservative";
    radioCons.checked = true;
    radioCons.addEventListener("change", () => {
      if (radioCons.checked) selectedPreset = "conservative";
    });
    const labelCons = el("label", "stack");
    labelCons.setAttribute("for", "preset-conservative");
    labelCons.append(
      el("strong", "", "Conservative: Aave USDC lending"),
      el("span", "note", "Keeps capital in stablecoins and lends it on Aave. Limits: 100 USD per tx, 300 USD per day."),
    );
    optConservative.append(radioCons, labelCons);

    const optDiversified = el("div", "row");
    const radioDiv = el("input");
    radioDiv.type = "radio";
    radioDiv.name = "preset_id";
    radioDiv.id = "preset-diversified";
    radioDiv.value = "diversified";
    radioDiv.addEventListener("change", () => {
      if (radioDiv.checked) selectedPreset = "diversified";
    });
    const labelDiv = el("label", "stack");
    labelDiv.setAttribute("for", "preset-diversified");
    labelDiv.append(
      el("strong", "", "Diversified: lending, vaults, Morpho and swaps (testnet)"),
      el("span", "note", "Lends on Aave, deposits in Stata vaults, supplies to Morpho Blue, and swaps on Uniswap v3. Limits: 100 USD per tx, 300 USD per day."),
    );
    optDiversified.append(radioDiv, labelDiv);
    presetsBox.append(optConservative, optDiversified);
    card.append(presetsBox);

    const btn = el("button", ready ? "primary" : "", "Activate agent");
    btn.type = "button";
    btn.disabled = !ready;
    const msg = el("p", "msg", "");
    msg.setAttribute("role", "status");
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      msg.className = "msg";
      msg.textContent = "Waiting for your wallet…";
      try {
        const res = await activateAgent(
          ctx,
          ACTIVATE_TIER,
          selectedPreset === "conservative" ? undefined : selectedPreset,
        );
        msg.className = "msg ok";
        msg.textContent = "✓ Agent activated: tier " + res.tier + ". It may act inside your limits.";
        ctx.changed();
        ctx.go("#/chat");
      } catch (e) {
        msg.className = "msg error";
        msg.textContent = ctx.describeError(e);
        btn.disabled = false;
      }
    });
    card.append(btn, msg, el("p", "note", "Wallet prompt: one free signature to raise the tier."));
    return card;
  }

  load();
  return root;
}

function skeleton(el) {
  const s = el("div", "skeleton");
  s.setAttribute("aria-busy", "true");
  s.append(el("p", "note", "Loading…"));
  return s;
}

export function copyButton(ctx, text) {
  const b = ctx.el("button", "link", "Copy");
  b.type = "button";
  b.addEventListener("click", async () => {
    try {
      await globalThis.navigator.clipboard.writeText(text);
      b.textContent = "Copied";
    } catch (_) {
      b.textContent = "Select the address and copy it";
    }
  });
  return b;
}
