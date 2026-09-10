import assert from "node:assert/strict";
import { webcrypto } from "node:crypto";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

// Every fetch is intercepted. No real CloudStudio, Cloudflare, or Browserless calls.
const source = readFileSync(new URL("../worker/start.js", import.meta.url), "utf8")
  .replace("export default {", "globalThis.worker = {");
const FOUR_AM = Date.parse("2026-09-10T20:00:00Z");

function harness(options = {}) {
  let clock = options.now ?? FOUR_AM;
  let readIndex = 0;
  const states = options.states ?? ["RUNNING", "STOPPED", "RUNNING"];
  const calls = [];
  const logs = [];
  const waits = [];
  const timeouts = [];
  const env = {
    TENCENT_SECRET_ID: "test-id",
    TENCENT_SECRET_KEY: "test-key",
    KEEPALIVE_HOST: "https://keepalive.example.com",
    SPACE_KEYS: "alpha",
    DAILY_RESTART_ENABLED: "true",
    ...options.env,
  };
  const keys = [...new Set((env.SPACE_KEYS || env.WORKER_DEFAULT_SPACE_KEY || "")
    .split(",").map((key) => key.trim()).filter(Boolean))];
  class Clock extends Date {
    constructor(...args) { super(...(args.length ? args : [clock])); }
    static now() { return clock; }
  }
  const reply = (body, status = 200) => new Response(JSON.stringify(body), { status });
  const fetch = async (url, init = {}) => {
    assert.ok(init.signal, "all outbound requests must have a timeout signal");
    if (url === "https://cloudstudio.tencentcloudapi.com") {
      const action = init.headers["X-TC-Action"];
      const payload = JSON.parse(init.body);
      const call = { action, spaceKey: payload.SpaceKey, payload };
      calls.push(call);
      if (options.failAction === action) throw new Error(`${action} unavailable`);
      if (action === "DescribeWorkspaces") {
        assert.deepEqual(payload, {}, "DescribeWorkspaces must not include SpaceKey/pagination");
        const state = states[Math.min(readIndex++, states.length - 1)];
        if (state instanceof Error) throw state;
        if (state instanceof Response) return state.clone();
        const rows = typeof state === "string"
          ? keys.map((key) => ({ SpaceKey: key, Status: state })) : state;
        call.statuses = Array.isArray(rows) ? rows.map((row) => row.Status) : [];
        return reply({ Response: { Data: rows } });
      }
      if (action === "CreateWorkspaceToken") return reply({ Response: { Token: "test-workspace-token" } });
      if (action === "StopWorkspace" || action === "RunWorkspace") return reply({ Response: { RequestId: "test-request" } });
      throw new Error(`Unexpected Tencent action: ${action}`);
    }
    if (url.startsWith("https://ide.cloud.tencent.com/api/workspace/")) {
      calls.push({ action: "heartbeat", spaceKey: new URL(url).pathname.split("/")[3] });
      return reply({ data: { evict: options.evict ?? false, cause: "NOT_RUNNING" } }, options.heartbeatStatus ?? 200);
    }
    if (url.startsWith("https://production-sfo.browserless.io/function?")) {
      calls.push({ action: "browserless" });
      return new Response("private page contents must not appear in logs", { status: options.browserlessStatus ?? 200 });
    }
    throw new Error(`Unexpected network access: ${url}`);
  };
  const context = vm.createContext({
    Date: Clock, crypto: webcrypto, TextEncoder, URL, Response, Request,
    AbortSignal: { timeout(ms) {
      assert.ok(Number.isInteger(ms) && ms > 0);
      timeouts.push(ms);
      return new AbortController().signal;
    } },
    fetch,
    console: {
      log: (...args) => logs.push(args.join(" ")),
      error: (...args) => logs.push(args.join(" ")),
    },
    setTimeout: (fn, ms) => { waits.push(ms); clock += ms; fn(); return 0; },
  });
  vm.runInContext(source, context, { filename: "worker/start.js" });
  return {
    env, calls, logs, waits, timeouts, context,
    get now() { return clock; },
    scheduled: (scheduledTime = FOUR_AM) => context.worker.scheduled({ scheduledTime }, env, {}),
    fetch: (path) => context.worker.fetch(new Request(env.KEEPALIVE_HOST + path), env, {}),
    actions: () => calls.map((call) => call.action),
    mutations: () => calls.filter((call) => ["StopWorkspace", "RunWorkspace"].includes(call.action)),
  };
}

for (const flag of [undefined, "false", "1", "TRUE", true]) {
  test(`daily restart is opt-in: ${String(flag)} does not stop workspaces`, async () => {
    const h = harness({ env: { DAILY_RESTART_ENABLED: flag } });
    await h.scheduled();
    assert.deepEqual(h.mutations(), []);
    assert.deepEqual(h.actions(), ["CreateWorkspaceToken", "heartbeat"]);
  });
}

