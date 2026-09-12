# CloudStudio 部署指南：new-api + Cloudflare Tunnel

## 概述

本文档记录在腾讯云 CloudStudio 环境中部署 new-api 和 Cloudflare Tunnel 的完整流程，包括自启动配置和防重启清理的关键步骤。

---

## 环境信息

- **平台**: 腾讯云 CloudStudio
- **SSH**: `<workspace-id>-<username>@<username>.<region>.ssh.cloudstudio.work`
- **配置**: Intel Xeon 系列 / 60GB+ 内存 / 无 GPU
- **Shell**: zsh
- **系统**: Linux 容器环境

---

## 一、关键注意事项

### 1.1 CloudStudio 重启行为

⚠️ **CloudStudio 容器重启后会清理以下内容**：
- 运行中的进程（所有后台服务）
- 临时文件（/tmp 目录）
- 未持久化的配置

✅ **不会被清理的内容**：
- `/workspace` 目录下的所有文件
- 用户主目录 `~` 的配置文件（如 `.zshrc`）
- 安装在用户空间的二进制文件

### 1.2 自启动策略

CloudStudio **没有 systemd**，必须使用以下方法实现自启动：
1. **Shell 配置文件**：`~/.zshrc` 或 `~/.bashrc`
2. **启动脚本**：在 `/workspace` 创建启动脚本并在 Shell 配置中调用
3. **进程管理器**：使用 `pm2` / `supervisor` 等工具

---

## 二、通用服务部署流程

### 2.1 准备阶段

#### 下载和安装二进制文件

```bash
# 进入工作目录
cd /workspace

# 下载服务二进制文件（示例）
wget <github-release-url> -O <service-name>
chmod +x <service-name>

# 或者使用 curl
curl -L <download-url> -o <service-name>
chmod +x <service-name>

# 验证
./<service-name> --version
```

#### 创建持久化目录

⚠️ **所有数据必须放在 `/workspace` 下才能在重启后保留**

```bash
# 创建服务专用目录
mkdir -p /workspace/<service-name>-data
mkdir -p /workspace/<service-name>-logs
mkdir -p /workspace/<service-name>-config
```

### 2.2 配置文件

**原则**：
- 所有配置文件放在 `/workspace/<service-name>-config/`
- 所有数据文件（数据库、缓存）放在 `/workspace/<service-name>-data/`
- 所有日志文件放在 `/workspace/<service-name>-logs/`

**示例配置文件** `/workspace/<service-name>-config/config.yaml`：

```yaml
# 根据实际服务调整
port: 3000
data_dir: /workspace/<service-name>-data
log_file: /workspace/<service-name>-logs/app.log
```

⚠️ **凭据文件处理**：如果服务需要认证文件（如 OAuth token、API key、证书），必须将其从默认位置复制到 `/workspace` 下：

```bash
# 例如：Cloudflare Tunnel 凭据
cp ~/.cloudflared/<tunnel-id>.json /workspace/<service-name>-config/

# 例如：SSH 密钥
cp ~/.ssh/id_rsa /workspace/<service-name>-config/
chmod 600 /workspace/<service-name>-config/id_rsa
```

### 2.3 创建启动脚本

创建 `/workspace/start-<service-name>.sh`：

```bash
#!/bin/bash

SERVICE_NAME="<service-name>"
SERVICE_BIN="/workspace/${SERVICE_NAME}"
CONFIG_FILE="/workspace/${SERVICE_NAME}-config/config.yaml"
LOG_FILE="/workspace/${SERVICE_NAME}-logs/service.log"
PID_FILE="/workspace/${SERVICE_NAME}-data/service.pid"

# 检查是否已经在运行
if [ -f "$PID_FILE" ]; then
  OLD_PID=$(cat "$PID_FILE")
  if ps -p "$OLD_PID" > /dev/null 2>&1; then
    echo "${SERVICE_NAME} 已在运行 (PID: $OLD_PID)"
    exit 0
  else
    # 清理过期的 PID 文件
    rm -f "$PID_FILE"
  fi
fi

# 启动服务
cd /workspace
nohup "$SERVICE_BIN" --config "$CONFIG_FILE" \
  >> "$LOG_FILE" 2>&1 &

# 保存 PID
echo $! > "$PID_FILE"
echo "${SERVICE_NAME} 已启动 (PID: $(cat $PID_FILE))"

# 可选：等待几秒后检查进程是否还在运行
sleep 2
if ps -p $(cat $PID_FILE) > /dev/null 2>&1; then
  echo "✅ ${SERVICE_NAME} 启动成功"
else
  echo "❌ ${SERVICE_NAME} 启动失败，请检查日志: $LOG_FILE"
  exit 1
fi
```

