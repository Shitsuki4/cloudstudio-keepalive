# CloudStudio Keepalive

> 腾讯 CloudStudio 工作区 24 小时保活系统 — Cloudflare Worker 定时心跳 + GitHub Actions 自动化部署

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
[![Deploy](https://github.com/Shitsuki4/cloudstudio-keepalive/actions/workflows/deploy.yml/badge.svg)](https://github.com/Shitsuki4/cloudstudio-keepalive/actions/workflows/deploy.yml)

---

## 📖 项目简介

本项目通过 **Cloudflare Worker** 每分钟心跳和 **GitHub Actions** 自动化部署，实现腾讯 CloudStudio 工作区的 24 小时保活。

**核心特性：**

- 🔄 **全自动保活** — Worker 每分钟发送心跳，保持工作区 RUNNING 状态
- 🚀 **原生启动框架** — 基于 Supervisor include 的启动系统，工作区重启后自动执行自定义脚本
- 🌐 **自定义域名访问** — 通过 Cloudflare Worker 代理，支持自定义域名直接访问 IDE
- 📊 **状态监控 API** — 提供 `/status` 和 `/metrics` 端点，实时查看工作区状态
- 🔧 **多工作区支持** — 一套配置管理多个 CloudStudio 工作区
- ⚡ **零成本运行** — 基于 Cloudflare Worker 免费额度（每天 100,000 次请求）

---

## 🏗️ 架构设计

```
┌─────────────────────────────────────────────────────────────────┐
│                    Cloudflare Worker (Cron)                     │
│              每分钟触发 → 腾讯云 API → 发送心跳                  │
└────────────────────────────┬────────────────────────────────────┘
                             ↓
┌─────────────────────────────────────────────────────────────────┐
│                  CloudStudio 工作区保活检测                      │
│         状态正常 → 继续心跳 | 检测关机 → 触发唤醒               │
└────────────────────────────┬────────────────────────────────────┘
                             ↓
┌─────────────────────────────────────────────────────────────────┐
│              GitHub Actions (IDE Boot Workflow)                 │
│   通过 Puppeteer 打开网页 IDE → 触发 CloudStudio 自动唤醒       │
└─────────────────────────────────────────────────────────────────┘
```

### 工作流程

1. **心跳保活**：Cloudflare Worker 每分钟调用腾讯云 API，为工作区发送心跳
2. **状态监测**：Worker 实时检测工作区状态（RUNNING / STOPPED）
3. **自动唤醒**：检测到关机时触发 GitHub Actions，通过浏览器自动化打开 IDE
4. **原生启动**：工作区重启后，Supervisor 自动执行 `/workspace/.keepalive/start.d/*.sh` 中的启动脚本

---

## 🚀 快速开始

### 方式一：一键部署（推荐）

1. **Fork 本仓库**
2. **配置密钥**（见下方配置清单）
3. **运行部署 Workflow**：
   ```
   Actions → Deploy → Run workflow
   ```
4. **验证部署**：
   ```bash
   curl https://your-subdomain.your-domain.com/status
   ```

### 方式二：分步部署

详细步骤请查看 **[完整部署指南](./DEPLOYMENT_GUIDE.md)**

---

## 🔑 配置清单

### GitHub Secrets（必需）

访问：`Settings → Secrets and variables → Actions → Secrets`

| Name | 说明 | 示例 |
|------|------|------|
| `TENCENT_SECRET_ID` | 腾讯云 API SecretId | `AKID****************************` |
| `TENCENT_SECRET_KEY` | 腾讯云 API SecretKey | `********************************` |
| `CF_AUTH_EMAIL` | Cloudflare 账号邮箱 | `your-email@example.com` |
| `CF_GLOBAL_API_KEY` | Cloudflare Global API Key | `cfk_************************` |
| `KEEPALIVE_DOMAIN` | 自定义域名（不含协议） | `your-subdomain.your-domain.com` |

### GitHub Variables（必需）

访问：`Settings → Secrets and variables → Actions → Variables`

| Name | 说明 | 示例 |
|------|------|------|
| `SPACE_KEYS` | CloudStudio 工作空间 ID（多个用逗号分隔） | `workspace1,workspace2` |

### Cloudflare DNS 配置（必需）

| 字段 | 值 |
|------|-----|
| Type | A |
| Name | your-subdomain |
| IPv4 address | `192.0.2.1` |
| Proxy status | **✅ Proxied（橙色云朵）** |

---

## 📡 API 端点

### 1. 查看工作区状态

```bash
GET https://your-subdomain.your-domain.com/status
```

**响应示例：**
```json
{
  "workspaces": [
    {
      "spaceKey": "your-workspace-id",
      "name": "MyWorkspace",
      "status": "RUNNING",
      "region": "ap-shanghai",
      "lastHeartbeat": "2026-09-12T10:30:00Z"
    }
  ],
  "timestamp": "2026-09-12T10:30:15Z"
}
```

### 2. Prometheus 监控指标

```bash
GET https://your-subdomain.your-domain.com/metrics
```

**响应示例：**
```
# HELP cloudstudio_workspace_status 工作区状态 (1=RUNNING, 0=STOPPED)
# TYPE cloudstudio_workspace_status gauge
cloudstudio_workspace_status{space_key="workspace1",name="MyWorkspace"} 1

# HELP cloudstudio_heartbeat_total 心跳发送总数
# TYPE cloudstudio_heartbeat_total counter
cloudstudio_heartbeat_total 1440
```

### 3. 直接访问 IDE

```bash
GET https://your-subdomain.your-domain.com/
```

自动跳转到 CloudStudio IDE 并保持登录态。

---

## 🔧 原生启动框架

### 启动脚本位置

```
/workspace/.keepalive/
├── boot.sh                    # 主启动脚本（由 Supervisor 调用）
├── keepalive-boot.conf        # Supervisor include 配置
├── start.d/                   # 用户自定义启动脚本目录
│   ├── 01-example.sh          # 按文件名顺序执行
│   └── 02-another.sh
└── logs/
    └── boot.log               # 启动日志
```

### 添加启动脚本示例

```bash
# 创建启动脚本
cat > /workspace/.keepalive/start.d/01-myservice.sh <<'EOF'
#!/bin/bash
set -e

# 启动自定义服务
cd /workspace/myservice
nohup ./myservice >> /workspace/.keepalive/logs/myservice.log 2>&1 &
echo "MyService started with PID $!"
EOF

chmod +x /workspace/.keepalive/start.d/01-myservice.sh
```

工作区下次重启时会自动执行该脚本。

**查看启动日志：**
```bash
tail -f /workspace/.keepalive/logs/boot.log
```

---

## 🛠️ 故障排查

### 问题 1：域名无法访问

**检查步骤：**
```bash
# 1. 验证 DNS 解析
nslookup your-subdomain.your-domain.com

# 2. 检查 Cloudflare 代理状态
# 访问 Cloudflare Dashboard → DNS → 确认橙色云朵已开启

# 3. 测试 Worker 响应
curl -I https://your-subdomain.your-domain.com/status
```

### 问题 2：工作区未保活

**检查步骤：**
```bash
# 1. 查看工作区状态
curl https://your-subdomain.your-domain.com/status | jq .

# 2. 检查 Worker 日志
# Cloudflare Dashboard → Workers → Logs

# 3. 验证腾讯云密钥权限
# 访问腾讯云控制台 → 访问管理 → API 密钥 → 检查权限

# 4. 手动触发 IDE Boot
# Actions → Keepalive IDE Boot → Run workflow
```

### 问题 3：部署失败

| 错误信息 | 解决方案 |
|---------|---------|
| `SPACE_KEYS not found` | 检查 GitHub Variables 配置 |
| `Invalid credentials` | 检查腾讯云 SecretId/Key 是否正确 |
| `Zone not found` | 检查 Cloudflare API Key 和 Email 是否匹配 |
| `Route not created` | 确认 DNS 记录已开启 Proxy（橙色云朵） |

更多排查步骤请查看：**[部署指南 - 故障排查](./DEPLOYMENT_GUIDE.md#故障排查)**

---

## 📚 文档索引

- **[完整部署指南](./DEPLOYMENT_GUIDE.md)** — 从零开始的详细部署步骤
- **[CloudStudio 通用服务部署](./CLOUDSTUDIO_DEPLOYMENT.md)** — 在 CloudStudio 中部署任意服务的最佳实践
- **[GitHub Secrets 配置](./setup-secrets.md)** — Secrets/Variables 配置详解

---

## 🔒 安全建议

1. **定期轮换密钥** — 每 90 天更换腾讯云 API 密钥和 Cloudflare API Key
2. **最小权限原则** — 腾讯云 SecretId 仅授予 CloudStudio 相关权限
3. **敏感信息隔离** — 启动脚本中的凭据应存储在 `/workspace/.keepalive/.env`（添加到 `.gitignore`）
4. **监控异常访问** — 定期检查 Cloudflare Worker 日志，发现异常流量及时封禁

---

## 📈 高级用法

### 多工作区管理

在 `SPACE_KEYS` 中用逗号分隔多个工作空间 ID：

```
workspace1,workspace2,workspace3
```

Worker 会依次为每个工作区发送心跳。

### 自定义心跳间隔

编辑 `worker/wrangler.toml`：

```toml
[triggers]
crons = ["*/5 * * * *"]  # 改为每 5 分钟
```

然后重新部署：
```bash
git add worker/wrangler.toml
git commit -m "chore: 调整心跳间隔为 5 分钟"
git push
```

触发 `Deploy` Workflow 完成更新。

### 接入监控系统

将 `/metrics` 端点接入 Prometheus：

```yaml
scrape_configs:
  - job_name: 'cloudstudio-keepalive'
    static_configs:
      - targets: ['your-subdomain.your-domain.com']
    metrics_path: '/metrics'
    scheme: 'https'
```

---

## 🤝 贡献指南

欢迎提交 Issue 和 Pull Request！

**贡献前请：**
1. Fork 本仓库
2. 创建功能分支：`git checkout -b feature/your-feature`
3. 提交代码：`git commit -m "feat: 添加新功能"`
4. 推送分支：`git push origin feature/your-feature`
5. 提交 Pull Request

---

## 📄 许可证

本项目采用 [MIT License](LICENSE) 开源协议。

---

## 💬 常见问题

<details>
<summary><strong>Q: 心跳会消耗多少流量？</strong></summary>

A: 心跳请求极小（<1KB），Cloudflare Worker 每天免费 100,000 次请求，按每分钟一次计算，一天仅 1,440 次请求，完全在免费额度内。
</details>

<details>
<summary><strong>Q: 工作区会自动关机吗？</strong></summary>

A: 配置完成后，Worker 每分钟发送心跳，工作区会一直保持 RUNNING 状态。即使意外关机（如凌晨维护窗口），Actions 会自动唤醒，启动脚本会自动执行。
</details>

<details>
<summary><strong>Q: 需要一直开着浏览器吗？</strong></summary>

A: 不需要。所有操作都在云端自动执行（Worker + Actions），本地无需任何进程。
</details>

<details>
<summary><strong>Q: 支持多个工作空间吗？</strong></summary>

A: 支持。在 `SPACE_KEYS` 中用逗号分隔多个 ID 即可，单个 Worker 可管理多个工作区。
</details>

<details>
<summary><strong>Q: 腾讯云会收费吗？</strong></summary>

A: CloudStudio 免费版工作空间完全免费，API 调用在免费额度内，无额外费用。
</details>

<details>
<summary><strong>Q: 启动脚本什么时候执行？</strong></summary>

A: 工作区每次重启（冷启动、维护窗口、手动重启）后，Supervisor 会自动调用 `/workspace/.keepalive/boot.sh`，按文件名顺序执行 `start.d/*.sh` 中的所有脚本。
</details>

<details>
<summary><strong>Q: 如何在工作区中部署 new-api、Cloudflare Tunnel 等服务？</strong></summary>

A: 请参考 **[CloudStudio 通用服务部署指南](./CLOUDSTUDIO_DEPLOYMENT.md)**，其中详细介绍了如何使用启动框架部署任意服务，并确保重启后自动恢复。
</details>

---

## 🔗 相关链接

- [腾讯云 CloudStudio](https://cloudstudio.net/)
- [Cloudflare Workers](https://workers.cloudflare.com/)
- [GitHub Actions 文档](https://docs.github.com/actions)
- [Supervisor 配置文档](http://supervisord.org/configuration.html)

---

## ⭐ Star History

如果本项目对你有帮助，请点个 Star ⭐

[![Star History Chart](https://api.star-history.com/svg?repos=Shitsuki4/cloudstudio-keepalive&type=Date)](https://star-history.com/#Shitsuki4/cloudstudio-keepalive&Date)

---

<p align="center">
  Made with ❤️ by <a href="https://github.com/Shitsuki4">Shitsuki4</a>
</p>
