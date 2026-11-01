# 固定 Windows 原生构建

## 构建并发资源上限

标准 builder 可选 --jobs 1 或 --jobs 2，使用官方 Cargo --jobs 参数。固定 PROFILE / release_environment 中 CARGO_BUILD_JOBS=2 是锁定的最大并发值；显式 CLI override 优先，实际 argv 和 locked_max_build_jobs / effective_build_jobs / build_jobs_override 会同时写入构建回执。打包器严格核对这些字段与实际命令，并在分发 provenance 记录有效资源设置。无 override 的旧回执沿用原校验。此资源限制保留代码生成参数、源指纹、原生四目标和 owner/cache 的检查。

## P7 迁移换行兼容候选（R241，尚未生成四目标产物或部署）

第七个补丁 migration-checksum-eol-0165.patch 只修改原生 state crate 的迁移适配和对应测试，不修改 SQL 源文件或 SQLx。Windows 全新 profile 按同版本官方 App 的 CRLF SQL 执行并记录其 SHA384；已有迁移只接受同一 SQL 的 LF / CRLF 两个精确身份，在内存中选取相应 SQL 和校验值，不更新既有账本的 checksum 或用户行。旧 LF profile 后续才创建的辅助库从配置实际 primary 路径只读继承 LF，保留 P6 回退能力。未知内容和 dirty 迁移仍按 SQLx 拒绝，legacy38→39 修复和原有事务保持。

已有 LF 账本不能由未经修改的 stock App 直接读取；本补丁不承诺任意旧、新或跨系统版本共享同一数据库。SQLx SQLite migration lock / unlock 是 no-op，不作额外互斥保证。当前只进行独立 codex-state 定向 unit 资格，四目标 producer、部署和 App 运行资格另行验收。

## P6 构建前资格（R224 历史记录）

R224 该阶段的 active lock 为五补丁 P5：5bfdec0bc745a3a31e209078b1424d9f063d1eea73918eb394c8eae59f01648b，完整源 3830ef7a9318ff1c6016ecaa6840805250c527c9a2b75b1e77b6b9c334210548 /9516 文件。P5 已经通过正式安装入口加载，CLI 为 ebec4115b77c4710feb71d9d460d893bb6cb164c2fbee906087783d83ac685fa；其真实 App LaTeX 范围证据见 [R222](qualification-r222.md)，不表示整体 App 已通过。

P6 在 P5 后追加 guardian-provider-transport-0165.patch（SHA256 2d7504fcbfc86918c72eef7629760d590914b78c7bf705042e05f62749bacefe）。独立从已核验官方 720fec archive 重放六个补丁，得到完整源 5d10fd97306cd2d11a1bc5d4ae2327468240bb650639cc93c39d19b2b7b58edb /9516 文件；Cargo.lock 不变。此阶段只有来源重放和构建/打包软件资格，尚未生成新四目标产物、部署或进行 P6 App 验收。

打包器只接受完整且有序的已知四、五、六补丁栈；不允许跳过 Tectonic 后直接添加 P6，或接受未知补丁。显式 --build-lock 在输入、staging、archive 使用同一精确锁和 SHA，而不是借用 active lock 的 patch 白名单。所有声明 patch 及原始锁都必须完整携带且校验相等。active lock 的晋升须由已确认旧 lease/构建终态的操作者统一完成；使用私有候选锁不意味着可以提前改现有 owner/source/target。

本轮 139 项离线构建/打包检查通过；其中 main 集成使用明确的模拟 Cargo metadata 和测试输出，不调用 Cargo、编译器、App 或模型，不能当作真实 producer。标准 builder 本身没有 patch 数量硬编码，仍只允许一条锁定的四目标 Cargo 命令。

下面四补丁产物表保留为**历史 P4 记录**，不能用于选择当前或 P6 二进制。

当前配方为 O165，对应官方 App26.1023.6621.0，upstream0.165.0-alpha.1.2。build_native.py只构建，不安装、启动App、配置沙箱或运行智能体。

## 精确输入与历史 P4 产物