```bash
chmod +x /workspace/start-<service-name>.sh
```

### 2.4 测试启动脚本

```bash
# 手动运行测试
/workspace/start-<service-name>.sh

# 检查进程
ps aux | grep <service-name>

# 查看日志
tail -f /workspace/<service-name>-logs/service.log

# 停止服务（测试用）
kill $(cat /workspace/<service-name>-data/service.pid)
```

---

## 三、配置自启动

### 4.1 修改 ~/.zshrc

在 `~/.zshrc` 末尾添加：

```bash
# ======== 自启动服务 ========

# 启动 new-api
if [ -f /workspace/start-newapi.sh ]; then
  /workspace/start-newapi.sh
fi

# 启动 Cloudflare Tunnel
if [ -f /workspace/start-tunnel.sh ]; then
  /workspace/start-tunnel.sh
fi

echo "✅ 所有服务已启动"
```

### 4.2 使配置生效

```bash
source ~/.zshrc
```

### 4.3 验证自启动

**模拟重启**：
1. 关闭所有服务进程
2. 重新打开 SSH 连接
3. 检查服务是否自动启动：

```bash
ps aux | grep new-api
ps aux | grep cloudflared
```

---

## 四、使用 PM2 管理（推荐）

### 5.1 安装 PM2

```bash
# 安装 Node.js（如果没有）
curl -fsSL https://deb.nodesource.com/setup_lts.x | bash -
apt-get install -y nodejs

# 安装 PM2
npm install -g pm2
```

### 5.2 配置 PM2

创建 `/workspace/ecosystem.config.js`：

```javascript
module.exports = {
  apps: [
    {
      name: 'new-api',
      script: '/workspace/new-api',
      args: '--config /workspace/new-api-data/config.yaml',
      cwd: '/workspace',
      out_file: '/workspace/new-api-data/pm2-out.log',
      error_file: '/workspace/new-api-data/pm2-error.log',
      autorestart: true,
      max_restarts: 10,
      restart_delay: 5000
    },
    {
      name: 'cloudflared',
      script: '/workspace/cloudflared',
      args: 'tunnel --config /workspace/.cloudflared/config.yml run',
      cwd: '/workspace',
      out_file: '/workspace/.cloudflared/pm2-out.log',
      error_file: '/workspace/.cloudflared/pm2-error.log',
      autorestart: true,
      max_restarts: 10,
      restart_delay: 5000
    }
  ]
};
```

### 5.3 启动和保存

```bash
# 启动所有服务
pm2 start /workspace/ecosystem.config.js

# 保存 PM2 进程列表
pm2 save

# 生成启动脚本
pm2 startup
# 复制输出的命令并执行
```

### 5.4 在 ~/.zshrc 中添加

```bash
# PM2 自启动
if command -v pm2 &> /dev/null; then
  pm2 resurrect > /dev/null 2>&1
fi
```

---

## 五、常用管理命令

### 6.1 查看服务状态

```bash
# 查看进程
ps aux | grep new-api
ps aux | grep cloudflared

# PM2 方式
pm2 status
pm2 logs
pm2 monit
```

### 6.2 重启服务

```bash
# 手动方式
pkill new-api
pkill cloudflared
/workspace/start-newapi.sh
/workspace/start-tunnel.sh

# PM2 方式
pm2 restart all
pm2 restart new-api
pm2 restart cloudflared
```

### 6.3 查看日志

```bash
# 手动方式
tail -f /workspace/new-api-data/newapi.log
tail -f /workspace/.cloudflared/tunnel.log

# PM2 方式
pm2 logs
pm2 logs new-api
pm2 logs cloudflared --lines 100
```

### 6.4 停止服务

```bash
# 手动方式
kill $(cat /workspace/new-api-data/newapi.pid)
kill $(cat /workspace/.cloudflared/tunnel.pid)

# PM2 方式
pm2 stop all
pm2 delete all
```

---

## 六、故障排查

### 7.1 服务未自启动

**检查清单**：
1. `.zshrc` 是否正确配置
2. 启动脚本是否有执行权限（`chmod +x`）
3. 进程是否已在运行（避免重复启动）
4. 查看日志文件中的错误信息

```bash
# 手动运行启动脚本测试
bash -x /workspace/start-newapi.sh
bash -x /workspace/start-tunnel.sh
```

### 7.2 Tunnel 连接失败

**常见原因**：
1. 凭据文件路径错误
2. Tunnel ID 配置错误
3. 网络问题

