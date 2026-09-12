# CloudStudio 通用服务部署指南

> 在腾讯 CloudStudio 免费工作区中部署长期运行服务的完整指南

---

## 一、关键注意事项

### ⚠️ CloudStudio 重启行为

- **每日维护窗口**：工作区可能在凌晨时段重启（通常 2:00-4:00）
- **重启后状态**：
  - ✅ 持久化目录（`~/workspace`）数据保留
  - ❌ 系统进程全部停止
  - ❌ 环境变量重置
  - ❌ 临时文件清空（`/tmp`）

### 🎯 核心策略

1. **持久化一切**：配置、数据、凭据都放在 `~/workspace` 下
2. **自启动必配**：`.zshrc` 启动脚本 + PM2 进程管理
3. **凭据隔离**：敏感信息单独文件，不提交到 Git
4. **路径绝对化**：脚本中使用绝对路径，避免 CWD 问题

---

## 二、通用服务部署流程

### 2.1 下载二进制文件

```bash
# 示例：下载服务二进制
cd ~/workspace
wget https://example.com/service-linux-amd64 -O service
chmod +x service

# 或使用 GitHub Release
wget https://github.com/user/repo/releases/download/v1.0/service
```

### 2.2 创建持久化目录结构

```bash
mkdir -p ~/workspace/service/{bin,config,data,logs}

# 目录说明：
# bin/     - 可执行文件
# config/  - 配置文件
# data/    - 运行时数据（数据库、缓存等）
# logs/    - 日志文件
```

### 2.3 配置文件管理

**方式一：直接创建配置文件**

```bash
cat > ~/workspace/service/config/config.yaml <<'EOF'
server:
  port: 8080
  host: 0.0.0.0
database:
  path: /home/cloudstudio/workspace/service/data/app.db
log:
  path: /home/cloudstudio/workspace/service/logs/app.log
EOF
```

**方式二：模板 + 环境变量**

```bash
# 1. 创建配置模板
cat > ~/workspace/service/config/config.template.yaml <<'EOF'
server:
  port: ${PORT}
api_key: ${API_KEY}
EOF

# 2. 创建凭据文件（不提交到 Git）
cat > ~/workspace/service/config/secrets.env <<'EOF'
PORT=8080
API_KEY=your-secret-key
EOF

# 3. 在启动脚本中渲染配置
envsubst < config/config.template.yaml > config/config.yaml
```

### 2.4 创建启动脚本

```bash
cat > ~/workspace/service/start.sh <<'EOF'
#!/bin/bash
set -e

# 配置路径
SERVICE_DIR="$HOME/workspace/service"
cd "$SERVICE_DIR"

# 加载凭据（如果存在）
if [ -f config/secrets.env ]; then
    source config/secrets.env
fi

# 渲染配置（如果使用模板）
if [ -f config/config.template.yaml ]; then
    envsubst < config/config.template.yaml > config/config.yaml
fi

# 启动服务
exec ./bin/service \
    --config config/config.yaml \
    >> logs/service.log 2>&1
EOF

chmod +x ~/workspace/service/start.sh
```

### 2.5 凭据文件处理

```bash
# 创建 .gitignore 排除凭据
cat > ~/workspace/service/.gitignore <<'EOF'
config/secrets.env
config/*secret*
*.key
*.pem
data/
logs/
EOF

# 创建凭据示例文件（可提交到 Git）
cat > ~/workspace/service/config/secrets.env.example <<'EOF'
# 复制此文件为 secrets.env 并填入真实凭据
PORT=8080
API_KEY=your-api-key-here
DATABASE_URL=sqlite:///path/to/db
EOF
```

---

## 三、配置自启动

### 3.1 编辑 .zshrc

```bash
# 在 .zshrc 末尾添加启动逻辑
cat >> ~/.zshrc <<'EOF'

# === 服务自启动 ===
SERVICE_DIR="$HOME/workspace/service"
if [ -d "$SERVICE_DIR" ] && [ -f "$SERVICE_DIR/start.sh" ]; then
    # 检查服务是否已运行（避免重复启动）
    if ! pgrep -f "service" > /dev/null; then
        echo "Starting service..."
        cd "$SERVICE_DIR"
        nohup ./start.sh &
        echo "Service started (PID: $!)"
    fi
fi
EOF
```

### 3.2 验证自启动

```bash
# 重新加载 .zshrc
source ~/.zshrc

# 或新开终端测试
```

---

## 四、使用 PM2 管理（推荐）

### 4.1 安装 PM2

```bash
npm install -g pm2
```

### 4.2 创建 PM2 配置

```bash
cat > ~/workspace/service/ecosystem.config.js <<'EOF'
module.exports = {
  apps: [{
    name: 'my-service',
    script: './start.sh',
    cwd: '/home/cloudstudio/workspace/service',
    interpreter: '/bin/bash',
    watch: false,
    autorestart: true,
    max_restarts: 10,
    min_uptime: '10s',
    error_file: './logs/pm2-error.log',
    out_file: './logs/pm2-out.log',
    log_date_format: 'YYYY-MM-DD HH:mm:ss',
    env: {
      NODE_ENV: 'production'
    }
  }]
};
EOF
```

