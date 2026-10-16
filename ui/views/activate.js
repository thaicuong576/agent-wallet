// Activate agent: the control that lifts a new owner from tier T0 (read only) to T2.
// Raising the tier widens the agent's authority, so it needs a fresh wallet signature bound to
// the new tier (design 6.2). Lowering and the emergency stop need the session only. The button
// itself lives on the Setup screen (views/setup.js).

export const ACTIVATE_TIER = "T2";
export const TIER_NAMES = { T0: "read only", T1: "propose only", T2: "may act inside your limits" };

// POST /auth/step-up, wallet signature, then PUT /tier with X-Step-Up.
export async function activateAgent(ctx, tier = ACTIVATE_TIER, presetId = undefined) {
  const header = await ctx.stepUp("tier_raise", { tier });
  const res = await ctx.api.setTier(tier, header, presetId);
  ctx.state.tier = { tier: res.tier, emergency_stop: Boolean(res.emergency_stop) };
  ctx.state.justActivated = true;
  return res;
}

export async function lowerTier(ctx, tier) {
  const res = await ctx.api.setTier(tier, null);
  ctx.state.justActivated = false;
  ctx.state.tier = { tier: res.tier, emergency_stop: Boolean(res.emergency_stop) };
  return res;
}

// Lower-the-tier buttons, session only. Shown when the tier is above T0.
export function lowerControls(ctx, currentTier) {
  const { el } = ctx;
  const row = el("div", "row");
  const msg = el("p", "msg", "");
  msg.setAttribute("role", "status");
  const targets = currentTier === "T2" ? ["T1", "T0"] : currentTier === "T1" ? ["T0"] : [];
  for (const t of targets) {
    const b = el("button", "", "Lower to " + TIER_NAMES[t] + " (" + t + ")");
    b.type = "button";
    b.addEventListener("click", async () => {
      b.disabled = true;
      msg.className = "msg";
      msg.textContent = "Working…";
      try {
        await lowerTier(ctx, t);
        msg.textContent = "Tier is now " + t + ".";
        ctx.changed();
      } catch (e) {
        msg.className = "msg error";
        msg.textContent = ctx.describeError(e);
        b.disabled = false;
      }
    });
    row.append(b);
  }
  return targets.length ? [row, msg] : [];
}
