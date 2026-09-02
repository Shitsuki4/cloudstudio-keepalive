> **⚠️ 本仓库包含服务器部署脚本,请保持 Private!**

# CloudStudio Keepalive — 腾讯云 CloudStudio 免费工作区 24h 保活

把腾讯 CloudStudio 免费工作区变成永久免费小服务器的完整可复现方案:

```
┌─ Cloudflare Worker(每分钟 cron)────────────────┐
│  ① /heart/<space>   心跳 → 工作区 evict:false 不回收 │
│  ② 每天 04:00(北京) RunWorkspace 重启工作区       │
│  ③ browserless 打开网页 IDE → 触发 preview.yml    │
└────────────────────┬───────────────────────────┘
                     ▼ autoOpen
┌─ CloudStudio 容器(只有 /workspace 跨重建幸存)──────┐
│  restore-assets.sh  重建软链兼容层                  │
│  boot-all.sh        new-api(或任意应用) :3000      │
│  tunnel-start.sh    cloudflared tunnel 公网入口     │
│  probe-start.sh     CF-Server-Monitor 探针(可选)    │
│  selfheal-start.sh  每分钟自愈 + 每小时 SQLite 热备  │
└──────────────────────────────────────────────┘
```

## 前置条件

1. **腾讯云账号**已建好至少一个 CloudStudio 工作区(https://ide.cloud.tencent.com)
2. 该账号的 **API 密钥**(SecretId/SecretKey,访问管理 → API 密钥管理)
3. **Cloudflare 账号**有一个 zone(域名已托管到 CF),workers.dev 在国内被污染,必须用自定义域

## 一键部署

1. **Fork / 推送本仓库到你的 GitHub(Private)**
2. **仓库 Settings → Secrets and variables → Actions → Secrets** 添加(就这 5 个):

   | Secret | 必须 | 说明 |
   |---|---|---|
   | `TENCENT_SECRET_ID` | ✅ | 腾讯云 API 密钥 Id(`AKID...`) |
   | `TENCENT_SECRET_KEY` | ✅ | 腾讯云 API 密钥 Key |
   | `CF_AUTH_EMAIL` | ✅ | Cloudflare 账号邮箱 |
   | `CF_GLOBAL_API_KEY` | ✅ | Cloudflare Global API Key(仅用于部署时铸临时 token,用完即删) |
   | `KEEPALIVE_DOMAIN` | ✅ | worker 入口域名,如 `keepalive.example.com`(zone 内可建 DNS) |

   **可选**(VPS 侧部署,不填则自动跳过):

   | Secret | 说明 |
   |---|---|
   | `BROWSERLESS_KEY` | browserless.io key,凌晨重启后自动打开网页 IDE 触发 preview.yml |
   | `SSH_HOST` | CloudStudio SSH 网关域名,如 `<space>.xxxx.ssh.cloudstudio.work` |
   | `SSH_USER` | 网关用户名,形如 `<hash>-<spaceKey>` |
   | `SSH_PASSWORD` | 工作区 root 密码(没配密钥时用) |
   | `SSH_PRIVATE_KEY` | SSH 私钥(优先于密码) |

3. **Actions → Keepalive Setup → Run workflow**(会校验密钥、自动发现你的工作区 spaceKey,然后触发部署)
4. 完成后验证:`https://<KEEPALIVE_DOMAIN>/heart/<spaceKey>` 返回 `{"evict":false,...}` 即保活生效

## 工作方式

- **spaceKey 不用手填**:部署时用腾讯云密钥调 `DescribeWorkspaces` 自动发现账号下全部工作区
- **Global API Key 不直接部署**:Actions 运行时用它铸一个仅限本 zone 的临时 API token,部署完自动删除;Worker 长期运行只需 SecretId/Key
- **改代码后再部署**:push `worker/**` 或 `vps/**` 自动触发,或手动 Run workflow

## 目录结构

```
worker/
  start.js            Worker 源码(TC3 签名调 CloudStudio API)
  wrangler.toml       cron 每分钟;需要 global_fetch_strictly_public
vps/
  preview.yml         写到 /workspace/<项目>/.vscode/preview.yml(autoOpen 启动链)
  restore-assets.sh   容器重建后重建软链(唯一入口,幂等)
  boot-all.sh         应用自启(示例:new-api)
  tunnel-start.sh     cloudflared tunnel
  probe-start.sh      监控探针(可选)
  selfheal*.sh        守护 + SQLite 热备
.github/
  scripts/cf-token.py     Global Key → 临时 token + zone/account 自动发现
  scripts/tc-discover.py  腾讯云密钥 → spaceKey 自动发现
  scripts/bind-route.sh   DNS A 记录 + workers route 绑定
  workflows/setup.yml     首次引导(校验 → 部署)
  workflows/deploy.yml    主部署(CF Worker + 可选 VPS)
```

## 已知坑(都踩过)

- **workers.dev 域名被污染**:VPS 和本地都解析到假 IP,必须自定义域 + workers route
- **GitHub runner DNS 查不到腾讯 CNAME**:部署时自动走 Google DoH 解析网关 IP 写 /etc/hosts
- **无特权容器**:跑不了 docker,服务全部裸跑二进制
- **满载会被回收**:32 核满载基准测试两次触发容器重建,负载控制在 8 核内
- **只有 `/workspace` 跨重建幸存**:其余目录(/opt /etc /usr/local/bin)重建即清,靠 restore-assets.sh 重建软链
- SSH 网关是 keyboard-interactive,**不能加 BatchMode**;scp 要加 `-O`
