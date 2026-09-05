// ide-exec.js — 在网页 IDE 终端里执行命令并读回输出,彻底取代 SSH(免 accessToken 7 天轮换)
//
// 原理:通过 runner Chrome 打开 Worker 的 /ide/<space> 端点 → Worker 当场铸 token 并 302 到 tty 页面
//   (xterm 终端)。终端 I/O 走同一条 WebSocket,输出以 {"id":N,"event":"..."} 文本帧回传,
//   直接从 WS 帧读回,绕过 DOM/canvas。打字用 xterm 的 helper textarea,命令末尾追加 sentinel 判完成。
//
// 用法:
//   node ide-exec.js <ideUrl> [chromePath] [--cmd "..."] [--file f] [--sync] [--expect "marker"]
//   ideUrl        Worker /ide/<space> 端点,如 https://keepalive.example.eu.org/ide/qligjr
//   --cmd "..."   要执行的 shell 命令(可多次,按顺序执行)
//   --file f      从文件读取命令(逐行,空行/# 跳过)
//   --sync        内置同步:把 ../../vps/* 文件 base64 写入工作区并跑启动链(部署用)
//   --expect m    输出必须包含 m,否则 exit 1
//   --dump        打印读回的全部终端输出
const puppeteer = require("puppeteer-core");
const fs = require("fs");
const path = require("path");

const DWELL_MS = parseInt(process.env.IDE_DWELL_MS || "8000", 10);

function b64(s) { return Buffer.from(s, "utf8").toString("base64"); }

function decodePayload(d) {
  if (typeof d === "string" && d.startsWith("{")) {
    try { return { kind: "json", obj: JSON.parse(d) }; } catch {}
  }
  if (typeof d === "string") {
    try {
      const buf = Buffer.from(d, "base64");
      const s = buf.toString("utf8");
      const found = [];
      const re = /\{"id":(\d+),"event":"((?:[^"\\]|\\.)*)"\}/g;
      let m;
      while ((m = re.exec(s))) {
        try { found.push({ id: +m[1], event: JSON.parse('"' + m[2] + '"') }); }
        catch { found.push({ id: +m[1], event: m[2] }); }
      }
      return { kind: "bin", text: s, termEvents: found };
    } catch {}
  }
  return { kind: "raw", text: String(d).slice(0, 120) };
}

function stripAnsi(s) {
  return s.replace(/\[[0-9;?]*[a-zA-Z]/g, "").replace(/\][^]*/g, "");
}

function parseArgs(argv) {
  const pos = [];
  const cmd = [], opts = { expect: null, sync: false, dump: false };
  let file = null;
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--cmd") { cmd.push(argv[++i]); }
    else if (a === "--file") { file = argv[++i]; }
    else if (a === "--sync") { opts.sync = true; }
    else if (a === "--expect") { opts.expect = argv[++i]; }
    else if (a === "--dump") { opts.dump = true; }
    else pos.push(a);
  }
  if (file) {
    cmd.push(...fs.readFileSync(file, "utf8").split("\n").map((l) => l.trim()).filter((l) => l && !l.startsWith("#")));
  }
  return { ideUrl: pos[0], exe: pos[1], cmd, opts };
}

