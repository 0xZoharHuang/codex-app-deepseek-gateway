# Windows 桌面 App 的本机 Python 回环

Windows 的 Python `asyncio` 会用本机 TCP 建立内部 `socketpair()`。工程需要这一能力时，可以给可信工程定义一项命名权限，保留外网边界。先选一个本项目可以持续使用的空闲回环端口，以下示例使用 31283。将字段合并到工程的 `.codex/config.toml` 中。[官方网络权限配置](https://learn.chatgpt.com/docs/permissions#network-permissions)

```toml
default_permissions = "local-python"
approval_policy = "on-request"

[features]
network_proxy = true

[permissions.local-python]
extends = ":workspace"

[permissions.local-python.network]
enabled = true
proxy_url = "http://127.0.0.1:31283"
enable_socks5 = false
allow_upstream_proxy = false
allow_local_binding = true
mode = "limited"

[permissions.local-python.network.domains]
"localhost" = "allow"
"example.com" = "deny"
```

在目标工程的 App 权限菜单里选择 **`local-python`**。菜单里的 `Custom (config.toml)` 是独立的旧式选项。受信任工程刚添加配置时，可以先结束当前任务，正常退出并重新启动这份 CADSG App，再从工程里打开权限菜单。App Server 的 `permissionProfile/list` 接受工程 cwd，创建线程时可以通过 `permissions` 明确选择命名配置。[官方线程与权限接口](https://learn.chatgpt.com/docs/app-server#start-or-resume-a-thread)

## 当 Windows 要求更新沙箱设置

首次设置或代理端口、本机绑定选项发生变化时，Windows 可能要求提权以部署这份 home 专用的沙箱策略。在普通交互桌面上，核对这份 App 和原生 helper 的身份后走正常设置流程。端口固定后，可回读这一命名空间的 setup marker，再用真实工具检查连接。

受管 Windows 的管理员也可以从已验证的原生包运行 `sandbox setup --elevated --current-user --codex-home <CADSG 专用 home>`。这个管理命令从目标 home 解析配置。若权限仅定义在工程层，需通过该次命令的原生 `-c` 参数显式传入同一项权限。以下 Python 示例用于同一目标用户的管理员会话，从上面的工程配置读取端口。由另一名管理员管理其他用户时，应使用原生的目标用户选项和该用户自己的 CADSG home。

```python
import hashlib
import os
import subprocess
import tomllib
from pathlib import Path

native = Path(input("P20 codex.exe 的绝对路径：")).resolve(strict=True)
expected = input("从可信发行清单读取的 codex.exe SHA-256：").strip().lower()
with native.open("rb") as binary:
    if hashlib.file_digest(binary, "sha256").hexdigest() != expected:
        raise ValueError("原生程序与可信清单的 SHA-256 不匹配")
project = Path(input("受信任工程的绝对路径：")).resolve(strict=True)
home = (Path(os.environ["LOCALAPPDATA"]) / "codex-app-deepseek-app").resolve(strict=True)
config = tomllib.loads((project / ".codex/config.toml").read_text(encoding="utf-8-sig"))
profile = config["permissions"]["local-python"]
url = profile["network"]["proxy_url"]
prefix = "http://127.0.0.1:"
if not (url.startswith(prefix) and url[len(prefix):].isascii() and url[len(prefix):].isdecimal()):
    raise ValueError("代理地址必须是明确的 IPv4 本机回环地址和端口")
port = int(url[len(prefix):])
if not 1024 <= port <= 65535:
    raise ValueError("端口超出本示例的范围")
table = ('{extends=":workspace",network={enabled=true,'
         f'proxy_url="http://127.0.0.1:{port}",'
         'enable_socks5=false,allow_upstream_proxy=false,allow_local_binding=true,'
         'mode="limited",domains={localhost="allow","example.com"="deny"}}}')
if tomllib.loads("v=" + table)["v"] != profile:
    raise ValueError("工程权限与这个受管配置示例不同，请核对部署参数")
subprocess.run([
    str(native), "-c", "features.network_proxy=true",
    "-c", 'default_permissions="local-python"',
    "-c", "permissions.local-python=" + table,
    "sandbox", "setup", "--elevated", "--current-user", "--codex-home", str(home),
], check=True)
```

## 在 App 里核对行为

核对真实 turn 的 `active_permission_profile.id`、工作目录以及所选端口的监听进程。让同一工具在工作区创建临时文件并运行 `socket.socketpair()` 本地收发，再试受保护的兄弟文件、直接外部连接、被明确拒绝和未加入许可表的代理请求。保留每一步的实际结果。

允许本机绑定也允许目标沙箱账户连接宿主的其他本机服务，应只在需要这项能力的工程中使用。回到离线策略时，恢复本次工程配置并通过同一原生入口部署该策略，然后复测回环拒绝。P20 对历史内核过滤规则的处置与保留边界见[实现及资格说明](windows-loopback-reconciliation.md)。
