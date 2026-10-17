# Architecture: Chat SSE Stream

Owner: `api/sse.py` (backend), `ui/lib/sse.js` (frontend).

Server-Sent Events progress streaming for chat jobs (ticket C-C, spec PR C), providing real-time job phase updates without waiting for polling intervals, while preserving resilient polling fallback.

## Architecture

```mermaid
flowchart LR
  subgraph Frontend ["ui/"]
    CV["views/chat/index.js<br/>follow(jobId)"]
    SSE_UI["ui/lib/sse.js<br/>fetchEventSource"]
    PC["views/progress.js<br/>progress card"]
    POLL["main.js<br/>ctx.pollJob"]
  end

  subgraph Backend ["api/"]
    EP["GET /chat/stream/{job_id}<br/>api/chat.py"]
    SSE_BE["api/sse.py<br/>StreamingResponse"]
    JS[("JobStore<br/>jobs table")]
  end

  CV -->|1. stream phase| SSE_UI
  SSE_UI -->|GET /chat/stream| EP
  EP --> SSE_BE
  SSE_BE -->|poll every 0.5s| JS
  SSE_BE -- "event: phase (elapsed_s)" --> SSE_UI
  SSE_BE -- ": ping (every 15s)" --> SSE_UI
  SSE_BE -- "event: done (job payload)" --> SSE_UI
  SSE_UI -->|update phase| PC
  CV -.->|on error / unsupported| POLL
  POLL -.->|fallback poll /jobs/{id}| JS
```

## Module Responsibilities

1. **`api/sse.py`**:
   - Encodes events using `ServerSentEvent` (`event:`, `data:` JSON, `id:`, comment pings).
   - Serves `GET /chat/stream/{job_id}` via Starlette `StreamingResponse` (`Cache-Control: no-cache, no-transform`, `X-Accel-Buffering: no`).
   - Polls `JobStore` every 0.5 s in an async generator (no new thread) and emits `phase` on every phase transition.
   - Emits `: ping\r\n\r\n` every 15 s.
   - Emits a final `done` event with the full job payload once terminal status is reached.
   - Cleanly terminates on client disconnect without cancelling the job.

2. **`ui/lib/sse.js`**:
   - Plain JavaScript SSE parser and stream consumer adapted from `Azure/fetch-event-source` (MIT).
   - Supports `ReadableStream`, custom headers (e.g. `X-AW-CSRF`), `Last-Event-ID` resumption, and `AbortController` cancellation.
   - Zero third-party dependencies.

3. **`ui/views/chat/index.js`**:
   - `follow(jobId)` uses `streamJob` to follow `phase` events in real-time.
   - If streaming fails, is rejected, or is unsupported, seamlessly falls back to `ctx.pollJob(jobId)` without user-visible difference.
