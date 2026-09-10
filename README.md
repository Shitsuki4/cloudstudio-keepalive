> **⚠️ 本仓库包含部署脚本,请保持 Private!**
>
> **升级部署前先设置仓库 Actions 变量 `SPACE_KEYS`。** 只填写需要保活的真实 spaceKey;未配置时部署会安全停止,不再默认纳入全部工作区。
>
> **真实停启会中断业务。** 每日维护默认关闭;只有验收持久化、原生自启和维护窗口后,才将 `DAILY_RESTART_ENABLED` 设为字符串 `"true"`。推送 `main` 下的 `worker/**` 会自动部署。

# CloudStudio Keepalive — 腾讯云 CloudStudio 免费工作区 24h 保活

通过定时心跳和有条件的自动唤醒,尽量保持腾讯 CloudStudio 工作区运行;不承诺平台永久保留工作区或预览链接:

```
┌─ Cloudflare Worker(每分钟 cron)────────────────────┐
│  ① /heart/<space>   心跳 → 工作区 evict:false 不回收     │
│  ② 可选:每天 04:00(北京) Stop → 确认停止 → Run          │
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

3. **同一页面 → Variables** 添加仓库变量 `SPACE_KEYS`,填写明确要保活的真实 spaceKey,多个用英文逗号分隔。spaceKey 可从 IDE 地址 `/tty/<spaceKey>/` 获取,不是工作区显示名。
   - 只填写需要保活的工作区,不要加入希望保持关机的空间。部署不会默认选中账号下全部工作区。
   - 未设置、空白、格式错误、不存在或已回收的 key 都会在修改 Cloudflare 前使部署失败;不会退回全量发现。重复 key 去重,保留填写顺序。
   - 已有部署升级时也必须先设置此变量。`DAILY_RESTART_ENABLED="false"` 不会关闭所选工作区原有的自动唤醒。
4. **Actions → Keepalive Setup → Run workflow**(会校验密钥和指定工作区,然后触发部署)
5. 完成后验证:`https://<KEEPALIVE_DOMAIN>/heart/<spaceKey>` 返回 `{"evict":false,...}` 即保活生效

## 启用真正的每日停启（可选，默认关闭）

旧实现仅调用 `RunWorkspace`,不能据此声称正在运行的容器已重建。新流程先确认状态,仅对 `RUNNING` 发一次 `StopWorkspace`,轮询确认 `STOPPED` 后才 `RunWorkspace`,最后确认 `RUNNING`。已停止的工作区只启动;未知或过渡状态不改动。

1. 先完成下面的原生启动链验收,并确认需要保存的数据在 `/workspace`、停机窗口可接受。
2. 在 `worker/wrangler.toml` 的**现有** `[vars]` 内把 `DAILY_RESTART_ENABLED = "false"` 改为 `"true"`,再部署。默认值不会主动停掉现有业务。
3. 每天北京时间 04:00、04:03、04:06……按去重后的 `SPACE_KEYS` 顺序维护。`/scheduled` HTTP 入口只补心跳,不能手动触发这套停启流程。
4. API/状态确认失败会记日志,不盲目重复 Stop。关闭每日维护**不等于**关闭原有的关机自动唤醒。

停机验收、超时恢复、IDE `undefined` 预览链接与验证范围见 [维护与预览排障](docs/maintenance.md)。测试只模拟接口,不代表线上停启已经验收。

## 原生开机启动（可选，需先确认原钩子）