test("RUNNING is stopped, confirmed STOPPED, started, and confirmed RUNNING in order", async () => {
  const h = harness({ states: ["RUNNING", "STOPPING", "STOPPED", "STARTING", "RUNNING"] });
  await h.scheduled();
  assert.deepEqual(h.actions(), [
    "DescribeWorkspaces", "StopWorkspace", "DescribeWorkspaces", "DescribeWorkspaces",
    "RunWorkspace", "DescribeWorkspaces", "DescribeWorkspaces", "CreateWorkspaceToken", "heartbeat",
  ]);
  assert.deepEqual(h.waits, [5000, 5000]);
  assert.ok(h.logs.some((line) => line.includes("维护状态确认")));
});

test("an already STOPPED workspace starts once without an unnecessary stop", async () => {
  const h = harness({ states: ["STOPPED", "RUNNING"] });
  await h.scheduled();
  assert.deepEqual(h.mutations().map((call) => call.action), ["RunWorkspace"]);
  assert.equal(h.actions()[0], "DescribeWorkspaces");
});

for (const state of ["STARTING", "STOPPING", "INVALID", "UNKNOWN", "", "PENDING"]) {
  test(`maintenance does not mutate a workspace with state ${state || "missing"}`, async () => {
    const h = harness({ states: [state] });
    await h.scheduled();
    assert.deepEqual(h.mutations(), []);
    assert.ok(h.logs.some((line) => line.includes("维护跳过")));
  });
}

test("a missing workspace is never stopped or started", async () => {
  const h = harness({ states: [[]] });
  await h.scheduled();
  assert.deepEqual(h.mutations(), []);
});

test("lowercase known states are normalized for maintenance", async () => {
  const h = harness({ states: ["running", "stopped", "running"] });
  await h.scheduled();
  assert.deepEqual(h.mutations().map((call) => call.action), ["StopWorkspace", "RunWorkspace"]);
});

test("stop timeout is bounded, logged, and never followed by a blind Run", async () => {
  const h = harness({ states: ["RUNNING"] });
  await h.scheduled();
  assert.equal(h.now - FOUR_AM, 90_000);
  assert.deepEqual(h.mutations().map((call) => call.action), ["StopWorkspace"]);
  assert.ok(h.logs.some((line) => line.includes("waiting for STOPPED timed out")));
  assert.ok(h.timeouts.every((ms) => ms <= 15_000));
});

test("temporary read errors only retry Describe, not Stop", async () => {
  const h = harness({ states: ["RUNNING", new Error("temporary read failure"), "STOPPED", "RUNNING"] });
  await h.scheduled();
  assert.deepEqual(h.mutations().map((call) => call.action), ["StopWorkspace", "RunWorkspace"]);
  assert.deepEqual(h.waits, [5000]);
});

test("persistent read errors after Stop time out without a blind Run", async () => {
  const h = harness({ states: ["RUNNING", new Error("persistent read failure")] });
  await h.scheduled();
  assert.equal(h.now - FOUR_AM, 90_000);
  assert.deepEqual(h.mutations().map((call) => call.action), ["StopWorkspace"]);
  assert.ok(h.logs.some((line) => line.includes("last error=persistent read failure")));
});

test("initial Describe failure cannot trigger Stop", async () => {
  const h = harness({ failAction: "DescribeWorkspaces" });
  await h.scheduled();
  assert.deepEqual(h.mutations(), []);
  assert.ok(h.logs.some((line) => line.includes("定时任务失败")));
});

test("failed Stop is not retried or followed by Run", async () => {
  const h = harness({ failAction: "StopWorkspace" });
  await h.scheduled();
  assert.deepEqual(h.mutations().map((call) => call.action), ["StopWorkspace"]);
});

test("failed Run is logged without claiming successful maintenance", async () => {
  const h = harness({ failAction: "RunWorkspace" });
  await h.scheduled();
  assert.deepEqual(h.mutations().map((call) => call.action), ["StopWorkspace", "RunWorkspace"]);
  assert.ok(h.logs.some((line) => line.includes("RunWorkspace unavailable")));
  assert.ok(!h.logs.some((line) => line.includes("维护状态确认")));
});

test("startup timeout is bounded and cannot cause another Stop", async () => {
  const h = harness({ states: ["RUNNING", "STOPPED", "STARTING"] });
  await h.scheduled();
  assert.equal(h.now - FOUR_AM, 90_000);
  assert.deepEqual(h.mutations().map((call) => call.action), ["StopWorkspace", "RunWorkspace"]);
  assert.ok(h.logs.some((line) => line.includes("waiting for RUNNING timed out")));
});

test("cron uses scheduledTime even when the invocation is delayed", async () => {
  const h = harness({ now: FOUR_AM + 6 * 60_000 });
  await h.scheduled(FOUR_AM);
  assert.equal(h.mutations().length, 2);
});

test("the second configured workspace gets the 04:03 slot", async () => {
  const h = harness({ env: { SPACE_KEYS: "alpha,beta" } });
  await h.scheduled(FOUR_AM + 3 * 60_000);
  assert.deepEqual(h.mutations().map((call) => call.spaceKey), ["beta", "beta"]);
  assert.equal(h.calls.filter((call) => call.action === "heartbeat").length, 2);
});

