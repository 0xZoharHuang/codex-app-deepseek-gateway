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

## 发布状态与固定组合

| 组合 | 当前交付范围 |
| --- | --- |
| P7 / `v0.1.0` | 公开 Windows 安装包。固定 Windows 10 x64 19045、官方 App `26.1023.6621.0` 和原生 `0.165.0-alpha.1.2`。按同一发行页的外部 `SHA256SUMS.json` 核对网关 ZIP 与原生 tar.gz。 [发行页](https://github.com/0xZoharHuang/codex-app-deepseek-gateway/releases/tag/v0.1.0) · [该版本的 Windows 安装说明](https://github.com/0xZoharHuang/codex-app-deepseek-gateway/blob/v0.1.0/docs/windows-quickstart.md) |
| P167 / O167 | 公开候选源码与构建流程。固定原生 `0.167.0-alpha.3.2`。当前可下载的稳定组合仍为 P7。 [P167 候选分支](https://github.com/0xZoharHuang/codex-app-deepseek-gateway/tree/codex/p167-hosted-ci-r626) |
| P169 / O169 | 本分支的受限 Windows App 源码资格。固定官方签名 App `26.1111.2228.0`、原生 `0.169.0-alpha.11.1` 和独立 CPython 3.13 x64。真实 App 已验证项目首次普通命令的 ACL 顺序、原生 Guardian 放行与拒绝、两阶段记忆及冷进程无工具召回。 [O169 实机验收范围](docs/qualification-status.md#o169-真实-windows-app-验收) |

P169 的公开二进制交付还需要由公开源码构建并配对校验的网关与原生归档、可获取的锁定依赖、外部资产校验索引，以及消费者从下载到正常隔离安装和实启 App 的复测。 [P169 依赖锁](release/dependencies.windows-py313.json)

## P169 私有资格环境的安装与检查

以下入口适用于获准的 Windows 验收桌面及其独立用户数据目录。启动器校验 Windows 会话 1 的 `WinSta0` 专用 `CADSG-P169-Acceptance-D857` 桌面、官方 App 签名与资源，以及本机实际加载的后端。O169 使用同一源码锁构建的六个原生程序，装配入口为 `scripts/package_native_release.py --development-qualification`。 [源码锁](native/p169-source-lock.service-restore-acl-order.draft.json) · [私有准入输入](native/p169-admission.draft.json)

1. 准备验收用离线网关包、可信 `release.json` SHA-256、同组合的原生归档及其 manifest 摘要。用包内 `install_gateway.ps1` 执行 `-Action Install -ManifestPath $gatewayManifest -ManifestSha256 $gatewaySha -Wheelhouse $wheelhouse -PythonExe $basePython -InstallRoot $managed -DataHome $state`。核对成功回执后执行 `-Action Activate -ReleaseId $releaseId -InstallRoot $managed`，其中 `$releaseId` 为安装回执中的 `release_id`。用激活回执中的 `command` 作为 `$cdag`，运行 `& $cdag setup-credentials` 在 Windows 凭据存储中设置上游认证。
2. 将已核验的原生归档解压到验收环境的独立永久版本目录。在专用桌面用 `app-prepare-runtime --install-root $managed --native-manifest $rawManifest --native-manifest-sha256 $rawSha` 生成本机清单。将回执中的路径和摘要赋给 `$preparedManifest`、`$preparedSha`，再运行 `app-bind --install-root $managed --native-manifest $preparedManifest --native-manifest-sha256 $preparedSha`。在独立 home 中从同一个 `codex.exe` 读取 `--version` 与 `debug models --bundled`，由安装模块的 `catalog.build_catalog` 生成匹配目录。接着运行 `app-configure --install-root $managed --catalog $catalog --catalog-sha256 $catalogSha --port $port`。
3. 用 `service-start --install-root $managed --port $port` 启动该网关，`app-check --install-root $managed` 通过后执行一次 `app-launch --install-root $managed`。用 `app-status --install-root $managed` 回读运行。外层等待超时时，用同一 `--run-id` 回读；验收以实际 App 与原生进程身份、工具结果和状态为准。 [安装版 App 入口参数](docs/installed-app-entrypoints.md) · [图片缓存迁移与回滚](docs/image-cache-format.md)

如果工程由另一个应用创建在私有目录里，除了工作区权限，还应核对私有父目录的元数据权限。宿主可以在自己创建目录时给已核验的沙箱组设置非继承的 `READ_ATTRIBUTES | SYNCHRONIZE`。[私有工程与错误 267](docs/windows-private-workspace.md)

## 模型和联网

普通模型请求使用固定的 `deepseek/deepseek-v4.1-flash-1017`，OpenRouter 对应版本为 `deepseek/deepseek-v4.1-flash-20261017`，默认选择官方 `deepseek` 提供方。同一固定模型也支持已验证的显式兼容路线，例如 `morph/fp8`。切换普通请求路线前，先让当前任务结束并正常停止本项目服务，再修改项目的 `ordinary_default_provider`，重启后回读生效配置。失败会保留原来的错误和提供方。压缩、记忆和审批等辅助请求有明确的模型映射和运行回执。 [模型与后端配置](docs/windows-quickstart.md#模型和网页后端)

原生网页操作可以搜索、打开网页、页内查找和跟随链接。文本搜索可配置 OpenRouter Exa 或 Firecrawl，图片索引可配置 Wikimedia Commons 或 Firecrawl。需要密钥的后端使用单独的凭据。网页、天气和图片数据以所选服务的实际覆盖为准。模型质量、模型模态、OpenAI 订阅和私有云数据服务另行列明。[网页](docs/firecrawl-search.md) · [天气](docs/weather-service.md) · [图片传输](docs/media-transport.md)

## 日常诊断和回滚

```powershell
& $cdag diagnose --install-root $managed
& $cdag health --port 42817
& $cdag app-status --install-root $managed
```

`diagnose` 核对已安装文件、磁盘配置，并对照发行清单的官方 App 基线。`health` 查看运行中的网关，`app-status` 核对实际 App 和后端身份。将示例端口 42817 换成此安装的实际端口。模型路由可另用 OpenRouter generation 回读。

升级或卸载前先结束任务、正常退出此安装的 App，再运行 `& $cdag service-stop --install-root $managed`。保留旧 release、原生版本目录、配置检查点及绑定备份。用 `app-restore-binding --install-root $managed --backup $configureBackup --backup $bindBackup` 按由新到旧的顺序恢复绑定。源自同一受信包的安装器支持 `-Action Rollback -InstallRoot $managed` 恢复已登记的配置检查点，旧网关版本另用 `-Action Activate -ReleaseId $previousReleaseId -InstallRoot $managed` 选择。`-Action Uninstall -InstallRoot $managed -PythonExe $basePython` 清理当前 release 及其托管状态，默认保留用户会话、工程、数据和凭据。 [安装生命周期与回滚](docs/installed-app-entrypoints.md#stop-and-roll-back)

## 验收和构建来源

[资格索引](docs/qualification-status.md) 区分真实桌面操作、原生 API / SDK 验收和组件测试，并列出原始失败、适用条件和云端边界。构建记录固定了六个 Windows 原生程序和网关包各自的来源。原生 runtime 继续负责沙箱、工具、审批和线程管理。CDS 的复用范围集中在协议与 Windows HTTP 兼容代码。[原生构建](docs/native-build.md) · [依赖清单](release/dependencies.windows-py313.json)
