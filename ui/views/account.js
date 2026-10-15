// The account control: a "Log out" button next to the owner address chip in the header (and in
// the Setup header). It asks the server to end the session, then the shell drops its client
// state and shows the signed-out connect screen. Nothing is stored in the browser.
export function renderAccountControl(ctx) {
  const { el } = ctx;
  const btn = el("button", "logout", "Log out");
  btn.type = "button";
  btn.setAttribute("aria-label", "Log out of agent-wallet");
  btn.addEventListener("click", async () => {
    btn.disabled = true;
    await ctx.logout();
  });
  return btn;
}
