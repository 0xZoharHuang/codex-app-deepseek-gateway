# 当前机制验收状态

本页描述固定 Windows App/runtime 组合的本地机制，不是模型能力榜单。当前尚未稳定发布；结论按机制、实际组合和证据边界区分，软件测试数量不代替 App 验收。

## 当前版本

- App：26.1023.6621.0；原生：0.165.0-alpha.1.2 / upstream 720fec862a608ae1e5878d1eecb04fb93192e93c。
- 当前原生 CLI：18ef99bac7865066e1e802521e0c0c058862956cc569a6abc193b2005fa36b7f。
- 完整源码：39d578673f2031097dfa82fd97b5126a492ea4ba3a692b565ab5fcaef4dce33f；9514 文件；build lock dbb9faf2a71531369435611a2bf2c1104daeb4a4461509369d12648f67e4b90d。
- 产品 source3229244 新增仅含哈希的有界调用回执。97相关检查通过；Windows普通权限下完整源码与安装版各1541唯一案例通过，均无skip/error/failure。安装版另核4623个setup/call/teardown阶段、精确JUnit身份、产品和六个纯协议模块来源。这是软件资格，实际App故障恢复仍未关闭。
- 当前安装包 manifest ff0e750049630d60e3a2537a0434b9694b7e3602cb4888f53a5355a1788c093f；wheel dd8f31e07b2eda64ea144b414d6c5a03689a747bbcc1ff5b8d479a031bb719ec。后续公共QA/文档不改变25个产品文件；最终发行尚未生成。
- 原始记录私有保留；公开发布须附去敏结论和可复核来源清单。

## 机制矩阵

“范围通过”只关闭该列的具体行为；“承接”要求对应代码及输入逐字节核对，不能表示整套在新版本重跑。

| 机制 | 当前证据和限制 | 状态 |
| --- | --- | --- |
| 官方 App / 独立 profile / 实际 runtime 选择 | 官方包、SID/session、原生路径、创建时间和哈希绑定；18ef 实际加载。其他 App/CDS 不被接管。 | 范围通过 |
| 原生 AppTools / 插件 / MCP 发现 | 无 legacy 顶层替代定义；官方资源哈希核验，经原生 PluginManager 安装，真实发现及调用。 | C98/875 范围通过；新包按差异复核 |
| Responses/SSE/工具历史/结构化输出/Code Mode | 软件与真实复杂任务有证据；未知语义拒绝。新 receipt 元数据改变的最终安装、故障历史和物理请求链仍需验证。 | 已有软件合同和正常复杂任务范围证据；最终D故障窗口待关闭 |
| 复杂工程任务 | 真实 App 中完成多文件 Jinja 修复；908原测试+22独立检查干净重建通过。模型有先前记忆，任务有人监督，非盲测或质量等价证明。 | 工作流范围通过 |
| 原生多级智能体与冷恢复 | 同一原 Root/child UUID、前缀和消息关系有实证；V2 loaded-only 列表与按已知路径懒恢复分开核对。V1/V2 具体生命周期不能互相替代。 | 当前0.165多级协作/原UUID冷恢复已实测；18ef仅Windows元数据/cfg(test)差异，核心线程逻辑相同并明确承接 |
| 人工审批 / 文件网络边界 | 实际允许、拒绝和受限执行分别留证；不以 full access 通过。 | 已有范围证据；最终组合差异核对 |
| Guardian 内部记忆上下文 | 最小已持有上下文补丁保留公共查询限制；实际记忆/内部评分不再因 internal thread lookup 失败。 | 875修复已验证；18ef相同逻辑承接 |
| Guardian 真实 Allow/Deny | 固定 DeepSeek reviewer 的 literal Allow、完整 Deny，与原生 fresh approval、具体动作和无副作用对应。实际 reviewer UUID 不同于 Parent，归属由原生构造 Parent/action 结构核实。 | 精确审批机制通过 |
| Guardian 后续整任务 | 同一窗口随后因8 live预算返回503，父任务最终失败。受控删除来自明确 synthetic QA 注入；不能称为 DeepSeek 主任务自然选择或整任务成功。旧409/failed-closed保留。 | 失败如实保留，不覆盖审批范围结论 |
| 记忆提取、整合、新会话读取 | 当前原生阶段有实证；曾有模型引用错误，修正与独立检查均留存。 | 具体阶段通过；不称模型永不出错 |
| 原生自动/手动压缩与冷继续 | 自动触发输入、完整调用/结果配对、原生 replacement 历史和冷继续分开核验。当前0.165不移植旧私有容量扩展。 | 当前已验机制与源相等承接，最终证据索引待发布 |
| App 客户端 Queue / Steer / goal / 终端 | 客户端Queue与原生Steer已有实际输入、原生输出、调用ID及归属证据；预置文本和单纯UIA返回不算成功。 | 客户端范围通过；Steer、goal暂停/正常恢复、客户端Queue、两个终端选择性中止及原B自然完成均有具体实测 |
| server queue API | server queue数据库为空只是状态观察，不能算队列API已被调用。若固定App未暴露该入口，单列未暴露条件。 | 未关闭：App确有6个thread/queue方法客户端，受native版本功能和codex-app-server-queued-follow-ups门控制；实际条件及API消费待核，空DB不能证明不可用或通过 |
| Windows UAC / secure desktop | 当前真实UAC人工同意与拒绝未实测，不由source相等或普通App审批推出；本次不借此新造UAC/privateRPC流程。 | 未实测，条件边界明确 |
| Windows sandbox平台 | 本次为Win10 19045实际支持的沙箱，不冒充Win11 MXC，也不外推任意账户、区域或OS安全桌面。 | 固定宿主范围 |
| Windows 默认 cwd | 18ef 原工作树一次默认 exec：SID1009、PWD/GetLocation/相对读正确，父属性可读且列目录被拒；严格ACL只新增1008 allow0x80 flags0，旧ACE/FID/字节保持。 | 原工作树范围通过 |
| 新目录正常物化 | 正常create/attach的新parent与leaf执行前均无1008权限；一次正常默认命令后，parent仅128/noinherit，leaf及policy明确可写的旧restoredroot按原生Modify/cap/.git只读carveout物化，14节点严格after已核；完整小JSON、默认cwd、888B相对读、父list拒绝都真通过。 | 首次新目录范围通过；新父链实际执行和14路径权限复核已关闭 |
| 原生 worktree 归档/恢复 | primary保护拒绝是预期。正常任务归档曾因缺Git身份使snapshot commit-tree128，修fixture local身份后原队列完成快照/删除，GUI恢复39B成功。新目录、secondary正向归档及slash/backslash残留仍在最终读回。 | 未关闭：GUI实体恢复已通过；Windows slash/backslash快照别名导致attach/公开restore实际失败，未手改ref或队列 |
| OAuth | 当前C98/875：GUI添加/Authenticate/Reconnect，HTTP服务器用户角色Deny/Allow，真实函数成功、TTL刷新、fixture-only revoke后的isError、同Root冷复用、原生CLI精确logout/remove和GUI缺席。 | 当前配置后端范围通过 |
| OAuth 证据限制 | Consent不是browser click；680字段是固定fixture数据；无全存储模式新复测声明。启动前置失败及3条 unknown_or_expired 400/no grant全部保留。 | 限制明确 |
| 网页搜索/open/find/click | 原生六文件与先前已验实现逐字节一致，已有实际搜索导航记录。源相等不是新版全部端到端重跑。天气、组合域名与当前旧引用冷恢复仍需最终明确。 | 已有受支持搜索/导航和天气实测；最终服务恢复后原引用单次App重开待完成。四网页模块全字节相同，旧protocol-state全部定义相同，新helper仅观测 |
| 429/503/413/截流/取消/服务丢失/同端口认证恢复 | 原始失败不得伪装成完成，执行后断流不能重复副作用；实际MAIN请求、原native prompt、物理编号、body、receipt、状态必须联结。 | 未关闭：D正常升级后验证有限故障、真实取消、canonical同端口恢复与认证 |
| App IAB / 计算机工具 | 原生 SDK 门、账户/主机条件与实际可用性需正向验收或官方不可用条件的有据分类。web.run/预览不替代它们，不强制feature flag。 | 未关闭：UI曾呈禁用；实际SDK getter/有效门待有界只读分类，不强制flag、不以预览代替 |
| 公共发布、回滚、最终清理 | 原生包和Python软件资格不等于稳定发布。只在最终机制关闭后更新公共repo、分发、回滚和清理；周额度只按最后实时查询记录。 | 尚未发布；最终生命周期、交付、Windows语义清理及最后实时周额度重置确认均待完成 |

