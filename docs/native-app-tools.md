# 官方本地 App Tools 的资源绑定

此集成只处理固定 Windows App 26.1103.4239.0 候选基线 随包提供的原版资源和原生 PluginManager 配置。工具服务器、工具实现、审批、延迟发现和线程调用仍由 App 及其原生运行时负责；网关没有复制这些业务逻辑。原版资源不进入仓库或公开发布包。

## 安装顺序与边界

`app-prepare-runtime` 在既有签名校验、专用 home 和空闲检查内核验 860 个资源文件的 SHA256，再把原版字节绑定到原生约定的 `CODEX_HOME/.tmp/bundled-marketplaces/openai-bundled`。固定清单仅含路径、大小和哈希；总计 35,191,141 字节。版本或文件不同会明确拒绝，不下载替代、不改保留 ID、marketplace 策略或 SDK flags。

已有完全相同的原版树可以复用；App 自己过滤过的树只有在 marketplace 严格保留原条目顺序及元数据、全部已有文件仍是原版、所列插件文件完整且本地 App Tools 五个文件齐全时才复用。复用的用户资源不归项目所有。未知文件、改过的文件和重解析路径都阻断操作。

`app-configure` 先检查 legacy 顶层 `mcp_servers.codex_app` 冲突，随后才允许任何配置或 catalog 写入。旧条目需要单独 checkpoint 并明确迁移，命令不会覆盖它。用户明确关闭 plugins、目标插件或目标 MCP 时也不会被偷偷打开。

首次配置先应用并登记 provider checkpoint，保留原有 fresh `workspace-write` 与 `on-request` 默认值；然后调用原生 `plugin marketplace add`、`plugin add codex-app-tools@openai-bundled`，通过普通 CAS checkpoint 仅启用 `plugins."codex-app-tools@openai-bundled".mcp_servers.codex_app.enabled`。管理子进程临时使用 file-only auth/MCP store，不改用户全局凭据策略，也不读取上游凭据。其他原生 approval、defer、transport 和工具元数据保持原版。

原生 CLI 修改配置后必须重新 prepare provider；只有实际变化才增加第二个 provider checkpoint。MCP 叶子 checkpoint 只由 `native-app-tools/installation.json` 认领，不登记进 provider 链。重复配置会做冷配置核验，不重复安装或制造无变化 checkpoint。

## 状态和回滚

`app-check` 使用绑定的原生 CLI 冷读取目标 MCP，核验其原版五文件缓存、启用状态、stdio 及 cache 工作目录。这个结果是配置检查；只有真实 App 启动该服务器、由模型发现并调用工具才算 App 功能验收。CLI 读取不代表 GUI、工具执行或云端资格已经通过。

回滚先沿用既有 provider checkpoint 逆序链的只读预检，再通过原生 PluginManager 移除本项目创建且仍未被用户采用的对象，最后恢复 provider 字段。配置语义、真实注释、插件缓存或用户引用发生变化时保留用户采用对象。原生删除造成的纯空白排版变化不算用户采用。未知/未完成阶段不会被伪装成成功或自动重放。

原生 `marketplace remove` 保留原版资源目录；本项目同样明确保留并记录它，而不是递归删除可能已被引用的资源。普通卸载保留用户数据；需要释放这约 35 MB 时，应在最终整理阶段先确认回执、原版哈希和所有引用，再精确处理。失败回执、配置 checkpoint 与用户后续改动不能因整理而丢失。

## 从旧签名包迁移

保留原安装及其 App 绑定、配置 checkpoint、模型目录与资源回执。App 正常退出且独立桌面可用后，先从旧网关版本按既有 checkpoint 链回滚由项目创建的 App Tools 配置。原生回滚会保留旧资源目录。只有旧回执仍明确属于本项目、目标目录与旧清单逐字节一致、配置已无该目标引用时，才可将该目录和对应的 `resources.json` 一起移到独立保留位置。用户采用的资源及未确认归属的资源应保留。

用版本匹配的原生运行时生成新模型目录，并在专用网关的显式允许列表中加入新版 App 的可视化模型名 `gpt-6.1-terra`。其他允许项、提供方策略和会话继续沿用。新绑定和准备操作仍核验签名包、原生运行时及资源的精确指纹。若迁移中止，使用原版本的回执与 checkpoint 恢复对应的绑定和配置。

## 验收层次

纯单元反例覆盖原版/过滤资源、篡改与重解析点、legacy 冲突、显式关闭、native 管理命令失败、单叶所有权、checkpoint 顺序、原生回滚和用户采用保留。另用独立 Limited HOME 运行真实 875 原生 CLI 的安装、启用、冷读取和逆序恢复；两类证据分别记录。真实 App 在没有 legacy MCP 的配置下从原生插件缓存启动并调用 App Tools，仍须独立记录并绑定最终发行版本，不能用本节测试替代。
