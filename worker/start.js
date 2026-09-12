// CloudStudio 免费工作区保活 Worker(腾讯云 SecretId/Key TC3 签名版)
// Secrets(必需): TENCENT_SECRET_ID / TENCENT_SECRET_KEY / KEEPALIVE_HOST
// Secret(可选): BROWSERLESS_KEY — 重启后打开网页 IDE 触发 preview.yml
//   原生启动链优先; vps-boot.yml 仅支持手动触发,不是定时自愈保证
// Vars: SPACE_KEYS 逗号分隔的工作区 key(留空则只有 WORKER_DEFAULT_SPACE_KEY)
//       DAILY_RESTART_ENABLED="true" 才启用每天 04:00(北京)的真实停启,默认关闭
// 端点:/ 或 /ide/<space> → 302 进网页 IDE 终端(根路径自动选第一个工作区)
//      /heart/<space> 心跳 | /start/<space> /start/all 开机 | /stop/<space> 关机 | /scheduled 手动补心跳(不维护)
//      /status 全部工作区状态 | 心跳链失败(HTTP 非200 / 铸token抛错 / evict:true)且明确关机 → 自动唤醒(见 tryWake)

// 访问网页 IDE,等待终端加载完成 -> 触发 preview.yml autoOpen 启动应用
const API_TIMEOUT_MS = 15_000;
const RESTART_WAIT_MS = 90_000;
const RESTART_POLL_MS = 5_000;

const openIde = (spaceKey, token, browserlessKey) => {
  return fetch("https://production-sfo.browserless.io/function?token=" + browserlessKey, {
    headers: { "Content-Type": "application/javascript" },
    body:
      'export default async({page})=>{await page.goto("https://ide.cloud.tencent.com/tty/' +
      spaceKey +
      '/?report_open_type=list_open&token=' +
      token +
      '",{waitUntil:"networkidle2"});await page.waitForSelector(`.xterm-link-layer`, { timeout: 180000 });}',
    method: "POST",
    signal: AbortSignal.timeout(180_000),
  });
};

export default {
  scheduled,
  fetch: fetchHandler,
};

// * * * * * 由 wrangler.toml 的 [triggers] crons 每分钟触发。
// HTTP /scheduled 仅补心跳:不能让公开 HTTP 请求触发新增的破坏性停启流程。
async function scheduled(event, env, ctx) {
  const cronTime = Number.isFinite(event?.scheduledTime) ? event.scheduledTime : null;
  const now = new Date(cronTime ?? Date.now());
  const beijingHour = (now.getUTCHours() + 8) % 24;
  const beijingMinute = now.getUTCMinutes();
  const spaceKeys = getSpaceKeys(env);
  const restartEnabled = cronTime !== null && env.DAILY_RESTART_ENABLED === "true";

  console.log(
    `UTC ${now.getUTCHours()}:${now.getUTCMinutes()} | 北京 ${beijingHour}:${beijingMinute} | spaces: ${spaceKeys.join(",")}`
  );

  const results = await Promise.allSettled(spaceKeys.map(async (spaceKey, index) => {
    // 4:00 4:03 4:06 ...;使用触发时间,不受 Cron 实际执行延迟影响。
    if (restartEnabled && beijingHour === 4 && beijingMinute === index * 3) {
      // 先停启再心跳,避免心跳先唤醒 STOPPED 工作区后又被本轮停机。
      await restartWorkspace(env, spaceKey);
    }
    const response = await fetchHandler(`${env.KEEPALIVE_HOST}/heart/${spaceKey}`, env, ctx);
    if (!response.ok) throw new Error(`heartbeat HTTP ${response.status}`);
  }));
  results.forEach((result, index) => {
    if (result.status === "rejected") {
      console.error(`定时任务失败: ${spaceKeys[index]}: ${result.reason?.message || result.reason}`);
    }
  });
}

// --- 显式启用的每日维护 ---
// RunWorkspace 对 RUNNING 工作区不是重启;必须确认 Stop 完成后再 Run。
// 不新增公开 /restart 路由,不推断未知状态,不在网络抖动时重复发 Stop。
function workspaceStatus(rows, spaceKey) {
  const ws = rows.find((w) => w.SpaceKey === spaceKey || (!w.SpaceKey && w.Name === spaceKey));
  return ws ? String(ws.Status || "").toUpperCase() : "";
}