| 输入/产物 | 固定值 |
| --- | --- |
| upstream commit | 720fec862a608ae1e5878d1eecb04fb93192e93c |
| upstream tar.gz SHA256 | 37f7f01a5e404b5015b47decececba7a3f0b9526bc951d0624bd57008a972a14 |
| 完整prepared source | 39d578673f2031097dfa82fd97b5126a492ea4ba3a692b565ab5fcaef4dce33f /9514文件 |
| build-lock.json SHA256 | dbb9faf2a71531369435611a2bf2c1104daeb4a4461509369d12648f67e4b90d |
| Cargo.lock SHA256 | 7537da7c5e154334b15ef9b59091f5fed3a8e480b77c037e3a8b7f07d1614709 |
| Rust/Cargo/target | 1.99.1 / x86_64-pc-windows-msvc |
| producer source commit | 7cc0a6f8bc69f2876d7816aeec904c30a7d3dd89 |
| raw producer manifest SHA256 | 1c56319997c896cb8b33ed5eaf413fec57afa2d128ae44396660e03abe79010e |
| raw distribution manifest SHA256 | 0bd930c845f94a6e46a05dc79af5a40cda172b20a4f6338ca8cfcc3c7fe07893 |
| raw archive SHA256 | 3231dbea3c8983f8472ac58d7e8dfa4741550f1338f4339d41ff37231f86adf6 |

四个补丁严格按锁顺序：

| 补丁 | SHA256 |
| --- | --- |
| windows-home-namespace-0165.patch | 1e436f88e4201e6e33bba7d509e43265071c321006efd360215a260bf643d83b |
| mcp-keyring-isolation-0165.patch | 0cfe1ae48ab50ef8d930298c42cd7ee6d2de4277750cceacca7e5d2c2d0c8efc |
| guardian-thread-context-0165.patch | 6f434a95d3e0dd34c501539c0b8b562ac8bb2efc2973edf42a3d659cf1eaca60 |
| windows-cwd-ancestor-metadata-0165.patch | 9544c5b02e0e4e3546ba24fa1931b4e9592c5cf84d9ce0e7009b57868ac3c002 |

它们不重写agent loop、审批器、工具执行器、压缩或智能体持久化。父目录补丁只补必要的原生读取属性物化；public thread lookup仍保持限制。

## 先单测、后冻结、再构建

修改原生代码时先在该确切prepared tree运行相关Rust单测，并审查真实断言都执行；再导出patch、更新完整source指纹和锁，运行当前真实锁的Python构建/打包合同检查，最后才构建四目标。不要用“编译成功”代替尚未运行的单测，也不要先编40分钟再发现fixture失败。

历史 P4 当时的顺序为：最终39d源码的完整唯一cwd回归1pass/0fail/0ignored → 当前锁100个合同检查 → dbb9标准四目标producer。原失败101和旧506b producer保留。100是该固定版本的记录，不是将来需要硬凑的测试数量。

在x64 MSVC Developer shell内，使用已固定的工具链和同一release profile。对现有开发prepared source示例：

~~~powershell
$env:CARGO_BUILD_JOBS = '2'
$env:CARGO_INCREMENTAL = '0'
$env:CARGO_PROFILE_RELEASE_INCREMENTAL = 'false'
$env:CARGO_PROFILE_RELEASE_DEBUG = '0'
$env:CARGO_PROFILE_RELEASE_STRIP = 'symbols'
$env:CARGO_PROFILE_RELEASE_LTO = 'false'
$env:CARGO_PROFILE_RELEASE_CODEGEN_UNITS = '16'

# 在已核完整source指纹的 codex-rs 目录，调用固定 cargo：
& $cargo test --locked --offline --release -p codex-windows-sandbox --lib setup_provisioning::cwd_metadata::tests::owned_worktree_parent_metadata_is_narrow_and_idempotent -- --exact --nocapture

# 在网关repo，以有正确依赖的解释器运行当前锁的合同：
& $buildPython -I -B -m pytest -q .\tests\test_native_build_metadata.py .\tests\test_native_release_package.py
~~~

新准备的单测树可用现有build_native.py的prepare_source函数，从已核验archive/patch锁生成独立测试树；不要手写或伪造发布builder的owner记录。测试树与发布树需完整指纹一致，缓存路径不同则正常重编。CLI没有prepare-only选项，不应在文档或控制器里假造该参数。

任何包括cfg(test)的字节变化都会改变完整源码指纹。必须重新冻结最终patch/锁并生成对应producer，不能把旧输出换标签、混源拼包或回拨mtime来伪装同源。

## 标准四目标构建

工具准备：独立Python3.11+、Git、固定Rust1.99.1、x64MSVC linker/LIB及Windows SDK。此步骤不下载安装工具。

