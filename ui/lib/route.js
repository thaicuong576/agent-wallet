// Clean URLs: the app lives at /chat, /overview, /activity, /rules and /setup. Views still speak
// in "#/name" strings; this module maps them to paths when the page is served at a path, and falls
// back to the hash (old /ui/#/... links, the test harness) everywhere else.

const PATH_RE = /^\/(chat|overview|activity|rules|setup|portfolio|dashboard|approvals|feed|onboard)(\/|$)/;

function pathMode(g) {
  return Boolean(g && g.history && g.history.pushState && g.location && PATH_RE.test(g.location.pathname || ""));
}

// The current route as "#/name[/id]", from the hash when there is one, otherwise from the path.
export function currentHash(g = globalThis) {
  const loc = g && g.location;
  if (!loc) return "";
  if (loc.hash && loc.hash.startsWith("#/")) return loc.hash;
  const p = loc.pathname || "";
  return PATH_RE.test(p) ? "#" + p : "";
}

// Move an old "/ui/#/chat" or "/chat#/rules" address to its clean path without reloading.
export function upgrade(g = globalThis) {
  const loc = g && g.location;
  if (!loc || !g.history || !g.history.replaceState) return;
  if (loc.hash && loc.hash.startsWith("#/")) g.history.replaceState(null, "", loc.hash.slice(1) + (loc.search || ""));
}

// Navigate to "#/name[/id]". Listeners keep using the "hashchange" event in both modes.
export function go(hash, g = globalThis) {
  if (!g || !g.location) return;
  if (pathMode(g)) {
    const path = String(hash).replace(/^#/, "") || "/chat";
    if (g.location.pathname === path && !g.location.hash) return;
    g.history.pushState(null, "", path + (g.location.search || ""));
    g.dispatchEvent(new Event("hashchange"));
  } else {
    g.location.hash = hash;
  }
}

// In path mode, links written as href="#/name" navigate by path instead of adding a hash.
export function interceptLinks(doc, g = globalThis) {
  doc.addEventListener("click", (ev) => {
    if (!pathMode(g) || ev.defaultPrevented || ev.button !== 0) return;
    if (ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.altKey) return;
    const a = ev.target && ev.target.closest ? ev.target.closest("a[href^='#/']") : null;
    if (!a) return;
    ev.preventDefault();
    go(a.getAttribute("href"), g);
  });
}

// Prefix for files under /ui/ (fixtures, token icons) when the page is served at a clean path.
export function uiBase(g = globalThis) {
  const p = (g && g.location && g.location.pathname) || "";
  return p && !p.startsWith("/ui") ? "/ui/" : "";
}
