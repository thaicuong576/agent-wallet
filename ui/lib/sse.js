// Plain JavaScript Server-Sent Events client adapted from Azure/fetch-event-source (MIT).
// Supports ReadableStream, GET or POST with custom headers, Last-Event-ID resume,
// and cancellation via AbortController.

export const EventStreamContentType = "text/event-stream";
const DefaultRetryInterval = 1000;
const LastEventId = "Last-Event-ID";

const ControlChars = {
  NewLine: 10,
  CarriageReturn: 13,
  Space: 32,
  Colon: 58,
};

function concat(a, b) {
  const res = new Uint8Array(a.length + b.length);
  res.set(a);
  res.set(b, a.length);
  return res;
}

function newMessage() {
  return {
    data: "",
    event: "",
    id: "",
    retry: undefined,
  };
}

/**
 * Converts a ReadableStream into chunks for processing.
 */
export async function getBytes(stream, onChunk) {
  const reader = stream.getReader();
  let result;
  while (!(result = await reader.read()).done) {
    onChunk(result.value);
  }
}

/**
 * Parses arbitrary byte chunks into line buffers.
 * Handles \r, \n, and \r\n line terminators across chunk boundaries.
 */
export function getLines(onLine) {
  let buffer;
  let position = 0;
  let fieldLength = -1;
  let discardTrailingNewline = false;

  return function onChunk(arr) {
    if (buffer === undefined) {
      buffer = arr;
      position = 0;
      fieldLength = -1;
    } else {
      buffer = concat(buffer, arr);
    }

    const bufLength = buffer.length;
    let lineStart = 0;

    while (position < bufLength) {
      if (discardTrailingNewline) {
        if (buffer[position] === ControlChars.NewLine) {
          lineStart = ++position;
        }
        discardTrailingNewline = false;
      }

      let lineEnd = -1;
      for (; position < bufLength && lineEnd === -1; ++position) {
        switch (buffer[position]) {
          case ControlChars.Colon:
            if (fieldLength === -1) {
              fieldLength = position - lineStart;
            }
            break;
          case ControlChars.CarriageReturn:
            discardTrailingNewline = true;
            lineEnd = position;
            break;
          case ControlChars.NewLine:
            lineEnd = position;
            break;
        }
      }

      if (lineEnd === -1) {
        // Reached end of buffer without end of line; wait for next chunk
        break;
      }

      onLine(buffer.subarray(lineStart, lineEnd), fieldLength);
      lineStart = position;
      fieldLength = -1;
    }

    if (lineStart === bufLength) {
      buffer = undefined;
    } else if (lineStart !== 0) {
      buffer = buffer.subarray(lineStart);
      position -= lineStart;
    }
  };
}

/**
 * Parses line buffers into EventSource messages.
 */
export function getMessages(onId, onRetry, onMessage) {
  let message = newMessage();
  const decoder = new TextDecoder();

  return function onLine(line, fieldLength) {
    if (line.length === 0) {
      // Empty line signals dispatch of current message
      if (onMessage) onMessage(message);
      message = newMessage();
    } else if (fieldLength > 0) {
      const field = decoder.decode(line.subarray(0, fieldLength));
      const valueOffset = fieldLength + (line[fieldLength + 1] === ControlChars.Space ? 2 : 1);
      const value = decoder.decode(line.subarray(valueOffset));

      switch (field) {
        case "data":
          message.data = message.data ? message.data + "\n" + value : value;
          break;
        case "event":
          message.event = value;
          break;
        case "id":
          if (onId) onId((message.id = value));
          break;
        case "retry": {
          const retry = parseInt(value, 10);
          if (!isNaN(retry) && onRetry) {
            onRetry((message.retry = retry));
          }
          break;
        }
      }
    }
  };
}

function defaultOnOpen(response) {
  if (!response || !response.ok) {
    throw new Error(`HTTP error: ${response ? response.status : "unknown"}`);
  }
  const contentType = response.headers?.get?.("content-type") || "";
  if (!contentType.includes(EventStreamContentType)) {
    throw new Error(`Expected content-type to include ${EventStreamContentType}, got ${contentType}`);
  }
  if (!response.body || typeof response.body.getReader !== "function") {
    throw new Error("ReadableStream body not available");
  }
}

/**
 * Connects to an SSE endpoint using fetch and ReadableStream with automatic reconnection.
 */
export function fetchEventSource(input, {
  signal: inputSignal,
  headers: inputHeaders,
  onopen: inputOnOpen,
  onmessage,
  onclose,
  onerror,
  fetch: inputFetch,
  ...rest
} = {}) {
  return new Promise((resolve, reject) => {
    const headers = { ...inputHeaders };
    if (!headers.accept && !headers.Accept) {
      headers.accept = EventStreamContentType;
    }

    let curRequestController;
    let retryInterval = DefaultRetryInterval;
    let retryTimer = null;
    let disposed = false;

    function dispose() {
      disposed = true;
      if (retryTimer !== null) {
        clearTimeout(retryTimer);
        retryTimer = null;
      }
      if (curRequestController) {
        curRequestController.abort();
      }
    }

    if (inputSignal) {
      if (inputSignal.aborted) {
        dispose();
        resolve();
        return;
      }
      inputSignal.addEventListener("abort", () => {
        dispose();
        resolve();
      });
    }

    const doFetch = inputFetch || globalThis.fetch;
    const onopen = inputOnOpen || defaultOnOpen;

    async function create() {
      if (disposed || inputSignal?.aborted) return;
      curRequestController = new AbortController();

      try {
        const response = await doFetch(input, {
          ...rest,
          headers,
          signal: curRequestController.signal,
        });

        await onopen(response);

        await getBytes(
          response.body,
          getLines(
            getMessages(
              (id) => {
                if (id) {
                  headers[LastEventId] = id;
                } else {
                  delete headers[LastEventId];
                }
              },
              (retry) => {
                retryInterval = retry;
              },
              onmessage,
            ),
          ),
        );

        if (onclose) onclose();
        dispose();
        resolve();
      } catch (err) {
        if (!curRequestController.signal.aborted && !inputSignal?.aborted) {
          try {
            const interval = onerror ? onerror(err) : retryInterval;
            if (interval === null || interval === false) {
              dispose();
              reject(err);
              return;
            }
            const waitMs = typeof interval === "number" ? interval : retryInterval;
            if (retryTimer !== null) clearTimeout(retryTimer);
            retryTimer = setTimeout(create, waitMs);
          } catch (innerErr) {
            dispose();
            reject(innerErr);
          }
        } else {
          dispose();
          resolve();
        }
      }
    }

    create();
  });
}
