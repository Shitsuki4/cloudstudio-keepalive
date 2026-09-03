// tc-wtoken.py 的配套:用 runner 自带 Chrome 打开网页 IDE,触发 preview.yml 的 autoOpen 启动链
// 取代 browserless(付费)和 SSH(token 7 天轮换):minted workspace token 驱动 tty 页面即可
//
// 关键认知(逆向 CloudStudio.browser-preview-lite 得出):
//   1. autoOpen 找的是 workspaceFolders[0]/.vscode/preview.yml;tty 直开时根目录是 /workspace
//      (code-server --default-folder=/workspace),所以 preview.yml 必须放在 /workspace/.vscode/
//   2. 终端在 /ws/embeded iframe 里,主 frame 等不到 .xterm 选择器,不要等
//   3. 页面断开 → exthost 约 20s 内退出 → 扩展终端被杀;&& 链没跑完会中断,
//      所以必须驻留足够久让整条启动链执行完(nohup 的子进程不受影响)
// 用法: node open-ide.js <spaceKey> <token> [chromePath]
const puppeteer = require("puppeteer-core");

const DWELL_MS = parseInt(process.env.IDE_DWELL_MS || "75000", 10);

(async () => {
  const [spaceKey, token, exe] = process.argv.slice(2);
  if (!spaceKey || !token) {
    console.error("usage: node open-ide.js <spaceKey> <token> [chromePath]");
    process.exit(1);
  }
  const browser = await puppeteer.launch({
    executablePath: exe || "google-chrome",
    headless: true,
    args: ["--no-sandbox", "--disable-setuid-sandbox", "--disable-gpu", "--disable-dev-shm-usage"],
  });
  try {
    const page = await browser.newPage();
    const url = `https://ide.cloud.tencent.com/tty/${spaceKey}/?report_open_type=list_open&token=${token}`;
    console.log("open:", url.replace(token, "***"));
    try {
      await page.goto(url, { waitUntil: "networkidle2", timeout: 120000 });
    } catch (e) {
      console.log("goto timeout(正常,页面有长连接),继续驻留…");
    }
    // 诊断信息:frame 结构(终端/工作台在 iframe 里)
    for (const f of page.frames()) {
      console.log("frame:", f.url().slice(0, 120));
    }
    // 驻留:给 exthost 激活 → 轮询 preview.yml(500ms)→ 终端执行整条 && 启动链留足时间
    console.log(`dwell ${DWELL_MS / 1000}s 保持页面打开(autoOpen 启动链执行中)…`);
    await new Promise((r) => setTimeout(r, DWELL_MS));
  } finally {
    await browser.close();
  }
  console.log("DONE");
})().catch((e) => {
  console.error("FAIL:", e.message);
  process.exit(1);
});
