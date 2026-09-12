# 部署指南

本项目提供腾讯 CloudStudio 工作区 24 小时保活服务，通过 Cloudflare Worker 定时心跳 + GitHub Actions 自动唤醒实现。

## 架构概览

```
Cloudflare Worker (每分钟心跳)
    ↓
腾讯云 API (获取 token + 发送心跳)
    ↓
CloudStudio 工作区保活
    ↓
如果检测到关机 → 触发 GitHub Actions
    ↓
browserless 访问 preview 页面 → 自动唤醒
```

---

## 前置准备

### 1. 腾讯云 API 密钥

访问：https://console.cloud.tencent.com/cam/capi

创建或获取：
- **SecretId**（格式：`AKID****************************`）
- **SecretKey**（32 位字符串）

### 2. Cloudflare 账号

需要准备：
- Cloudflare 账号邮箱
- Global API Key（访问 https://dash.cloudflare.com/profile/api-tokens 获取）
- 一个已托管在 Cloudflare 的域名

### 3. CloudStudio 工作空间 ID

访问 https://cloudstudio.net/dashboard，进入你的工作空间，从 URL 中获取 `spaceKey`：

```
https://cloudstudio.net/dashboard/workspace/<spaceKey>
```

---

## 部署步骤

### 第一步：Fork 本仓库

点击右上角 **Fork** 按钮，将仓库 fork 到你的 GitHub 账号。

### 第二步：配置 GitHub Secrets

访问：`https://github.com/<你的用户名>/cloudstudio-keepalive/settings/secrets/actions`

点击 `New repository secret`，依次添加以下 5 个 Secrets：

| Name | Value | 说明 |
|------|-------|------|
| `TENCENT_SECRET_ID` | `AKID****************************` | 腾讯云 API SecretId |
| `TENCENT_SECRET_KEY` | `********************************` | 腾讯云 API SecretKey |
| `CF_AUTH_EMAIL` | `your-email@example.com` | Cloudflare 账号邮箱 |
| `CF_GLOBAL_API_KEY` | `cfk_************************************************` | Cloudflare Global API Key |
| `KEEPALIVE_DOMAIN` | `your-subdomain.your-domain.com` | 绑定的自定义域名 |

### 第三步：配置 GitHub Variables

访问：`https://github.com/<你的用户名>/cloudstudio-keepalive/settings/variables/actions`

点击 `New repository variable`，添加：

| Name | Value | 说明 |
|------|-------|------|
| `SPACE_KEYS` | `your-workspace-id` | CloudStudio 工作空间 ID（多个用逗号分隔） |

**多工作空间示例：**
```
workspace1,workspace2,workspace3
```

### 第四步：配置 Cloudflare DNS

1. 登录 Cloudflare：https://dash.cloudflare.com/
2. 选择你的域名
3. 进入 **DNS** → **Records**
4. 点击 **Add record**

配置如下：

| 字段 | 值 |
|------|-----|
| Type | A |
| Name | your-subdomain |
| IPv4 address | 192.0.2.1 |
| Proxy status | **Proxied（橙色云朵）✅** |
| TTL | Auto |

**⚠️ 重要：** 必须开启 **Proxied**（代理状态），否则 Worker 无法绑定路由！

### 第五步：触发部署

访问：`https://github.com/<你的用户名>/cloudstudio-keepalive/actions/workflows/deploy.yml`

1. 点击右侧 **Run workflow** 按钮
2. 确认分支为 `main`
3. 点击绿色的 **Run workflow** 按钮

等待 2-3 分钟，部署完成。

---

## 验证部署

### 查看工作空间状态

访问：
```
https://your-subdomain.your-domain.com/status
```

应该返回：
```json
{
  "workspaces": [
    {
      "spaceKey": "your-workspace-id",
      "name": "你的工作空间名称",
      "status": "RUNNING"
    }
  ]
}
```

### 直接访问 IDE

访问：
```
https://your-subdomain.your-domain.com/
```

会自动跳转到 CloudStudio IDE，并保持登录态。

---

## 工作原理

### 1. 定时心跳（Cloudflare Worker）

Worker 每分钟执行：

```javascript
// 1. 调用腾讯云 API 获取工作空间 token
const token = await getWorkspaceToken(spaceKey);

// 2. 发送心跳到 CloudStudio
await fetch('https://cloudstudio.net/api/heartbeat', {
  headers: { 'Authorization': `Bearer ${token}` }
});

// 3. 检测工作空间状态
if (status === 'STOPPED') {
  // 触发 GitHub Actions preview.yml
  await fetch('https://api.github.com/repos/.../actions/workflows/preview.yml/dispatches');
}
```

