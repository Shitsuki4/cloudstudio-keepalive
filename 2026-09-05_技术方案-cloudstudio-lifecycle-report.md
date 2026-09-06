# CloudStudio 原生开机启动方案

首次 Actions 初始化，后续通过腾讯云生命周期钩子自动启动服务，脱离 Actions 与浏览器。

- 日期：2026-09-05（北京时间）
- 项目：cloudstudio-keepalive
- 性质：腾讯云官方文档核查与实施方案
- 状态：官方能力已确认，目标工作空间的生命周期钩子尚未实测

> **结论：优先采用腾讯云原生 `Lifecycle.Start`，不再把云端浏览器触发 `preview.yml` 作为首选。** 首次 Actions 安装文件并注册钩子，之后由腾讯云平台在工作空间真正启动时执行服务启动脚本。本次仅生成文档，没有修改工作空间、开关机或更新线上 Worker。

## 1. 需求与结论

用户希望首次运行 Actions 时自动配置 `/workspace/.vscode/preview.yml`，之后保活脚本进行开机时，服务自动恢复，不再依赖 Actions 或人工打开 IDE。

腾讯云官方提供了对应机制：`ModifyWorkspace` 接受 `Lifecycle` 参数，其中 `Start` 的语义是“每次工作空间启动时执行”。应先验证原生钩子，成功后以它作为主启动通道。Cloudflare Browser Run / Browserless 降级为原生钩子不可用时的备选。

**边界：** 官方接口存在不等于目标空间已经验收成功。本方案不承诺永久免费、永不回收，也不改变腾讯云的使用政策、计费及资源生命周期规则。

## 2. 官方依据：Evidence → Finding → Path

以下来源于 2026-09-05 在线核查。API 文档版本为 `2023-05-08`，与当前项目一致；不能将不同产品或新旧版本能力直接等同。