### 4.3 配置 PM2 自启动

```bash
# 在 .zshrc 中添加
cat >> ~/.zshrc <<'EOF'

# === PM2 自启动 ===
if command -v pm2 > /dev/null; then
    pm2 resurrect > /dev/null 2>&1 || true
fi
EOF

# 启动服务并保存
cd ~/workspace/service
pm2 start ecosystem.config.js
pm2 save
```

---

## 五、常用管理命令

### 服务管理

```bash
# 启动服务
pm2 start ecosystem.config.js

# 停止服务
pm2 stop my-service

# 重启服务
pm2 restart my-service

# 查看状态
pm2 status

# 查看日志
pm2 logs my-service

# 查看实时日志（最后 100 行）
pm2 logs my-service --lines 100

# 监控
pm2 monit
```

### 进程检查

```bash
# 查看进程
ps aux | grep service

# 查看端口占用
netstat -tulpn | grep :8080
lsof -i :8080

# 杀死进程
pkill -f service
```

### 日志管理

```bash
# 查看最新日志
tail -f ~/workspace/service/logs/service.log

# 查看错误日志
tail -f ~/workspace/service/logs/pm2-error.log

# 清理旧日志
find ~/workspace/service/logs -name "*.log" -mtime +7 -delete
```

---

## 六、故障排查

### 问题 1：重启后服务未自动启动

**检查清单：**

```bash
# 1. 确认 .zshrc 配置
cat ~/.zshrc | grep -A 10 "服务自启动"

# 2. 手动加载测试
source ~/.zshrc

# 3. 检查启动脚本权限
ls -l ~/workspace/service/start.sh

# 4. 查看 PM2 列表
pm2 list

# 5. 检查 PM2 dump 文件
cat ~/.pm2/dump.pm2
```

### 问题 2：服务启动失败

```bash
# 1. 查看错误日志
cat ~/workspace/service/logs/pm2-error.log

# 2. 手动运行启动脚本
cd ~/workspace/service
./start.sh

# 3. 检查配置文件
cat config/config.yaml

# 4. 检查凭据文件
cat config/secrets.env

# 5. 检查端口占用
netstat -tulpn | grep 8080
```

### 问题 3：数据丢失

**确认数据目录：**

```bash
# 持久化目录（重启后保留）
~/workspace/           ✅ 安全
~/workspace/service/   ✅ 安全

# 临时目录（重启后清空）
/tmp/                  ❌ 危险
/var/tmp/              ❌ 危险
```

### 问题 4：环境变量丢失

```bash
# 不要依赖 export 设置环境变量（重启后失效）
export API_KEY=xxx     ❌ 错误

# 正确做法：在启动脚本中 source
source ~/workspace/service/config/secrets.env  ✅ 正确
```

---

## 七、安全建议

### 7.1 凭据管理

```bash
# 1. 创建独立凭据文件
cat > ~/workspace/service/config/secrets.env <<'EOF'
API_KEY=sk-xxxxx
DATABASE_URL=mysql://user:pass@host/db
EOF

# 2. 限制文件权限
chmod 600 ~/workspace/service/config/secrets.env

# 3. 添加到 .gitignore
echo "config/secrets.env" >> ~/workspace/service/.gitignore

# 4. 提供示例文件
cat > ~/workspace/service/config/secrets.env.example <<'EOF'
API_KEY=your-api-key-here
DATABASE_URL=mysql://user:pass@host/db
EOF
```

### 7.2 日志脱敏

```bash
# 启动脚本中过滤敏感信息
exec ./bin/service 2>&1 | \
    sed 's/api_key=[^&]*/api_key=****/g' | \
    sed 's/password=[^&]*/password=****/g' \
    >> logs/service.log
```

### 7.3 定期备份

```bash
# 创建备份脚本
cat > ~/workspace/service/backup.sh <<'EOF'
#!/bin/bash
BACKUP_DIR="$HOME/workspace/backups"
mkdir -p "$BACKUP_DIR"

DATE=$(date +%Y%m%d_%H%M%S)
tar -czf "$BACKUP_DIR/service_$DATE.tar.gz" \
    -C "$HOME/workspace" \
    --exclude="service/logs" \
    --exclude="service/data/*.tmp" \
    service/

# 保留最近 7 天的备份
find "$BACKUP_DIR" -name "service_*.tar.gz" -mtime +7 -delete
EOF

chmod +x ~/workspace/service/backup.sh

# 添加到 crontab（如果 CloudStudio 支持）
# 0 3 * * * /home/cloudstudio/workspace/service/backup.sh
```

---

## 八、快速参考

### 标准目录结构

