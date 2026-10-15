// Activate agent: the control that lifts a new owner from tier T0 (read only) to T2.
// Raising the tier widens the agent's authority, so it needs a fresh wallet signature bound to
// the new tier (design 6.2). Lowering and the emergency stop need the session only.

export const ACTIVATE_TIER = "T2";
export const TIER_NAMES = { T0: "read only", T1: "propose only", T2: "may act inside your limits" };

// POST /auth/step-up, wallet signature, then PUT /tier with X-Step-Up.
export async function activateAgent(ctx, tier = ACTIVATE_TIER) {
  const header = await ctx.stepUp("tier_raise", { tier });
  const res = await ctx.api.setTier(tier, header);
  ctx.state.tier = { tier: res.tier, emergency_stop: Boolean(res.emergency_stop) };
  ctx.state.justActivated = true; // the dashboard keeps showing the result after it reloads
  return res;
}

export async function lowerTier(ctx, tier) {
  const res = await ctx.api.setTier(tier, null);
  ctx.state.justActivated = false;
  ctx.state.tier = { tier: res.tier, emergency_stop: Boolean(res.emergency_stop) };
  return res;
}

// The explanation and the button. `onDone` runs after a successful raise.
export function activateCard(ctx, currentTier, onDone) {
  const { el } = ctx;
  const card = el("div", "card activate");
  card.setAttribute("role", "region");
  card.setAttribute("aria-label", "Activate agent");
  card.append(
    el("h2", "", "Activate your agent"),
    el(
      "p",
      "",
      "Right now the agent is " +
        (TIER_NAMES[currentTier] || "inactive") +
        " (tier " +
        currentTier +
        "), so every order is blocked. Activating lets the agent act inside your limits; you can stop it at any time.",
    ),
    el("p", "note", "Your wallet will ask for one free signature. No transaction is sent."),
  );
  const btn = el("button", "primary", "Activate agent (signature)");
  btn.type = "button";
  const msg = el("p", "status", "");
  msg.setAttribute("role", "status");
  btn.addEventListener("click", async () => {
    btn.disabled = true;
    msg.className = "status";
    msg.textContent = "Waiting for your wallet…";
    try {
      const res = await activateAgent(ctx);
      msg.className = "status ok";
      msg.textContent = "✓ Agent activated: tier " + res.tier + ". It may act inside your limits.";
      ctx.changed();
      if (onDone) await onDone(res);
    } catch (e) {
      msg.className = "status error";
      msg.textContent = ctx.describeError(e);
      btn.disabled = false;
    }
  });
  card.append(btn, msg);
  return card;
}

// The result of an activation, shown on the dashboard until the tier is lowered.
export function activatedCard(ctx, tier) {
  const card = ctx.el("div", "card activate done");
  const msg = ctx.el(
    "p",
    "status ok",
    "✓ Agent activated: tier " + tier + ". It may act inside your limits; you can stop it at any time.",
  );
  msg.setAttribute("role", "status");
  card.append(msg);
  return card;
}

// Lower-the-tier buttons, session only. Shown when the tier is above T0.
export function lowerControls(ctx, currentTier) {
  const { el } = ctx;
  const row = el("div", "row");
  const msg = el("p", "status", "");
  msg.setAttribute("role", "status");
  const targets = currentTier === "T2" ? ["T1", "T0"] : currentTier === "T1" ? ["T0"] : [];
  for (const t of targets) {
    const b = el("button", "secondary", "Lower to " + TIER_NAMES[t] + " (" + t + ")");
    b.type = "button";
    b.addEventListener("click", async () => {
      b.disabled = true;
      msg.className = "status";
      msg.textContent = "Working…";
      try {
        await lowerTier(ctx, t);
        msg.textContent = "Tier is now " + t + ".";
        ctx.changed();
      } catch (e) {
        msg.className = "status error";
        msg.textContent = ctx.describeError(e);
        b.disabled = false;
      }
    });
    row.append(b);
  }
  return targets.length ? [row, msg] : [];
}
