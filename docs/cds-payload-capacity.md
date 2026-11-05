# 长图文请求的传输容量与原生错误分类

**版本边界：** 以下保留 2026-10-23 起的历史组件研究、旧原生基线和实际 probes。当前私有 P9 已另行准备 0.165 容量/取消及 host source proposal，仍未编译或取得实际 App GUI 资格；以[当前资格索引](qualification-status.md)和[原生来源](native-build.md)分层记录，不能把本页 101 图组件或旧 680 patch 说明当作 P9/96 图恢复通过。公开 0.1.0 的固定依赖和安装资产不因此改为 CDS 开发分支。

状态：2026-10-23 的独立开发验收材料。`image_transport.py` 已完成 gateway 源码接线和独立回归，尚未替换运行中的 App 网关或已发布 CDS；原生补丁的构建验收由独立 native 记录负责。本文件不能作为 App 或 CDS 已彻底解决长图文问题的证明。

## 问题属于哪一层

下游报告的长图文历史含 1,206 items、101 张内嵌图像，重建 JSON 约 34,009,154 bytes，最后成功 input usage 435,747 tokens，低于 650,000 的原生压缩阈值。此前有一次原生 compaction 成功。这里的 34 MB 是重建历史测量，不能冒充捕获到了失败请求的完整线上 body；报错为 HTTP 413 HTML，也不能由此推断某个提供方已公布 32 MiB 上限。

两个容量需要分别处理：模型按 token 接收上下文，HTTP 链路按序列化 bytes 接收请求。把原始图像编码成 base64 后，HTTP body 可能先达到某一层的限制。降低 token 压缩阈值既不是可靠的字节预算，也可能让压缩请求本身仍携带过大的图像历史。

固定 CDS 上游 `fe136d4e70ef2b5bbed40a2d22b3aabf267796d3` 的来源已经逐文件缓存、校验：

- `core/src/client.rs` 1746–1754：请求 zstd 压缩需原生 Codex backend authentication 与 OpenAI provider 同时成立；自定义 provider 不走此条件。
- `core/src/session/context_window.rs` 58–117：压缩门槛按 token 计算。
- `core/src/compact.rs` 300–335：本地 compaction 仍向模型发送当前历史；358–369 的移除最早项分支仅用于 `ContextWindowExceeded`。
- `codex-api/src/api_bridge.rs` 245–258 与 `protocol/src/error.rs` 441–455：普通 413 会保留为 `UnexpectedStatus`，原默认策略会重试。compaction 还另有自己的重试循环。

