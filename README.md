# Codex App DeepSeek Gateway（CADSG）

让官方 **Codex 桌面 App 使用固定版本 DeepSeek**。界面、主任务、原生子智能体、工具、审批、沙箱、终端、会话和恢复仍由 Codex 管理；CADSG 连接模型协议、传输、认证和明确的数据后端。必要的开源 runtime 补丁集中在兼容边界，不另写 agent loop、审批器或工具执行器。

```text
官方 Codex App（独立 profile）
        |
        v
Codex 原生 runtime + 固定最小补丁
  | 任务 / 子智能体 / 工具 / 审批 / 沙箱 / 会话
  |
  +-- 模型请求 --> 本机 CADSG --> OpenRouter --> 固定 DeepSeek
  |
  +-- 原生 web.run --> 显式网页 / 图片索引 / 天气数据后端
```

本项目与 [CDS](https://github.com/0xZoharHuang/codex-deepseek-runtime) 独立：CDS 面向 CLI，CADSG 面向桌面 App。这里不通过 CDS 启动 App，不分发官方闭源 App，不迁移 OpenAI 会话；安装、数据和项目凭据项与现有 App/CDS 分开。

本版源码对应 CADSG 0.2.0。Windows 安装使用同一发行页中的网关包、P19 原生包和外部校验索引。P19 扩展长上下文请求恢复、取消和 Windows 权限处理，并把本地压缩接入官方可选诊断。实际验收范围和已知限制见[资格索引](docs/qualification-status.md)。0.1.0/P7 的历史结果单独保留。

## 固定组合

| 项目 | 范围 |
| --- | --- |
| 宿主 | 已验证 Windows 10 x64，19045；目标用户的普通交互会话 |
| Python | 本轮资格使用独立 CPython 3.13.3 x64；其他构建须按实际兼容检查 |
| 官方 App | `26.1023.6621.0`，正常入口校验官方签名、包身份与资源 |
| 原生 runtime | `0.165.0-alpha.1.2`；上游 `720fec862a608ae1e5878d1eecb04fb93192e93c`；P19 精确源码锁，六个同源程序 |
| 模型请求 ID | `deepseek/deepseek-v4.1-flash-1017` |
| 上游版本身份 | `deepseek/deepseek-v4.1-flash-20261017` |
| 协议依赖 | CDS `0.6.10`，固定提交 `6d6ccd32c92d06fc52ecd72712140f24496c30a2` |

当前 Windows 结果不外推为 Linux/macOS 桌面 App、Win11 MXC 或系统安全桌面已验收。App 自动更新后需要重新确认组合，修改配置中的版本字符串不能代替兼容验证。

## 开始使用

完整的逐步命令在 **[Windows 安装与首次启动](docs/windows-quickstart.md)**。顺序是：

1. 从 [v0.2.0 发行页](https://github.com/0xZoharHuang/codex-app-deepseek-gateway/releases/tag/v0.2.0) 获取网关包、原生包和外部 `SHA256SUMS.json`，核对下载字节；下载可用性与最终发布状态以该页为准。
2. 用独立 Python 3.13 正常 Install，再显式 Activate；`setup-credentials` 将 key 写入当前 Windows 用户的项目专用凭据存储。
3. **把已核原生 tar 解压到永久版本目录**，再 prepare / bind。P19 包内的 Code Mode host 与 CLI 同源构建，prepare 校验该 host、官方 App 包身份和插件资源后生成本机清单。
4. 从这次原生二进制的 `debug models --bundled` 生成匹配 catalog，正常 app-configure、service-start、app-check、app-launch。

```text
%LOCALAPPDATA%\codex-app-deepseek-gateway\
  managed-install\releases\<release ID>\venv
  native-runtimes\<不可变版本目录>
  gateway.json / protocol.sqlite / web-data
%LOCALAPPDATA%\codex-app-deepseek-app\
  config.toml / sessions / electron-user-data
```

安装不会直接启动 App。按普通权限运行；不要复用其他 App/CDS 的 Python 或 venv，不改全局 PATH/代理，不全局结束 `codex.exe`。若启动返回 pending、timeout 或 unknown，先用 **同一次 `app-status` / 实际 run ID** 回读，不能据此重复 launch。

## 模型与数据后端

普通请求默认 `deepseek`。可在排空任务、正常停止本项目服务后，明确选择已允许的 `ordinary_default_provider: "morph/fp8"`；这是同一个固定模型的显式兼容提供方，失败不会自动切换。严格 schema、特定原生历史和显式串行工具有各自固定路线，不能靠改变工具语义规避失败。操作与健康检查见[安装指南](docs/windows-quickstart.md#模型和网页后端)。

文本后端可选 `openrouter_exa` 或 `firecrawl`；图片可选关闭、`wikimedia_commons` 或 `firecrawl`。Firecrawl 图片要求文本后端也选 Firecrawl。后端不在失败后隐式互换；keyless 服务的配额、限速和可用性由服务方决定。

- recency 连接到 **UTC 日历日期范围**，不承诺精确滚动时间窗或服务端日期准确率。
- 城市/国家是地理偏置，**不是地理围栏**；原始 query 不追加地点。
- 搜索 timezone、图片 recency 明确 unsupported。图片 live/cached/indexed 的索引元数据不等于图片缓存快照、索引新鲜度或像素已取回。
- open/find/click、引用归属、错误、取消和恢复仍属于本地 runtime 接线；不能统统归入“云权益”而排除。
- 天气是带来源的 forecast，不是实时传感器观测。金融报价、体育等 OpenAI 私有 typed feeds 未接入，普通网页片段不冒充这些接口。

参见[网页合同](docs/firecrawl-search.md)、[天气](docs/weather-service.md)和[图片传输](docs/media-transport.md)。图片 URL/元数据、实际像素传输、App 显示和模型视觉理解是不同层；模型本身的视觉质量不由网关创造。

## 日常状态、升级与回退

```powershell
& $cdag diagnose --install-root $managed
& $cdag health --port 42817
& $cdag app-status --install-root $managed
```

`diagnose` 描述磁盘配置/版本，health 描述运行服务加载的配置，实际 App/native 路径与哈希才证明加载了哪个 runtime。三者不能互相代替，也不代表模型 generation 已成功。

升级先排空任务、正常退出本项目 App、停止本项目服务，保留旧 release、原生目录、绑定和检查点，再按正常入口安装/选择新版本。Rollback 恢复登记的 App 配置，不自动切回旧 gateway release；卸载默认保留会话、工程、凭据和数据。完整步骤及冲突处理见[升级、回滚与卸载](docs/windows-quickstart.md#升级回滚与卸载)。不要用旧数据库或整份旧配置覆盖后来工作。

## 来源与交付

六个纯协议模块和一个带 PSF 许可的 process-local Windows HTTP 清理 helper 来自固定 CDS；不复用其 launcher、监督器、线程管理或工具执行。依赖锁、原生补丁、许可证和实际范围见[原生构建](docs/native-build.md)、[依赖清单](release/dependencies.windows-py313.json)及[验收状态](docs/qualification-status.md)。

最终交付索引是 **[资格状态页](docs/qualification-status.md) + 发行页外部 `SHA256SUMS.json`**。后者作为单独资产在最终归档后生成，不回写参与 wheel METADATA 的源码或被其校验的 zip/tar；README 不硬编码自身网关包或 manifest 的哈希。先从可信仓库发行渠道取得索引，再核下载文件；本地现算一个 hash 只能证明本地字节，不能自证来源可信。

原始回执、会话、配置备份和请求上下文私有保留，不上传 key、token 或完整模型上下文。已知原版 Windows `restore_worktree` 缺陷保留为负向基线，不宣称 Restore 正向通过。
