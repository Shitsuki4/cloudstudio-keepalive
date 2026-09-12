# CloudStudio 单次停启与预览实测

> **本次实测已获准停机，但不构成后续停机授权。** 修复版已部署，`DAILY_RESTART_ENABLED="false"` 保持不变；PR 未合并。结果仅适用于本次工作区、镜像和现有启动配置，不保证预览凭据的固定有效期或链接永久有效。

## 结论与范围

2026-09-11 北京时间 03:41 对 `jqtwjs` 完成一次 Stop→确认 STOPPED→Run。容器 PID 1 确认重新启动，应用与隧道在未打开 IDE 的情况下自行恢复；随后预览凭据接口由 401 恢复到 200，按其返回字段生成的 3000 端口预览地址也返回 200。

- 代码：`2971486a1b3e5e44d969c5056a6d6cbf0577c1f4`。
- 线上 Worker 的源码下载后校验，与该提交一致。保留既有 `SPACE_KEYS="jqtwjs"`、腾讯云 secret bindings、自定义域路由和每分钟 Cron，仅新增默认关闭的维护开关。
- 没有使用会重新发现全部工作区的通用部署工作流；`zindqk` 测试前后均为 STOPPED，没有对它发送开停请求。
- 实际 Stop/Run 由本机 Node 加载同一提交的 `restartWorkspace()` 执行，调用真实腾讯云 API；加了单目标、最多一次 Stop/Run 和源码哈希守卫。**本次没有触发 Cloudflare 的每日 `scheduled()` 重启分支**，不能据此宣称其运行时限额、定时到达率已验收。
- 沿用现场已有 `/workspace/.keepalive` 启动链，未覆盖为仓库当前的启动脚本；本次也不是全新安装器的线上验收。

## 结果

| 检查 | 观察 | 结论边界 |
|---|---|---|
| 开停次数 | StopWorkspace 1 次，RunWorkspace 1 次 | 没有为 IDE 加载问题再次停机 |
| API 状态 | RUNNING→STOPPED→RUNNING，全流程约 1.68 秒 | 只是控制面状态，不是业务就绪时间 |
| 容器 PID 1 | 从 `2026-09-08 09:47:28 UTC` 变为 `2026-09-10 19:41:19 UTC` | 确认容器内主进程重启，不是宿主机重启 |
| 对外服务 | 采样到约 17.2 秒 HTTP 502，随后连续两次 200、`success=true` | 约 2 秒采样加网络耗时，非精确停机时长 SLA |
| 原生恢复 | 首次故障后成功响应在 03:41:25.799；此时未打开 IDE | 应用恢复不依赖此次人工打开 IDE |
| 本地应用 | 3000 端口主页和 `/api/status` 均 200 | 未代替全部业务功能、模型调用或账务测试 |
| 进程与文件 | `new-api`、`cloudflared` 各 1 个；启动配置和服务脚本哈希未变 | 只检查本次使用的服务与配置 |
| 预览凭据 | 停启前接口 401、token/domain 缺失；之后 200、两字段非空，凭据指纹发生变化 | 支持本次故障恢复，不证明固定 TTL 或所有故障都能靠停启解决 |
| 新预览地址 | 使用本次接口字段构造，HTTP 200 | 没有在报告或 CI 中发布含凭据的完整 URL |
| 最终状态 | `jqtwjs` RUNNING，另一工作区 STOPPED；心跳 200、`evict=false` | 每日维护仍关闭 |

**重要时间差：** API 在 03:41:04.032 已返回 RUNNING，但监测在 03:41:08.582 才观察到 502，直到 03:41:25.799 才恢复 200。不能看到 RUNNING 就结束业务验收，也不能用 RUNNING 推断旧容器已经完全退出。

IDE 首次终端加载没有成功，重新打开后于 03:49 完成容器和预览检查。启动标记可能被后续 Lifecycle/IDE 活动更新，因此这里以 **PID 1 时间变化 + 打开 IDE 前的外部服务恢复** 为主要证据，不把最后一次 `last-start.txt` 时间单独当作冷启动证据。

