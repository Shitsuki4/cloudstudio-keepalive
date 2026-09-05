// CloudStudio 免费工作区保活 Worker(腾讯云 SecretId/Key TC3 签名版)
// Secrets(必需): TENCENT_SECRET_ID / TENCENT_SECRET_KEY / KEEPALIVE_HOST
// Secret(可选): BROWSERLESS_KEY — 重启后打开网页 IDE 触发 preview.yml
//   (现已有每小时铸 token + Actions Chrome 的 vps-boot.yml 兜底,不配也能自愈)
// Vars: SPACE_KEYS 逗号分隔的工作区 key(留空则只有 WORKER_DEFAULT_SPACE_KEY)
// 端点:/ 或 /ide/<space> → 302 进网页 IDE 终端(根路径自动选第一个工作区)
//      /heart/<space> 心跳 | /start/<space> /start/all 开机 | /stop/<space> 关机 | /scheduled(cron)
//      /status 全部工作区状态 | 心跳链失败(HTTP 非200 / 铸token抛错 / evict:true)且明确关机 → 自动唤醒(见 tryWake)

// 访问网页 IDE,等待终端加载完成 -> 触发 preview.yml autoOpen 启动应用
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
  });
};

export default {
  scheduled,
  fetch: fetchHandler,
};

// * * * * * 由 wrangler.toml 的 [triggers] crons 每分钟触发
async function scheduled(event, env, ctx) {
  const now = new Date();
  const beijingHour = (now.getUTCHours() + 8) % 24;
  const beijingMinute = now.getUTCMinutes();
  const spaceKeys = getSpaceKeys(env);

  console.log(
    `UTC ${now.getUTCHours()}:${now.getUTCMinutes()} | 北京 ${beijingHour}:${beijingMinute} | spaces: ${spaceKeys.join(",")}`
  );

  // 每分钟:全部空间打心跳(evict:false 不回收)
  await Promise.allSettled(
    spaceKeys.map((spaceKey) => fetchHandler(`${env.KEEPALIVE_HOST}/heart/${spaceKey}`, env, ctx))
  );

  // 凌晨 4 点(北京时间)重启工作区并打开网页 IDE 拉起应用(需 BROWSERLESS_KEY)
  if (beijingHour !== 4) return;
  for (const [index, spaceKey] of spaceKeys.entries()) {
    // 4:00 4:03 4:06 ... 间隔开,避免同时启动
    if (beijingMinute === index * 3) {
      try {
        await fetchHandler(`${env.KEEPALIVE_HOST}/start/${spaceKey}`, env, ctx);
      } catch (error) {
        console.log(error);
      }
    }
  }
}

// --- 关机自动唤醒 ---
// 心跳链失败(铸 token 抛错、心跳非 200、或心跳 body 报 evict:true)→ 查 DescribeWorkspaces,**状态明确是关机才** RunWorkspace:
//  - 只认 STOPPED 黑名单(见 STOPPED_STATUS),认不出的状态一律不动——最坏=维持原状等每天 04:00 重启兜底,
//    绝不冒"把在跑的工作区误重启"的险;限流抖动时 DescribeWorkspaces 也会挂(直接跳过),双保险
//  - INVALID(已回收)不唤;每 5 分钟最多试一次(防持续失败时反复重启)
const STOPPED_STATUS = new Set(["STOPPED", "Stopped", "STOP", "Stop", "SHUTDOWN", "Shutdown", "OFF", "Off"]);

// DescribeWorkspaces:payload 必须是 {}(不收分页参数);响应在 Response.Data(可能是裸数组或带 WorkspaceList)
async function describeWorkspaces(env) {
  const desc = await runTencentCloudAPI(env, "", "DescribeWorkspaces", {});
  let rows = desc.Response.Data || desc.Response.WorkspaceList || [];
  if (rows && !Array.isArray(rows)) rows = rows.WorkspaceList || [];
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

  if (url.pathname.includes("/scheduled")) {
    await scheduled(request, env, ctx);
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
      if (!env.BROWSERLESS_KEY) {
        console.log("未配置 BROWSERLESS_KEY,跳过打开 IDE(需自行触发 preview.yml)");
      } else {
        const tokenRes = await runTencentCloudAPI(env, spaceKey, "CreateWorkspaceToken");
        const res = await openIde(spaceKey, tokenRes.Response.Token, env.BROWSERLESS_KEY);
        console.log(await res.text());
      }
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
  return keys.length ? keys : [env.WORKER_DEFAULT_SPACE_KEY].filter(Boolean);
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

async function runTencentCloudAPI(env, spaceKey, action = "RunWorkspace", payloadOverride) {
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
  });

  const data = await fetchResponse.json().catch(() => null);
  if (!fetchResponse.ok || (data && data.Response && data.Response.Error)) {
    const err = data && data.Response && data.Response.Error;
    throw new Error(`API Error: ${fetchResponse.status} ${err ? err.Code + " " + err.Message : ""}`);
  }
  return data;
}
