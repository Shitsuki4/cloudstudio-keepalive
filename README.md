> **⚠️ 本仓库包含部署脚本,请保持 Private!**

# CloudStudio Keepalive — 腾讯云 CloudStudio 免费工作区 24h 保活

把腾讯 CloudStudio 免费工作区变成不回收的永久工作区:

```
┌─ Cloudflare Worker(每分钟 cron)────────────────────┐
│  ① /heart/<space>   心跳 → 工作区 evict:false 不回收     │
│  ② 每天 04:00(北京) RunWorkspace 重启工作区             │
│  ③ / 或 /ide/<space> 302 进网页 IDE 终端(免 SSH)        │
│  ④ /status 查全部工作区状态 | 关机自动唤醒(见下)        │
└──────────────────────────────────────────────────┘
```

心跳是 Worker 出站调腾讯云 API 铸 workspace token 后打的 HTTP 请求,工作区本身**不需要开放任何端口**。

## 前置条件

1. **腾讯云账号**已建好至少一个 CloudStudio 工作区(https://ide.cloud.tencent.com)
2. 该账号的 **API 密钥**(SecretId/SecretKey,访问管理 → API 密钥管理)
3. **Cloudflare 账号**有一个 zone(域名已托管到 CF),workers.dev 在国内被污染,必须用自定义域

## 一键部署

1. **Fork / 推送本仓库到你的 GitHub(Private)**
2. **仓库 Settings → Secrets and variables → Actions → Secrets** 添加(就这 5 个):

   | Secret | 必须 | 说明 | 值样例(轻码) |
   |---|---|---|---|
   | `TENCENT_SECRET_ID` | ✅ | 腾讯云 API 密钥 Id | `AKIDfMo7****tqyrV` |
   | `TENCENT_SECRET_KEY` | ✅ | 腾讯云 API 密钥 Key | `ThfOsIcj****XjiV5` |
   | `CF_AUTH_EMAIL` | ✅ | Cloudflare 账号邮箱 | `31105****72@qq.com` |
   | `CF_GLOBAL_API_KEY` | ✅ | Cloudflare Global API Key(仅用于部署时铸临时 token,用完即删) | `950bc807****0ff83a8f` |
   | `KEEPALIVE_DOMAIN` | ✅ | worker 入口域名(zone 内可建 DNS,不带 `https://`) | `keepalive.****4.eu.org` |

   > 看样例认格式:SecretId 固定 `AKID` 开头共 36 位;Global Key 是 37 位十六进制。

3. **Actions → Keepalive Setup → Run workflow**(会校验密钥、自动发现你的工作区 spaceKey,然后触发部署)
4. 完成后验证:`https://<KEEPALIVE_DOMAIN>/heart/<spaceKey>` 返回 `{"evict":false,...}` 即保活生效

## 工作方式

- **spaceKey 不用手填**:部署时用腾讯云密钥调 `DescribeWorkspaces` 自动发现账号下全部工作区
- **Global API Key 不直接部署**:Actions 运行时用它铸一个仅限本 zone 的临时 API token,部署完自动删除;Worker 长期运行只需 SecretId/Key
- **每天 04:00(北京)重启工作区**:Worker 调 `RunWorkspace`,容器重建(进程清零,`/workspace` 数据幸存),之后心跳继续,工作区永不因空闲被回收
- **关机自动唤醒**(`tryWake`):心跳链失败(铸 token 抛错、心跳非 200、或心跳 body 报 `evict:true`——关机工作区心跳仍返 200,实测 `cause:NOT_RUNNING`)→ Worker 查 `DescribeWorkspaces`,**状态明确是关机才** `RunWorkspace` 拉起(手动关机最迟 5 分钟内自动爬起)。安全设计:只认 STOPPED 黑名单,认不出的状态一律不动——最坏=维持原状等每天 04:00 重启兜底,绝不误重启在跑的工作区;已回收(INVALID)不唤;每 5 分钟最多试一次
- **`/status` 端点**:`https://<KEEPALIVE_DOMAIN>/status` 只读查全部工作区状态(排查用)
- **每小时免 SSH 打开网页 IDE**(`vps-boot.yml`):Actions 用腾讯云密钥铸 workspace token(~10 分钟有效),runner Chrome 打开 tty 页面——工作区装有 `/workspace/.vscode/preview.yml` 时触发 autoOpen 启动链;配套 `ide-exec.js` 终端通道(打开 `https://<KEEPALIVE_DOMAIN>` 根路径,Worker 自动选工作区并 302 进网页终端;多工作区用 `/ide/<spaceKey>` 精确指定)可免 SSH 执行任意命令读回输出,SSH accessToken 7 天轮换不再依赖
- **改代码后再部署**:push `worker/**` 自动触发,或手动 Run workflow

## 目录结构

```
worker/
  start.js            Worker 源码(TC3 签名调 CloudStudio API)
  wrangler.toml       cron 每分钟;需要 global_fetch_strictly_public
.github/
  scripts/cf-token.py     Global Key → 临时 token + zone/account 自动发现
  scripts/tc-discover.py  腾讯云密钥 → spaceKey 自动发现
  scripts/tc-wtoken.py    腾讯云密钥 → workspace token 铸造(TC3,~10 分钟有效,免 SSH 打开网页 IDE)
  scripts/open-ide.js     用 runner Chrome 打开网页 IDE(有 preview.yml 则触发 autoOpen)
  scripts/ide-exec.js     网页终端执行器:铸 token → 打开 IDE → 终端执行命令读回输出,免 SSH
  scripts/bind-route.sh   DNS A 记录 + workers route 绑定
  workflows/setup.yml     首次引导(校验 → 部署)
  workflows/deploy.yml    主部署(CF Worker + 自定义域路由 + 心跳验证)
  workflows/vps-boot.yml  每小时铸 token 打开网页 IDE + 终端通道自检(免 SSH)
  workflows/ci.yml        push 语法 lint
```

## 已知坑(都踩过)

- **workers.dev 域名被污染**:VPS 和本地都解析到假 IP,必须自定义域 + workers route
- **无特权容器**:跑不了 docker,服务全部裸跑二进制
- **满载会被回收**:32 核满载基准测试两次触发容器重建,负载控制在 8 核内
- **只有 `/workspace` 跨重建幸存**:其余目录(/opt /etc /usr/local/bin)重建即清
- **SpaceKey ≠ Name**:`DescribeWorkspaces` 返回的 `Name` 是显示名,拿去调其他 API 会报 Workspace Not Found,必须用 `SpaceKey`
- **DescribeWorkspaces 不收分页参数**:payload 必须是 `{}`,传了分页参数报错
- **DescribeWorkspaces 会列出已回收工作区**:`Status == "INVALID"` 的是已删除的幽灵工作区,拿去 RunWorkspace/heartbeat 报 `Workspace had been removed`,发现脚本已自动过滤
- **腾讯云 API 偶发限流**:错误伪装成 `AuthFailure.SignatureFailure`,过几分钟自愈,别去改签名代码
- **CF 临时 token 用完即删**:部署用 Global Key 铸最小权限临时 token,部署完 DELETE,只留 Global Key 长期凭证
- **deploy.yml 心跳 curl 在 DNS 未就绪时 exit 6 会杀脚本**:workflow 默认 `bash -e`,bind-route 刚建完 proxied DNS 记录有传播延迟,`code=$(curl ...)` 一旦 curl 因 DNS 未解析(exit 6)失败就触发 `set -e` 直接退出,12 次重试循环根本没跑(日志无 try 输出)。兜底:curl 尾部加 `2>/dev/null) || code=000`,让重试循环真正生效
- **setup.yml permissions 坑**:写了 `permissions:` 块未列出的权限归零,checkout 私有仓库要 `contents: read` 一起写
