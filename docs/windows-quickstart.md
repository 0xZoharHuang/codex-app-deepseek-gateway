# Windows 安装、运行与回退

以下使用固定的官方 App 26.1023.6621.0 / runtime 0.165.0-alpha.1.2。目标用户使用**非提升 PowerShell**，先准备独立 x64 Python 3.13，本轮实际资格为 3.13.3。其他 Python 构建若触发兼容检查应保留错误，不跳过 process-local helper 的已知实现守卫。首次使用的官方 onboarding 和沙箱设置按 App 正常完成；已有其他 App/CDS 实例和数据不接管。

## 1. 下载并核对外部发行索引

从 [v0.2.0 发行页](https://github.com/0xZoharHuang/codex-app-deepseek-gateway/releases/tag/v0.2.0) 取得网关 zip、原生 tar.gz、`SHA256SUMS.json`。最终文件名、可用性、大小和 SHA 由该页的资产索引给出。**先确认索引来自可信仓库/标签，再复制对应 SHA**，不要用本地现算值充当可信期望值。

索引是单独发行资产，不塞回被其校验的归档。网关 zip 内应有 `install_gateway.ps1`、`release.json`、`wheels/`。网关 archive 和 `release.json` 的 SHA 在最终构建后写入外部索引，不硬编码回参与构建的本页。

```powershell
$state = Join-Path $env:LOCALAPPDATA 'codex-app-deepseek-gateway'
$managed = Join-Path $state 'managed-install'
$basePython = Read-Host '独立 Python 3.13 的 python.exe 完整路径'
$gatewayZip = Read-Host '已下载网关 zip 的完整路径'
$gatewayZipSha = Read-Host '从可信 SHA256SUMS.json 复制该网关 zip 的 SHA256'
$gatewaySha = Read-Host '从同一索引复制包内 release.json 的 SHA256'
$nativeArchive = Read-Host '已下载原生 tar.gz 的完整路径'
$nativeArchiveSha = Read-Host '从 v0.2.0 外部校验索引复制 P20 原生 tar.gz 的 SHA256'
$rawNativeSha = Read-Host '从同一索引复制 P20 原生 manifest.json 的 SHA256'
$nativeMemberCount = [int](Read-Host '从同一发行页的原生包库存回执复制普通文件条目数')
if ((Get-FileHash -LiteralPath $gatewayZip -Algorithm SHA256).Hash -ne $gatewayZipSha) { throw '网关归档 SHA 不符' }
if ((Get-FileHash -LiteralPath $nativeArchive -Algorithm SHA256).Hash -ne $nativeArchiveSha) { throw '原生归档 SHA 不符' }
$bundle = Join-Path $state 'downloads\gateway-v0.2.0'
if (Test-Path -LiteralPath $bundle) { throw '输出目录已存在，请先核原回执，不覆写' }
Expand-Archive -LiteralPath $gatewayZip -DestinationPath $bundle
$installer = Join-Path $bundle 'install_gateway.ps1'
```

固定原生 tar 为 142,231,452 字节、3,037 个普通文件。它不含闭源 App/Code Mode host。上述两个原生 SHA 还须与可信发行索引对应；不要混用不同版本的资产。

## 2. 正常安装、选择 release 和保存凭据

```powershell
$reply = & powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $installer `
  -Action Install -ManifestPath (Join-Path $bundle 'release.json') -ManifestSha256 $gatewaySha `
  -Wheelhouse (Join-Path $bundle 'wheels') -PythonExe $basePython -InstallRoot $managed -DataHome $state
if ($LASTEXITCODE -ne 0) { throw 'Install 失败，保留原回执' }
$install = $reply | ConvertFrom-Json
$reply = & powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $installer `
  -Action Activate -ReleaseId $install.release_id -InstallRoot $managed
if ($LASTEXITCODE -ne 0) { throw 'Activate 失败，停止后续操作' }
$active = $reply | ConvertFrom-Json
$cdag = $active.command
$python = Join-Path (Split-Path -Parent $cdag) 'python.exe'
& $cdag --version
& $cdag setup-credentials
```

Install 校验所有锁定 wheel，离线创建独立 venv，**不激活、不启动、不配置 App**。Activate 只选择已经验证的 release，已有 `gateway.json` 不被覆盖。`activated_not_started` 不是运行证明。

`setup-credentials` 隐藏输入 OpenRouter key，写入当前 Windows 用户的项目专用 WinCred 项 `ai.codex.app-deepseek.openrouter`；App 通过另一个 `ai.codex.app-deepseek.local-token` 使用本地网关，不获得上游 key。同一 Windows 用户的多个 CADSG home 共享这两个项目凭据项，不谎称每个 home 各有独立 WinCred。

不要将 key 写入命令参数、提示、仓库或日志，不分享 `auth-token` 的 stdout。默认不读取 CDS；仅明确需要从当前用户 CDS 项复制时使用已有的 `setup-credentials --import-existing-cds`。

本轮完整网页任务明确选用 Morph + Firecrawl。若复现这个已验组合，先按下方[模型和网页后端](#模型和网页后端)在服务未启动时显式选择，再继续。省略选择仍保持源码官方/Exa 默认及其参数限制，不自动切换。

## 3. 解压到永久原生版本目录，再 prepare / bind

正常退出占用本项目 profile 的 App，停止本项目网关。不要全局结束 Codex 或其他用户进程。

```powershell
$nativeInput = Join-Path $state ('native-runtimes\o165-p20-' + $nativeArchiveSha.Substring(0,12))
# 标准库 data filter；只解压已核定的固定发行归档，输出必须是全新目录。
$extract = @'
from pathlib import Path, PurePosixPath
import sys, tarfile
archive, root = Path(sys.argv[1]), Path(sys.argv[2])
if root.exists() or root.is_symlink(): raise SystemExit('Existing native directory: inspect, do not overwrite')
with tarfile.open(archive, 'r:gz') as tf:
    members = tf.getmembers()
    if int(sys.argv[3]) <= 0 or len(members) != int(sys.argv[3]): raise SystemExit('Unexpected native inventory')
    seen = set()
    for item in members:
        p = PurePosixPath(item.name)
        if not item.isfile() or p.is_absolute() or '..' in p.parts or ':' in item.name or chr(92) in item.name or item.name.casefold() in seen:
            raise SystemExit('Unsafe native archive member')
        seen.add(item.name.casefold())
    root.mkdir(parents=True)
    tf.extractall(root, filter='data')
'@
& $basePython -I -B -c $extract $nativeArchive $nativeInput $nativeMemberCount
if ($LASTEXITCODE -ne 0) { throw '解压未完成，保留原状态，不继续绑定' }
$reply = & $cdag app-prepare-runtime --install-root $managed `
  --native-manifest (Join-Path $nativeInput 'manifest.json') --native-manifest-sha256 $rawNativeSha
if ($LASTEXITCODE -ne 0) { throw 'prepare 失败' }
$prepared = $reply | ConvertFrom-Json
& $cdag app-bind --install-root $managed --native-manifest $prepared.manifest `
  --native-manifest-sha256 $prepared.manifest_sha256
if ($LASTEXITCODE -ne 0) { throw 'bind 失败' }
```

prepare 在永久版本目录中校验 P20 的六个同源程序，并核对本机已安装的官方 App 和插件资源。生成的 `app-runtime-manifest.json` 包含本机路径，仅供当前用户绑定。多个版本可以并排保留，升级或回滚时使用各自的清单和 SHA。

## 4. 从选定 runtime 生成目录并配置

没有另一个 catalog CLI。使用安装模块公开的 `catalog.build_catalog`，元数据来自上一步实际二进制；以下两个 metadata 命令使用独立 home，不读取默认 `.codex` 或生产 App 会话。

```powershell
$native = Join-Path $nativeInput 'codex.exe'
$metadata = Join-Path $state ('catalog-build\o165-p20-' + $rawNativeSha.Substring(0,12))
$makeCatalog = @'
from pathlib import Path
import hashlib, json, os, subprocess, sys
from codex_app_deepseek.catalog import build_catalog
native, out = sys.argv[1], Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=False)
home = out / 'native-home'; home.mkdir()
(home / 'config.toml').write_bytes(b'cli_auth_credentials_store="file"\nmcp_oauth_credentials_store="file"\n')
env = {k:v for k,v in os.environ.items() if not k.upper().startswith(('CODEX','OPENAI','OPENROUTER','PYTHONPATH','PYTHONHOME'))}
env['CODEX_HOME'] = str(home)
version = subprocess.run([native, '--version'], env=env, check=True, stdout=subprocess.PIPE).stdout.decode('utf-8-sig').strip()
raw = subprocess.run([native, 'debug', 'models', '--bundled'], env=env, check=True, stdout=subprocess.PIPE).stdout
(out / 'bundled-models.json').write_bytes(raw)
value = build_catalog(json.loads(raw), native_version=version, multi_agent_version='v2')
body = (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
path = out / 'deepseek-catalog.json'; path.write_bytes(body)
print(json.dumps({'path':str(path), 'sha256':hashlib.sha256(body).hexdigest(), 'native_version':version}))
'@
$reply = & $python -I -B -c $makeCatalog $native $metadata
if ($LASTEXITCODE -ne 0) { throw '目录生成失败，不改输入继续' }
$catalog = $reply | ConvertFrom-Json
& $cdag app-configure --install-root $managed --catalog $catalog.path `
  --catalog-sha256 $catalog.sha256 --multi-agent-version v2 --port 42817
if ($LASTEXITCODE -ne 0) { throw 'app-configure 失败' }
```

这里对可信二进制现场生成的 catalog 计算 SHA 是正常本地来源链；它不能替代下载资产的可信 SHA。app-configure 保存不可变目录，合并独立 App 配置，登记检查点并通过原生 PluginManager 接通官方 AppTools；不写替代 agent/MCP 系统。

`gpt-5.6-luna`、`gpt-6-luna`、`gpt-6.1-sol` 是这份 App/runtime 的明确辅助路由别名，推理仍固定 DeepSeek。Guardian、记忆与压缩不能为通过验收而关闭。默认多智能体 V2；需要 V1 时，另生成 `multi_agent_version='v1'` 的目录并在 app-configure 显式选 v1，再开新 chat；已有会话保留其记录版本。

### 升级已有网关时校准声明的原生版本

已有安装沿用原 InstallRoot、DataHome 和监听端口。正常退出本项目 App、停止该网关。备份 DataHome 下 `gateway.json` 的原始字节和 SHA256，备份目录使用与配置相同的私有权限。prepare 完成后，从已核验的 manifest 读取 `codex.exe` 的 SHA，与发行索引和永久目录中的文件核对。

若 `gateway.json` 声明了旧 `native_sha256`，在网关仍停止时只更新该字段。以备份的原始 SHA 为条件调用已安装模块 `codex_app_deepseek.app_config._atomic_write` 的 `expected_sha256` 参数；冲突则重新读取，不覆盖并发修改。解析写后 JSON 并确认其他键和值与原配置相同，保留原始、写后两个摘要供回滚。回滚时核验当前 native_sha256 等于本次安装值，再基于最新内容、以最新 SHA 作条件恢复旧字段，保留后续无关设置。省略该字段的已有配置可继续使用原选项。

升级前保留原网关的 release ID。记录 app-bind 与 app-configure 各自返回的
binding_backup。若需恢复旧绑定，先正常退出本项目 App，在当前网关仍激活时执行以下命令，
把新目录配置的备份放在运行时重绑备份之前。随后分别恢复配置检查点、网关声明并重新激活原版本。

```powershell
& $cdag app-restore-binding --install-root $managed --backup $configureBackup --backup $bindBackup
```

命令校验整条变更链和当前绑定，成功时只替换一次。若其间有人选择了其他运行时或目录，
命令会保留对方的更改。已经选中相同绑定的重复操作没有新备份，只传实际返回的目录。

声明字段记录操作者选择的版本。`diagnose` 单独核对官方签名 App 自带的原生基线；`app-status` 以实际 App 与 backend 的 PID、出生时间、映像路径和 SHA 核验本次加载。切换后同时检查配置摘要、运行服务加载的配置以及实际进程身份。

## 5. 启动并回读同一次操作

```powershell
& $cdag service-start --install-root $managed --port 42817
if ($LASTEXITCODE -ne 0) { throw '先检查原服务状态，不重复 start' }
& $cdag app-check --install-root $managed --accessibility --observation-seconds 120
if ($LASTEXITCODE -ne 0) { throw '检查未通过，不 launch' }
& $cdag app-launch --install-root $managed --accessibility --observation-seconds 120 --wait-seconds 120
& $cdag app-status --install-root $managed
```

冷启动可能超过观察窗口。pending/timeout/unknown **不等于未创建**：先运行 app-status 找到实际 run ID，再用 `app-status --install-root $managed --run-id <实际32位run_id>` 回读原操作。不要重复 launch、全局 kill 或改回旧配置来制造成功。`app-check`、health 与已受理的 GUI 动作均不是整个真实任务通过；还要核实际 App/native 映像与界面。

单次诊断可给 `app-launch` 增加 `--trace-root` 和用户拥有的本地目录。启动器为这次启动创建受保护的子目录，再核对实际 App 与 backend 的环境。官方 trace 会保存请求、图片和工具输出，请只在私有目录保留原始文件。日常启动无需启用该选项。

## 模型和网页后端

这些是明确策略修改，不是失败后 fallback。先结束任务、退出本项目 App、正常 `service-stop --install-root $managed`；保存原文件与 SHA，对 `$state\gateway.json` 只合并所需字段，保留模型别名、allowlist、代理和其他配置。

```json
{"ordinary_default_provider":"morph/fp8"}
```

默认普通 route 为 `deepseek`，支持的另一值只有 `morph/fp8`；没有 CADSG provider-set CLI。标准 allowlist 已有 deepseek/morph/fp8/primeintellect；严格 schema 与显式 serial 等特殊路由保持原优先级，Prime 不是普通默认选项。所有请求仍是固定模型且禁止自动 fallback。切回官方也是一次明确修改。

```json
{"web_search_backend":"firecrawl","image_search_backend":"firecrawl"}
```

省略文本选择保留 openrouter_exa；图片还可为 null 或 wikimedia_commons。Firecrawl 图片要求文本同时选 Firecrawl。query/URL/图片可能发往选定外部服务，localhost 不表示数据不出本机。认证、配额和可用性以服务方合同为准。

修改后正常 service-start，再对照：

```powershell
& $cdag diagnose --install-root $managed
& $cdag health --port 42817
& $cdag app-check --install-root $managed
```

磁盘 `configured_gateway_policy` 与运行 health 的 config_sha256、ordinary_default_provider、allowlist 应相符。`configured_baseline` 是声明；实际加载由绑定、App/native PID/出生时间/映像路径和哈希证明。服务不热加载文件，health 不进行模型 generation。模型实际供应方身份只能按真实上游证据说明，不据 `provider.only` 或空 usage 编造。

## 升级、回滚与卸载

升级保留独立 Python、旧网关 release、旧永久原生目录、绑定与登记的检查点。正常退出本项目 App、停止服务后，用新已核包再次 Install，再显式 Activate、prepare/bind/configure/check。不要覆盖运行文件或复制活数据库单文件；需要数据备份时先闭库，保一致性快照与会话。

```powershell
& $cdag service-stop --install-root $managed
& powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $installer -Action Rollback -InstallRoot $managed
# 卸载只针对本项目，仍使用保留的可信安装器/独立 Python。
& powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $installer `
  -Action Uninstall -InstallRoot $managed -PythonExe $basePython
```

Rollback 按原始/已应用/当前内容恢复登记的配置字段，保留后来无关设置；**不自动选择旧 gateway release**。需要旧网关时对保留的 release 执行 `-Action Activate -ReleaseId <旧release ID> -InstallRoot $managed`，再用该版本正常绑定/配置/检查。不要把旧 binding JSON 或整个旧数据库盖回后来工作。原生已知 LF/CRLF 兼容不代表任意旧版本数据库互读。

正常 app-configure 自动登记检查点。仅低层 configure-app 得到未登记 checkpoint 时，使用 `-Action RegisterCheckpoint -Checkpoint <实际checkpoint.json> -InstallRoot $managed`；不要手造检查点。临时操作者设置若不在托管字段内，应保留单独逐字段 CAS 记录，恢复时仅还原自己实际改动且当前仍等于自己的值。

Uninstall 先用原生规则清理本项目自有 namespace，再由独立 Python 两阶段清理文件，默认保留工程、会话、凭据、数据 home 与恢复输入。未知 owner、活任务、冲突或权限不足会停止，不切 full access/改防护绕过。`uninstalled_cleanup_pending` 按同一回执重试收尾；原生清理未知时先检查，必要时才显式 `-RetryNativeCleanup`，不能把未知当完成。