async function waitForWorkspaceStatus(env, spaceKey, expected) {
  const deadline = Date.now() + RESTART_WAIT_MS;
  let lastStatus = "unknown";
  let lastError = "";
  while (Date.now() < deadline) {
    try {
      const rows = await describeWorkspaces(env, Math.min(API_TIMEOUT_MS, deadline - Date.now()));
      lastStatus = workspaceStatus(rows, spaceKey) || "not found";
      lastError = "";
      if (expected === "STOPPED" ? STOPPED_STATUS.has(lastStatus) : lastStatus === expected) return;
    } catch (error) {
      // Describe 的限流/网络错误只重试读状态,绝不再次 Stop 或盲目 Run。
      lastError = error.message;
    }
    const remaining = deadline - Date.now();
    if (remaining > 0) await new Promise((resolve) => setTimeout(resolve, Math.min(RESTART_POLL_MS, remaining)));
  }
  throw new Error(
    `${spaceKey}: waiting for ${expected} timed out after ${RESTART_WAIT_MS / 1000}s; last status=${lastStatus}` +
    (lastError ? `; last error=${lastError}` : "")
  );
}

async function openWorkspaceIde(env, spaceKey) {
  if (!env.BROWSERLESS_KEY) {
    console.log("未配置 BROWSERLESS_KEY:依赖已验收的原生启动链,不会自动打开 IDE;RUNNING 不代表业务健康");
    return;
  }
  const tokenRes = await runTencentCloudAPI(env, spaceKey, "CreateWorkspaceToken");
  const response = await openIde(spaceKey, tokenRes.Response.Token, env.BROWSERLESS_KEY);
  if (!response.ok) throw new Error(`IDE bootstrap HTTP ${response.status}`);
  // Browserless 正文可能包含页面/凭据,不原样写日志。
  await response.text();
  console.log(`IDE bootstrap completed: ${spaceKey}`);
}

async function restartWorkspace(env, spaceKey) {
  const before = workspaceStatus(await describeWorkspaces(env), spaceKey);
  const wasRunning = before === "RUNNING";
  if (!wasRunning && !STOPPED_STATUS.has(before)) {
    console.log(`维护跳过: ${spaceKey} 状态 ${before || "not found"};不修改未知/过渡状态`);
    return;
  }
  if (wasRunning) {
    console.log(`维护停机: ${spaceKey} RUNNING → StopWorkspace`);
    await runTencentCloudAPI(env, spaceKey, "StopWorkspace");
    await waitForWorkspaceStatus(env, spaceKey, "STOPPED");
  }
  // 已经 STOPPED 时只开机,不重复停机。Run/确认失败交给明确关机自动唤醒兜底。
  console.log(`维护开机: ${spaceKey} STOPPED → RunWorkspace`);
  await runTencentCloudAPI(env, spaceKey, "RunWorkspace");
  await waitForWorkspaceStatus(env, spaceKey, "RUNNING");
  console.log(`维护状态确认: ${spaceKey} RUNNING;仍需单独检查业务健康与 IDE 凭据`);
  await openWorkspaceIde(env, spaceKey);
}

// --- 关机自动唤醒 ---
// 心跳链失败(铸 token 抛错、心跳非 200、或心跳 body 报 evict:true)→ 查 DescribeWorkspaces,**状态明确是关机才** RunWorkspace:
//  - 只认明确关机状态(见 STOPPED_STATUS),认不出的状态一律不动;每日维护默认关闭,不是无条件兜底,
//    绝不冒"把在跑的工作区误重启"的险;限流抖动时 DescribeWorkspaces 也会挂(直接跳过),双保险
//  - INVALID(已回收)不唤;每 5 分钟最多试一次(防持续失败时反复重启)
const STOPPED_STATUS = new Set(["STOPPED", "Stopped", "STOP", "Stop", "SHUTDOWN", "Shutdown", "OFF", "Off"]);

// DescribeWorkspaces:payload 必须是 {}(不收分页参数);响应在 Response.Data(可能是裸数组或带 WorkspaceList)
async function describeWorkspaces(env, timeoutMs = API_TIMEOUT_MS) {
  const desc = await runTencentCloudAPI(env, "", "DescribeWorkspaces", {}, timeoutMs);
  let rows = desc.Response.Data || desc.Response.WorkspaceList || [];
  if (rows && !Array.isArray(rows)) rows = rows.WorkspaceList || [];
  if (!Array.isArray(rows)) throw new Error("Invalid DescribeWorkspaces response: workspace list is not an array");
  return rows;
}