源码入口：[固定版 Codex error.rs](https://github.com/openai/codex/blob/fe136d4e70ef2b5bbed40a2d22b3aabf267796d3/codex-rs/protocol/src/error.rs)、[固定版 compaction](https://github.com/openai/codex/blob/fe136d4e70ef2b5bbed40a2d22b3aabf267796d3/codex-rs/core/src/compact.rs)。

## 已排除与已证明的路径

此前三个极小的真实 encoding 请求使用同一固定 DeepSeek 模型：普通 JSON 成功，gzip 与 zstd 分别返回 400 JSON parsing failed。不能把本地压缩往返正确当作上游接受 `Content-Encoding` 的证明，也没有重复这些请求。

OpenRouter Responses 文档明确要求每次重放历史，`store:true` 或非空 `previous_response_id` 被拒绝。不能用一个 response ID 替代丢失的历史。[官方 Responses 概览](https://openrouter.ai/docs/api_reference/responses/overview)

Files API 的当前文档与当前 schema 存在分歧：指南仍将上传文件描述为 sandbox 使用；当前 `InputImage` schema 明确允许 `file_id`。这次采用合成图片做了封闭实测，没有上传用户图片。上传 254 bytes、96×96 的随机四色 PNG 后，固定 DeepSeek 官方路由通过 `input_image.file_id` 正确识别四象限；相同 bytes 的 inline 对照也识别正确，二者原生 input usage 均为 253。generation 元数据进一步确认实际 provider 为 DeepSeek、实际 canonical model 为固定的 `deepseek/deepseek-v4.1-flash-20261017`。上传文件已 DELETE 200，再 GET 404 验证删除。

这证明私有 file ID 路径实际可用；不证明 OpenRouter 服务端存储与上传前逐 byte 相等。上传 body 的原 bytes SHA、返回 MIME/size、随机视觉对照分别留证；该 API 不允许直接下载普通上传文件，故不能声称做过服务端下载哈希比对。相关证据保存在私有 `.evidence/cds-payload-research/`。

官方来源：[当前 OpenAPI](https://openrouter.ai/openapi.json)、[Files API 指南](https://openrouter.ai/docs/guides/features/files-api)。Files 为 beta、文件按 workspace 归属、上传上限 100 MiB、workspace 文件不自动到期，删除必须由客户端明确执行。这些约束已进入资源生命周期设计。

初始小 probe 的 `request_bytes` 是 compact JSON 的逻辑序列化尺寸，实际调用用了 aiohttp 默认 `json=` 序列化。原件保持不变，`probe-wire-size-correction.json` 补充实际所用序列化的 592/924 bytes 与 SHA。后续组件 probe 直接发送 `PreparedImages.body`，记录实际发送的 bytes。

## 独立传输模块的职责

`src/codex_app_deepseek/image_transport.py` 不管理原生线程，不运行工具，不生成摘要，不更改 native history。它只在显式的预防阈值以上，按净字节节省选择最多 16 个出现位置，将相应原图上传为同一凭据范围内的私有文件；其他图像继续保留原始 inline bytes。这里只改待发送 body 的副本，所有图像的顺序与 detail 不变。

```text
native history（仍保留原始图像）
          |
          v
  复制待发送 Responses body
          |
    小于预防阈值 --------------------> 原样 JSON bytes
          |
    超过阈值且包含内嵌图像
          |
    SHA256 原图 bytes + MIME 核验
          |
    私有 Files API / 已确认的缓存
          |
    保持 detail、顺序、索引、call ID
          |
    body 中仅 data URL → private file ID
          |
    原模型、原 provider、原 Responses 流
```

阈值名称为 `activate_above_bytes`，属于 operator prevention policy，不是已证实的上游 body cap。可另设 `wire_budget_bytes`：最佳混合编码的预估 body 若仍超预算，在凭据访问/上传前直接报 `PayloadBudgetExceeded`；上传后按实际 file ID 长度再次检查，不向上游发送超出该预算的请求。caller 已有的 `file_id`/`file_ids` 先占 reference slots，重复图像的每次引用也各占一项。图像、文本、工具 schema、JSON 字符串不能混为一谈：模块只识别明确的 input image content，不会把工具 arguments 或 schema 中长得相似的对象拿去上传。没有图像的超大请求不删除文本或伪造成功；设置了 wire budget 时同样在本地明确报413，未设置预算则原样交由上游处理。

重复图像按原始 bytes 摘要去重，MIME 不一致明确报错。同一图像在不同位置的 `detail` 保持不变。既有外部 URL 与 caller 自己的 file ID 不被认领、上传、缓存或删除。

缓存根目录必须专用、绝对、无 symlink/junction。Windows 存储在写 salt/数据库前设置 current SID + SYSTEM 的 protected DACL；POSIX 使用 owner-only 权限。scope 为本地随机 salt 对当前 key 的 HMAC，不存 key；不同 key 不复用文件，即使它们可能归属同一 OpenRouter 账户。轮换 key 后的旧 scope 不会被新 key 静默清理，诊断会明确列出仍有远端资源的旧 scope。

## 上传与删除状态

```text
upload_pending ──确认元数据──> ready ──空闲到期且无pin──> delete_pending
      |                         |                              |
      | 断线/进程退出           | 外部GET 404                  | DELETE/GET失联
      v                         v                              v
upload_unknown               deleted                      delete_unknown
      |                                                        |
      | 明确诊断；不自动再传                     GET同一ID 404确认 / 显式DELETE
      |                                                        |
      +── 未核清前不能当作ready                                 v
                                                           deleted
```

上传前的 nonce filename、hash、bytes、MIME 与阶段先写入 SQLite。多进程 OS 锁避免同图重复上传；推理期间不持有上传锁，主请求和其他调用各持自己的 transport pin。进程死亡只可回收尚未发给上游的 prepared pin，不能证明远端推理结束。

如果 upload 已发出但没有拿到 ID，禁止自动重传或仅按 filename 自动认领为可复用缓存。显式 `reconcile_unknown_uploads()` 使用上传前持久化的随机 nonce filename、MIME、size 和本项目操作 journal 做有界列表对账：默认每页 50、最多 4 页，同一轮为所有 unknown 一次扫描。只有分页完整、唯一严格匹配且逐 ID GET 元数据复核后，才进入 **cleanup-only** 路径；不会变为 ready，也不把元数据当服务端内容 SHA 证明。零匹配、多匹配、循环 cursor、未读完分页均保留 unknown。

官方 REST Files List 没有 filename 精确过滤，也没有已公布的上传幂等键；因此该显式命令可能在本次凭据范围内读取最多 200 条账户文件元数据，但不读取文件内容，不把外部文件名/ID/原页面写进输出或日志。代码只保留本项目 nonce 的候选。此功能用严格 fake 分页和故障回归验收，**没有在真实账户做 Files List**。[官方 List Files](https://openrouter.ai/docs/api/api-reference/files/list-files)

DELETE 返回不明时先 GET 原 ID；404 可核销而无需重复 DELETE。GET 仍存在时只对元数据与本次确认上传相同的 ID 执行删除。权限失败、未知结果和旧凭据 scope 都会留下明确 pending，而不是被“清理成功”掩盖。

## 上游消费与客户端交付分开记录

调用者必须在发 Responses POST **之前**执行 `mark_upstream_started(request_ref)`。这会持久化 unknown pin。只有验证过的 provider terminal 才可执行 `mark_upstream_settled(response_id, terminal)`；可确定未接纳的 HTTP 4xx 通过 `mark_upstream_rejected(status)` 记录。500、断线、超时、客户端取消、PID 退出都不能冒充远端消费结束。

```python
async with transport.prepare(upstream_payload, credential=captured_request_key) as prepared:
    await prepared.mark_upstream_started(unique_http_attempt_receipt)
    # 必须发送 prepared.body，而不是重新用 json= 进行不同序列化。
    # 请求仍固定原模型与显式 provider，禁止 failure fallback。
    # HTTP/SSE reader 验证 provider terminal 后：
    await prepared.mark_upstream_settled(response_id, "completed")
```

若 provider 已发出可验证 terminal，而后面的客户端交付/EOF 校验失败，前者仍然说明 provider 不再消费图像，后者仍说明本次本地任务交付失败；不能互相覆盖。unknown pin 的恢复需要真实 generation/terminal 证据与原 request reference，不能仅依赖计时器。`settle_unknown()` 是该证据核验后的底层入口，不代替核验器。

Files 上传、GET、DELETE 均需显式传入与模型请求一致的 `proxy_url`；不能假定 aiohttp client 已全局设置代理。metadata JSON 拒绝重复 key、NaN/Infinity 与溢出的浮点数。HTTP 错误只保留状态、机器 code 和限量允许的 Retry-After/rate-limit 字段，不保留完整响应 body 或凭据。

服务需要周期性调用 `cleanup()`，卸载或验收结束调用 `cleanup(all_unpinned=True)`。遇到 Files HTTP 错误立即停止本批次并留下剩余资源数，不连续磨共享配额。`complete` 是该轮清理没有 pending，`all_owned_files_deleted` 还要求没有未到期保留文件。稳定交付必须核验后者，并逐 ID GET 404；不能删除缓存目录来掩盖远端欠账。

## 原生 413 最小补丁

App 固定上游 `680c4ba93ec79c292287fb9e0b627dc7a76e3b5e` 的候选在 `native/payload-too-large.patch`，CDS 固定 `fe136...` 的独立候选在 `patches/codex-fe136-payload-retry.patch`。二者都只涉及错误分类与本地 compaction 重试判断：

- 413 不改名为 context-window error，保留原 HTTP status/body/error details。
- 同 body 的 413 没有 Retry-After 时停止机械重传。
- 服务端明确给出 Retry-After 时，使用该建议；其他错误仍保持原来的策略。
- 不改 compaction 摘要算法、历史裁剪、token 预算、agent loop、审批或工具执行。

这是 HTTP 重试语义的修正，不是容量修复本身。RFC 9110 对暂时性 413 的 Retry-After 有明确定义。[RFC 9110 §15.5.14](https://www.rfc-editor.org/rfc/rfc9110.html#section-15.5.14)

两个 patch 的 `git apply --check` 已通过。680 仅在独立构建源码应用；未修改运行中的安装。原生 regression test 用来断言无 Retry-After 的 413 不再重试且仍是 UnexpectedStatus，并保留带 Retry-After 的分支。当前 native 源码已经交接冻结；Rust 构建与真实 App 验收结果应以独立 native 构建记录为准，不能把本组件的 apply check 或 Python 测试当作 native 运行通过。

## 验收与仍需完成的门槛

当前 image transport、bridge、schema3 state、GC、generation readback 和 call reconciliation 六个相关测试文件合并回归为 **204 passed / 11.20s**。覆盖实际 Windows Python 多进程锁、真实进程在上传阶段退出、同图复用、原始 bytes 回还与整体 payload 对齐、外部失效 ID、key rotation、DELETE 响应丢失、上游 unknown pin、provider terminal 后客户端失败，以及 101 张合法合成 PNG 的混合编码变化；新增了 16-reference 原始失败回归、最大净节省选择、已有引用占位、不能满足 wire budget 的预检、明确 proxy 逐方法透传及 metadata JSON 歧义拒绝。本次独占测试临时目录已清理。POSIX 分支尚未在本次任务实跑。

`synthetic_image_capacity.py` 可在内存生成 34,009,154-byte、1,206-item、101 张唯一 400×210 PNG 的完整 body。首尾白条各有不同的随机六位十六进制视觉 nonce，nonce 答案不写进文字 prompt；其余原图为高熵像素，避免只靠高度可压缩的填充假装验证容量。脚本没有网络调用，也不保存图像原 bytes。

小组件 probe 与一次大组件 probe 必须固定 Morph fp8、同一 DeepSeek snapshot、最大输出 2,048，禁止 provider/model fallback 和自动重放。不能把官方 DeepSeek 的小图成功外推成 Morph 大请求成功：OpenRouter 内部可能展开 file ID，仍触发下游 body cap。大 probe 的判定要分别记录 HTTP 是否为 413、全部 101 个图像引用是否齐全、视觉 nonce 是否准确、实际 serving provider、token/cost，以及所有文件删除结果。

当前公开 Morph 定价为 input $0.253/M、output $0.6/M；在一次请求通过其 1,048,576 context 限制且价格不变的条件下，2,048 输出的保守上界为 $0.266518528。价格与支持参数以私有保存的当前 endpoint metadata 为准。[当前固定模型 endpoints](https://openrouter.ai/api/v1/models/deepseek/deepseek-v4.1-flash-1017/endpoints)

生产发布仍需：审核并接通固定 CDS 协议模块（官方 DeepSeek 与 Morph 的同容量组件 probe 均已通过）；确认原生 retry 补丁运行证据；完成真实 App 的既定全部验收与跨平台验证。没有进入依赖方业务目录，没有改它们冻结的 CDS 安装，也没有调用任何 native sandbox setup。


## 真实大请求的失败与修正记录

第一次真实大 probe 严格保留 101 张原图，全部换成私有引用后，实际发出的 body 从 34,009,154 bytes 降至 217,342 bytes。但 OpenRouter 返回 **HTTP 400，101 references 超过每请求 16-reference limit**。没有 response ID、usage 或模型结果，不能将这次称为推理成功，也不能称为已修好 413。此限制来自本次真实 API 错误，不冒充官方 metadata 已公布的能力字段。

该次只发送一次 Responses POST，没有自动重放。最初清理确认 53 个 GET404，并将另外 48 项保留为 pending；原适配快照只记录了 `FileOperationError` 类型，没有足够证据确定原失败 HTTP status。后续对相同已确认 owned IDs 限速对账，130 个请求均为 200/404，48 项全部核销；合计 **101/101 GET404**，pending/pins/unknown 为零。单项只读 headers 诊断也为 200、owned metadata 一致，没有 Retry-After/rate-limit 字段。因此不能反推初次失败一定是 429。

后续修正为最多 16 个出现引用的混合策略，保留剩余 85 张原始 inline 图像；模型、provider、原始 fixture 和图像总数均不变。第二次已授权的真实 mixed probe 使用 30 MiB **operator policy**，16 uploads + 一次 Responses POST，已得到 HTTP 200 completed；实际 body 为 28,625,730 bytes，首尾视觉 nonce 都准确。generation 回读确认 actual Morph 与固定 DeepSeek canonical snapshot，native input 38,341、output 162，实际成本 $0.009797473。16/16 文件 DELETE + GET404，remaining assets/pending/pins/unknown 全部为零，执行任务已正常退出和自删。该值不是已知上游限额；即使单例通过，也不能取消原生字节预算与 compaction 衔接的发布门槛。

公开最新 OpenAPI 仍限定 `store=false`、拒绝非空 `previous_response_id`，没有公开的 conversation/input-append 接口。`/presets/{slug}/responses` 只保存配置并忽略消息/提示，不能用来绕过历史传输。仅做了文档和 schema 查询，没有启用 store 或调用额外推理。


混合策略的真实成功只覆盖上述合成容量场景。两端 nonce 的图像在本次最大节省选择中保持 inline，私有图像路径另由小组件 nonce 实测覆盖；不能将视觉答案准确包装成模型逐像素审阅了全部 101 张图的证明。每个上传 bytes SHA、引用与原图位置对应、其余 inline 字节和整个原 payload 回还相等由独立断言检查。


## 网关接线、有限后台回收与未知消费收敛

`create_app(..., image_transport_factory=...)` 在每个有效请求捕获一次上游 key，文件操作与 Responses POST 使用同一值，避免凭据轮换期间串 scope。发送使用 `data=prepared.body`；不重新序列化或修改 native history。资源回执写入已有 schema3 的 `adaptations.image_transport`，没有升级协议数据库 schema。上游 provider terminal 与客户端 delivery 分别记录，后续 SSE 矛盾、EOF 或结构化输出错误仍是请求失败，不能抹掉已知终态；500、断流和取消则保持 unknown pin。可确认的本地 operator byte budget 返回 HTTP413，确定参数错误返回400，Files429/409保留安全 Retry-After，不返回假200。

启用 image transport 后，网关 HTTP lifetime 启动一项自己的回收任务：每60秒、总10秒、最多2个 eligible asset，只 GET/DELETE 已知 owned IDs；停止首个 Files 错误，原子保存安全 Retry-After 冷却。退出只取消自己的 GC task。它不做 Files List、模型调用、native 线程管理或摘要。健康检查只展示计数和错误类别；坏状态、只读目录及持久化失败不能显示为“清理成功”。

显式 `reconcile_image_calls(transport, receipt_path, state_path, credential_provider, ...)` 最多处理2个 unknown calls、总10秒，最低60秒持久冷却，按散列游标轮转，避免第一批没有 ID 的未知项永久挡住后面的项。它以 SQLite `mode=ro` 只读取本项目指定 request_ref 的 schema3 回执，不构造会重置运行态的 ProtocolState。凭据 scope、请求 ID、固定模型和 provider 必须对应；没有 scope、没有响应 ID 或 ID 冲突均保留 unknown。

已保存且通过协议检查的 provider terminal 可直接证明消费结束。否则仅对该已知响应 ID GET `/generation`，并核验响应 ID、实际 provider、exact model 或官方 canonical alias 证据；当前接受 `stop/stop` 和 `length/length` 的明确 finish 对。`null`、工具调用中间阶段、矛盾的结束原因、404、时间流逝、PID 死亡、费用或 token 数都不是终态。真实本项目 3 个既有 generation 回执离线经过同一分类器复核，实际 `api_type=completions` 是提供方内部标签，不要求它等于外层 Responses 入口名。[官方 generation 元数据](https://openrouter.ai/docs/api/api-reference/generations/get-request-&-usage-metadata-for-a-generation)

释放 pin 前必须写入私有、字段白名单筛选后的终态 proof，并在资源 journal 记其 SHA；写入失败则保留 pin。collector 返回的只有处理数、已释放数、剩余未知数、安全服务错误和冷却时间，不公开 request/response/file IDs。它不会重放模型或删除云文件；解除 pin 后仍由常规有界 TTL cleanup 执行 GET/DELETE/GET404。未能证明终态的项继续保留并明确诊断，不能假设“已经很久了”就删。


## 官方 DeepSeek 的同容量独立对照

随后对同一批 101 张原始 PNG、同一视觉 nonce 和 1,206 items 做了官方 DeepSeek only 对照，`allow_fallbacks=false`，一次推理、最多2,048输出。路由名从 `morph/fp8` 改成 `deepseek` 少1byte，因此仅在原 synthetic padding 补1byte，维持原始 body 恰好 **34,009,154 bytes**；图像 bytes 不变。

当前模块实际发送 **28,625,730 bytes**，16 private references + 85 unchanged inline。HTTP200 completed，两端视觉 nonce 正确；独立完整 payload 回还断言再次确认全部101图 bytes/位置/detail 均保存。generation 回读为 actual **DeepSeek**、官方 metadata 证明的固定 canonical snapshot，native input38,341、output220，实际 **$0.00588315**。本次16个文件全部 DELETE 后 GET404，pending/pins/unknown/remaining均为0；InteractiveToken worker退出0并删除自己唯一任务。

原始报告 SHA256：`85172b0461617ef84c78d68a8071bd45d458e499231f98a112896ac0d4bb5be9`；独立断言在私有 `official-mixed-independent-assertions.json`。这次真实 generation 也经过最终 `generation_terminal()` 分类器离线复核为completed，无额外推理或 Files List。包括此前失败后的回收，全部组件 probes 累计 **135/135** owned files 已 GET404。

这关闭了“仅 Morph 大请求通过，官方 DeepSeek 大请求未经测试”的组件缺口。它仍不代表任意长度图文必能传送，也不替代 native runtime、真实 App GUI、compaction 和跨平台验收。