## Evidence → Finding → Path

完整脱敏观测 JSON 留在项目本机的 `.artifacts/`，不随仓库发布；以下哈希可由项目持有者离线核对。包含应用配置和隧道凭据的恢复压缩包不属于可发布证据。

| Evidence | source_type / source_ref（仓库根目录起） | 脱敏观察 | SHA-256 |
|---|---|---|---|
| E-001 | log / `.artifacts/live-restart.json` | 03:41:02–03:41:04，单次 Stop/Run 与状态序列 | `b93b4fcdfcb16230124d42df0f60d614d4a01b0c7a343dc75b63af40bef51df1` |
| E-002 | network / `.artifacts/live-external-monitor.json` | 03:41:08 起 502，03:41:25 起恢复；期间未打开 IDE | `8d4047520095e7b961e6bc28609ea842d6b330b7717d034f5079233cb4f6903c` |
| E-003 | file / `.artifacts/live-result.json` | PID 1、进程、配置哈希与预览接口前后比较 | `22fe89e3e07c7f76bc4c31007322e6716f51a9bcd1f910dcc1033089b09a62b9` |
| E-004 | network / `.artifacts/live-final-state.json` | 03:54:56，单目标配置、维护关闭、服务正常 | `43c90d7000d0b0ef32c44c9d3c585b88cebefd7cbee7f939425d39ae2d64ea9c` |

- **F-001（validated，confidence=high）**：本次真实停启及既有应用启动链通过；证据 E-001、E-002、E-003。路径位置：`worker/start.js` 的 `restartWorkspace()` 与现场 `/workspace/.keepalive/start.d/`。
- **F-002（validated，confidence=high）**：本次预览凭据故障恢复；证据 E-003。路径位置：IDE 预览凭据接口及生成的 3000 端口预览地址。结论不外推到固定凭据有效期。
- **F-003（validated，confidence=high）**：控制面 RUNNING 早于数据面恢复；证据 E-001、E-002。运维仍须执行独立应用健康检查。

**P-001（callflow）：** 获批并备份 → 单目标控制器读取 RUNNING、Stop、确认 STOPPED、Run（E-001 / F-001）→ 不打开 IDE，独立监测应用恢复（E-002 / F-001、F-003）→ 再检查 PID 1、应用与预览字段（E-003 / F-002）→ 核对其他工作区和维护开关未变（E-004）。

## 复核与再次测试

离线限制：需项目持有者提供上述脱敏 JSON 才能校验；不要索取或上传恢复压缩包、完整凭据或完整预览 URL。拿到文件后，从仓库根目录运行：

```powershell
Get-FileHash -Algorithm SHA256 .artifacts/live-restart.json, .artifacts/live-external-monitor.json, .artifacts/live-result.json, .artifacts/live-final-state.json
```

重复实网测试前必须重新获准维护窗口，按[维护验收步骤](maintenance.md#在维护窗口验收)重新记录基线和备份。调用顺序必须是单次 Stop→读状态至明确 STOPPED→单次 Run→独立检查应用，不能只重复 Run 或靠打开 IDE 掩盖自启失败。记录 API 状态、PID 1 和服务时间线；预览接口只记录状态、字段存在性与凭据是否变化，不记录凭据值。

本次已在工作区外保存启动配置及 SQLite 在线备份，传输校验和与 SQLite `quick_check` 均通过。备份排除二进制、日志、缓存和符号链接；不是整个工作区的完整灾备镜像。原服务文件未做覆盖或恢复写入。

## 后续边界

- 自动化回归测试仍是模拟 API，不等于此次实网测试，也不等于多次长周期稳定性测试。
- 保持每日维护关闭，直到使用者决定是否接受每天的中断；本次单次开测没有启用每天自动停机。
- 现网代码从修复提交直接部署，PR 仍未合并；后续从旧 `main` 部署可能覆盖本次修复，且通用部署流程会重新发现工作区。合并或再次部署前核对版本与单目标范围。
