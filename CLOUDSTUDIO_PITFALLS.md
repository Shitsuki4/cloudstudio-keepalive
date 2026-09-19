# CloudStudio 部署踩坑实录

> 在腾讯 CloudStudio 免费工作区实际运维中逐条复现、验证过的坑。
> 与 [通用服务部署指南](./CLOUDSTUDIO_DEPLOYMENT.md) 互补——那边讲"怎么做"，这边讲"哪里会翻车"。

---

## 一、重启语义：04:00 维护窗口对 RUNNING 工作区是 no-op

流传的说法是"工作区每天凌晨 2:00-4:00 会重启一次，服务靠启动链自动恢复"。**实测不成立。**

观测：某工作区自 2026-09-10 19:41Z 起连续运行，跨越 09-11 ~ 09-19 共 **9 个** 04:00 窗口，容器一次都没重建（启动日志无新记录，PID1 未变）。

**结论：**

- 只有 **STOP → RUN** 这条路径才会真正重建容器
- RUNNING 状态下定时触发的 RunWorkspace 是 **no-op**，既不重建也不跑启动钩子
- 因此"反正每天会自动恢复一次"**不能作为容错假设**，服务必须自己扛得住长期运行

---

## 二、区分"容器真重启"和"工作区被唤醒"

两者都会在启动日志留下 `event=started`，极易混淆：

| 日志特征 | 实际含义 |
|---|---|
| `source=supervisor event=started` | **真重建**（容器冷启动，supervisord 拉起 include） |
| `source=lifecycle event=started`，且服务报 `already running` | **唤醒/恢复**（有人开 IDE、SSH 或心跳触发），**不是重建** |

辅助证据：

- `/proc/1` 的 mtime = 容器创建时刻，真重建后它会变
- 常驻关键进程的启动时间（`ps -o lstart`）应与 PID1 一致；不一致说明该进程自己重启过

**坑：** 把 lifecycle 的 `already running` 当成"重启后自动恢复成功"，会得出完全相反的结论——它其实证明**根本没重启**。

---

## 三、只有 /workspace 跨重建幸存

重建后会丢失：

- `/usr/local/bin`、`/etc/config`、`/var/log` 下的全部内容
- 容器创建时注入 PID1 的**环境变量**（无刷新机制，也不会重新注入）
- `/tmp`、`/var/tmp`

所以二进制、配置、日志要么放进 `/workspace`，要么接受"重建后重新生成"。**让 `start.d` 脚本重跑安装器**通常比手工摆文件更稳（版本不会钉死）。

---

## 四、启动链：用 supervisor 的 start.d，别指望 .zshrc / systemd / PM2

工作区 PID1 是 **supervisord**，不是 systemd：

- ❌ **systemd 不存在**。安装脚本里的 systemd 分支会**静默失败**——看着像装好了，实际 `/etc/systemd/system/` 下没有 unit，也没有 `systemctl` 可用
- ❌ **`.zshrc` 不能用于自启**：只在交互式登录时执行，容器冷启动不跑
- ❌ **PM2 不可靠**：需额外安装，且 `pm2 resurrect` 同样挂在 shell 启动上
- ✅ **可靠路径**：`/workspace/.keepalive/boot.sh` 由 supervisord include 调用，按文件名顺序执行 `start.d/*.sh`

`boot.sh` 的约定（写 start.d 脚本必须遵守）：

- `set -euo pipefail`：**任一脚本非 0 退出，后续脚本不再执行**，整条启动链记为 failed
- 输出重定向到 `.keepalive/logs/boot.log`，结果写入 `last-start.txt`
- 因此脚本要**幂等**且**尽早成功退出**：服务已在跑就直接 `exit 0`

---

## 五、⚠️ 按进程名杀进程的连环坑（最容易中招）

很多服务和安装器在重启旧实例时，是按 **cmdline 匹配自己的名字**来杀的，例如 `pkill -f cf-probe`。

这会误杀一切 cmdline 里含该字符串的进程，包括：

1. **你自己的 start.d 脚本**——脚本名叫 `30-cf-probe.sh`，启动安装器时就把自己杀了
2. **你的 SSH 会话**——命令行里带了该字符串，会话被直接掐断（exit 255）

**典型症状极具欺骗性：**

```
Starting 30-cf-probe.sh
Terminated
2026-09-19T18:28:37Z source=manual event=failed exit=143
```

但去查进程会发现**服务其实已经起来了**。这是假失败，很容易误判成"安装失败"而反复折腾。

**规避：**

