# CADSG 原生 runtime-only 包准备

这里是私有源码提案，不是已编译、签名、安装或通过 App 的发行物。活动 P9、P10/P11 锁、
原始 App 和 stock 服务均未更改。

`layout/AppxManifest.xml` 是可审阅的具体布局：只包含开源原生 runtime/service 的位置、
独立服务名、四个 console execution aliases 与本项目自制图标；没有复制闭源 UI，
也没有放入假 exe。原生资产七项清单见 `required-runtime-assets.json`，现均缺失。
Publisher `CN=CADSG Native Runtime` 是待绑定合法签名证书的提案名称，绝不是 OS 身份证明。
0.0.0.1 仅为清单草案，不改变产品版本。

原 App 支持裸 `CODEX_CLI_PATH` override，并通过原 stdio 三管道启动 app-server。
但它主动清掉 override 的 RegisteredCore 环境标记。因此计划用自己经 OS 激活的
ApplicationID 明确选择 Core/Legacy；这只是原生模式请求。服务原来的真实同 family、
同版本目录、owner/home 验证完整保留，不添加自称身份的授权例外。
现有 Python launcher 的文件路径观察规则要有独立的 package/alias 严格分支，不能
把实际进程路径与 alias reparse 文件路径相等当成通过。

本机 DeveloperMode 开关及相关显式策略未启用，不能无授权用 loose `-Register`
路线。Win10 19045 可使用现有可信链签名的标准 MSIX；`-AllowUnsigned` 仅为 Win11
功能。packaged LocalSystem service 要管理员安装，且自身及 sandbox 用户注册时都
必须能验证签名链。没有读取私钥、签名、添加证书或变更策略。

后续制包顺序（本次没有执行签名或部署）：
1. 完成 SOURCE-RESTORATION-PLAN 中同一 native0165 图的改动及编译，固定所有 PE SHA。
2. 将实际 producer 资产按 required-runtime-assets 清单填入 layout/runtime；先验证
   owner/版本/签名清单，拒绝缺失、额外或 stock 旧 service/helper。
3. 用现有 SDK MakeAppx 对完整 layout 正常制包，不使用 /nv 跳过验证。
4. 最终 Publisher 与实际合法代码签名证书 subject 完全一致；SignTool SHA256 签名、
   时间戳及证书链由最终签名步骤提供。不得为此自动导入信任或修改 DeveloperMode。
5. 原生别名/IPC、自己的服务注册/回滚及真实 App 资格分别执行；本提案不代这些证明。

`manifest-validation-only` 保留了 SDK 对草案的真实失败：首版缺少 console alias 必需的
SupportsMultipleInstances，已修；当前 MakeAppx 只报告缺少尚未编译的真实 executable。
这些是准备阶段失败证据，不能记成完整包通过。