// 内置 vps 同步命令:写全部脚本 + preview.yml(两处),再跑启动链
function buildSyncCommands() {
  const vpsDir = path.resolve(__dirname, "../../vps");
  const files = ["restore-assets.sh", "boot-all.sh", "tunnel-start.sh", "probe-start.sh", "selfheal.sh", "selfheal-start.sh"];
  const NA = "/workspace/programming-language-demo/apps/new-api";
  const cmds = [
    `mkdir -p ${NA} /workspace/.vscode /workspace/programming-language-demo/.vscode`,
  ];
  for (const f of files) {
    const data = fs.readFileSync(path.join(vpsDir, f), "utf8");
    cmds.push(`printf '%s' '${b64(data)}' | base64 -d > ${NA}/${f}`);
  }
  const pv = fs.readFileSync(path.join(vpsDir, "preview.yml"), "utf8");
  cmds.push(`printf '%s' '${b64(pv)}' | base64 -d > /workspace/.vscode/preview.yml`);
  cmds.push(`printf '%s' '${b64(pv)}' | base64 -d > /workspace/programming-language-demo/.vscode/preview.yml`);
  cmds.push(`chmod +x ${NA}/*.sh`);
  cmds.push(`cd ${NA} && bash restore-assets.sh && bash boot-all.sh && bash tunnel-start.sh && (bash probe-start.sh || true) && bash selfheal-start.sh; sleep 6; echo '--- 进程 ---'; ps aux | grep -E 'new-api|cloudflared|cf-probe|selfheal' | grep -v grep | awk '{print $11}' | sort | uniq -c; echo '--- 本地 3000 ---'; curl -s -o /dev/null -w 'HTTP %{http_code}\\n' http://localhost:3000/ || true`);
  return cmds;
}