- start.d 脚本名**不要包含服务二进制的名字**，用 `30-agent.sh` 这类中性名
- 远程调试的命令行里也不要出现该字面量；观测时用 `grep -E "cf[-]probe"`、`/run/*probe*.pid` 等写法绕开

---

## 六、⚠️ 安装器会杀死自己的整个进程组

某些安装器重启服务时会 kill 掉自身进程组，后果：

- **前台直接跑**：连调用它的 boot.sh **和 SSH 会话一起杀掉**，启动链中断
- **从 start.d 调用**：boot.sh 被杀 → boot.log 记 `Terminated`，后续脚本不再执行

**正确写法：`setsid` 隔离，且不要信它的退出码**

```bash
setsid bash -c '<安装命令>' </dev/null >>"$install_log" 2>&1 &

# 以 pid 文件为唯一成功判据，而不是安装器的退出码
for i in $(seq 1 60); do
  sleep 2
  if [ -f "$pid_file" ] && kill -0 "$(cat "$pid_file")" 2>/dev/null; then
    echo "service started"; exit 0
  fi
done
echo "service not running after 120s" >&2; exit 1
```

要点：

- `setsid` 隔离会话，避免组杀波及调用方
- `</dev/null` 断开 stdin，防止安装器读终端
- 带超时兜底，避免启动链被永久卡住
- **别用安装器退出码判成败**——它可能自杀了但服务已起来

---

## 七、start.d 脚本模板（幂等 + 可观测）

```bash
#!/usr/bin/env bash
set -uo pipefail

pid_file="/run/myservice.pid"

# 1. 幂等：已在跑就直接成功退出，让启动链继续走后面的脚本
if [ -f "$pid_file" ] && kill -0 "$(cat "$pid_file")" 2>/dev/null; then
  echo "myservice already running"
  exit 0
fi

# 2. 启动（必要时 setsid 隔离进程组）
setsid bash -c '<启动命令>' </dev/null \
  >>/workspace/.keepalive/logs/myservice-install.log 2>&1 &

# 3. 以 pid 文件判定成败，带超时
for i in $(seq 1 60); do
  sleep 2
  if [ -f "$pid_file" ] && kill -0 "$(cat "$pid_file")" 2>/dev/null; then
    echo "myservice started"; exit 0
  fi
done
echo "myservice not running after 120s" >&2
exit 1
```

注意这里用 `set -uo pipefail` 而**不是** `set -e`：下面的 `kill -0` 探测天然会返回非 0，`set -e` 会在探测处意外退出。

---

## 八、改动启动链后的验证清单

必须实测**两条路径**，缺一不可：

- [ ] **恢复路径**：杀掉服务 → `boot.sh manual` → 确认自动拉起，`boot.log` 记 `result=commands_completed`
- [ ] **幂等路径**：服务在跑时 → `boot.sh manual` → 确认直接跳过、不重复安装

只测第一条是最常见的疏漏。幂等路径没测的话，会在**每次**启动时重复下载安装，拖慢甚至拖垮整条启动链。

---

## 九、其他小坑

- **长时间无日志 ≠ 挂了**：有些探针/上报型服务只在启动和建连时打日志，之后静默上报。判断存活要看 CPU 时间增长或连接状态，别只看日志尾行
- **SQLite 服务的 DSN**：部分服务（如 new-api）不接受把 SQLite 文件路径塞进 `SQL_DSN`，必须用专用的 `SQLITE_PATH`
- **不要给服务开 `SESSION_COOKIE_SECURE=true`**，除非同时配好 `SESSION_COOKIE_TRUSTED_URL`——否则重启后直接起不来
- **容器内多层引号会被 shell 吃掉**：远程执行复杂脚本用 base64 传输 —— `echo <b64> | base64 -d | bash`
- **GitHub API 从容器 IP 调用会限流**，需要带 token
- **对超大单行压缩文件**（几十上百 KB 挤在一行）用 `grep -o` 抽取会卡死，先拉到本地再解析
- **SSH 到工作区**：服务端只提供 keyboard-interactive，无需输入即通过。**不要加 `-o BatchMode=yes`**（直接 Permission denied）；非交互执行用 `ssh -o NumberOfPasswordPrompts=1 <target> "命令"`
- **IDE 预览 URL 显示 `xxx-undefined-3000.undefined`**：预览 token 只在容器创建时注入，跑久了 API 返 401 导致扩展拼不出 URL。真实地址仍可用——网关**不校验中间段**，`https://<spaceKey>-<任意不含横杠的串>-<端口>.app.cloudstudio.work` 即可访问，且无鉴权。只有真正 Stop→Run 才会换新 token