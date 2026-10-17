// Test loadRecords single-request optimization and per-job fallback.
import assert from "node:assert/strict";
import { loadRecords } from "../../ui/views/jobs.js";

async function testOneRequestReplacesN() {
  const jobCalls = [];
  const jobsCalls = [];

  const mockJobs = [
    {
      job_id: "1".repeat(32),
      kind: "run",
      status: "succeeded",
      planner_client: "scripted",
      order_ids: ["ord-1"],
      records: [
        {
          seq: 1,
          order_id: "ord-1",
          outcome: "executed",
          function: "supply",
          token: "USDC",
          amount: { value: "5000000", decimals: 6 },
        },
      ],
    },
    {
      job_id: "2".repeat(32),
      kind: "mint",
      status: "succeeded",
      planner_client: "none",
      order_ids: [],
      records: [
        {
          seq: 2,
          order_id: "ord-2",
          outcome: "executed",
          function: "mint",
          token: "USDC",
          amount: { value: "10000000", decimals: 6 },
        },
      ],
    },
  ];

  const ctx = {
    api: {
      fixtures: false,
      jobs: async (records) => {
        jobsCalls.push(records);
        return { items: mockJobs };
      },
      job: async (id) => {
        jobCalls.push(id);
        return mockJobs.find((j) => j.job_id === id);
      },
    },
  };

  const records = await loadRecords(ctx);
  assert.equal(jobsCalls.length, 1, "jobs() should be called once");
  assert.equal(jobsCalls[0], true, "jobs() should be called with records=true");
  assert.equal(
    jobCalls.length,
    0,
    "job(id) should NOT be called when records are present; 1 request replaces N requests",
  );
  assert.equal(records.length, 2, "Should return 2 records");
  assert.equal(records[0].seq, 1);
  assert.equal(records[1].seq, 2);

  return { singleRequestReplacesN: true, jobCallsCount: jobCalls.length, jobsCallsCount: jobsCalls.length };
}

async function testFallbackWhenNoRecords() {
  const jobCalls = [];
  const jobsCalls = [];

  const mockJobsWithoutRecords = [
    {
      job_id: "1".repeat(32),
      kind: "run",
      status: "succeeded",
      planner_client: "scripted",
      order_ids: ["ord-1"],
    },
    {
      job_id: "2".repeat(32),
      kind: "mint",
      status: "succeeded",
      planner_client: "none",
      order_ids: [],
    },
  ];

  const ctx = {
    api: {
      fixtures: false,
      jobs: async (records) => {
        jobsCalls.push(records);
        return { items: mockJobsWithoutRecords };
      },
      job: async (id) => {
        jobCalls.push(id);
        return {
          job_id: id,
          records: [
            { seq: id === "1".repeat(32) ? 1 : 2, outcome: "executed" },
          ],
        };
      },
    },
  };

  const records = await loadRecords(ctx);
  assert.equal(jobsCalls.length, 1, "jobs() should be called once");
  assert.equal(jobCalls.length, 2, "job(id) should be called as fallback once per job");
  assert.equal(records.length, 2, "Should return 2 records from fallback");

  return { fallbackWorks: true, fallbackJobCallsCount: jobCalls.length };
}

const res1 = await testOneRequestReplacesN();
const res2 = await testFallbackWhenNoRecords();
console.log(JSON.stringify({ ...res1, ...res2 }));