(async () => {
  const { ideUrl, exe, cmd, opts } = parseArgs(process.argv.slice(2));
  if (!ideUrl) {
    console.error("usage: node ide-exec.js <ideUrl> [chromePath] [--cmd \"...\"] [--sync] [--expect m]");
    process.exit(2);
  }
  const commands = opts.sync ? buildSyncCommands() : cmd;
  if (!commands.length) { console.error("no command (use --cmd/--file/--sync)"); process.exit(2); }

  const browser = await puppeteer.launch({
    executablePath: exe || "google-chrome",
    headless: true,
    args: ["--no-sandbox", "--disable-setuid-sandbox", "--disable-gpu", "--disable-dev-shm-usage"],
  });

  const wsUrls = new Map();
  const termBuf = new Map(); // id -> screen text

  try {
    const page = await browser.newPage();
    const client = await page.createCDPSession();
    await client.send("Network.enable");
    client.on("Network.webSocketCreated", (e) => wsUrls.set(e.identifier, e.url));
    client.on("Network.webSocketFrameReceived", (e) => {
      const p = decodePayload(e.response && e.response.payloadData);
      if (p.kind === "json" && p.obj && p.obj.event != null && p.obj.id != null) {
        termBuf.set(p.obj.id, (termBuf.get(p.obj.id) || "") + p.obj.event);
      } else if (p.kind === "bin") {
        for (const te of p.termEvents || []) termBuf.set(te.id, (termBuf.get(te.id) || "") + te.event);
      }
    });

    console.log("open:", ideUrl);
    try { await page.goto(ideUrl, { waitUntil: "networkidle2", timeout: 120000 }); }
    catch (e) { console.log("goto timeout(正常,长连接),继续…"); }

    // 终端面板偶发不自动展开(需 Ctrl+` 切换):先聚焦工作区再发快捷键兜底
    async function nudgeTerminal() {
      try {
        await page.mouse.click(400, 300);
        await page.keyboard.down("Control");
        await page.keyboard.press("Backquote");
        await page.keyboard.up("Control");
      } catch {}
    }
    let ta = null;
    for (let i = 0; i < 30 && !ta; i++) {
      await new Promise((r) => setTimeout(r, 3000));
      ta = await page.$("textarea.xterm-helper-textarea, textarea");
      if (!ta) await nudgeTerminal();
    }
    if (!ta) {
      // 偶发加载慢/终端未挂载:重新加载页面再等一轮
      console.log("!! 首轮 90s 未找到 textarea,重新加载页面重试…");
      try { await page.goto(page.url(), { waitUntil: "domcontentloaded", timeout: 60000 }); } catch {}
      for (let i = 0; i < 20 && !ta; i++) {
        await new Promise((r) => setTimeout(r, 3000));
        ta = await page.$("textarea.xterm-helper-textarea, textarea");
        if (!ta) await nudgeTerminal();
      }
    }
    if (!ta) {
      console.error("!!! 终端 textarea 仍未出现");
      console.log("url:", page.url());
      console.log("body:", (await page.evaluate(() => document.body.innerText.slice(0, 600)).catch(() => "")).replace(/\n/g, "\\n"));
      await page.screenshot({ path: "/tmp/ide-exec-fail.png" }).catch(() => {});
      process.exitCode = 1; return;
    }
    console.log("textarea ready");

    const allText = () => stripAnsi([...termBuf.values()].join(""));

    // 等 shell 就绪:提示符出现(autoOpen 启动链可能正在跑,等它回到提示符)
    // 就绪后额外等 5s 再确认一次,避免把命令打进仍在输出的启动链里
    const ready = () => /[→➜$#>]\s*$/.test(allText().replace(/[\r\n]+$/, "")) || /➜\s*\/workspace/.test(allText());
    let gotReady = false;
    for (let i = 0; i < 30; i++) {
      await new Promise((r) => setTimeout(r, 3000));
      if (ready()) { gotReady = true; console.log(`@${(i + 1) * 3}s shell 就绪`); break; }
    }
    if (!gotReady) console.log("!! 90s 未检测到提示符,仍尝试执行(新工作区可能直接是纯提示符)");
    else {
      // 稳定确认:再等 5s,提示符仍在才动手
      await new Promise((r) => setTimeout(r, 5000));
      if (!ready()) console.log("!! 提示符不稳定(启动链可能仍在输出),仍继续");
    }

    await page.evaluate(() => {
      const x = document.querySelector(".xterm");
      if (x) x.click();
      const t = document.querySelector("textarea.xterm-helper-textarea, textarea");
      if (t) t.focus();
    });
    await new Promise((r) => setTimeout(r, 500));

    // 逐条执行,每条末尾追加 sentinel 判完成,再读回输出
    const sentinel = `__IDEEXEC_${Date.now().toString(36)}__`;
    let lastOut = "";
    for (let i = 0; i < commands.length; i++) {
      const c = commands[i];
      const marker = `${sentinel}_${i}`;
      console.log(`\n>>> [${i + 1}/${commands.length}] ${c.length > 120 ? c.slice(0, 120) + "…" : c}`);
      termBuf.clear();
      await ta.type(`${c}\n; echo ${marker} $?\n`, { delay: 8 }).catch((e) => console.log("type fail:", e.message));

      // 等 sentinel 出现(或超时)
      let ok = false;
      for (let w = 0; w < 120; w++) {
        await new Promise((r) => setTimeout(r, 1000));
        if (allText().includes(marker)) { ok = true; break; }
      }
      const out = allText();
      lastOut = out;
      // 截取 sentinel 前内容作为本条输出
      const idx = out.indexOf(marker);
      const body = idx >= 0 ? out.slice(0, idx) : out;
      const tail = stripAnsi(body).replace(/^\s*\S*\s*$/, "").trim();
      console.log(`--- 输出(${ok ? "OK" : "超时"}):`);
      console.log(tail.slice(-1500) || "(空)");
      if (!ok) { console.error(`!!! 命令 ${i + 1} 120s 无 sentinel 回显`); process.exitCode = 1; break; }
      await new Promise((r) => setTimeout(r, 300));
    }

    if (opts.dump) {
      console.log("\n=== 全部终端输出 ===");
      console.log(stripAnsi(allText()).slice(-4000));
    }

    if (opts.expect) {
      if (stripAnsi(lastOut).includes(opts.expect)) console.log(`\n✓ 命中期望标记: ${opts.expect}`);
      else { console.error(`\n✗ 未命中期望标记: ${opts.expect}`); process.exitCode = 1; }
    }

    await new Promise((r) => setTimeout(r, DWELL_MS));
  } finally {
    await browser.close();
  }
  console.log(process.exitCode ? "FAIL" : "DONE");
})().catch((e) => { console.error("FAIL:", e.message); process.exit(1); });
