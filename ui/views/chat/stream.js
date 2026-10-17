// SSE event handling for phase, token, reset, done.
import { ROUTES as DEFAULT_ROUTES } from "../../lib/api.js";
import { fetchEventSource as defaultFetchEventSource } from "../../lib/sse.js";

export async function streamJob(
  jobId,
  callbacksOrOnUpdate = {},
  maybeOnTokenOrDeps = {},
  maybeOnReset,
  maybeDeps = {}
) {
  let onUpdate;
  let onToken;
  let onReset;
  let deps;

  if (typeof callbacksOrOnUpdate === "function") {
    onUpdate = callbacksOrOnUpdate;
    onToken = typeof maybeOnTokenOrDeps === "function" ? maybeOnTokenOrDeps : undefined;
    onReset = typeof maybeOnReset === "function" ? maybeOnReset : undefined;
    deps =
      typeof maybeDeps === "object" && maybeDeps !== null && Object.keys(maybeDeps).length > 0
        ? maybeDeps
        : typeof maybeOnTokenOrDeps === "object" && maybeOnTokenOrDeps !== null
          ? maybeOnTokenOrDeps
          : {};
  } else {
    const cb = callbacksOrOnUpdate || {};
    onUpdate = cb.onUpdate;
    onToken = cb.onToken;
    onReset = cb.onReset;
    deps = typeof maybeOnTokenOrDeps === "object" && maybeOnTokenOrDeps !== null ? maybeOnTokenOrDeps : {};
  }

  const api = deps.api;
  const routes = deps.routes || deps.ROUTES || DEFAULT_ROUTES;
  const fes = deps.fetchEventSource || defaultFetchEventSource;

  if ((api && api.fixtures) || typeof globalThis.ReadableStream === "undefined") {
    throw new Error("SSE not available");
  }
  const url = routes.chatStream.replace("{job_id}", jobId);
  const controller = new AbortController();
  let doneJob = null;

  await fes(url, {
    signal: controller.signal,
    headers: {
      Accept: "text/event-stream",
      "X-AW-CSRF": "1",
    },
    onopen(res) {
      if (!res || !res.ok) {
        throw new Error(`HTTP ${res ? res.status : "error"}`);
      }
      const ct = res.headers?.get?.("content-type") || "";
      if (!ct.includes("text/event-stream")) {
        throw new Error("Not event stream");
      }
      if (!res.body || typeof res.body.getReader !== "function") {
        throw new Error("No ReadableStream");
      }
    },
    onmessage(ev) {
      if (ev.event === "token") {
        try {
          const data = JSON.parse(ev.data);
          if (data && typeof data.text === "string" && onToken) {
            onToken(data.text);
          }
        } catch (_) {}
      } else if (ev.event === "reset") {
        if (onReset) onReset();
      } else if (ev.event === "phase") {
        try {
          const data = JSON.parse(ev.data);
          if (data && typeof data.phase === "string" && onUpdate) {
            onUpdate(data.phase, "running");
          }
        } catch (_) {}
      } else if (ev.event === "done") {
        try {
          doneJob = JSON.parse(ev.data);
        } catch (_) {}
        controller.abort();
      }
    },
    onerror(err) {
      // Fatal or client errors bail out to polling immediately
      throw err;
    },
  });

  if (!doneJob) {
    throw new Error("Stream closed without done event");
  }
  return doneJob;
}
