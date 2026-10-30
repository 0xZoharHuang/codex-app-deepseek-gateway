# Codex App DeepSeek Gateway

让官方 Codex 桌面 App 使用固定版本的 DeepSeek，并继续由 Codex 原生运行时管理任务、子智能体、工具、权限、沙箱和会话。

**当前仍在最终验收，尚未稳定发布。** 已关闭的机制、有限范围的通过和剩余项目分开列在[验收状态](docs/qualification-status.md)。安装版能启动、模型能回复、软件测试通过，都不能单独证明完整等价。

本轮实际App问答、代码审查、认证刷新、完整GUI工作树恢复及相同产品安装生命周期见[真实结果](docs/qualification-r219.md)和[后续补充](docs/qualification-r220.md)。原生浏览器/桌面控制及secondary恢复工具的未关闭问题仍明确列出。

本项目的“等价”是固定 App 在该 Windows 环境下实际支持的本地 runtime 能力。模型推理质量、模型自身模态限制和 OpenAI 私有云账户权益分别说明。

~~~text
你在官方 Codex App 中交付一个工程任务
                  |
      原生主任务 -> 子智能体 -> 孙智能体
                  |
     原生工具 / 审批 / 沙箱 / 终端 / diff
                  |
          原生模型请求与工具历史
                  v
       本机专用 DeepSeek 网关
        | Responses / SSE 协议适配
        | 明确的主模型、辅助模型和提供方路由
        +---------------------> OpenRouter -> 固定 DeepSeek
        |
        + alpha/search -------> 显式配置的网页后端
~~~

网关负责翻译请求和结果。真正运行 shell、应用补丁、决定权限、管理原生智能体和保存任务的仍是 Codex。App 的原有界面和工具也继续来自用户本机安装的官方 App。

## 和 CDS 有什么关系

