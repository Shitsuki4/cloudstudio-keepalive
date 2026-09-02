// CloudStudio 免费工作区保活 Worker
// 所有敏感参数通过 Cloudflare Worker 的环境变量(Secrets/Vars)注入:
//   FOREVER_TOKEN_<SPACEKEY 大写>  — 空间对应的长效 token(几十年有效期),省 TC3 签名
//   TENCENT_SECRET_ID / TENCENT_SECRET_KEY — 腾讯云 API 密钥(没有 forever token 时才需要)
//   BROWSERLESS_KEY                — browserless.io key,用于凌晨重启后打开网页 IDE 触发 preview.yml
//   KEEPALIVE_HOST                 — 本 worker 的入口域名(必须是自定义域,workers.dev 在国内被污染)
// 非敏感配置用 Vars:  SPACE_KEYS(逗号分隔,如 "udfxel,abc123")

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

  // 凌晨 4 点(北京时间)重启工作区并打开网页 IDE 拉起应用
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

async function fetchHandler(request, env, ctx) {
  const url = new URL(typeof request === "string" ? request : request.url);
  const spaceKey = url.pathname.replace(/(.*\/)/, "");

  if (url.pathname.includes("/scheduled")) {
    await scheduled(request, env, ctx);
    return json("completed");
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
  if (!(isStart || isStop || isHeart || isIde)) return new Response("Not Found", { status: 404 });

  const action = isStart ? "RunWorkspace" : isStop ? "StopWorkspace" : "CreateWorkspaceToken";

  try {
    if (!spaceKey) return json({ Error: "Missing spaceKey" }, 400);

    const response = await runTencentCloudAPI(env, spaceKey, action);

    // 启动应用:开机后需要打开网页 IDE 做初始化,触发 .vscode/preview.yml 的 autoOpen
    if (action === "RunWorkspace") {
      const tokenRes = await runTencentCloudAPI(env, spaceKey, "CreateWorkspaceToken");
      const token = tokenRes.Response.Token;
      const res = await openIde(spaceKey, token, env.BROWSERLESS_KEY);
      console.log(await res.text());
    }

    if (action === "CreateWorkspaceToken") {
      const token = response.Response.Token;
      if (isHeart) {
        return fetch(`https://ide.cloud.tencent.com/api/workspace/${spaceKey}/heartbeat`, {
          headers: {
            authorization: `Bearer ${token}`,
            "x-ide-workspace-session-id": `baa4d673-2hg6-4014-861a-${(Date.now() + "").slice(1, 13)}`,
          },
          body: null,
          method: "GET",
        });
      }
      if (isIde) {
        return Response.redirect(
          `https://ide.cloud.tencent.com/tty/${spaceKey}/?report_open_type=list_open&token=${token}`,
          301
        );
      }
    }

    return json({ Success: response });
  } catch (error) {
    return json({ Error: error.message }, 500);
  }
}

function getSpaceKeys(env) {
  return (env.SPACE_KEYS || "")
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
}

function json(data, status = 200) {
  return new Response(JSON.stringify(data, null, 2), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

// --- 腾讯云 TC3 签名工具 ---

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

async function runTencentCloudAPI(env, spaceKey, action = "RunWorkspace") {
  // 优先用 forever token(免签名、免 API 密钥)
  const foreverToken = env[`FOREVER_TOKEN_${spaceKey.toUpperCase()}`];
  if (foreverToken && action === "CreateWorkspaceToken") {
    return await Promise.resolve({ Response: { Token: foreverToken } });
  }

  if (!env.TENCENT_SECRET_ID || !env.TENCENT_SECRET_KEY) {
    throw new Error(
      `Missing token: set Secret FOREVER_TOKEN_${spaceKey.toUpperCase()} or TENCENT_SECRET_ID/KEY`
    );
  }

  const host = "cloudstudio.tencentcloudapi.com";
  const service = "cloudstudio";
  const version = "2023-05-08";
  const timestamp = Math.floor(Date.now() / 1000);
  const date = getDate(timestamp);
  const payload = JSON.stringify({ SpaceKey: spaceKey });

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

  if (!fetchResponse.ok) {
    throw new Error(`API Error: ${fetchResponse.status} ${await fetchResponse.text()}`);
  }
  return await fetchResponse.json();
}