腾讯云 [Lifecycle.Start](https://cloud.tencent.com/document/product/1039/94097#LifeCycle) 定义为“每次工作空间启动时执行”。原生钩子直接调用启动脚本，不需要 Actions 定时任务、SSH 或浏览器触发 `preview.yml`。当前实现提交配置后不自动重启，实际触发效果需要单独停启验收。

1. 首次运行 **Keepalive Setup** 时勾选 `initialize_startup`；已有部署可单独运行 **Keepalive Startup Setup**。
2. `space_key` 在账号只有一个有效工作区时可留空，多工作区必须明确指定。
3. `lifecycle_baseline` 填写当前完整 Lifecycle JSON；**只有确认没有旧钩子时才填 `{}`**。官方列表 API 不返回原钩子，因此不自动猜测为空，未填写会安全停止。
4. 流程等待 Worker 部署成功，备份生命周期计划，安装文件，再注册 `Lifecycle.Start`。未托管或被用户改过的 `preview.yml` 保留；不覆盖未知 `boot.sh`。
5. 下载 Actions 的 `lifecycle-backup-*` 回滚文件并长期保存，平台只保留 7 天。基线输入和备份不要包含密钥；现有钩子若包含敏感命令，应改用受保护的本地文件流程，不填入 Actions 表单。

安装内容：

- `/workspace/.keepalive/boot.sh`：带锁的启动入口，依次调用 `start.d/*.sh`，不自带业务应用。
- `/workspace/.keepalive/start.d/`：放你的幂等服务启动脚本，脚本自行后台化和检查健康，不能无限阻塞；启动锁不会被服务子进程继承。
- `/workspace/.keepalive/logs/boot.log`、`last-start.txt`：启动来源、时间与命令结果；没有服务时明确记录 `no_services_configured`，不冒充应用健康。
- `/workspace/.vscode/preview.yml`：文件不存在时安装；默认 `autoOpen: false`，避免打开 IDE 重复启动，原生钩子不依赖它。
- `/workspace/.keepalive/supervisor-start.sh`、`/workspace/.keepalive/keepalive-boot.conf` 与 `/usr/local/share/supervisor/keepalive-boot.conf`：镜像原生 `supervisord` 的自动启动入口。安装前会读取活动 `supervisord` 配置并确认 `[include] files` 实际匹配运行时副本；不匹配或无法确认时停止，不修改主配置。安装器会备份并拒绝覆盖外部修改。`KEEPALIVE_INSTALL_OK` 只表示文件事务成功，不表示 Supervisor 已加载或钩子已经执行。

在当前 CloudStudio 镜像中，PID 1 使用 `/.PlnPyKFp4CRfFtgC1/bin/supervisord -c /.PlnPyKFp4CRfFtgC1/supervisord-conf/supervisord.conf`；部署前仍应由安装器确认活动配置加载 `/usr/local/share/supervisor/*.conf`。Supervisor 运行时副本位于非 `/workspace` 路径，镜像重建后是否持久必须现场确认；如果副本丢失，应先由持久副本补齐并重新做冷启动验收。其他镜像必须先确认相同 include 目录与启动命令；安装器无法证明时会停止，不会伪造成功。

`ModifyWorkspace` 成功不等于钩子执行成功；必须在没有 IDE/Actions 参与的真正启动中确认 `source=lifecycle` 或 `source=supervisor`。对运行态重复调用 `RunWorkspace` 是否重建、是否重跑钩子，不能仅凭官方“运行空间”的描述保证。维护窗口前不要主动停机。

回滚有两个互不替代的范围：`lifecycle.py restore --plan ...` 恢复腾讯云 Lifecycle 基线；安装器生成的 `rollback` 命令恢复 `/workspace` 与 `/usr/local/share/supervisor` 中本方案的受管文件。文件 rollback 会在当前文件被外部修改时拒绝执行，不删除 `start.d`、日志或其他用户文件。新版安装状态绑定备份 manifest 摘要；旧安装器生成的未认证 state/backup 不会被静默迁移，需使用当次 Actions 保存的原始 rollback 命令或人工核验后重装。两项操作都不会自动停止或启动空间；Lifecycle API 返回不确定时应先人工核对，不要据此宣称 Lifecycle 未应用。

详细官方依据与验收方案见 [原生开机启动方案](2026-09-05_技术方案-cloudstudio-lifecycle-report.md)。

## 工作方式

- **显式限定保活范围**:部署时用腾讯云密钥调 `DescribeWorkspaces` 只读校验仓库变量 `SPACE_KEYS`,仅将其中指定的有效工作区写入 Worker;不会自动追加新建或已关机的其他工作区。`/status` 的全账号只读查询不受此部署范围限制
- **Global API Key 不直接部署**:Actions 运行时用它铸一个仅限本 zone 的临时 API token,部署完自动删除;Worker 长期运行只需 SecretId/Key
- **显式启用的每日维护**:`DAILY_RESTART_ENABLED="true"` 才执行 Stop → 等待 STOPPED → Run → 等待 RUNNING。每个状态确认阶段最多等待 90 秒,约每 5 秒检查一次,单次 API 请求最多 15 秒;使用 Cron 的 `scheduledTime` 定位维护槽位。RUNNING 只说明平台状态,不代表业务健康或 IDE 凭据已刷新
- **关机自动唤醒**(`tryWake`):心跳链失败(铸 token 抛错、心跳非 200、或 body 报 `evict:true`)后,每逢分钟数可被 5 整除时查 `DescribeWorkspaces`,**状态明确是关机才**尝试 `RunWorkspace`;已回收、未知、运行中或过渡状态不动。API 不可用时不能保证 5 分钟内恢复。此行为不受每日维护开关控制,手动关机仍可能被唤醒
- **`/status` 端点**:`https://<KEEPALIVE_DOMAIN>/status` 只读查全部工作区状态(排查用)
- **免 SSH 打开网页 IDE**(`vps-boot.yml`,手动触发):Actions 用腾讯云密钥铸 workspace token(~10 分钟有效),runner Chrome 打开 tty 页面——工作区装有 `/workspace/.vscode/preview.yml` 时触发 autoOpen 启动链;配套 `ide-exec.js` 终端通道(打开 `https://<KEEPALIVE_DOMAIN>` 根路径,Worker 自动选工作区并 302 进网页终端;多工作区用 `/ide/<spaceKey>` 精确指定)可免 SSH 执行任意命令读回输出。曾经每小时定时跑,因 GitHub Actions schedule 丢槽严重(实测 ~48 槽只 fire ~11 次)且保活实测只靠心跳就够,已改为纯手动——需要时 Actions 里 Run workflow 即可
- **改代码后再部署**:push 到 `main` 且修改 `worker/**`、部署工作流或工作区校验脚本会自动部署;修复分支只跑 CI,不会部署。也可手动 Run workflow

## 目录结构

```
worker/
  start.js            Worker 源码(TC3 签名调 CloudStudio API)
  wrangler.toml       cron 每分钟;需要 global_fetch_strictly_public
.github/
  scripts/cf-token.py     Global Key → 临时 token + zone/account 自动发现
  scripts/tc-discover.py  腾讯云密钥 → 校验 SPACE_KEYS 显式部署范围
  scripts/tc-wtoken.py    腾讯云密钥 → workspace token 铸造(TC3,~10 分钟有效,免 SSH 打开网页 IDE)
  scripts/open-ide.js     用 runner Chrome 打开网页 IDE(有 preview.yml 则触发 autoOpen)
  scripts/ide-exec.js     网页终端执行器:铸 token → 打开 IDE → 终端执行命令读回输出,免 SSH
  scripts/bind-route.sh   DNS A 记录 + workers route 绑定
  workflows/setup.yml     首次引导(校验 → 部署)
  workflows/deploy.yml    主部署(CF Worker + 自定义域路由 + 心跳验证)
  workflows/vps-boot.yml  免 SSH 打开网页 IDE + 终端通道(手动 dispatch,不定时)
  workflows/ci.yml        push 语法 lint + Worker/启动安装器回归测试
tests/
  worker.test.mjs         模拟 API 的 Worker 行为测试,不操作线上工作区
  test_tc_discover.py      部署范围校验与输出回归测试,不调用云 API
docs/
  maintenance.md         每日维护验收、回退与 IDE 预览排障
```

## 已知坑(都踩过)

- **Linux 启动脚本不能使用 CRLF 换行**:否则 Bash 可能报 `pipefail: invalid option name`。仓库通过 `.gitattributes` 固定 `*.sh` 为 LF,并有源码格式回归测试;不要手工把部署脚本改回 Windows 换行。
- **`RunWorkspace` 不等于重启正在运行的工作区**:不能把 API 成功响应当成容器重建证据;真实停启必须先 Stop 并确认状态。每日维护默认关闭,避免升级后未经确认就中断业务。
- **IDE 预览出现 `undefined`**:在本次排查的扩展版本中,预览 URL 依赖 `get-access-url-token` 返回的 `data.token` / `data.domain`;接口 401 或空数据可能导致错误拼接。这不等于应用端口已停止,也不能仅凭 401 断言凭据有固定有效期。见[排障步骤](docs/maintenance.md#排查-ide-预览链接中的-undefined)。
- **预览链接不保证永久有效,也不是访问控制**:工作区、端口、域名规则、网关鉴权变化都可能让旧地址失效。不要把一次测试得到的 URL 或中间字段行为当成平台契约;长期入口建议使用自己管理的域名、隧道和业务鉴权。

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
