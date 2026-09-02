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

## 一键部署

1. **Fork / 推送本仓库到你的 GitHub(Private)**
2. **仓库 Settings → Secrets and variables → Actions → Secrets** 添加:

   | Secret | 必须 | 说明 |
   |---|---|---|
   | `CF_API_TOKEN` | ✅ | Cloudflare API Token(编辑 Workers 权限) |
   | `CF_ZONE_ID` | ✅ | 自定义域所在 zone 的 ID(workers.dev 在国内被污染,必须自定义域) |
   | `CF_ACCOUNT_ID` | ✅ | Cloudflare Account ID |
   | `KEEPALIVE_DOMAIN` | ✅ | worker 入口域名,如 `keepalive.example.com`(zone 内需可建 DNS) |
   | `FOREVER_TOKEN` | ✅ | 工作区长效 token(网页 IDE 打开后,从请求里抓 `CreateWorkspaceToken` 的 Token,几十年有效) |
   | `SSH_HOST` | ✅ | CloudStudio SSH 网关,如 `udfxel.88q5n6vn.ssh.cloudstudio.work` |
   | `SSH_USER` | ✅ | 网关用户名,形如 `<hash>-<spaceKey>` |
   | `SSH_PASSWORD` | ⬜ | 工作区 root 密码(没配密钥时用) |

   **Variables**(同页面 → Variables 选项卡):

   | Variable | 默认 | 说明 |
   |---|---|---|
   | `SPACE_KEYS` | `udfxel` | 逗号分隔的多个工作区 key |

3. **Actions → Keepalive Deploy → Run workflow**(或 `setup.yml` 会引导)
4. 完成后验证:
   - `https://<KEEPALIVE_DOMAIN>/heart/<spaceKey>` 返回正常心跳
   - VPS:`pgrep -x new-api` 等进程在跑

## 二次部署 / 改配置

改 `worker/start.js` 或 `vps/*.sh` 后直接 push,`deploy.yml` 自动重新部署两部分。

## 目录结构

```
worker/
  start.js          Worker 源码(心跳/重启/打开 IDE)
  wrangler.toml     cron 每分钟;需要 global_fetch_strictly_public
vps/
  preview.yml       写到 /workspace/<项目>/.vscode/preview.yml(autoOpen 启动链)
  restore-assets.sh 容器重建后重建软链(唯一入口,幂等)
  boot-all.sh       应用自启(示例:new-api)
  tunnel-start.sh   cloudflared tunnel
  probe-start.sh    监控探针(可选)
  selfheal*.sh      守护 + SQLite 热备
.github/workflows/
  setup.yml         首次引导:检查 Secrets → 触发部署
  deploy.yml        一键部署 CF Worker + VPS 启动链
```

## 已知坑(都踩过)

- **workers.dev 域名被污染**:VPS 和本地都解析到假 IP,必须自定义域 + workers route
- **无特权容器**:跑不了 docker,服务全部裸跑二进制
- **满载会被回收**:32 核满载基准测试两次触发容器重建,负载控制在 8 核内
- **只有 `/workspace` 跨重建幸存**:其余目录(/opt /etc /usr/local/bin)重建即清,靠 restore-assets.sh 重建软链
- SSH 网关是 keyboard-interactive,**不能加 BatchMode**;scp 要加 `-O`
