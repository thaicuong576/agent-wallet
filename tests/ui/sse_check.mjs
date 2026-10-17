// Unit tests for ui/sse.js against fake streams and Node.js builtins.
// Prints one JSON object of named results; tests/ui/test_sse.py asserts them. No network.
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const UI = path.join(ROOT, "ui");

const COPY = fs.mkdtempSync(path.join(os.tmpdir(), "aw-ui-sse-"));
fs.copyFileSync(path.join(UI, "sse.js"), path.join(COPY, "sse.js"));
fs.writeFileSync(path.join(COPY, "package.json"), '{"type":"module"}');

const { getBytes, getLines, getMessages, fetchEventSource } = await import(
  pathToFileURL(path.join(COPY, "sse.js")).href
);

const out = {};

// Helper to create a ReadableStream from Uint8Array chunks
function streamFromChunks(chunks) {
  let i = 0;
  return new ReadableStream({
    pull(controller) {
      if (i < chunks.length) {
        controller.enqueue(chunks[i++]);
      } else {
        controller.close();
      }
    },
  });
}

// 1. Chunk boundaries inside a line
{
  const encoder = new TextEncoder();
  const chunks = [
    encoder.encode("event: ph"),
    encoder.encode("ase\r\ndata: {\"pha"),
    encoder.encode("se\":\"queued\"}\r\n\r\n"),
  ];
  const stream = streamFromChunks(chunks);
  const messages = [];
  await getBytes(
    stream,
    getLines(
      getMessages(
        null,
        null,
        (msg) => messages.push(msg)
      )
    )
  );
  out.chunkBoundaries = messages;
}

// 2. CRLF handling
{
  const encoder = new TextEncoder();
  const chunks = [
    encoder.encode("id: 101\r\nevent: phase\r\ndata: rules_check\r\n\r\n"),
  ];
  const stream = streamFromChunks(chunks);
  const messages = [];
  let capturedId = null;
  await getBytes(
    stream,
    getLines(
      getMessages(
        (id) => { capturedId = id; },
        null,
        (msg) => messages.push(msg)
      )
    )
  );
  out.crlf = { messages, capturedId };
}

// 3. Multi-line data
{
  const encoder = new TextEncoder();
  const chunks = [
    encoder.encode("data: line1\r\ndata: line2\r\ndata: line3\r\n\r\n"),
  ];
  const stream = streamFromChunks(chunks);
  const messages = [];
  await getBytes(
    stream,
    getLines(
      getMessages(
        null,
        null,
        (msg) => messages.push(msg)
      )
    )
  );
  out.multiLine = messages;
}

// 4. Retry with Last-Event-ID
{
  const encoder = new TextEncoder();
  let fetchCount = 0;
  const recordedHeaders = [];

  const fakeFetch = async (url, init = {}) => {
    fetchCount++;
    recordedHeaders.push({ ...init.headers });

    if (fetchCount === 1) {
      const stream = streamFromChunks([
        encoder.encode("id: 42\r\nevent: phase\r\ndata: queued\r\nretry: 50\r\n\r\n"),
      ]);
      return {
        ok: true,
        headers: { get: (k) => (k.toLowerCase() === "content-type" ? "text/event-stream" : null) },
        body: stream,
      };
    } else {
      const stream = streamFromChunks([
        encoder.encode("id: 43\r\nevent: done\r\ndata: finished\r\n\r\n"),
      ]);
      return {
        ok: true,
        headers: { get: (k) => (k.toLowerCase() === "content-type" ? "text/event-stream" : null) },
        body: stream,
      };
    }
  };

  const received = [];
  const controller = new AbortController();

  await fetchEventSource("http://test/chat/stream/job1", {
    fetch: fakeFetch,
    signal: controller.signal,
    onmessage(msg) {
      received.push(msg);
      if (msg.event === "done") {
        controller.abort();
      }
    },
    onclose() {
      // Simulate connection close after stream 1 so retry triggers
      if (fetchCount === 1) {
        throw new Error("Stream closed prematurely");
      }
    },
    onerror(err) {
      return 20; // retry after 20ms
    },
  });

  out.retry = {
    fetchCount,
    recordedHeaders,
    received,
  };
}

// 5. Polling fallback simulation
{
  let polled = false;
  let sseError = null;

  async function simulateFollow(shouldError) {
    let job = null;
    try {
      if (shouldError) {
        throw new Error("SSE failed 404");
      }
      job = { status: "succeeded", via: "sse" };
    } catch (e) {
      sseError = e.message;
      polled = true;
      job = { status: "succeeded", via: "polling" };
    }
    return job;
  }

  const result = await simulateFollow(true);
  out.fallback = {
    polled,
    sseError,
    result,
  };
}

console.log(JSON.stringify(out));
