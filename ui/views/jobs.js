// Shared by Portfolio, Activity and the approvals card: job progress and record loading.
// Every API string is written with textContent.
import { explainJob, recordFromSummary } from "../explain.js";
import { plannerLabel, recordCard } from "./cards.js";

const STATUS_WORDS = {
  queued: "Queued…",
  running: "Running…",
  succeeded: "Finished.",
  denied: "Blocked by the policy engine.",
  failed: "Failed.",
  interrupted: "Interrupted.",
};

// Records of one job, in the DecisionRecord shape. A real job carries summaries; the fixtures
// job carries order ids that are looked up in the demo decisions.
export async function recordsOfJob(ctx, job) {
  if (Array.isArray(job.records)) return job.records.map((r) => recordFromSummary(r, job));
  const ids = Array.isArray(job.order_ids) ? job.order_ids : [];
  if (!ids.length || !ctx.api.fixtures) return [];
  const page = await ctx.api.decisions(200);
  return page.items.filter((r) => ids.includes(r.order_id));
}

// Records for Activity and the approvals card, oldest first.
export async function loadRecords(ctx, limit = 50) {
  if (ctx.api.fixtures) return (await ctx.api.decisions(limit)).items;
  const list = await ctx.api.jobs();
  const rows = Array.isArray(list.items) ? list.items.slice(0, 10) : [];
  const details = await Promise.allSettled(rows.map((j) => ctx.api.job(j.job_id)));
  const out = [];
  for (const d of details) {
    if (d.status !== "fulfilled" || !Array.isArray(d.value.records)) continue;
    for (const r of d.value.records) out.push(recordFromSummary(r, d.value));
  }
  out.sort((a, b) => (a.seq ?? 0) - (b.seq ?? 0));
  return out;
}

// A card that follows one job from "queued" to its records.
export function jobPanel(ctx) {
  const { el } = ctx;
  const node = el("div", "card job-panel");
  node.hidden = true;
  const title = el("h2", "", "Latest job");
  const status = el("p", "msg", "");
  status.setAttribute("role", "status");
  const planner = el("p", "chip", "");
  planner.hidden = true;
  const list = el("ul", "steps");
  node.append(title, status, planner, list);

  function paint(job) {
    node.hidden = false;
    title.textContent = "Latest job: " + String(job.kind || "job");
    status.className = "msg" + (job.status === "failed" || job.status === "interrupted" ? " error" : "");
    status.textContent = STATUS_WORDS[job.status] || "Working…";
    if (job.status === "failed" || job.status === "interrupted" || job.status === "denied") {
      status.textContent += " " + explainJob(job);
    }
    const label = plannerLabel(job);
    planner.hidden = !label;
    planner.textContent = label || "";
  }

  return {
    node,
    begin(text) {
      node.hidden = false;
      title.textContent = "Latest job";
      status.className = "msg";
      status.textContent = text;
      planner.hidden = true;
      list.replaceChildren();
    },
    paint,
    async finish(job) {
      paint(job);
      let records = [];
      try {
        records = await recordsOfJob(ctx, job);
      } catch (e) {
        status.className = "msg error";
        status.textContent += " " + ctx.describeError(e);
      }
      list.replaceChildren(...records.map((r) => recordCard(ctx, r)));
    },
  };
}

// Start a job, follow it, and draw the result. Returns the finished job.
export async function runJob(ctx, panel, startText, start) {
  panel.begin(startText);
  const accepted = await start();
  const job = await ctx.pollJob(accepted.job_id, (j) => panel.paint(j));
  await panel.finish(job);
  return job;
}

// "Audit chain: verified" from GET /audit/verify. Words and an icon, not colour alone.
export function chainChip(ctx) {
  const chip = ctx.el("span", "chip", "Audit chain: checking…");
  async function check() {
    try {
      const v = await ctx.api.auditVerify();
      if (v.ok) {
        chip.className = "chip ok";
        chip.textContent = "✓ Audit chain verified (" + v.records + " records)";
      } else {
        chip.className = "chip bad";
        chip.textContent = "✕ Audit chain broken at record " + v.first_bad_seq;
      }
    } catch (e) {
      chip.className = "chip";
      chip.textContent = "Audit chain: unavailable (" + ctx.describeError(e) + ")";
    }
  }
  return { node: chip, check };
}