```
~/workspace/service/
├── bin/
│   └── service              # 可执行文件
├── config/
│   ├── config.yaml          # 主配置文件
│   ├── config.template.yaml # 配置模板
│   ├── secrets.env          # 凭据文件（不提交）
│   └── secrets.env.example  # 凭据示例（可提交）
├── data/
│   ├── app.db               # 数据库
│   └── cache/               # 缓存
├── logs/
│   ├── service.log          # 应用日志
│   ├── pm2-out.log          # PM2 输出
│   └── pm2-error.log        # PM2 错误
├── start.sh                 # 启动脚本
├── ecosystem.config.js      # PM2 配置
├── .gitignore               # Git 忽略规则
└── README.md                # 部署文档
```

### 一键部署模板

```bash
#!/bin/bash
# deploy-service.sh - 通用服务一键部署脚本

set -e

SERVICE_NAME="my-service"
SERVICE_DIR="$HOME/workspace/$SERVICE_NAME"
DOWNLOAD_URL="https://example.com/service-linux-amd64"

echo "==> 创建目录结构"
mkdir -p "$SERVICE_DIR"/{bin,config,data,logs}

echo "==> 下载服务"
wget "$DOWNLOAD_URL" -O "$SERVICE_DIR/bin/$SERVICE_NAME"
chmod +x "$SERVICE_DIR/bin/$SERVICE_NAME"

echo "==> 创建配置文件"
cat > "$SERVICE_DIR/config/config.yaml" <<EOF
server:
  port: 8080
  host: 0.0.0.0
log:
  path: $SERVICE_DIR/logs/app.log
EOF

echo "==> 创建启动脚本"
cat > "$SERVICE_DIR/start.sh" <<'SCRIPT'
#!/bin/bash
set -e
cd "$(dirname "$0")"
exec ./bin/my-service --config config/config.yaml >> logs/service.log 2>&1
SCRIPT
chmod +x "$SERVICE_DIR/start.sh"

echo "==> 配置 PM2"
cat > "$SERVICE_DIR/ecosystem.config.js" <<'PM2'
module.exports = {
  apps: [{
    name: 'my-service',
    script: './start.sh',
    cwd: process.env.HOME + '/workspace/my-service',
    interpreter: '/bin/bash',
    autorestart: true,
    max_restarts: 10
  }]
};
PM2

echo "==> 启动服务"
cd "$SERVICE_DIR"
pm2 start ecosystem.config.js
pm2 save

echo "==> 配置自启动"
if ! grep -q "PM2 自启动" ~/.zshrc; then
    cat >> ~/.zshrc <<'ZSH'

# === PM2 自启动 ===
if command -v pm2 > /dev/null; then
    pm2 resurrect > /dev/null 2>&1 || true
fi
ZSH
fi

echo "✅ 部署完成！"
echo "状态查看: pm2 status"
echo "日志查看: pm2 logs $SERVICE_NAME"
```

---

## 九、相关资源

- [腾讯云 CloudStudio 文档](https://cloud.tencent.com/document/product/1039)
- [PM2 官方文档](https://pm2.keymetrics.io/)
- [Bash 脚本编程指南](https://www.gnu.org/software/bash/manual/)

---

## 十、常见服务部署示例

### 示例 1：部署 HTTP 服务

```bash
cd ~/workspace
mkdir -p myhttp/{bin,logs}

# 创建简单 HTTP 服务
cat > myhttp/server.py <<'EOF'
#!/usr/bin/env python3
from http.server import HTTPServer, BaseHTTPRequestHandler
import sys

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'Hello from CloudStudio!')

port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
HTTPServer(('0.0.0.0', port), Handler).serve_forever()
EOF

chmod +x myhttp/server.py

# PM2 启动
pm2 start myhttp/server.py --name myhttp --interpreter python3 -- 8080
pm2 save
```

### 示例 2：部署定时任务

```bash
# 创建任务脚本
cat > ~/workspace/task.sh <<'EOF'
#!/bin/bash
echo "[$(date)] Task executed" >> ~/workspace/task.log
EOF

chmod +x ~/workspace/task.sh

# 添加到 .zshrc（每次终端启动时在后台定期执行）
cat >> ~/.zshrc <<'EOF'

# === 定时任务 ===
(while true; do
    sleep 3600
    ~/workspace/task.sh
done) > /dev/null 2>&1 &
EOF
```

---

## 总结

**关键要点：**

1. ✅ 所有内容放在 `~/workspace` 下
2. ✅ 使用 PM2 管理进程
3. ✅ 配置 `.zshrc` 自启动
4. ✅ 凭据文件独立管理
5. ✅ 使用绝对路径
6. ✅ 定期备份重要数据

**部署检查清单：**

- [ ] 目录结构创建完成
- [ ] 启动脚本可执行
- [ ] PM2 配置正确
- [ ] .zshrc 自启动配置
- [ ] 凭据文件已创建
- [ ] .gitignore 已配置
- [ ] 手动启动测试成功
- [ ] 重启后自动启动验证

按照本指南，你可以在 CloudStudio 中稳定运行任何长期服务！
