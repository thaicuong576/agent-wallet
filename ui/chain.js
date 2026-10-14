// Audit chain check, run in the browser. Mirrors contracts/README.md "Audit record hash":
// sorted keys, compact separators, UTF-8, nulls kept, record without its hash field.
// Authoritative verification is `python -m core.audit.verify`.
"use strict";

const ZERO_HASH = "0".repeat(64);

function canonical(value) {
  if (value === null) return "null";
  if (Array.isArray(value)) return "[" + value.map(canonical).join(",") + "]";
  if (typeof value === "object") {
    return (
      "{" +
      Object.keys(value)
        .sort()
        .map((k) => JSON.stringify(k) + ":" + canonical(value[k]))
        .join(",") +
      "}"
    );
  }
  return JSON.stringify(value);
}

async function recordHash(record) {
  const body = {};
  for (const k of Object.keys(record)) {
    if (k !== "hash") body[k] = record[k];
  }
  const bytes = new TextEncoder().encode(canonical(body));
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, "0")).join("");
}

// Returns one entry per record: { seq, linkOk, hashOk }, plus the first bad seq (or null).
async function verifyChain(records) {
  const rows = [];
  let firstBad = null;
  let expectedPrev = ZERO_HASH;
  for (let i = 0; i < records.length; i++) {
    const r = records[i];
    const linkOk = r.prev_hash === expectedPrev && r.seq === i;
    const hashOk = (await recordHash(r)) === r.hash;
    rows.push({ seq: r.seq, linkOk, hashOk });
    if (firstBad === null && !(linkOk && hashOk)) firstBad = r.seq;
    expectedPrev = r.hash;
  }
  return { rows, firstBad };
}

if (typeof module !== "undefined") {
  module.exports = { canonical, recordHash, verifyChain, ZERO_HASH };
}
