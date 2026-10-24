# Codex App DeepSeek Gateway

让官方 Codex 桌面 App 使用固定版本的 DeepSeek，同时保留 App 的界面、
原生主线程与子智能体、工具执行、审批、沙箱和会话机制。

**当前状态：开发及验收中，尚未稳定发布。** 已在真实 Windows App 中完成原生
工具调用、文件审批、写入、回读和失败修复；完整能力矩阵仍有未关闭项。
本仓库不会用单元测试数量、模型选择器或一次成功回复来证明完整等价。

```text
官方 Codex App
  ├─ 界面 / 项目 / diff / 终端
  ├─ 原生主线程 → 子智能体 → 孙智能体
  ├─ 原生工具 / Code Mode / 审批 / 沙箱 / 会话
  └─ 模型及独立网页后端请求
              |
              v
       本机专用网关
         ├─ Responses、SSE、工具历史协议转换
         ├─ 明确的主模型和辅助请求路由
         ├─ 原生 web.run 对应的 alpha/search 后端
         ├─ 私有图像文件传输及生命周期（开发中）
         └─ OpenRouter → 固定 DeepSeek
```

网关不执行 shell、不运行补丁、不决定审批、不安排子智能体，也不另写 agent loop。
这些工作继续由 Codex 原生运行时负责。

## 与 CDS 的关系