test("ordinary minutes and hours never restart", async () => {
  for (const time of [FOUR_AM - 60_000, FOUR_AM + 60_000, FOUR_AM + 3600_000]) {
    const h = harness();
    await h.scheduled(time);
    assert.deepEqual(h.mutations(), []);
  }
});

test("duplicate space keys are processed once and preserve first-seen order", async () => {
  const h = harness({ env: { SPACE_KEYS: "alpha,alpha, ,beta,beta" } });
  await h.scheduled(FOUR_AM + 3 * 60_000);
  assert.deepEqual(h.mutations().map((call) => call.spaceKey), ["beta", "beta"]);
  assert.equal(h.calls.filter((call) => call.action === "heartbeat").length, 2);
});

test("the legacy default workspace still gets heartbeats and maintenance", async () => {
  const h = harness({ env: { SPACE_KEYS: "", WORKER_DEFAULT_SPACE_KEY: "alpha" } });
  await h.scheduled();
  assert.equal(h.mutations().length, 2);
});

test("a failure for one workspace cannot suppress another workspace's heartbeat", async () => {
  const h = harness({ env: { SPACE_KEYS: "alpha,beta" }, failAction: "StopWorkspace" });
  await h.scheduled();
  assert.ok(h.calls.some((call) => call.action === "heartbeat" && call.spaceKey === "beta"));
  assert.ok(h.logs.some((line) => line.includes("定时任务失败: alpha")));
});

test("HTTP /scheduled cannot invoke destructive maintenance at 04:00", async () => {
  const h = harness();
  const response = await h.fetch("/scheduled?scheduledTime=" + FOUR_AM);
  assert.equal(response.status, 200);
  assert.deepEqual(h.mutations(), []);
  assert.deepEqual(h.actions(), ["CreateWorkspaceToken", "heartbeat"]);
});

test("missing or non-finite cron timestamps cannot authorize a restart", async () => {
  for (const scheduledTime of [undefined, NaN, Infinity, null, String(FOUR_AM)]) {
    const h = harness();
    await h.context.worker.scheduled({ scheduledTime }, h.env, {});
    assert.deepEqual(h.mutations(), []);
  }
});

test("no new public restart route is exposed", async () => {
  const h = harness();
  assert.equal((await h.fetch("/restart/alpha")).status, 404);
  assert.deepEqual(h.calls, []);
});

test("manual /start keeps its start-only semantics", async () => {
  const h = harness();
  assert.equal((await h.fetch("/start/alpha")).status, 200);
  assert.deepEqual(h.actions(), ["RunWorkspace"]);
});

test("manual /stop keeps its existing stop semantics", async () => {
  const h = harness();
  assert.equal((await h.fetch("/stop/alpha")).status, 200);
  assert.deepEqual(h.actions(), ["StopWorkspace"]);
});

test("a heartbeat can still wake a confirmed stopped workspace with maintenance disabled", async () => {
  const h = harness({ states: ["STOPPED"], evict: true, env: { DAILY_RESTART_ENABLED: "false" } });
  await h.scheduled();
  assert.deepEqual(h.mutations().map((call) => call.action), ["RunWorkspace"]);
});

for (const response of [
  new Response("not JSON"),
  new Response(JSON.stringify({ success: true })),
  new Response(JSON.stringify({ Response: { Error: { Code: "AuthFailure", Message: "denied" } } })),
  new Response(JSON.stringify({ error: "unavailable" }), { status: 503 }),
]) {
  test("invalid or failed API responses never authorize a stop", async () => {
    const h = harness({ states: [response] });
    await h.scheduled();
    assert.deepEqual(h.mutations(), []);
    assert.ok(h.logs.some((line) => line.includes("定时任务失败")));
  });
}

test("malformed workspace lists fail closed", async () => {
  const h = harness({ states: [{ WorkspaceList: { unexpected: true } }] });
  await h.scheduled();
  assert.deepEqual(h.mutations(), []);
  assert.ok(h.logs.some((line) => line.includes("workspace list is not an array")));
});

test("IDE bootstrap happens only after RUNNING is confirmed and does not log page contents", async () => {
  const h = harness({ env: { BROWSERLESS_KEY: "test-browserless-key" } });
  await h.scheduled();
  const browserlessIndex = h.actions().indexOf("browserless");
  const runningIndex = h.calls.findLastIndex((call) => call.action === "DescribeWorkspaces" && call.statuses.includes("RUNNING"));
  assert.ok(browserlessIndex > runningIndex);
  assert.ok(!h.logs.some((line) => line.includes("private page contents")));
  assert.ok(h.timeouts.includes(180_000));
});

test("failed IDE bootstrap reports failure without stopping the recovered workspace again", async () => {
  const h = harness({ env: { BROWSERLESS_KEY: "test-browserless-key" }, browserlessStatus: 503 });
  await h.scheduled();
  assert.deepEqual(h.mutations().map((call) => call.action), ["StopWorkspace", "RunWorkspace"]);
  assert.ok(h.logs.some((line) => line.includes("IDE bootstrap HTTP 503")));
});
