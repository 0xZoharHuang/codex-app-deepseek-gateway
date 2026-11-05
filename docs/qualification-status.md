# 当前资格与交付索引

本页区分软件、原生产物、实际 App 工作流和外部服务证据。0.2.0 的正式文件以对应发行页的外部 SHA256SUMS.json 核对；[v0.1.0 发行页](https://github.com/0xZoharHuang/codex-app-deepseek-gateway/releases/tag/v0.1.0) 保留 P7 组合的历史资产与索引。外部校验值在归档后生成，不回写 wheel 所用的 README。

**下列公开 0.1.0 / P7 固定组合的本地 runtime 范围已分层关闭，最终交付门仍单列。** R27 的正确 typed 图像输入、provider 媒体接受、App 内嵌 PDF 预览，以及正确目录的宿主输出读取均有新证据。原始失败和私有证据保留；本页不公开会话正文、账户凭据、机器身份或闭源 App 代码。

## 0.2.0 的来源与验收范围

0.2.0 的 Windows 原生包使用 `native/build-lock.O165-P19-source.json`，SHA256 为 `163f6e61d605adf7d7698835f33b99d48c5cb25f017b8231f5062bd0ffb0a543`。网关协议依赖固定 CDS 0.6.10，提交 `6d6ccd32c92d06fc52ecd72712140f24496c30a2`。正式资产的实际来源、安装与 App 验收结果，以对应发行页和版本验收记录核对。

沿用固定版本的 37 项合同。R17（server queue）、R35（额外 UAC/secure desktop）、R36（IAB/CUA）是条件项。R25 的原版 Windows `restore_worktree` 保留同版本负向对照。P7 的历史效果按具体组件与配置承接。本轮核验涵盖：

- 真实官方 App 加载六个同源原生程序，使用自建 Code Mode host 和 Legacy 路径。在新只读项目核对 Node/cwd、允许与拒绝，再验证 workspace-write、网络、人工审批和原生子任务。
- 96 张原 PNG、真实请求字节门槛、压缩摘要对应的输入前缀、后缀保留、取消、副作用及同 UUID 的冷恢复。普通请求和本地压缩均可用官方 trace 与网关回执核对。
- GUI 用户消息、原生 turn、网关请求、操作结果分别确认。失败响应、半截流、无稳定响应 ID 和未确认的图片租约保留各自状态。
- Guardian、记忆、自动/手动压缩与原生协作按实际变更范围补验。模型自身能力及云端权益沿用原边界。

## 历史 P9 源码候选记录（非当前验收状态）

当时记录的 `native/build-lock.json` 是 P9 十补丁 source proposal；原七补丁锁保留在 `build-lock.O165-P7-original.json`，P8 源锁另存。**该记录中的 P9 compiled、native/runtime qualified、App GUI qualified 均为 false。** 精确 source map 是从既有完整库存加已核变更推导，不冒称一次新的全量重建或运行。当前锁可供独立 source/build 准备，发布 packager 仍拒绝用 P7 的资格为 P8/P9 放行；详见[候选原生来源](native-build.md)。

CDS P29 的 Linux exact44 是其固定 0.164 测试产物的机制结果；CDS Windows 三个 helper 的 R350 production 构建也只绑定各自产物。两者都不能代替 CADSG 0.165 port 的编译、真实工具、旧 UUID/CP6 和 96 图恢复、service-station 或实际 P9 GUI。已有 P7 profile/媒体/网页证据仍只证明下列原组合，受影响路径须独立补验。

架构仍是模型事件经 CADSG 网关交回原生 runtime，再由原生工具/审批/沙箱执行；没有引入 CDS launcher、Store、监督器或另一套 agent loop。原生 grace 内允许已接受的 compaction 在取消请求之后完成，不能以请求发送时间作为“零安装”分界；新增检查点的 token/提交顺序保护属于安全细化，不是上游本来保证的恢复。

该 P9 历史记录未进行发布、版本修改或 App 切换；它不限制后续 P19 工作，也不替后续运行关闭门槛。完整候选门槛与实际入口/加载身份确认后，再更新稳定方案；原始 evidence 不进入公开安装包。

## 0.1.0 / P7 已测固定来源

| 组件 | 固定身份 |
| --- | --- |
| App / 已测平台 | 官方签名 `26.1023.6621.0`；Windows 10 x64 19045 普通交互会话 |
| Native / upstream | `0.165.0-alpha.1.2` / `720fec862a608ae1e5878d1eecb04fb93192e93c` |
| Native 完整源码 | `1125f5c87733fb267177522de600918b06a0a41489543b57c721a3bcce6d7c56`，9,516 files |
| Native build lock | `da4e194baa449c3caa4457021770876d4ddc5d663869995bd0fb1cae6eb497e6`，七个有序补丁 |
| 实际 P7 CLI | `d4ba603ccd287f4ce7ae863d9a0e0e02c2f37c2fa594d6a5ddea4df699a7b202` |
| 公共 raw native manifest | `4cd522fbdf4fede602a30537cbbfca0fd53ab36c6e8f49ea8bfd8ff50966683e` |
| 公共 raw native tar | `32b380bdeb24a09f223af9e037576566ade11018a083b90b472c75b3c58da03c`，142,231,452 bytes / 3,037 普通成员 |
| 本机官方 Code Mode host | `510687c53c57f52dbf105b8b0050f595d255ab6dafc968c00940c782aa5141c2`，只从匹配官方包物化，不公开分发 |
| 推理 | `deepseek/deepseek-v4.1-flash-1017`；已知上游版本身份 `deepseek/deepseek-v4.1-flash-20261017` |
| 纯协议依赖 | CDS 0.6.9 / `a70417460d823a906ebc7274834e2412be1ebe53` |
| CDS 固定公开源码 archive | `a6a90199358b90539495b8d1799c1afbfed75da14a121b5a03c5df2188cd07ce` |
| 由该源码可复现构建的依赖 wheel | `4f2c97fde6de3abfd42da16ffad7bc8ecf6a4567383fb8a05304fcaaa180e5d1`；离线包携带，不虚构另一个公开 wheel URL |

公开 tar 的资格对象是原始四产品与公开许可证/provenance 库存，**不包含官方闭源 App sidecar**。prepare 生成的本机绝对路径 manifest 与 Code Mode host 不重新打包上传。最终网关包哈希不内嵌本页或 README；以外部资产索引核对，不能拿自己现算的值证明下载来源可信。

## 0.1.0 / P7 已关闭的组合范围

| 层级 | 实际证据 | 结论边界 |
| --- | --- | --- |
| 安装版 Python 软件 | code-qualified source `5bf6c6a130d1f568fe909066594de34c05e1a8cd`；1,747 unique 主节点通过，0 fail/error/skip；宿主 1,742 与普通用户 5 个权限前置用例分别执行 | 另有 19 个 SubtestReport 单列；metadata 2、source focus 429 和重叠子集不相加。不是 1,768 个互异 App 行为 |
| Native 构建 | 标准同一次 Cargo 命令生成四产品，source/lock/工具链/profile/四产品身份相符；七补丁来源及定向原生断言闭合 | 编译通过不代替 App；中途资源失败与后续正常完成各自保留，不把打包器的 native_build_executed=false 解读成未编译 |
| 公开装配与许可 | 3,037 个普通成员逐项 SHA/size/type/path 校验；1,122 个 crate 许可条目及 19 份 SPDX 文本、来源与 alias 目标字节核实 | 许可来源归档与实际编译、分发库存是分层证据，不声称第三方外部认证 |
| 正常本机升级 | no-activate 安装、独立永久原生目录、正常 prepare/bind/catalog/configure/service/单次 App launch | 临时 build output 的先前 prepare 结果保留，但未作为永久生产绑定；无全局 kill、共享状态或订阅迁移 |
| 实际 P7 加载 | 正常 app-status 回读同一次启动，实际 App、主后端和另一原生后端均匹配固定签名/版本、永久路径与 P7 hash；运行服务加载显式 Morph/配置哈希 | 原 app-launch 的外层等待超时保留；实际 App 后创建，经同 run 状态确认，未重复 launch。health 不是 generation 成功 |
| 旧 profile 保留 | 110 旧会话/rollout 前缀、8 个原数据库的 53 个表 schema、既有 SQLx 账本和用户稳定行投影核对；一致性备份和旧回退物保留 | 日志/sequence 与已标识观察 cursor 的真实变化逐字段记录；不以活 DB 文件整字节不变作错误断言 |
| fresh 原生互读 | 新 P7 创建的 canonical CRLF profile → 同版本原官方 stock → P7，真实 initialize/EOF/出生时间终态和存储投影核对 | 只算实际创建并打开的 stores；lazy history 未创建不能算打开。与旧 LF 生产 profile 保留是不同控制 |
| 真实网页工程 | 当前 App 的主任务与原生 child 完成源码/官方文档交叉审查并写两份交付物；输入受保护，非 controller 代写 | 子线程继承历史中的 aborted 不计本次 Stop；child 实际新调用与继承记录分开，V2 不凭空要求 V1 close 工具 |
| 网页请求/引用 | 52 个实际操作：open 14、find 32、click 2、search/image/time/weather 各 1；13 次 Code Mode 调用、25 个原生网页事件；操作 hash、时间、session、refs 联结 | Find 为 20 次命中、12 次正常零匹配。一个并行 open 的摘要缺 lineno，有两个 nested-ID 候选；保候选，不捏造唯一归属 |
| 完整数据服务 payload | 文本与图片的实际完整 canonical JSON hash 重构均匹配真实 HTTP 200/完整响应；原 q、低 context、城市/国家、文本 recency、图片省略 tbs 全核 | 这是 source 支持的完整语义 payload hash 证明，不是 direct Alpha raw-body 捕获，也不是排序/日期精度、远程图片像素安全门认证 |
| time / weather / MCP | UTC 时间处于真实任务窗口；SF duration=1/start 省略得到当地当天 forecast，两次正式数据 HTTP 及 raw hash 匹配；两次 App MCP 只读 list_projects 均成功 | 只公开数量，不公开项目名。额外 open_in_codex 的 queued 回执不计作只读工具或 UI 已渲染 |
| 模型路由与失败 | 请求固定 canonical DeepSeek，普通 Morph 明确选择，无客户端隐式 fallback；三次独立上游 metadata GET 样本确认 Morph/固定版本 | 原本地 observed_provider=null 不补写。网页窗口只 3 样本，媒体另 1 样本；不称全窗口供应方均独证；reasoning-only 无 final 的结构化失败仍 failed，不能放松 validator。usage={} 不表示免费/未计 token |

最终仅文档后继不能把未经检查的字节自动算成已资格。最终组包需将 22 个 Python 模块、native 输入/补丁/锁、依赖清单等与上述 code-qualified 来源逐字核对；确有产品变更时按影响补验。公开 tag 与外部校验索引提供最终来源，不在源码中强求“本页包含自身最终 commit/SHA”的固定点。

## R27 媒体分层关闭，原失败保留

- **实际 PDF → 原生 screenshot → App 显示**：本轮真实打开公共 W3C PDF，raw SHA `3df79d34abbca99308e79cb94461c1893582604d68329a41fd4bec1885e6adb4`。page 0 经 pypdfium2 5.14.0 / PDFium 156.0.8076.0、scale 2 渲染为 1190×1684 / 14,763-byte PNG，SHA `380c744a3a9089315c94da97ed8454ba608cbc60f5745907fa254dcd7e35ee40`；保留 BLOB、本地文件、native ref/Markdown 同源。实际 App 截图与 UIA 对上白色纵向页和 Dummy PDF file 内容，不是图标或纯 Markdown。没有用旧 0.163 同哈希图替当前执行，也没有 controller 重截图替代。
- **view_image → typed 输出 → provider**：首轮真实读到了原 PNG，但模型 `text(JSON.stringify(r))` 只输出文本，未算 typed 链通过。另一次同线程纠错直接使用原生 `image(r)`：helper 将原 DTO 规范为 typed PNG，119-byte、32×24、SHA `09632d3f1f7d25282d2ec464c63231899a87274cb453bdb1f46fd83f0ac6c45b` 保持原字节。实际 incoming call/output 哈希与 gateway receipt 对上，原协议保留类型/调用 ID/parts；小请求走 lossless inline，无 file URL/上传转换。真实 HTTP 200/完成对应的一个上游 metadata 样本确认 Morph、固定模型版本、`num_media_prompt=1`。这不是整个窗口供应方独证，也不评价模型识图质量。
- **传输计数的解释**：低于 prevention threshold 的 source fastpath 直接发送原始编码字节，并不扫描 image_occurrences；该路径的计数零/Files consumption not_applicable 不能误当没有 inline 图片。证据来自 typed bytes、incoming 完整哈希、明确 source 流程、实际 HTTP 和上述单样本 metadata；没有注入 tap 或声称捕获完整上游 wire body。
- **宿主 JSON 可读性**：原报告的拒读来自 Root 验收夹具将目录按文件权限处理、漏掉可继承的 host/SYSTEM grants。shipping 的 14 个目录/6 个文件 helper 调用无同类误用，未改 runtime。另建正确目录后由模型原生 apply_patch AddFile 一次，普通与提升宿主读到相同的 122-byte JSON、SHA `817c965ef227621ef736f83c43bbc8b27b5392991e13435df94853a8ca4eebf6`、owner 与 SDDL；owner 仍是原 sandbox 身份。没有修改原 R299 报告、ACL 或 owner。这只关闭夹具前置/宿主读取回归，不拿小 JSON 代替复杂任务。
- 扩大预览按钮的 Invoke NoPattern 原错误保留，不称扩大成功；本次正向为真实内嵌预览。最初的文本化输出、坏夹具、元数据尾缀错误、结构化模型失败与取消均保原件，不因最终成功抹掉。
- 实际任务后，只对自己改动的两个网页操作者叶字段按 fresh SHA CAS 恢复原 absence；原已 live 的 no-op 不认领/回滚，auth.command、后来新增项目 trust 和其他设置保留。原 checkpoint 不伪改成另一段历史。

## 尚为单独交付门

最终源码/双构网关包、外部校验索引与公开资产字节读回、正常生命周期交付，以及用户要求的 Windows 自有物有界整理和当时实际使用额度复核，由最终交付回执关闭。运行资格不能代替这些动作；不写成已发布、已清理或额度已重置。本页采用版本中性的外部发行索引，不把最终包 hash 反填进参与构建的 README。

## 原版缺陷与适用条件

固定原版 App + 原官方 native 的 Windows restore_worktree 返回相同 295-byte 错误；快照 blobs 存在，路径表示导致 snapshot key 不同。这一同基线负向证据保留，**不是 Restore 正向通过**；该路径作为原版负向边界保留，不从旧失败或源码相同推出正向恢复成功，与其他已验证 GUI 工作树生命周期分开。不靠 ref alias、路径替换、WSL、opaque JSON 或关闭原生机制冒充修复；正常 GUI 中其他已验证工作树行为仍按其具体范围陈述。

IAB/计算机工具、server queue 等按实际有效门分类。当前门为 false 的 SDK/API 不冒称已执行；固定 Windows 10 环境没有凭空新增 Win11 MXC、UAC 或 secure-desktop 必须通过的任务，也不能用普通 App 审批推出这些 OS 路径成功。金融/体育云 feed 单列，不能据“云权益”把所有网页或本地 API 一起排除。

## 历史范围与精确承接

历史源/配置/实际条件相同的权限、Guardian Allow/Deny、记忆提取/整合/跨会话读取、自动/手动压缩、Steer/客户端队列/goal、Hooks/diff/Undo、OAuth、同 UUID 冷恢复与旧引用等按组件相等证明承接；不写成每一项都在 P7 再跑了一遍。原生 local API getter、ZIP 或纯 fixture 不能代替 GUI/模型记忆形成。

历史说明：[R219](qualification-r219.md)、[R220](qualification-r220.md)、[R222](qualification-r222.md)、[R223](qualification-r223.md)、[R224](qualification-r224.md)。这些文档记录当时版本与失败，不能据旧的“当前”一词选择旧二进制或覆盖本页当前身份。改动触达处以新分项实测为准。

模型质量、模型固有模态能力、第三方排序/数据精度与云账户权益另列；本地协议、工具、权限、会话与真实错误传播仍在验收合同内。
