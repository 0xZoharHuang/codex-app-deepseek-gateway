# 同源原生程序包：0.2.0 / P20

默认候选采用官方 App 已支持的裸 `CODEX_CLI_PATH` override，交给同一版本开源原生 runtime 的 Legacy 分支执行。固定 App 的显式 override 会清除 RegisteredCore 环境选择器；这里不再为了请求 Core 模式而添加另一套身份或信任规则。

Native P20 同一次标准构建生成六个程序：CLI、Code Mode Host、command runner、setup helper、managed-deny probe 和 sandbox service。原生目标、Rust/MSVC/profile、源码与官方 V8 sandbox 输入继续使用原锁；锁中的 `cadsg-native-msix` 字段只参与选择六个构建目标，不写入强制 RegisteredCore 的编译参数。真实模式仍由原生启动逻辑和 OS 身份决定。

`cadsg-native-source-bundle` 明确声明 `runtime_mode=legacy`，是文件分发格式，不声明已安装 MSIX、OS package family、签名或 service。本版的新构建使用已审阅的 P20 锁，打包时校验完整六目标库存。旧 P7 四目标加官方本机 sidecar 的路径继续按旧规则校验，新的六目标包不会复制或替换其自建 Code Mode Host 为官方 sidecar。所有原生程序属于同一源码与工具链产物。

App 本体继续核对原固定官方签名、文件与版本。prepare 仍绑定其原生插件资源；每个自建二进制都以输入清单 SHA、文件 SHA、大小和路径闭合，不接受混合 producer、额外文件、缺失 helper、被改写的 host，或把无包文件伪称 RegisteredCore。已有同 home 的 RegisteredCore owner 仍阻止 Legacy 切换，必须走原生卸载流程。

源码比较显示，模式差异集中在部署/准备、受信服务和 runner 启动别名；权限解析、runner 传输、实际工具及会话调度继续由原生代码负责。该结论不是完整行为等价的证明。稳定发布前仍须完成原验收矩阵，尤其是全新只读工作区、文件/网络允许与拒绝、审批、Code Mode、原生子任务、冷恢复、压缩/记忆，以及实际 App 中的复杂任务。发现模式相关行为缺口必须继续修复，不能删掉对应验收项。

现有 P7 的真实内核回读还确认：官方 App 与通过裸 override 启动的自建 backend 都继承 `OpenAI.Codex` 的 package family/AUMID。这不把自建文件变成官方包内文件，也不表示开启 RegisteredCore；文件路径、哈希、owner/session 和实际模式仍须独立验证。新格式不伪造本项目 MSIX 身份。

Windows 上只读检查 CurrentUser/My 与 LocalMachine/My 的 code-signing EKU 证书，当前没有可用项。MSIX 草案保留为独立部署提案，没有导入证书、修改信任、Developer Mode 或 Defender。此文件路径沿用原生 helper 准备流程；不另写沙箱、工具调度、审批器或 agent loop。

早期六目标格式的软件记录为 259 项通过，其中包含六目标来源投影、归档完整性、旧格式回归、未签名文件不得自称 Core、混合 host 拒绝和准备阶段保持自建 host。P20 的实际 App 运行结果以对应发行记录为准。