原生 Plan 的 request_user_input/问答 UI 已确认存在，当前没有 CADSG 实际问答证据；需正常 GUI 提问、选择、答案回传并还原 Default。

[可移植被动观测和严格校验](../tests/fixtures/app_faults/evidence/README.md)已有35纯负控及本地真实HTTP/tap验证，无模型、App或凭据调用，不计作App验收。configured_baseline仅是策略声明，不证明实际二进制。

## 保留的真实失败

1. 930检查对应复用已知工程任务，非blind；模型先前记忆和错误引用纠正没有省略。
2. Guardian 起初私有QA把未知辅助请求占用MAIN互斥，出现409；后又遇预算503。不同线程header曾令旁路漏记。旧记录不改成通过，新旁路只改善归属证据，不能生成native verdict。
3. Windows 默认cwd曾因父目录缺读取属性使PowerShell回C盘。修复后单次QA脚本把Get-Content的ETS对象按Depth8反射展开约50MB并被原生裁剪；只从原输出完整唯一字段取证，不称完整JSON可解析，不重发成功读取。
4. 原生首次构建后才发现单测fixture AI控制位错误，浪费了一轮构建。原101/原producer保留；test-only同DACL stock初始化修好后完整断言通过，才冻结最终source/lock并重新标准构建。无生产ACL primitive更改或控制位mask。
5. packager曾只接受旧三补丁拓扑，拒绝当前四补丁；已修正真实接线和当前锁集成断言。输出父目录缺失的操作错误也保留，未伪造成功产物。
6. 归档需要合法Git身份。commit-tree128没有被当作删除成功；只修自有测试repo的local身份，不写用户global。
7. 历史helper被防护隔离及早期权限错误保留。当前工作未关闭Defender、添加例外、恢复隔离样本或更改全机安全策略。

更早候选仅见[历史摘要](qualification-history.md)。最终公开报告不能发布会话正文、凭据、内部项目或闭源 App 代码。