async function tryWake(env, spaceKey, reason) {
  if (new Date().getUTCMinutes() % 5 !== 0) return;
  try {
    const rows = await describeWorkspaces(env);
    const ws = rows.find((w) => w.SpaceKey === spaceKey || (!w.SpaceKey && w.Name === spaceKey));
    const st = ws ? String(ws.Status || "") : null;
    if (!st || st === "INVALID") return console.log(`唤醒跳过: ${spaceKey} 状态 ${st || "找不到"}`);
    if (!STOPPED_STATUS.has(st))
      return console.log(`唤醒跳过: ${spaceKey} 状态 ${st} 非明确关机,心跳失败(${reason})当抖动处理`);
    console.log(`自动唤醒: ${spaceKey} 状态 ${st}(${reason})→ RunWorkspace`);
    const res = await runTencentCloudAPI(env, spaceKey, "RunWorkspace");
    console.log("RunWorkspace:", JSON.stringify(res.Response).slice(0, 200));
  } catch (e) {
    console.log("唤醒检查失败(多半限流,下个 5 分钟窗口再试):", e.message);
  }
}

async function fetchHandler(request, env, ctx) {
  const url = new URL(typeof request === "string" ? request : request.url);
  let spaceKey = url.pathname.replace(/(.*\/)/, "");

  if (url.pathname === "/scheduled") {
    await scheduled(null, env, ctx);
    return json("completed");
  }

  // 查全部工作区状态(排查/验证用,只读)
  if (url.pathname.includes("/status")) {
    const rows = await describeWorkspaces(env);
    return json({ workspaces: rows.map((w) => ({ spaceKey: w.SpaceKey, name: w.Name, status: w.Status })) });
  }

  if (url.pathname.includes("/start/all")) {
    for (const spaceKey of getSpaceKeys(env)) {
      await fetchHandler(`${env.KEEPALIVE_HOST}/start/${spaceKey}`, env, ctx);
    }
    return json("ok");
  }

  const isStart = url.pathname.includes("/start");
  const isStop = url.pathname.includes("/stop");
  const isHeart = url.pathname.includes("/heart");
  const isIde = url.pathname.includes("/ide");
  // 根路径也当 IDE 入口:自动选第一个工作区(多工作区时用 /ide/<space> 精确指定)
  const isRoot = url.pathname === "/";
  if (!(isStart || isStop || isHeart || isIde || isRoot)) return new Response("Not Found", { status: 404 });
  if (isRoot) spaceKey = getSpaceKeys(env)[0];

  const action = isStart ? "RunWorkspace" : isStop ? "StopWorkspace" : "CreateWorkspaceToken";

  try {
    if (!spaceKey) return json({ Error: "Missing spaceKey" }, 400);

    const response = await runTencentCloudAPI(env, spaceKey, action);

    // 启动应用:开机后需要打开网页 IDE 做初始化,触发 .vscode/preview.yml 的 autoOpen
    if (action === "RunWorkspace") {
      await openWorkspaceIde(env, spaceKey);
    }

    if (action === "CreateWorkspaceToken") {
      const token = response.Response.Token;
      if (isHeart) {
        const hb = await fetch(`https://ide.cloud.tencent.com/api/workspace/${spaceKey}/heartbeat`, {
          headers: {
            authorization: `Bearer ${token}`,
            "x-ide-workspace-session-id": `baa4d673-2hg6-4014-861a-${(Date.now() + "").slice(1, 13)}`,
          },
          body: null,
          method: "GET",
          signal: AbortSignal.timeout(API_TIMEOUT_MS),
        });
        if (!hb.ok) {
          await tryWake(env, spaceKey, `heartbeat HTTP ${hb.status}`);
          return hb;
        }
        // 关机工作区的心跳仍返 200(实测 body {"evict":true,"cause":"NOT_RUNNING"})——必须读 body 才能触发唤醒
        const hbText = await hb.text();
        try {
          const d = JSON.parse(hbText);
          if (d && d.data && d.data.evict === true)
            await tryWake(env, spaceKey, `evict:true(${d.data.cause || ""})`);
        } catch {}
        return new Response(hbText, { status: hb.status, headers: { "Content-Type": "application/json" } });
      }
      if (isIde || isRoot) {
        // 302(不缓存):token 是一次性的,301 会被浏览器缓存导致下次跳旧 token
        return Response.redirect(
          `https://ide.cloud.tencent.com/tty/${spaceKey}/?report_open_type=list_open&token=${token}`,
          302
        );
      }
    }

    return json({ Success: response });
  } catch (error) {
    // 铸 token 失败(除限流外多半是工作区关了)→ 查状态,非运行态就自动唤醒
    if (isHeart) await tryWake(env, spaceKey, error.message);
    return json({ Error: error.message }, 500);
  }
}