[CDS](https://github.com/0xZoharHuang/codex-deepseek-runtime) 让 Codex CLI 使用 DeepSeek；本仓库让官方桌面 App 使用 DeepSeek。两者可以并行使用，安装目录、配置、会话、凭据项和进程归属独立。

本项目只固定复用已发布 CDS 0.6.7 的六个纯协议模块，提交为 cb788738864e97a16cde82f3c584164162ab6f7c。没有复制 CDS 启动器、线程管理、工具执行器或 agent loop，也不会通过 CDS 启动或接管桌面 App。

## 当前固定组合

| 项目 | 固定值 |
| --- | --- |
| 已实测宿主 | Windows 10 Pro 19045，x64；普通交互用户 |
| 官方 App | 26.1023.6621.0 |
| 原生源码 | 0.165.0-alpha.1.2 / 720fec862a608ae1e5878d1eecb04fb93192e93c |
| 当前原生候选 | O165；CLI SHA256 18ef99bac7865066e1e802521e0c0c058862956cc569a6abc193b2005fa36b7f |
| 完整原生源码指纹 | 39d578673f2031097dfa82fd97b5126a492ea4ba3a692b565ab5fcaef4dce33f，9514 个文件 |
| 模型请求 ID | deepseek/deepseek-v4.1-flash-1017 |
| OpenRouter canonical 模型 ID | deepseek/deepseek-v4.1-flash-20261017 |
| 已有安装版证据 | 产品 source 3229244；源码与安装版各 1541 项通过；当前包 manifest ff0e750049630d60e3a2537a0434b9694b7e3602cb4888f53a5355a1788c093f |
| 最终发布包 | 尚未发布；当前开发包正在真实 App 最终验收，不能作为稳定发布下载 |

当前实测是 Win10 的原生沙箱，不代表 Win11 MXC 或系统安全桌面/UAC。当前文档不把 Windows 的验收外推到 Linux/macOS 桌面 App。CDS 的跨平台资格由其独立仓库记录。App、runtime、依赖或模型版本变化后，需要重新核对兼容性和受影响机制。

## 四个必要的原生补丁

补丁顺序和哈希固定在 [native/build-lock.json](native/build-lock.json)，四个原生可执行文件同源构建：

1. Windows home 命名空间：避免多个独立 CODEX_HOME 共用固定沙箱账号和资源，保留原生 setup、token、runner、权限和生命周期。
2. MCP Direct 凭据隔离：按本项目的 canonical home 隔离原生 Direct 凭据；同一 home 的原生 Secrets 主密钥合同保持。
3. Guardian 内部上下文：让内部记忆和审核工作使用已持有的原生授权上下文；公共线程查询仍不暴露内部线程，不改变审批决定规则。
4. Windows 工作树父目录元数据：在正常原生 setup 物化阶段，对当前授权 cwd 所需的自有父链补充不继承的读取属性权限。它不授予列目录或文件写权限，也不替代原生叶目录权限。

旧 0.163 的容量扩展和压缩补丁不移植进 O165。当前的压缩、审批、工具调度和原生智能体机制使用固定上游实现。构建与来源见[原生构建](docs/native-build.md)。

## 安装前准备

使用目标 Windows 用户的普通 PowerShell。管理员账户也按非提升权限使用；不要把日常安装目录建成其他用户或提升权限主体所有。

需要已安装并可验证签名的固定官方 App、独立 Python 3.13，以及完整、经过发布 SHA256 核验的网关离线包和原生包。此仓库不分发官方闭源 App，也不把本机复制的 Code Mode Host 打进公共原生包。

下面命令的文件名和参数对应当前正式入口。发布前，下载地址和可信哈希必须由最终发行记录补齐；不要把本地现算的哈希当成来源信任。

## 首次安装

解压网关包与原生包到不同的自有永久目录。示例采用默认安装位置，不修改全局 PATH：

~~~powershell
$bundle = 'C:\CADSG\gateway-release'
$nativeInput = 'C:\CADSG\native'
$basePython = 'C:\Path\To\IndependentPython313\python.exe'
$installer = Join-Path $bundle 'install_gateway.ps1'
$gatewaySha = 'ff0e750049630d60e3a2537a0434b9694b7e3602cb4888f53a5355a1788c093f'
$rawNativeSha = '0bd930c845f94a6e46a05dc79af5a40cda172b20a4f6338ca8cfcc3c7fe07893'

$install = & powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $installer -Action Install -ManifestPath (Join-Path $bundle 'release.json') -ManifestSha256 $gatewaySha -Wheelhouse (Join-Path $bundle 'wheels') -PythonExe $basePython | ConvertFrom-Json
& powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $installer -Action Activate -ReleaseId $install.release_id
$cdag = $install.command
$python = Join-Path (Split-Path -Parent $cdag) 'python.exe'
& $cdag --version
& $cdag setup-credentials
~~~

setup-credentials 在交互界面读取 OpenRouter key，保存到 Windows 专用凭据项。不要把 key 放入参数、App 提示或仓库。导入 CDS 凭据是单独显式选项，首次独立安装无需读取 CDS。

正常关闭本项目使用的 App 实例，再准备和绑定原生包：

~~~powershell
$prepared = & $cdag app-prepare-runtime --native-manifest (Join-Path $nativeInput 'manifest.json') --native-manifest-sha256 $rawNativeSha | ConvertFrom-Json
& $cdag app-bind --native-manifest $prepared.manifest --native-manifest-sha256 $prepared.manifest_sha256
~~~

prepare 核验本机官方 App 和原生包，复制匹配的官方 sidecar 到私有目录，并记录来源；它不启动 App。bind 保存明确的选择，不猜测其他安装目录。

从绑定的原生版本生成保留其能力元数据的模型目录：

~~~powershell
$native = Join-Path (Split-Path -Parent $prepared.manifest) 'codex.exe'
$bundled = Join-Path $nativeInput 'bundled-models.json'
$catalog = Join-Path $nativeInput 'deepseek-catalog.json'
$nativeVersion = (& $native --version).Trim()

& $python -I -c "import subprocess,sys;from pathlib import Path;raw=subprocess.run([sys.argv[1],'debug','models','--bundled'],check=True,stdout=subprocess.PIPE).stdout;Path(sys.argv[2]).open('xb').write(raw)" $native $bundled
& $python -I -c "import json,sys;from pathlib import Path;from codex_app_deepseek.catalog import build_catalog;s=json.loads(Path(sys.argv[1]).read_text(encoding='utf-8-sig'));v=build_catalog(s,native_version=sys.argv[3]);Path(sys.argv[2]).open('x',encoding='utf-8',newline='\n').write(json.dumps(v,ensure_ascii=False,indent=2)+'\n')" $bundled $catalog $nativeVersion

$catalogSha = (Get-FileHash -LiteralPath $catalog -Algorithm SHA256).Hash.ToLowerInvariant()
& $cdag app-configure --catalog $catalog --catalog-sha256 $catalogSha --port 42817
& $cdag service-start --port 42817
& $cdag app-check
& $cdag app-launch --observation-seconds 120 --wait-seconds 120
& $cdag app-status
~~~

上述 --bundled 只读该二进制内嵌目录；该分支不创建在线模型或认证管理器。目录生成与配置不是能力通过证明。app-configure 通过原生 PluginManager 安装和启用官方 AppTools，记录独立回滚所有权；不靠复刻的 legacy MCP 定义替代官方工具。

默认原生 agent 模式为 V2。V1 可通过匹配目录及显式参数为新会话选择；已存在会话保留自身记录，不隐式迁移。重启后 list_agents 只列已加载原生线程，也不等于旧子会话丢失；原生已知路径可懒恢复原 UUID。

详细的身份条件、独立安装目录和升级步骤见[正式入口](docs/installed-app-entrypoints.md)与[安装生命周期](docs/lifecycle.md)。

## 模型和网页服务

主任务、子任务和显式辅助别名都请求固定 DeepSeek。新任务标题、线程描述、记忆和审核各有原生调用路径，未知模型名报错，不隐式换名。

默认 App 策略明确允许三种同模型路由：普通请求优先 deepseek；原生严格结构化场景允许 morph/fp8；显式串行工具语义允许 primeintellect。请求失败不会自动换提供方。实际服务商与请求路由分开记录，不能把 provider.only 当成实际服务商证明。

原生 web.run 的执行、引用和事件机制继续保留，网页检索和抓取服务由显式后端提供。搜索、打开、页内查找、跟随链接仍属于验收范围；无法表达的模式或约束必须明确失败，不默默改成无过滤搜索。后端和凭据见[网页路径](docs/web-runtime-path.md)。Firecrawl 目前使用有额度和速率限制的官方 keyless 服务，不承诺生产 SLA；时区地理偏置、日期精度等限制见[后端合同](docs/firecrawl-search.md)，不支持的约束不会悄悄丢掉。web.run、网页预览与 App 内置浏览器/计算机交互是不同能力。

模型质量、模型未具备的模态、金融报价/体育等 OpenAI 私有云数据服务和账户权益不包含在本地 runtime 等价中。App IAB/计算机工具仍需按[验收状态](docs/qualification-status.md)关闭，不能用云权益边界掩盖本地能力缺口。

## 日常使用、诊断与恢复

~~~powershell
& $cdag service-start
& $cdag diagnose
& $cdag app-check
& $cdag app-launch --observation-seconds 120 --wait-seconds 120
& $cdag app-status
& $cdag health --port 42817
~~~

health 只验证本地网关，不发模型请求。启动观察超时不等于进程不存在；先读 app-status 和原 run receipt，不要重复启动或按 codex.exe 名字全局结束进程。auth-token 的 stdout 是本地凭据，不应粘贴到日志。service.json 的 configured_baseline 只是策略声明，可能保留早期配置值；实际加载版本以绑定清单和 App/native 进程路径、出生时间及哈希读回为准。

| 现象 | 正常处理 |
| --- | --- |
| App 更新后不兼容 | 保留现有会话，停止使用旧组合，重新验证固定 App/runtime/catalog；不接受未知版本继续运行。 |
| 未知模型、字段或工具历史语义 | 查明确错误和本次请求回执；不要删字段、伪造工具成功或换模型绕过。 |
| 主工作树不能直接归档 | 这是原生保护。归档原任务并等待清理完成；恢复任务后使用正常 Restore worktree 入口。 |
| Git 快照/归档 commit-tree 128 | 自己的仓库需要合法 Git author/committer 身份。按用户意愿配置该仓库或自己的 Git；CADSG 不自动写全局身份，也不把失败算成功。 |
| 工作树恢复或队列仍待完成 | 先读本次原生状态和日志；不要重复 Archive、改队列或手工删除目录。 |

## 升级、回滚与卸载

升级采用新目录安装与显式 Activate。先正常关闭本项目 App，再保留旧包、原生目录、绑定及配置检查点；为新版本重新 prepare/bind/configure/check。升级不下载或替换未知 runtime，也不覆盖其他用户的 App。

~~~powershell
& $cdag service-stop
# 先通过 App 正常退出本项目实例，再执行：
& powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $installer -Action Rollback
# 完整卸载使用同一已校验安装器：
& powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $installer -Action Uninstall
~~~

回滚按项目拥有的字段和检查点恢复，保留后来由用户修改的内容；冲突明确报告。卸载使用原生 sandbox uninstall 清理本项目资源，不另写账号/权限管理器。用户工程、会话、凭据和原生按合同保留的文件系统权限不会被笼统清空；不承诺复原整张历史 DACL。

专用 App home、协议数据库、网页引用、图片状态和原始诊断属于私有工作数据。公开问题只提供去敏摘要与哈希。当前验收未关闭前不发布“全通过”，也不把旧组合的通过延伸到新版本。
