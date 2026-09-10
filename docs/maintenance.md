# 每日维护与 IDE 预览排障

> **先确认停机范围。** `StopWorkspace` 会中断工作区里的服务、隧道和 SSH 会话。平台可能重建容器,应按只有 `/workspace` 持久化来准备备份。默认 `DAILY_RESTART_ENABLED="false"`,升级本身不会启用每日停机。
>
> 初始补丁的自动化测试只验证控制流程。2026-09-11 已在获准维护窗口完成一次真实停启、原生服务恢复及预览检查,见[单次实测报告](2026-09-11_live-cloudstudio-restart-report.md)。**单次成功不保证所有镜像、启动链或 IDE 凭据都会得到相同结果**,新的部署仍须在获准窗口验收。

## 启用与回退

在 `worker/wrangler.toml` 的现有 `[vars]` 中设置字符串值,不要再添加第二个 `[vars]`:

```toml
DAILY_RESTART_ENABLED = "true"
```

只有精确的字符串 `"true"` 启用维护;缺省、`"false"` 和其他值均不启用。默认配置是 `"false"`。

启用前确认:

- 关键数据已备份,服务与隧道所需文件位于持久化目录。
- 原生启动入口和 `start.d/*.sh` 已经通过真实停启验收,而不只是看到安装器输出 `KEEPALIVE_INSTALL_OK`。
- `SPACE_KEYS` 确实是希望持续运行的工作区,多个工作区可同时运行的账号配额足够。部署脚本会重新发现账号下所有非 INVALID 工作区,不要假设它只选当前运行的那个。
- 维护顺序为去重后的工作区列表:北京时间 04:00、04:03、04:06……,当前小时内最多安排 20 个槽位。大量工作区还需评估 Worker 套餐的子请求额度与 API 限流,错开停启不代表绕过平台配额。
- `main` 上的 `worker/**` 修改会自动触发部署;修复分支不会自动部署。先审核差异、通过 CI,再合并或手动部署。

回退时将开关改回 `"false"` 并重新部署。这只阻止后续每日维护,不会撤销已经发出的 Stop,也不会关闭原有的关机自动唤醒。已经停止的工作区要先查询真实状态,再按现有开机流程恢复。

## 控制流程和失败语义

| 阶段 | 行为 | 不能据此得出的结论 |
|---|---|---|
| 读取状态 | 只处理明确 RUNNING 或明确关机;未知、INVALID、过渡状态跳过 | API 报错不是“工作区已关机” |
| 停机 | RUNNING 时仅发一次 StopWorkspace,轮询至 STOPPED | Stop 请求成功不代表停止已经完成 |
| 开机 | 确认 STOPPED 后发 RunWorkspace;初始已停止则直接走此步 | Run 请求成功不代表容器重建或业务已可用 |
| 确认 | 轮询至 RUNNING,之后可按配置打开 IDE | RUNNING 不代表应用健康、隧道就绪或预览凭据已刷新 |

每个状态确认阶段最多 90 秒,轮询间隔约 5 秒,单次腾讯云/心跳请求最多 15 秒。状态读取错误只重试读取,不重复 Stop。停止确认失败时不盲目 Run;开机失败时不再 Stop。日志会记录工作区、失败阶段和最近状态。

停止已发生但后续接口失败时,工作区可能暂时留在 STOPPED。原有心跳异常自动唤醒会在每个 5 分钟窗口尝试恢复**已确认关机**的工作区;持续限流、鉴权故障或额度不足时不能保证恢复时限,需要人工处理。不要用循环 Stop→Run 来处理持续 API 故障。

`BROWSERLESS_KEY` 是可选的旧式 IDE 启动入口。它只在启动确认后用于每日维护,请求上限 180 秒;失败不会再次停止已恢复的工作区。未配置时应依赖已验收的原生启动链。`vps-boot.yml` 是手动流程,不是每小时兜底任务。

HTTP `/scheduled` 仅补心跳,不会触发每日停启;没有新增公开 `/restart` 路由。原有 `/start`、`/stop`、`/ide` 等路由仍无内置鉴权,域名保密和私有 GitHub 仓库不能替代入口访问控制。加访问控制后还要验收自动化调用是否兼容。

## 在维护窗口验收