显式候选示例（变量须由操作者核对；不要把私有锁误认为已晋升的 active lock）：

~~~powershell
& $buildPython -I -B .\scripts\build_native.py --archive $sourceArchive --work-dir $work --toolchain-bin $toolchainBin --output $newEmptyOutput --cache-dir $cargoCache --target-dir $cargoTarget --build-lock $candidateBuildLock --dry-run
# 旧工作目录换输入时，还须标准 --transition-from-lock OLD_SHA，且真实源已完整匹配新锁。
~~~

~~~powershell
& $buildPython -I -B .\scripts\build_native.py --archive $sourceArchive --work-dir $work --toolchain-bin $toolchainBin --output $newEmptyOutput --dry-run
# 核对计划后，用同一组输入去掉 --dry-run：
& $buildPython -I -B .\scripts\build_native.py --archive $sourceArchive --work-dir $work --toolchain-bin $toolchainBin --output $newEmptyOutput
~~~

输出目录必须空。工作目录必须在网关repo外；现有非空目录只能使用标准builder自己的匹配owner记录。构建真实调用：

~~~text
cargo build --locked --release -p codex-cli -p codex-windows-sandbox --bin codex --bin codex-command-runner --bin codex-windows-sandbox-setup --bin codex-windows-managed-deny-probe
~~~

使用两jobs、below-normal优先级、无incremental/debug、无LTO、16 codegen units。只选择四个target，不用--bins。构建前后核完整树、archive、锁和patch SHA，原始manifest始终标built-not-app-qualified。

显式--cache-dir可复用本项目已有Cargo/target缓存，不搬源路径、不清空缓存。输入锁变化只允许--transition-from-lock OLD_SHA的标准CAS：旧构建必须终态，真实现有source已匹配新锁，固定原路径/mtime，拒绝redirect/reparse/hardlink风险，按旧记录内容保存owner/report/source-manifest三份备份，再换owner。默认路径仍严格，不采用旧mtime-reference/ABA做法。不降低磁盘guard或并行占用同一cache。

## 历史 P4 产物和通用打包

| 文件 | SHA256 |
| --- | --- |
| codex.exe | 18ef99bac7865066e1e802521e0c0c058862956cc569a6abc193b2005fa36b7f |
| codex-command-runner.exe | b411bdcc660b4f90f5ddc1cc7e1120271d3ca7916c785217d8aa34747404fd07 |
| codex-windows-sandbox-setup.exe | ad99dad63f2c6583622b1756cceabaffd677864e456276990a34a05a72db7ef0 |
| codex-windows-managed-deny-probe.exe | 97449b523b5053fac52b93ddd346315e22dbdf376db2494c5d0b8ee900e378b5 |

正常打包只接受显式选定的完整已知补丁锁、完整prepared source、可信producer manifest及四目标SHA/size/profile一致：

~~~powershell
& $buildPython -I -B .\scripts\package_native_release.py --source-root $preparedSource --native-manifest (Join-Path $newEmptyOutput 'manifest.json') --native-manifest-sha256 $producerManifestSha --build-lock $candidateBuildLock --cargo $cargo --cargo-home $cargoHome --git 'C:\Program Files\Git\cmd\git.exe' --output $newPublicStage --evidence-dir $newPrivateEvidence
~~~

先创建输出父目录，stage/evidence自身必须是新路径。公共组装要求源码干净且已提交；development qualification不冒充公开发行。输出记录固定依赖license与来源，不导出私有请求或构建机凭据。

当前raw归档3034个文件已逐成员对照分发目录SHA/size，无链接、越界或重复。它仍不代表完整App通过。正常app-prepare-runtime从用户本机固定官方App复制匹配sidecar；该prepared目录含闭源资源，不能重新打成公共包。

真实App还要验证官方包/普通用户/实际加载路径、权限、工具、会话和故障恢复。更新工具链、App、patch、metadata或依赖都会形成新的待验证组合。早期候选和失败仅见[历史摘要](qualification-history.md)。

## Checkpoint compatibility when downgrading

本节只说明历史0.163 M/N与CDS相关容量扩展，不把旧配方用于O165。历史N修正缺省/显式默认元数据的重放哈希并写入v3检查点；旧M不保证在下一次容量拒绝时消费它。回滚保留历史，不表示旧二进制理解新格式。不得手改检查点、拼历史或换producer标签绕过不兼容。O165使用上游压缩与恢复实现，按自身版本验收。
