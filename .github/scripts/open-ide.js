// tc-wtoken.py 的配套:用 runner 自带 Chrome 打开网页 IDE,触发 preview.yml 的 autoOpen 启动链
// 取代 browserless(付费)和 SSH(token 7 天轮换):minted workspace token 驱动 tty 页面即可
// 用法: node open-ide.js <spaceKey> <token> [chromePath]
const puppeteer = require("puppeteer-core");

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
    // 与 Worker openIde 相同的加载判定;IDE 页面 ws 长连接多,networkidle2 可能超时,超时也继续等终端
    try {
      await page.goto(url, { waitUntil: "networkidle2", timeout: 120000 });
    } catch (e) {
      console.log("goto timeout(正常,页面有长连接),继续等终端渲染…");
    }
    // xterm 终端渲染完成 = IDE 完全打开 = autoOpen 启动链已触发
    await page.waitForSelector(".xterm-screen, .xterm-link-layer", { timeout: 180000 });
    console.log("IDE terminal ready — autoOpen chain fired");
    // 给 restore→boot→tunnel→probe→selfheal 链留执行时间
    await new Promise((r) => setTimeout(r, 20000));
  } finally {
    await browser.close();
  }
  console.log("DONE");
})().catch((e) => {
  console.error("FAIL:", e.message);
  process.exit(1);
});