1. 在受控环境记录工作区状态、PID 1 启动时间、应用健康和隧道状态。不要把 API 密钥、workspace token、Cookie 或完整环境变量写入日志。
2. 先确认备份及原生启动链;执行一次经批准的 Stop→确认 STOPPED→Run,记录停机耗时。不需要为了测试修改系统时间或暴露新的重启 URL。
3. 平台回到 RUNNING 后,在工作区终端检查启动标记和本地应用。例如应用监听 3000 端口时:

   ```bash
   ps -p 1 -o lstart=
   cat /workspace/.keepalive/last-start.txt
   curl --fail --max-time 15 -o /dev/null -w 'HTTP %{http_code}\n' http://127.0.0.1:3000/
   ```

   启动时间和标记需与维护前比较。HTTP 200 只是其中一项证据,仍需检查业务功能与隧道入口。

4. 在重新登录的 IDE 中检查预览链接及相关接口状态。只记录 HTTP 状态和必要字段是否存在,不要记录凭据值。
5. 只有完成这些检查后,才将每日维护开关设为 `"true"`;失败则保持关闭并按平台状态恢复服务。

## 排查 IDE 预览链接中的 undefined

本次会话排查的 `CloudStudio.browser-preview-lite` 扩展按下面的字段拼接 URL:

```text
https://<spaceKey>-<previewToken>-<port>.<previewDomain>/
```

其中 `previewToken` 和 `previewDomain` 来自:

```http
GET https://ide.cloud.tencent.com/api/plugin/get-access-url-token?spaceKey=<spaceKey>
X-IDE-PLUGIN-ACCESS-TOKEN: <当前 IDE 注入的访问凭据>
```

这是本次排查版本的实现细节,不是稳定公开协议。旧会话记录过该接口返回 `401`、`data:null`,而扩展没有正确处理失败结果,最终拼出了包含 `undefined` 的 URL。凭据过期是可能原因之一,也应检查登录状态、账号权限、凭据注入和平台变化;不能从这一次 401 推导固定有效期。

排障顺序:

1. 先查本地监听端口和应用健康,将“预览地址生成失败”与“应用没有启动”分开。
2. 查看预览凭据接口的 HTTP 状态和 `data.token` / `data.domain` 是否非空;不要复制敏感请求头到工单、README 或 CI 日志。
3. 重新登录、重新打开 IDE。若容器内长期持有旧的注入凭据,在获准的维护窗口停启并重新验证;不要为了修链接直接盲重启生产服务。
4. 既有预览地址可以作为临时诊断线索,但必须重新检查实际可用性和业务鉴权。不要依赖任意中间字段被接受的历史观察,也不要承诺链接永久有效。
5. 长期服务入口使用自有域名和受控隧道,并设置应用鉴权或访问控制。即便猜不到地址,也不能视为安全隔离。

## 验证证据与范围

| 证据 | 发现 | 对应改动/验证路径 |
|---|---|---|
| 旧版 `scheduled()` 只调用 `/start/<space>` | 没有 Stop 和停止状态确认,不能承诺真实重启 | `worker/start.js` 的每日维护流程 |
| 停止延迟、读取错误和接口失败的模拟场景 | 必须先确认停止,限制等待,避免重复停机 | `tests/worker.test.mjs` |
| HTTP 入口与开关测试 | 默认升级和手动补心跳不能触发新增停机行为 | `worker/wrangler.toml`、`/scheduled` 回归测试 |
| Linux 启动测试报 `pipefail: invalid option name` | Windows 检出后的 shell 带 CRLF(Git 历史原文为 LF),可能阻止自启 | `.gitattributes` 固定 shell 为 LF,`tests/test_source_format.py` 防止回归 |
| 原 Claude 会话中的扩展排查记录 | 预览凭据接口失败可能形成 undefined URL | 本文排障步骤;当前线上凭据状态仍需现场验证 |

从仓库根目录运行:

```bash
node --check worker/start.js
node --test tests/worker.test.mjs
python3 -m unittest discover -s tests -v
```

Worker 测试拦截所有网络请求并使用虚拟时间,不会触碰线上工作区。Python 安装器测试在 Windows 会跳过部分 Unix 专用检查;GitHub CI 在 Linux 上运行完整套件。测试通过不等于真实停机验收通过。
