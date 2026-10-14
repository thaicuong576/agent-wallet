// Onboarding stepper: Connect, Fund, Mint, Policy, Run. Each step says what it will ask the wallet.
import { explainError, formatAmount, formatWei, parseUnits, shortHash } from "../explain.js";
import { txLink } from "./feed.js";

const STEPS = [
  { id: "connect", title: "Connect", asks: "Your wallet will ask you to connect and to sign a login message. Signing is free and sends no transaction." },
  { id: "fund", title: "Fund", asks: "Your wallet will ask you to send a small amount of test ETH to the agent address, to pay for gas. The agent cannot top up its own ETH." },
  { id: "mint", title: "Mint", asks: "No wallet prompt. The agent mints test USDC to itself through the gateway." },
  { id: "policy", title: "Policy", asks: "Your wallet will ask you to sign once to confirm the preset you pick." },
  { id: "run", title: "Run", asks: "No wallet prompt. The agent proposes, the policy engine decides, and you see the result in the feed." },
];

export function render(ctx) {
  const { el, api } = ctx;
  const root = el("section", "view");
  root.append(el("h1", "", "Set up your agent"));
  const body = el("div");
  root.append(body);

  async function load() {
    body.replaceChildren(skeleton(el));
    const me = await ctx.refreshSession();
    const data = { me, agent: null, policy: null, jobs: [] };
    if (me) {
      const [a, p, j] = await Promise.allSettled([
        api.agent(),
        api.fixtures ? api.policy() : Promise.resolve(null),
        api.jobs(),
      ]);
      data.agent = a.status === "fulfilled" ? a.value : null;
      data.policy = p.status === "fulfilled" ? p.value : null;
      data.jobs = j.status === "fulfilled" && Array.isArray(j.value.items) ? j.value.items : [];
      data.agentError = a.status === "rejected" ? ctx.describeError(a.reason) : null;
    }
    draw(data);
  }

  function status(data) {
    const a = data.agent;
    const floor = a ? BigInt(a.gas_floor_wei || "0") : 0n;
    const eth = a ? BigInt(a.eth_wei || "0") : 0n;
    return {
      connect: Boolean(data.me),
      fund: Boolean(a) && eth > 0n && eth >= floor,
      mint: Boolean(a) && (a.usdc.value !== "0" || a.ausdc.value !== "0"),
      // There is no policy route yet, so outside the demo the default preset applies.
      policy: api.fixtures ? Boolean(data.policy && data.policy.preset_id) : Boolean(data.me),
      run: data.jobs.some((j) => j.kind === "run"),
    };
  }

  function draw(data) {
    const done = status(data);
    const currentId = STEPS.find((s) => !done[s.id]);
    const list = el("ol", "stepper");
    for (const s of STEPS) {
      const state = done[s.id] ? "done" : currentId && currentId.id === s.id ? "current" : "blocked";
      const li = el("li", "step " + state);
      const head = el("h2", "step-title");
      head.append(
        el("span", "step-mark", state === "done" ? "✓" : String(STEPS.indexOf(s) + 1)),
        el("span", "", s.title),
        el("span", "step-state", state === "done" ? "Done" : state === "current" ? "Current step" : "Locked"),
      );
      li.append(head);
      li.append(el("p", "note", s.asks));
      if (state === "current" || (s.id === "run" && state === "done")) {
        li.append(actions[s.id](data));
      } else if (state === "done") {
        li.append(el("p", "", summary(s.id, data)));
      }
      list.append(li);
    }
    body.replaceChildren(...(data.me ? [] : [empty(el)]), list);
  }

  function summary(id, data) {
    if (id === "connect") {
      return "Connected as " + shortHash(data.me.address) + ". Agent address: " + (data.me.agent_address || "not created yet") + ".";
    }
    if (id === "fund") return "Agent has " + formatWei(data.agent.eth_wei) + " ETH for gas.";
    if (id === "mint")
      return "Agent holds " + formatAmount(data.agent.usdc) + " USDC and " + formatAmount(data.agent.ausdc) + " aUSDC.";
    if (id === "policy") {
      return data.policy && data.policy.preset_id
        ? "Policy preset: " + data.policy.preset_id + "."
        : "The default conservative preset applies. Choosing another preset arrives with a later release.";
    }
    return "";
  }

  function task(label, fn, opts = {}) {
    const wrap = el("div", "actions");
    const btn = el("button", "primary", label);
    btn.type = "button";
    const msg = el("p", "status", "");
    msg.setAttribute("role", "status");
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      msg.className = "status";
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
        msg.className = "status error";
        msg.textContent = ctx.describeError(e);
        btn.disabled = false;
      }
    });
    wrap.append(...(opts.before || []), btn, msg);
    return wrap;
  }

  const actions = {
    connect() {
      if (!ctx.wallet) {
        const box = el("div", "actions");
        box.append(
          el("p", "status error", "No browser wallet was found. Install an EIP-1193 wallet such as MetaMask, then reload."),
        );
        return box;
      }
      return task("Connect wallet", async () => {
        const [address] = await ctx.wallet.connect();
        if (!address) throw new Error("The wallet returned no account.");
        await ctx.wallet.ensureBaseSepolia();
        const n = await api.nonce(address);
        const sig = await ctx.wallet.signMessage(address, n.message);
        await api.verify(address, n.nonce, sig);
      });
    },
    fund(data) {
      const box = el("div");
      const addr = el("p", "addr");
      addr.append(el("span", "label", "Agent address: "), el("code", "", data.agent ? data.agent.address : "unavailable"));
      if (data.agent) addr.append(copyButton(ctx, data.agent.address));
      box.append(addr);
      if (data.agent) {
        box.append(
          el(
            "p",
            "note",
            "Gas floor: about " + formatWei(data.agent.gas_floor_wei) + " ETH (an estimate: 2 × the current base fee × 500,000 gas). Balance now: " + formatWei(data.agent.eth_wei) + " ETH.",
          ),
        );
      }
      const sent = el("p", "");
      if (data.agentError) box.append(el("p", "status error", data.agentError));
      const input = el("input");
      input.type = "text";
      input.inputMode = "decimal";
      input.value = (data.agent && formatWei(data.agent.suggested_topup_wei)) || "0.01";
      input.id = "fund-amount";
      input.setAttribute("aria-label", "ETH amount to send");
      const label = el("label", "", "ETH to send (Base Sepolia test ETH)");
      label.setAttribute("for", "fund-amount");
      box.append(
        task(
          "Send gas ETH from my wallet",
          async (msg) => {
            const wei = parseUnits(input.value, 18);
            if (wei === null || wei === 0n) throw new Error("Enter an amount such as 0.01.");
            if (!data.agent) throw new Error("The agent address is not available yet.");
            await ctx.wallet.ensureBaseSepolia();
            const hash = await ctx.wallet.sendTransaction({
              from: data.me.address,
              to: data.agent.address,
              valueWei: wei.toString(),
            });
            msg.textContent = "Sent. Waiting for confirmation…";
            const link = txLink(el, hash);
            if (link) sent.replaceChildren(link);
            // The node may not know the transaction for a few seconds after the wallet sent it.
            for (let i = 0; ; i++) {
              try {
                await api.registerTopup(hash);
                break;
              } catch (e) {
                if (!(e && e.status === 422) || i >= 6) throw e;
                await ctx.wait(3000);
              }
            }
            for (let i = 0; i < 40; i++) {
              const t = await api.topupStatus(hash);
              if (t.status === "confirmed") return true;
              if (t.status === "failed" || t.status === "rejected") {
                throw new Error("The top-up was " + t.status + ".");
              }
              await ctx.wait(3000);
            }
            throw new Error("Still pending. Check again in a minute.");
          },
          { before: [label, input] },
        ),
        sent,
      );
      return box;
    },
    mint() {
      const input = el("input");
      input.type = "text";
      input.inputMode = "numeric";
      input.value = "1000";
      input.id = "mint-amount";
      const label = el("label", "", "Whole test USDC to mint");
      label.setAttribute("for", "mint-amount");
      const note = el(
        "p",
        "note",
        "The faucet enforces its own limits: one mint per hour. A second mint inside the hour fails and the feed explains why.",
      );
      const t = task(
        "Get test USDC",
        async (msg) => {
          const accepted = await api.mint(input.value.trim());
          msg.textContent = "Minting…";
          const job = await ctx.pollJob(accepted.job_id);
          if (job.status !== "succeeded") throw new Error(explainError(job.error_code));
        },
        { before: [label, input] },
      );
      const box = el("div");
      box.append(note, t);
      return box;
    },
    policy() {
      const box = el("div");
      const holder = el("div", "presets");
      holder.append(el("p", "note", "Loading presets…"));
      box.append(holder);
      let chosen = null;
      api
        .presets()
        .then((p) => {
          holder.replaceChildren();
          if (!p.items.length) {
            holder.append(el("p", "empty", "No presets are available."));
            return;
          }
          for (const item of p.items) {
            const lab = el("label", "preset");
            const radio = el("input");
            radio.type = "radio";
            radio.name = "preset";
            radio.value = item.preset_id;
            radio.addEventListener("change", () => (chosen = item.preset_id));
            const text = el("span", "preset-text");
            text.append(el("strong", "", item.title), el("span", "", item.summary));
            const lim = el("span", "note");
            lim.textContent = Object.entries(item.limits || {})
              .map(([k, v]) => k.replace(/_/g, " ") + ": " + v)
              .join(" · ");
            text.append(lim, el("span", "note", "Policy hash " + shortHash(item.ips_hash)));
            lab.append(radio, text);
            holder.append(lab);
          }
        })
        .catch((e) => {
          holder.replaceChildren(el("p", "status error", ctx.describeError(e)));
        });
      box.append(
        task("Sign and apply preset", async () => {
          if (!chosen) throw new Error("Pick a preset first.");
          const header = await ctx.stepUp("set_preset", { preset_id: chosen });
          await api.setPreset(chosen, header);
        }),
      );
      return box;
    },
    run() {
      const box = el("div");
      const note = el("textarea");
      note.id = "run-note";
      note.rows = 3;
      note.maxLength = 2000;
      const label = el("label", "", "Optional note for the agent (untrusted text, the policy still decides)");
      label.setAttribute("for", "run-note");
      const mode = el("div", "row");
      const hostile = el("input");
      hostile.type = "checkbox";
      hostile.id = "run-hostile";
      const hl = el("label", "inline", "Use the scripted hostile input (labelled scripted, shows a blocked order)");
      hl.setAttribute("for", "run-hostile");
      mode.append(hostile, hl);
      box.append(label, note, mode);
      box.append(
        task("Run the agent once", async (msg) => {
          const body = { planner: hostile.checked ? "scripted_hostile" : "auto" };
          if (note.value.trim()) body.note = note.value.trim();
          const accepted = await api.run(body);
          msg.textContent = "Running…";
          const job = await ctx.pollJob(accepted.job_id);
          if (job.status === "failed" || job.status === "interrupted") {
            throw new Error(explainError(job.error_code));
          }
          ctx.go("#/feed");
        }),
      );
      return box;
    },
  };

  load();
  return root;
}

function skeleton(el) {
  const s = el("div", "skeleton");
  s.setAttribute("aria-busy", "true");
  s.append(el("p", "note", "Loading…"));
  return s;
}

function empty(el) {
  return el("p", "empty", "Connect a wallet to begin.");
}

export function copyButton(ctx, text) {
  const b = ctx.el("button", "tertiary", "Copy");
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
