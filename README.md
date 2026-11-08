# Codex App DeepSeek Gateway（CADSG）

CADSG 让 Windows 上的官方 **Codex 桌面 App** 通过 OpenRouter 使用固定版本的 DeepSeek。你仍在 App 中选择工程、审阅 diff、批准命令、恢复会话和使用子智能体。这些操作由 Codex 的原生 runtime 执行。网关负责模型协议、认证和提供方路由。

```text
官方 Windows Codex App（独立 profile）
                |
                v
           原生 Codex runtime
                +-- 文件、终端、审批、沙箱、会话、子智能体
                +-- 模型请求 --> 本机 CADSG --> OpenRouter --> DeepSeek
                +-- web.run --> 已配置的网页、图片索引或天气服务
```

CADSG 面向桌面 App。[CDS](https://github.com/0xZoharHuang/codex-deepseek-runtime) 面向 Codex CLI。CADSG 的安装、App profile、会话目录和 Windows 凭据项独立存放。官方 App 通过官方渠道安装。已有订阅使用的 App 可以继续使用它原来的 profile。

## 固定组合

| 组件 | 0.2.0 对应的版本 |
| --- | --- |
| 系统 | Windows 10 x64，build 19045，目标用户的普通交互会话 |
| 桌面 App | 官方签名的 `26.1023.6621.0` |
| 原生 runtime | `0.165.0-alpha.1.2`，上游提交 `720fec862a608ae1e5878d1eecb04fb93192e93c`，P20 Windows 构建 |
| 网关 Python | 本组合使用独立 CPython 3.13.3 x64 |
| 模型请求 ID | `deepseek/deepseek-v4.1-flash-1017` |
| OpenRouter 版本身份 | `deepseek/deepseek-v4.1-flash-20261017` |
| 协议代码 | CDS 0.7.0 源码的固定提交 `e83b4b3fa79714fa77b66ba4e96ae10abb9d6aad` |

App 和内置 runtime 改版后需要重新核验。启动入口会检查官方包身份、签名、关键文件及实际后端。P20 的 Windows 改动覆盖旧回环过滤规则的升级、明确允许的本机连接和沙箱 helper 的内容校验。[Windows 原生修复](docs/windows-loopback-reconciliation.md) · [资格与版本记录](docs/qualification-status.md)

## 安装和首次启动

1. 从同一发行页下载网关 ZIP、原生 tar.gz 和独立的 `SHA256SUMS.json`，核对两个归档的 SHA-256。[0.2.0 发行页](https://github.com/0xZoharHuang/codex-app-deepseek-gateway/releases/tag/v0.2.0)
2. 用独立 Python 执行网关包中的 `Install`，再显式 `Activate`。运行 `setup-credentials`，在 Windows 的项目专用凭据存储中设置 OpenRouter key。App 使用另一份本机认证。
3. 将已校验的 P20 原生包解压到永久的版本目录。依次运行 `app-prepare-runtime`、`app-bind` 和 `app-configure`，选择与这版 App 对应的模型目录。
4. 启动本机网关，通过 `app-check` 后运行 `app-launch`。进入真实 App 后，`app-status` 可回读当前实际加载的原生程序。日常安装使用预编译包。已有图片缓存升级时，先停止该安装的 App 和网关，再按维护步骤迁移格式。[完整 PowerShell 步骤](docs/windows-quickstart.md) · [图片缓存迁移与回滚](docs/image-cache-format.md)

如果工程由另一个应用创建在私有目录里，除了工作区权限，还应核对私有父目录的元数据权限。宿主可以在自己创建目录时给已核验的沙箱组设置非继承的 `READ_ATTRIBUTES | SYNCHRONIZE`。[私有工程与错误 267](docs/windows-private-workspace.md)

## 模型和联网

普通模型请求优先选择官方 `deepseek` 提供方。同一固定模型也支持已验证的显式兼容路线，例如 `morph/fp8`。切换普通请求路线前，先让当前任务结束并正常停止本项目服务，再修改项目的 `ordinary_default_provider`，重启后回读生效配置。失败会保留原来的错误和提供方。压缩、记忆和审批等辅助请求有明确的模型映射和运行回执。[模型与后端配置](docs/windows-quickstart.md#模型和网页后端)

原生网页操作可以搜索、打开网页、页内查找和跟随链接。文本搜索可配置 OpenRouter Exa 或 Firecrawl，图片索引可配置 Wikimedia Commons 或 Firecrawl。需要密钥的后端使用单独的凭据。网页、天气和图片数据以所选服务的实际覆盖为准。模型质量、模型模态、OpenAI 订阅和私有云数据服务另行列明。[网页](docs/firecrawl-search.md) · [天气](docs/weather-service.md) · [图片传输](docs/media-transport.md)

## 日常诊断和回滚

```powershell
& $cdag diagnose --install-root $managed
& $cdag health --port 42817
& $cdag app-status --install-root $managed
```

`diagnose` 核对安装文件和磁盘配置，`health` 查看运行中的网关，`app-status` 核对真实 App 和后端身份。模型路由另有 OpenRouter generation 回读。这几份证据分别回答“装了什么”“实际运行了什么”和“哪个模型处理了请求”。

升级前先排空任务，正常退出本项目 App 并停掉自己的服务。保留旧 release、原生版本目录、配置检查点及绑定备份。恢复绑定时按变更顺序倒序执行 `app-restore-binding`，遇到后续改选会停止并保留现场。网关版本切换与配置回滚分别执行。默认卸载保留用户会话、工程、数据和凭据。[升级、完整回滚和卸载](docs/windows-quickstart.md#升级回滚与卸载)

## 验收和构建来源

[资格索引](docs/qualification-status.md) 区分真实桌面操作、原生 API / SDK 验收和组件测试，并列出原始失败、适用条件和云端边界。构建记录固定了六个 Windows 原生程序和网关包各自的来源。原生 runtime 继续负责沙箱、工具、审批和线程管理。CDS 的复用范围集中在协议与 Windows HTTP 兼容代码。[原生构建](docs/native-build.md) · [依赖清单](release/dependencies.windows-py313.json)