function getSpaceKeys(env) {
  const keys = (env.SPACE_KEYS || "")
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
  return [...new Set(keys.length ? keys : [env.WORKER_DEFAULT_SPACE_KEY].filter(Boolean))];
}

function json(data, status = 200) {
  return new Response(JSON.stringify(data, null, 2), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

// --- 腾讯云 TC3 签名 ---

async function getHash(message) {
  const hashBuffer = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(message));
  return Array.from(new Uint8Array(hashBuffer))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

async function hmacSha256(key, message) {
  const keyData = typeof key === "string" ? new TextEncoder().encode(key) : key;
  const cryptoKey = await crypto.subtle.importKey(
    "raw",
    keyData,
    { name: "HMAC", hash: { name: "SHA-256" } },
    false,
    ["sign"]
  );
  return await crypto.subtle.sign("HMAC", cryptoKey, new TextEncoder().encode(message));
}

function arrayBufferToHex(buffer) {
  return Array.from(new Uint8Array(buffer))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

function getDate(timestamp) {
  const date = new Date(timestamp * 1000);
  return `${date.getUTCFullYear()}-${String(date.getUTCMonth() + 1).padStart(2, "0")}-${String(
    date.getUTCDate()
  ).padStart(2, "0")}`;
}

async function runTencentCloudAPI(env, spaceKey, action = "RunWorkspace", payloadOverride, timeoutMs = API_TIMEOUT_MS) {
  if (!env.TENCENT_SECRET_ID || !env.TENCENT_SECRET_KEY) {
    throw new Error("Missing TENCENT_SECRET_ID / TENCENT_SECRET_KEY");
  }

  const host = "cloudstudio.tencentcloudapi.com";
  const service = "cloudstudio";
  const version = "2023-05-08";
  const timestamp = Math.floor(Date.now() / 1000);
  const date = getDate(timestamp);
  // DescribeWorkspaces 的 payload 必须是 {}(不收分页参数),其余 action 都是 {SpaceKey}
  const payload = JSON.stringify(payloadOverride !== undefined ? payloadOverride : { SpaceKey: spaceKey });

  const canonicalHeaders = `content-type:application/json; charset=utf-8\nhost:${host}\n`;
  const hashedRequestPayload = await getHash(payload);
  const canonicalRequest = ["POST", "/", "", canonicalHeaders, "content-type;host", hashedRequestPayload].join("\n");

  const algorithm = "TC3-HMAC-SHA256";
  const credentialScope = `${date}/${service}/tc3_request`;
  const stringToSign = [algorithm, timestamp, credentialScope, await getHash(canonicalRequest)].join("\n");

  const kDate = await hmacSha256("TC3" + env.TENCENT_SECRET_KEY, date);
  const kService = await hmacSha256(kDate, service);
  const kSigning = await hmacSha256(kService, "tc3_request");
  const signature = arrayBufferToHex(await hmacSha256(kSigning, stringToSign));

  const authorization = `${algorithm} Credential=${env.TENCENT_SECRET_ID}/${credentialScope}, SignedHeaders=content-type;host, Signature=${signature}`;

  const fetchResponse = await fetch(`https://${host}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      Host: host,
      "X-TC-Action": action,
      "X-TC-Timestamp": timestamp.toString(),
      "X-TC-Version": version,
      "X-TC-Region": "ap-shanghai",
      Authorization: authorization,
    },
    body: payload,
    signal: AbortSignal.timeout(Math.max(1, Math.floor(timeoutMs))),
  });

  const data = await fetchResponse.json().catch(() => null);
  if (!fetchResponse.ok || (data && data.Response && data.Response.Error)) {
    const err = data && data.Response && data.Response.Error;
    throw new Error(`API Error: ${fetchResponse.status} ${err ? err.Code + " " + err.Message : ""}`);
  }
  if (!data || !data.Response || typeof data.Response !== "object" || Array.isArray(data.Response)) {
    throw new Error(`API Error: ${fetchResponse.status} invalid TencentCloud response`);
  }
  return data;
}