| 证据编号 | 官方来源 | 核查内容 |
|---|---|---|
| E-001 | [配置运行文件](https://cloud.tencent.com/document/product/1039/131807) | `.vscode/preview.yml` 配置启动命令、目录、端口及多服务，区分 `autoOpen` 与 `autoPreview` |
| E-002 | [数据结构：LifeCycle](https://cloud.tencent.com/document/product/1039/94097#LifeCycle) | 定义“工作空间生命周期自动执行脚本”；`Start` 每次空间启动执行 |
| E-003 | [修改工作空间：ModifyWorkspace](https://cloud.tencent.com/document/product/1039/94092) | 现有空间支持 `Lifecycle`，用户可自定义 Shell 命令 |
| E-004 | [创建工作空间：CreateWorkspace](https://cloud.tencent.com/document/product/1039/94096) | 创建空间时同样支持 `Lifecycle` |
| E-005 | [运行空间：RunWorkspace](https://cloud.tencent.com/document/product/1039/94090) | 用于运行空间，未承诺对运行态重复调用一定重启 |
| E-006 | [腾讯云官方 Python SDK](https://github.com/TencentCloud/tencentcloud-sdk-python/blob/master/tencentcloud/cloudstudio/v20230508/models.py) | `LifeCycle`、`LifeCycleCommand`、`ModifyWorkspaceRequest` 与文档一致 |

### 2.1 Finding：结论

| 编号 | 结论 | 证据 | 确认范围 |
|---|---|---|---|
| F-001 | `preview.yml` 是运行与预览配置，不能仅凭文件存在认定为系统开机任务 | E-001 | 官方该页未承诺仅启动空间即执行该文件 |
| F-002 | 平台提供独立的原生启动脚本机制 | E-002、E-006 | 接口定义已确认，目标空间执行待验证 |
| F-003 | 按接口设计，可修改现有空间的钩子，不必先假设需要新建空间 | E-003、E-004 | 权限及兼容性待验证 |
| F-004 | 运行空间不等同于重启空间并重跑钩子 | E-005 | 重复调用的实际效果不能由文档保证 |

### 2.2 Path：建议调用路径

P-001：首次 Actions 安装文件 → `ModifyWorkspace` 注册 `Lifecycle.Start`（E-003 / F-003）→ 后续 `RunWorkspace` 使空间真正启动（E-005 / F-004）→ 平台执行 `Start`（E-002 / F-002）→ 启动脚本恢复服务。

这是基于官方能力的设计路径，不是已经完成的端到端实测记录。

## 3. `preview.yml` 与生命周期钩子的区别

### 3.1 项目运行与预览配置

官方要求文件位于项目根目录下的 `.vscode/preview.yml`。

| 字段 | 官方含义 | 注意事项 |
|---|---|---|
| `run` | 启动命令 | 可调用独立脚本 |
| `root` | 启动命令工作目录 | 必须匹配实际目录 |
| `port` | 服务端口 | 字段不可删除，可留空 |
| `autoOpen` | 是否自动运行服务 | 不是自动打开预览窗口 |
| `autoPreview` | 是否自动打开预览窗口 | 与运行命令分开控制 |
| `mainPort` | 是否为主预览端口 | 官方说明在应用主页预览场景生效 |

项目历史记录曾通过 IDE 扩展实现确认：打开 IDE 后，扩展读取配置并执行自动运行项。这是历史实现证据，不是本次官方页面对触发时序的承诺。

当前 tty 直开链路使用 `/workspace` 作为目录，因此安装位置是 `/workspace/.vscode/preview.yml`。如果改为打开项目子目录，应重新核对文件位置。

**不要把 YAML 当成 Shell 脚本执行。** 原生开机路径应直接调用真正的服务启动脚本，而不是尝试执行 `preview.yml`。

### 3.2 平台生命周期配置

| 字段 | 官方原文 | 建议用途 |
|---|---|---|
| `Init` | 工作空间首次初始化时执行 | 新空间的一次性初始化，不假设修改后补跑 |
| `Start` | 每次工作空间启动时执行 | 本方案的开机启动入口 |
| `Destroy` | 每次工作空间关闭时执行 | 可选关闭操作，不保证异常回收时执行 |

每个阶段是 `LifeCycleCommand` 数组，指令包含 `Name`（描述）和 `Command`（具体命令）。字段大小写以数据结构和 SDK 为准：`Lifecycle`、`Start`、`Name`、`Command`。

平台钩子负责开机，预览配置保留 IDE 运行入口；需要双入口时，共用幂等脚本，避免重复启动。

## 4. 首次 Actions 初始化

用户只触发一次 Setup，内部按显式依赖顺序完成：

1. 校验现有五项 Secrets，发现并明确选择目标工作空间。
2. 部署 Worker，等待初始化需要的入口可用。
3. 使用现有 Actions 浏览器终端通道安装文件；浏览器仅用于首次安装，不作为后续日常启动依赖。
4. 备份或合并已有 `preview.yml`，保留原有应用配置。
5. 安装 `boot.sh`，验证文件内容、版本与命令真实退出码。
6. 核对原有钩子及 API 更新语义，再调用 `ModifyWorkspace` 注册受管理的启动项。
7. 完成无害探针验收，再接入实际服务逻辑。

建议文件布局：

```text
/workspace/.vscode/preview.yml
/workspace/.keepalive/boot.sh
/workspace/.keepalive/logs/
/workspace/.keepalive/lifecycle-start.log
```

现有 `ide-exec.js --sync` 引用了已删除的 `vps/` 资产，不能原样启用。需要面向本方案的最小安装逻辑，不擅自带回此前删除的整套 VPS 应用。

管理密钥、Global Key 和 GitHub token 不写入工作空间或日志。无需新增 Browserless 凭据，但腾讯云身份必须具备修改目标空间的权限。

## 5. 后续运行与脚本职责

- 保活链：Worker 按既有策略发心跳、检查空间状态。
- 启动链：空间需要启动时调用 `RunWorkspace`；腾讯云执行 `Lifecycle.Start`，由 `boot.sh` 恢复服务。

不要求 Worker 解释 YAML，不需要每分钟打开浏览器，也不依赖 GitHub 定时任务或 SSH accessToken。

`boot.sh` 应使用绝对路径、显式工作目录、锁及进程检查，处理后台运行、日志、健康检查和有限重试。不能让长驻服务无限阻塞生命周期命令，也不能仅凭 `nohup` 保证平台不清理子进程。应实际验证钩子结束后服务存活。

应用异常优先恢复应用，不应自动等同于工作空间需要重建。

## 6. 配置示例

以下示例未执行、未完成实机验收。JSON 是 `ModifyWorkspace` 的业务请求体，实际调用还需要 TC3 签名或官方 SDK，以及版本、地域等公共参数。

### 6.1 启动项请求体

使用当前目标空间 `pkfrql`。前提是启动脚本已安装并验证，原生命周期配置已经安全保留。

```json
{
  "SpaceKey": "pkfrql",
  "Lifecycle": {
    "Start": [
      {
        "Name": "keepalive-boot",
        "Command": "bash /workspace/.keepalive/boot.sh"
      }
    ]
  }
}
```

这里只展示受管理的启动项，不意味着可以直接覆盖完整配置。公开 `DescribeWorkspaces` 文档和 SDK 返回结构未列出 `Lifecycle`，不能假设列表接口能读回全部钩子；备份方式、替换语义和合并策略需要先确认。

### 6.2 `preview.yml` 示例

以 IDE 根目录为 `/workspace` 为前提。默认由原生钩子自动启动，关闭 IDE 自动运行以避免重复，保留运行入口。需要双入口且幂等性验证通过后，才考虑开启 `autoOpen`。

```yaml
apps:
  - name: workspace-services
    root: .
    run: bash /workspace/.keepalive/boot.sh
    port:
    autoOpen: false
    autoPreview: false
```

真实端口、多服务映射和预览需求，应按最终应用确定。

## 7. 安全验收与回滚

### 7.1 无害探针

保存原配置后，临时注册只记录时间戳的 `Start` 命令。下面命令运行于 Linux 工作空间钩子，不是在本机 PowerShell 中执行：

```sh
mkdir -p /workspace/.keepalive && date -u '+%Y-%m-%dT%H:%M:%SZ lifecycle-start' >> /workspace/.keepalive/lifecycle-start.log
```

不要将探针同时放进 `preview.yml` 或其他启动入口，以免假阳性。

1. 获得用户对真实停止、启动测试的确认，选择维护窗口。
2. 保存原配置，记录时间、空间状态和原标记文件内容。
3. 注册探针；API 成功只说明配置被接受，不代表执行成功。
4. 关闭 IDE 和自动浏览器，停用干扰性 Actions；协调 Worker 自动唤醒，避免测试抢跑。
5. 让空间真正从停止转为启动，不用对运行态重复调用代替。
6. 不开 IDE、不跑 Actions，等待启动完成并记录时间。
7. 测试结束后再读取日志，确认与本次启动对应；读取行为不能算作触发证据。
8. 验证执行环境、超时和进程存活后，换成正式脚本。
9. 检查重复调用不产生重复进程，恢复测试期间调整的保活策略。

### 7.2 成功标准

| 检查项 | 成功标准 |
|---|---|
| 配置接受 | 修改请求无权限或参数错误 |
| 原生触发 | 无 IDE / Actions 的真实启动中产生时间戳 |
| 服务健康 | 正式脚本启动后，进程与健康检查通过 |
| 进程存活 | 钩子结束、浏览器关闭后服务持续运行 |
| 幂等性 | 多次调用不产生重复进程或端口冲突 |
| 保活独立 | 不依赖 Actions 定时任务，心跳链正常 |
| 可回滚 | 恢复原生命周期及预览配置，仅撤销本方案变更 |

钩子不可用时先恢复原配置、保留现有保活，再评估浏览器备选，不自动升级付费服务或执行破坏性重建。

## 8. 待验证事项

回滚范围需要分开记录：`lifecycle.py restore --plan ...` 只恢复腾讯云 Lifecycle 基线；安装器生成的 `rollback` 命令只恢复 `/workspace` 与 `/usr/local/share/supervisor` 中本方案的受管文件。文件 rollback 在当前受管文件被外部修改、符号链接或备份完整性失败时拒绝执行，不删除 `start.d`、日志或其他用户文件。新版安装状态绑定备份 manifest 摘要；旧安装器生成的未认证 state/backup 不静默迁移，应使用当次 Actions 保存的原始 rollback 命令或人工核验后重装。`KEEPALIVE_INSTALL_OK` 只表示文件事务完成并通过本地读回校验，不表示活动 Supervisor 已加载 include，也不表示 Lifecycle 钩子已经执行。网络超时或断线后的 Lifecycle 结果可能未知，必须人工检查后再决定是否进行文件回滚。

1. 目标空间类型、权限是否支持并实际执行 `Lifecycle`。
2. 配置何时生效，修改对运行态空间是否有即时副作用。
3. 如何安全读回原配置，未指定阶段及原命令是保留还是替换。
4. 执行用户、权限、挂载就绪、命令顺序、网络及失败处理。
5. 钩子超时、子进程清理、适合长驻服务的管理方式。
6. 对运行态调用 `RunWorkspace` 的效果，不能推定每天调用一定重启。
7. 最终服务、命令、端口、健康检查和备份要求。

## 9. 最终建议与文档验收

**首次 Actions 安装文件并注册 `Lifecycle.Start`，后续由腾讯云原生开机钩子启动服务。** `preview.yml` 保留 IDE 运行与预览职责，不再承担唯一开机触发责任。先无害验证，再接入正式服务。

- [x] 引用腾讯云官方文档，并用官方 SDK 交叉核对。
- [x] 区分文档已确认能力、历史实现记录及待验证行为。
- [x] 给出 Evidence → Finding → Path 链与上下文明确的示例。
- [x] 覆盖初始化、启动、幂等保护、验收与回滚。
- [x] 不含管理密钥、访问 token 或密码。
- [x] 本次仅生成文档，不改部署代码或线上空间。
- 实机钩子测试与正式部署尚未执行，需后续确认。