```bash
# 测试 Tunnel 连接
./cloudflared tunnel info my-tunnel
./cloudflared tunnel list

# 手动运行（前台调试）
./cloudflared tunnel --config /workspace/.cloudflared/config.yml run
```

### 7.3 端口冲突

```bash
# 检查端口占用
netstat -tunlp | grep 3000
lsof -i:3000

# 修改 new-api 配置中的端口
```

---

## 七、安全建议

1. **不要将凭据提交到 Git**：
   ```bash
   echo ".cloudflared/*.json" >> .gitignore
   echo "new-api-data/*.db" >> .gitignore
   ```

2. **定期备份数据库**：
   ```bash
   cp /workspace/new-api-data/data.db /workspace/new-api-data/data.db.backup.$(date +%Y%m%d)
   ```

3. **限制 API 访问**：
   - 使用 Cloudflare Access 添加身份验证
   - 配置 rate limiting
   - 启用 API Key 验证

4. **监控日志**：
   ```bash
   # 定期检查异常访问
   grep "ERROR" /workspace/new-api-data/newapi.log
   ```

---

## 八、快速参考

### 目录结构

```
/workspace/
├── <service-name>                   # 服务二进制文件
├── start-<service-name>.sh          # 启动脚本
├── ecosystem.config.js              # PM2 配置（可选）
├── <service-name>-config/
│   ├── config.yaml                  # 服务配置
│   └── credentials.json             # 凭据文件（如需要）
├── <service-name>-data/
│   ├── data.db                      # 数据库文件
│   └── service.pid                  # 进程 ID
└── <service-name>-logs/
    └── service.log                  # 运行日志
```

### 一键部署模板脚本

创建 `/workspace/deploy-service.sh`：

```bash
#!/bin/bash
set -e

SERVICE_NAME="myservice"
DOWNLOAD_URL="https://github.com/user/repo/releases/latest/download/binary"

echo "🚀 开始部署 ${SERVICE_NAME}..."

# 下载服务
echo "📦 下载 ${SERVICE_NAME}..."
wget -q "$DOWNLOAD_URL" -O "/workspace/${SERVICE_NAME}"
chmod +x "/workspace/${SERVICE_NAME}"

# 创建目录
echo "📁 创建目录..."
mkdir -p "/workspace/${SERVICE_NAME}-config"
mkdir -p "/workspace/${SERVICE_NAME}-data"
mkdir -p "/workspace/${SERVICE_NAME}-logs"

# 创建启动脚本
echo "📝 创建启动脚本..."
cat > "/workspace/start-${SERVICE_NAME}.sh" << EOF
#!/bin/bash
SERVICE_NAME="${SERVICE_NAME}"
SERVICE_BIN="/workspace/\${SERVICE_NAME}"
CONFIG_FILE="/workspace/\${SERVICE_NAME}-config/config.yaml"
LOG_FILE="/workspace/\${SERVICE_NAME}-logs/service.log"
PID_FILE="/workspace/\${SERVICE_NAME}-data/service.pid"

if [ -f "\$PID_FILE" ]; then
  OLD_PID=\$(cat "\$PID_FILE")
  if ps -p "\$OLD_PID" > /dev/null 2>&1; then
    echo "\${SERVICE_NAME} 已在运行 (PID: \$OLD_PID)"
    exit 0
  else
    rm -f "\$PID_FILE"
  fi
fi

cd /workspace
nohup "\$SERVICE_BIN" --config "\$CONFIG_FILE" >> "\$LOG_FILE" 2>&1 &
echo \$! > "\$PID_FILE"
echo "\${SERVICE_NAME} 已启动 (PID: \$(cat \$PID_FILE))"

sleep 2
if ps -p \$(cat \$PID_FILE) > /dev/null 2>&1; then
  echo "✅ \${SERVICE_NAME} 启动成功"
else
  echo "❌ \${SERVICE_NAME} 启动失败，请检查日志: \$LOG_FILE"
  exit 1
fi
EOF

chmod +x "/workspace/start-${SERVICE_NAME}.sh"

echo "✅ 部署完成！"
echo ""
echo "📋 下一步："
echo "1. 配置 /workspace/${SERVICE_NAME}-config/config.yaml"
echo "2. 运行: /workspace/start-${SERVICE_NAME}.sh"
echo "3. 在 ~/.zshrc 中添加自启动配置"
```

```bash
chmod +x /workspace/deploy-service.sh
```

---

## 九、相关资源
- **PM2 文档**: https://pm2.keymetrics.io/docs/usage/quick-start/
- **CloudStudio 文档**: https://cloudstudio.net/docs

---

**最后更新**: 2026-09-12