[CDS](https://github.com/0xZoharHuang/codex-deepseek-runtime) 面向 Codex CLI；
本项目面向官方桌面 App，两者独立安装、状态和进程。
当前仅固定依赖 CDS 精确提交 `83056e7cf320461c30a609e911f3bcf0bea41be0`
中的纯协议转换模块及相关数据契约；不复制其启动器、进程监督器、原生线程管理
或工具执行逻辑。依赖源码归档在 `pyproject.toml` 中同时固定提交和 SHA256。

## 固定验收组合

| 组件 | 当前基线 |
| --- | --- |
| 宿主 | Windows 10 Pro 19045 |
| 官方 App | 26.1016.8120.0，官方签名 MSIX |
| App 内置 runtime | 0.163.0-alpha.11.1 |
| runtime 源码 | `680c4ba93ec79c292287fb9e0b627dc7a76e3b5e` |
| 模型请求 ID | `deepseek/deepseek-v4.1-flash-1017` |
| 官方 canonical 模型 ID | `deepseek/deepseek-v4.1-flash-20261017` |
| 网关 | 0.1.0.dev0，尚无稳定发布 |

一次 App 更新、runtime 更新或依赖更新都会形成新的待验证组合。旧组合的通过
结果不会自动延伸到新版本。模型质量、模型本身的模态限制以及云端账户权益与
本地 runtime 能力分开记录。

## 为什么有最小原生补丁

真实运行暴露了 Windows 10 legacy sandbox 的共享状态问题：机器级固定沙箱
账号和网络规则被多个 `CODEX_HOME` 共用，账号密码却分别保存在各自目录。
新环境 provisioning 会让其他目录的旧凭据失效；只分开配置目录不足以隔离。

本项目正在验证一份开源 runtime 补丁，将沙箱账号、组、网络对象和清理归属绑定
到真实用户及实际 home 目录身份，继续使用原生令牌、权限和执行算法。
不通过 full access、禁用审批或切换到更弱的沙箱来获得通过结果。
补丁必须实际被 App 加载并通过验收后才可作为交付方案。

- [隔离机制与剩余验证](docs/windows-sandbox-isolation.md)
- [补丁及逐文件来源](native/windows-sandbox-isolation-source-manifest.json)
- [完整验收矩阵](docs/acceptance-matrix.md)

App 的 `CODEX_CLI_PATH` 和 `CODEX_HOME` 入口已经过固定版本源码核对。
Windows 的进程级启动方式仍在验收，尚未把测试 helper 当作稳定 App 启动器。
原版 App 包和既有 CDS 安装保留，不修改其他工作的冻结安装。

## 路由与凭据

普通推理优先使用 OpenRouter 的官方 `deepseek` 路由。
严格结构化输出等请求可能需要同一个固定模型的兼容提供方；只有明确列入
`allowed_providers` 的路由才能使用。请求失败后不会隐式换模型或提供方。

| 请求 | 明确路由方式 |
| --- | --- |
| 主任务与原生子智能体 | 固定 DeepSeek 模型 |
| Guardian 的内部 `gpt-5.6-luna` 名称 | 明示为原生兼容别名，实际仍请求固定 DeepSeek |
| 记忆抽取、记忆合并 | 通过原生配置明确指定固定 DeepSeek |
| 本地压缩 | 使用原生当前模型及压缩机制 |
| 严格 JSON schema | 仅在显式允许时选 `morph/fp8` |
| 需要串行工具保证的请求 | 仅在显式允许时选 `primeintellect` |

请求路由与实际服务商分别记录。上游没有返回身份时记为未知，不能把
`provider.only` 当成实际服务商证据。可通过 OpenRouter generation metadata
进行只读回查；回查不会重放推理。

上游 key 仅保存在 Windows 专用凭据项 `ai.codex.app-deepseek.openrouter`。
App 通过官方 command-backed authentication 获取另一份本地网关 token；
正式安装配置使用已校验的私有 Python `-I -m codex_app_deepseek.cli auth-token`，避免继承的 Python 环境或当前目录改变认证模块。
上游 key 不进入 App 提示、配置文件、命令行参数或仓库。

## 安装与操作文档

稳定发布前请以开发及验收资料为准。安装器只接受固定 manifest 与完整离线 wheel
集合，并校验全部 SHA；默认不会启动网关、修改 App 配置或初始化凭据。

- [安装、版本检查、启动、停止、回滚及卸载](docs/lifecycle.md)
- [使用正式安装版启动、检查和观察私有 App](docs/installed-app-entrypoints.md)
- [App 配置与三方回滚](docs/app-configuration.md)
- [原生网页接口和后端边界](docs/web-runtime-path.md)

安装器的 Install / Activate 结果会返回已安装命令的绝对路径。将它保存为 `$cdag`，无需修改全局 PATH。正式安装后的常用入口：

```powershell
$cdag = '<安装器返回的 command>'
& $cdag --version
& $cdag setup-credentials
& $cdag service-start
& $cdag diagnose
& $cdag app-check
& $cdag app-launch
& $cdag app-status
```

`setup-credentials` 交互读取 key，不把 key 放入命令参数。
`health` 只验证本地网关，不产生模型请求。`auth-token` 是 App 内部认证 helper，
其 stdout 是本地凭据，不能将其输出复制到日志或诊断报告。

首次使用先按正式入口文档完成 `app-prepare-runtime`、`app-bind` 和 `app-configure`。固定 App 基线与本项目启动脚本随 wheel 交付；native manifest 及目录继续作为明确的独立发布输入。`app-configure` 使用安装版认证命令、保存自有 catalog 并登记回滚检查点。低层 `configure-app` / `restore-app` 仍可显式使用；`configure-app` 的默认目标已改为私有 App home。正式 App 启动前还需
经过 runtime 版本、身份隔离和实际加载路径检查，不能仅凭配置写入成功继续发布。

## 验收如何判断

每个能力项都有独立的原始记录、断言和适用条件。开发测试、真实上游请求、
原生 CLI 行为、App GUI 行为和最终工程结果分别记录。

已经观察到的 App 本地文件流程包含：两次网页契约拒绝、原生沙箱目录权限失败、
单文件人工审批、原生工具等待、读取发现多余换行、模型修复后精确回读。
这些失败保留在证据中，最终成功不会抹去中间问题。该轮九次推理的 generation
回读均确认实际提供方为 DeepSeek、模型为固定版本。

网页后端、Guardian、多级原生协作、记忆与压缩、复杂工程任务、中断恢复、
故障副作用和完整安装卸载仍须逐项完成，详见验收矩阵。

## 数据、回滚和清理

- 网关仅监听 loopback，所有服务端入口要求本地认证。
- 配置检查点和图片缓存使用专用私有目录；凭据不落入这些文件。
- 配置回滚只还原本项目拥有且未被用户改变的字段，保留后来添加的项目与设置。
- 停止服务必须同时匹配 PID、创建时间和归属；不按进程名称全局结束 Codex。
- 卸载保留 App 会话、用户工程和凭据，删除范围以本项目发布清单为准。
- 原始验收数据在 `.evidence/`，构建下载在 `.artifacts/`，两者都不提交 Git。
- 公开报告必须去除原始提示、企业资料、凭据和私有请求标识。

只有完整验收关闭且环境整理完成后，才创建公共仓库并推送稳定交付内容。