### 2. 自动唤醒（GitHub Actions）

当 Worker 检测到工作空间关机时，触发 `preview.yml`：

```yaml
- name: 访问 preview 页面唤醒工作空间
  run: |
    docker run --rm browserless/chrome \
      chromium-browser --headless \
      "https://cloudstudio.net/preview/${SPACE_KEY}"
```

访问 preview 页面会触发 CloudStudio 自动唤醒机制。

---

## 故障排查

### DNS 无法解析

**现象：** 访问域名提示无法解析

**解决方案：**
1. 确认 DNS A 记录已添加且开启代理（橙色云朵）
2. 等待 1-2 分钟让 DNS 传播
3. 使用 `nslookup your-subdomain.your-domain.com` 验证

### 部署失败

**现象：** Actions 日志报错

**常见错误：**

| 错误信息 | 解决方案 |
|---------|---------|
| `SPACE_KEYS not found` | 检查 Variables 是否正确配置 |
| `Invalid credentials` | 检查腾讯云 SecretId/Key 是否正确 |
| `Zone not found` | 检查 Cloudflare API Key 和 Email 是否匹配 |
| `Route not created` | 确认 DNS 记录已开启 Proxy（橙色云朵） |

### 心跳失败

**现象：** `/status` 返回但工作空间未保活

**解决方案：**
1. 确认 `SPACE_KEYS` 是你账号下的工作空间
2. 确认腾讯云密钥有 CloudStudio 权限
3. 访问腾讯云控制台确认工作空间状态
4. 查看 Worker 日志（Cloudflare Dashboard → Workers → Logs）

### Worker 未触发

**现象：** 部署成功但无心跳记录

**解决方案：**
1. 访问 Cloudflare Dashboard → Workers → 你的 Worker
2. 检查 **Triggers** 是否配置了 Cron Trigger（`* * * * *`）
3. 手动触发测试：点击 **Quick Edit** → **Send** 测试请求

---

## 配置清单

部署前请确认：

- [ ] 已获取腾讯云 SecretId 和 SecretKey
- [ ] 已获取 Cloudflare Global API Key
- [ ] 已获取 CloudStudio 工作空间 ID
- [ ] 已 Fork 本仓库
- [ ] 已添加 5 个 GitHub Secrets
- [ ] 已添加 1 个 GitHub Variable
- [ ] 已配置 Cloudflare DNS A 记录
- [ ] 已开启 Cloudflare 代理（橙色云朵）
- [ ] 已触发 GitHub Actions 部署
- [ ] 已验证 `/status` 端点
- [ ] 已验证 IDE 访问

---

## 高级配置

### 自定义心跳间隔

编辑 `wrangler.toml`：

```toml
[triggers]
crons = ["*/5 * * * *"]  # 改为每 5 分钟
```

然后重新部署。

### 多工作空间支持

在 `SPACE_KEYS` 中用逗号分隔多个工作空间 ID：

```
workspace1,workspace2,workspace3
```

Worker 会依次为每个工作空间发送心跳。

### 监控告警

Worker 支持导出 Prometheus 指标，访问：

```
https://your-subdomain.your-domain.com/metrics
```

可以接入 Grafana / Prometheus 进行监控。

---

## 许可证

MIT License

---

## 常见问题

**Q: 心跳会消耗流量吗？**

A: 心跳请求极小（<1KB），Cloudflare Worker 每天免费 100,000 次请求，完全够用。

**Q: 工作空间会自动关机吗？**

A: 配置完成后，Worker 会每分钟发送心跳，工作空间会一直保持 RUNNING 状态。即使意外关机，Actions 会自动唤醒。

**Q: 需要一直开着浏览器吗？**

A: 不需要。所有操作都在云端自动执行（Worker + Actions），本地无需任何进程。

**Q: 支持多个工作空间吗？**

A: 支持。在 `SPACE_KEYS` 中用逗号分隔多个 ID 即可。

**Q: 腾讯云会收费吗？**

A: CloudStudio 免费版工作空间完全免费，API 调用在免费额度内。

---

## 技术支持

遇到问题？欢迎提交 Issue：

https://github.com/Shitsuki4/cloudstudio-keepalive/issues
