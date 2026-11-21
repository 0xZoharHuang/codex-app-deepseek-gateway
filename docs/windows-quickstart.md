# Windows 安装、运行与回退

O169 面向独立的 Windows 10 x64 19045 验收环境。使用独立 CPython 3.13 x64、同一发行标签的 CADSG 制品，以及通过官方渠道取得的签名 Codex App `26.1111.2228.0`。固定原生基线为 `0.169.0-alpha.11.1`。旧 P7 安装及其配套 App 见 [v0.1.0 的 Windows 安装说明](https://github.com/0xZoharHuang/codex-app-deepseek-gateway/blob/v0.1.0/docs/windows-quickstart.md)。

在专用 Windows 用户的普通 PowerShell 操作。初次 App 引导、原生沙箱配置依照 App 提示。验收使用空白专用工程及独立配置，系统防护继续开启。

## 1. 下载和校验同一发行的制品

从 [GitHub Releases](https://github.com/0xZoharHuang/codex-app-deepseek-gateway/releases) 选择已提供 O169 资产的标签。每个标签的 `SHA256SUMS.json`、网关 ZIP、原生 tar.gz 及依赖源码附包各自单列。最终资格以对应发行记录为准。

```powershell
$repo = 'https://github.com/0xZoharHuang/codex-app-deepseek-gateway'
$tag = Read-Host '输入所选 O169 发行标签'
if ($tag -notmatch '^v[0-9]+(?:[.][0-9]+){2}(?:-[A-Za-z0-9][A-Za-z0-9.-]*)?$') { throw '标签无效' }
$basePython = Read-Host '输入独立 Python 3.13 的 python.exe 绝对路径'
$state = Join-Path $env:LOCALAPPDATA 'codex-app-deepseek-gateway'
$managed = Join-Path $state 'managed-install'
$assets = Join-Path $state ('downloads\' + $tag)
if (Test-Path -LiteralPath $assets) { throw '请检查已存在的下载目录' }
New-Item -ItemType Directory -Path $assets | Out-Null
$url = "$repo/releases/download/$tag"
$indexPath = Join-Path $assets 'SHA256SUMS.json'
Invoke-WebRequest -UseBasicParsing "$url/SHA256SUMS.json" -OutFile $indexPath
$index = Get-Content -LiteralPath $indexPath -Raw | ConvertFrom-Json
if ($index.schema_version -ne 1 -or $index.source_repository -ne $repo -or
    $index.release_tag -cne $tag -or $index.source_commit -notmatch '^[0-9a-f]{40}$' -or
    $index.native_tar.build_lock_sha256 -ne '0ab81f556d6062026b2ff6194351d697b04483bfc3b55a197a5b32944031ee72') {
  throw '发行索引与 O169 组合不符'
}
function Get-CADSGAsset($asset) {
  $name = [string]$asset.filename
  if ($name -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]+$' -or $asset.sha256 -notmatch '^[0-9a-f]{64}$') { throw '资产索引无效' }
  $path = Join-Path $assets $name
  Invoke-WebRequest -UseBasicParsing "$url/$name" -OutFile $path
  if ((Get-Item -LiteralPath $path).Length -ne [long]$asset.bytes -or
      (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -ne $asset.sha256) { throw "下载校验失败：$name" }
  $path
}
$gatewayZip = Get-CADSGAsset $index.gateway_zip
$nativeTar = Get-CADSGAsset $index.native_tar
$bundle = Join-Path $assets 'gateway'
Expand-Archive -LiteralPath $gatewayZip -DestinationPath $bundle
$gatewayManifest = Join-Path $bundle 'release.json'
$installer = Join-Path $bundle 'install_gateway.ps1'
if ((Get-FileHash -LiteralPath $gatewayManifest -Algorithm SHA256).Hash -ne $index.gateway_zip.release_manifest_sha256) {
  throw '网关清单校验失败'
}
$release = Get-Content -LiteralPath $gatewayManifest -Raw | ConvertFrom-Json
if ($release.source_commit -ne $index.source_commit -or $release.app_baseline.app_version -ne '26.1111.2228.0' -or
    $release.app_baseline.native_version -ne '0.169.0-alpha.11.1') { throw '网关清单不是索引固定的 App 组合' }
$nativeInput = Join-Path $state ('native-runtimes\o169-' + $index.native_tar.sha256.Substring(0,12))
$extract = @'
from pathlib import Path, PurePosixPath
import sys, tarfile
archive, root = Path(sys.argv[1]), Path(sys.argv[2])
if root.exists() or root.is_symlink(): raise SystemExit('Native output already exists')
with tarfile.open(archive, 'r:gz') as tf:
    members, seen = tf.getmembers(), set()
    for item in members:
        p = PurePosixPath(item.name)
        if not item.isfile() or p.is_absolute() or '..' in p.parts or ':' in item.name or chr(92) in item.name or item.name.casefold() in seen:
            raise SystemExit('Unsafe native archive member')
        seen.add(item.name.casefold())
    root.mkdir(parents=True)
    tf.extractall(root, filter='data')
'@
& $basePython -I -B -c $extract $nativeTar $nativeInput
if ($LASTEXITCODE -ne 0 -or (Get-FileHash -LiteralPath (Join-Path $nativeInput 'manifest.json') -Algorithm SHA256).Hash -ne $index.native_tar.native_manifest_sha256) {
  throw '原生归档或清单校验失败'
}
```

依赖源码附包用于重建，普通安装使用已配齐的离线 wheel。官方 App 与其闭源资源另行取得。[网关和锁定依赖构建](gateway-release-build.md)

## 2. 安装、激活并进入独立桌面

```powershell
$reply = & powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $installer `
  -Action Install -ManifestPath $gatewayManifest -ManifestSha256 $index.gateway_zip.release_manifest_sha256 `
  -Wheelhouse (Join-Path $bundle 'wheels') -PythonExe $basePython -InstallRoot $managed -DataHome $state
if ($LASTEXITCODE -ne 0) { throw 'Install 失败，保留原始回执' }
$install = $reply | ConvertFrom-Json
$reply = & powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $installer `
  -Action Activate -ReleaseId $install.release_id -InstallRoot $managed
if ($LASTEXITCODE -ne 0) { throw 'Activate 失败' }
$active = $reply | ConvertFrom-Json
$env:CADSG_BOOTSTRAP_PYTHON = $basePython
$env:CADSG_ASSET_TAG = $tag
$helper = Join-Path $bundle 'enter_app_desktop.py'
& $basePython -I -B $helper --install-root $managed --show
```

Install 校验并安装 wheel，Activate 选择不可变的安装 ID。两步均在启动 App 前完成。示例的脚本宿主参数只作用于本次校验后的安装器子进程。`--show` 在专用 Windows 环境显示本项目私有桌面，供交互验收。结束前正常退出该 App，再关闭私有 PowerShell。无人值守的隔离验收可用 `--script $trustedLocalPs1` 替代 `--show`，执行可信的本地脚本且不切换输入桌面。两种模式互斥。

## 3. 在私有 PowerShell 配置并启动

```powershell
$managed = $env:CADSG_DESKTOP_INSTALL_ROOT
$cdag = $env:CADSG_DESKTOP_COMMAND
$state = Split-Path -Parent $managed
$assets = Join-Path $state ('downloads\' + $env:CADSG_ASSET_TAG)
$index = Get-Content -LiteralPath (Join-Path $assets 'SHA256SUMS.json') -Raw | ConvertFrom-Json
$nativeInput = Join-Path $state ('native-runtimes\o169-' + $index.native_tar.sha256.Substring(0,12))
$python = Join-Path (Split-Path -Parent $cdag) 'python.exe'
$port = 42817
& $cdag setup-credentials
if ($LASTEXITCODE -ne 0) { throw '凭据设置失败' }
$reply = & $cdag app-prepare-runtime --install-root $managed `
  --native-manifest (Join-Path $nativeInput 'manifest.json') --native-manifest-sha256 $index.native_tar.native_manifest_sha256
if ($LASTEXITCODE -ne 0) { throw 'prepare 失败' }
$prepared = $reply | ConvertFrom-Json
$reply = & $cdag app-bind --install-root $managed --native-manifest $prepared.manifest `
  --native-manifest-sha256 $prepared.manifest_sha256
if ($LASTEXITCODE -ne 0) { throw 'bind 失败' }
$bound = $reply | ConvertFrom-Json

$makeCatalog = @'
from pathlib import Path
import hashlib, json, os, subprocess, sys
from codex_app_deepseek.catalog import build_catalog
native, out = sys.argv[1], Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=False)
home = out / 'native-home'; home.mkdir()
(home / 'config.toml').write_bytes(b'cli_auth_credentials_store=\x22file\x22\nmcp_oauth_credentials_store=\x22file\x22\n')
env = {k:v for k,v in os.environ.items() if not k.upper().startswith(('CODEX','OPENAI','OPENROUTER','PYTHONPATH','PYTHONHOME'))}
env['CODEX_HOME'] = str(home)
version = subprocess.check_output([native, '--version'], env=env).decode('utf-8-sig').strip()
raw = subprocess.check_output([native, 'debug', 'models', '--bundled'], env=env)
value = build_catalog(json.loads(raw), native_version=version, multi_agent_version='v2')
body = (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
path = out / 'deepseek-catalog.json'; path.write_bytes(body)
print(json.dumps({'path':str(path), 'sha256':hashlib.sha256(body).hexdigest()}))
'@
$metadata = Join-Path $state ('catalog-build\o169-' + $index.native_tar.sha256.Substring(0,12))
$reply = & $python -I -B -c $makeCatalog (Join-Path $nativeInput 'codex.exe') $metadata
if ($LASTEXITCODE -ne 0) { throw '目录生成失败' }
$catalog = $reply | ConvertFrom-Json
$reply = & $cdag app-configure --install-root $managed --catalog $catalog.path `
  --catalog-sha256 $catalog.sha256 --multi-agent-version v2 --port $port
if ($LASTEXITCODE -ne 0) { throw 'app-configure 失败' }
$configured = $reply | ConvertFrom-Json
& $cdag service-start --install-root $managed --port $port
if ($LASTEXITCODE -ne 0) { throw '请回读原网关状态' }
& $cdag app-check --install-root $managed --accessibility --observation-seconds 120
if ($LASTEXITCODE -ne 0) { throw 'check 失败' }
& $cdag app-launch --install-root $managed --accessibility --observation-seconds 120 --wait-seconds 120
& $cdag app-status --install-root $managed
```

凭据由命令交互读取至当前 Windows 用户的项目专用 WinCred；避免写入命令、脚本或日志。目录由已校验原生程序在新 metadata home 生成。App 通过其正常引导和沙箱权限入口完成初始化，验收核对真实 App、后端、工具结果和重新打开的会话。

外层等待超时时，按 `app-status --install-root $managed --run-id <实际 run_id>` 回读同一次操作。保留 `$install.release_id`、原生目录、绑定备份和已登记的配置检查点以用于回退。[安装版 App 参数](installed-app-entrypoints.md)

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

升级前正常退出此安装的 App 并停止网关。保留之前的 release ID、原生目录与绑定备份，用已核对的同组合制品执行 Install、Activate 和私有桌面的 prepare、bind、configure 与 check。

在原私有 PowerShell 先确认 App 已正常退出，再恢复本项目选中的绑定与已登记的配置检查点。绑定备份只传真实回执中存在的路径，按从新到旧的顺序传入。需要选择已保留的兼容 release 时，再执行 Activate 并回读验证。

```powershell
$basePython = $env:CADSG_BOOTSTRAP_PYTHON
$installer = Join-Path $assets 'gateway\install_gateway.ps1'
& $cdag service-stop --install-root $managed
if ($LASTEXITCODE -ne 0) { throw '先检查实际停止状态' }
$bindingArgs = @()
foreach ($backup in @($configured.binding_backup, $bound.binding_backup)) {
  if ($backup) { $bindingArgs += @('--backup', $backup) }
}
if ($bindingArgs.Count) {
  & $cdag app-restore-binding --install-root $managed @bindingArgs
  if ($LASTEXITCODE -ne 0) { throw '绑定恢复停止，保留原回执' }
}
& powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $installer -Action Rollback -InstallRoot $managed
if ($LASTEXITCODE -ne 0) { throw '配置恢复未完成，先读回检查点' }
# 如果选择先前的兼容网关版本：
# & powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $installer -Action Activate -ReleaseId $compatiblePreviousId -InstallRoot $managed
```

Rollback 恢复已登记的配置字段，保留后续无关设置。Activate 独立选择网关版本。历史 P7 需要自身匹配的签名 App 和原生包，O169 的 App 安装不提供此前提。不要把旧数据库或旧 binding 直接盖回当前状态。

如果直接卸载当前安装，正常退出该 App 并保留有效绑定和原生目录。退出私有 PowerShell 后，由同一 Windows 用户在专用环境的维护 PowerShell 运行已校验的安装器。若该用户已完成原生沙箱设置，请提升该用户自己的维护 PowerShell；管理员凭据若属于另一用户，停止并保留原绑定。重新赋值下面的路径为本次已记录的安装及验证来源。

```powershell
$installer = Read-Host '输入先前已校验的 install_gateway.ps1 路径'
$managed = Read-Host '输入本次记录的 InstallRoot'
$basePython = Read-Host '输入本次记录的独立 Python 路径'
& powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $installer `
  -Action Uninstall -InstallRoot $managed -PythonExe $basePython
```

先检查回执中的实际终态。Uninstall 保留工程、会话、数据和凭据；`uninstalled_cleanup_pending` 需要按同一回执继续处理。 [安装、回滚和卸载机制](lifecycle.md)
