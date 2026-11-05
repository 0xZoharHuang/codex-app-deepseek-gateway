# Windows 私有工程目录与错误 267

如果 App 已连到模型，但第一次终端工具返回 `CreateProcessWithLogonW failed: 267`，应分别核对模型请求和 Windows 进程启动。Windows 会用目标账户的安全上下文访问指定的可执行文件和当前目录。先核对实际 cwd、物理路径、目标账户能否使用该盘符，以及当前加载的 runner。[Windows API 说明](https://learn.microsoft.com/windows/win32/api/winbase/nf-winbase-createprocesswithlogonw)

在一次 Windows 10 的真实 App 验收中，模型正常返回了工具调用，Windows 在 Python 诊断开始前返回了错误 267。工程位于测试宿主创建的私有父目录下，工程本身已有沙箱组的读权限，父目录只有 SYSTEM、Administrators 和工程所有者的权限。

## 宿主创建私有工程时

1. 核对工程及其直接私有父目录的所有者和物理目录身份。根据这份 CADSG home 的 setup marker 及本机实际账户、组成员关系确认专用沙箱组 SID。
2. 在宿主原有的目录初始化步骤中，为确需访问的私有中间目录添加一条仅对目录本身生效的允许项，其权限为 `READ_ATTRIBUTES | SYNCHRONIZE`，掩码 `0x00100080`，继承和传播标志均为 None。范围限定到宿主拥有的目录及已核验的组。盘根、其他用户目录和保护凭据的兄弟目录应保持各自原有的权限。
3. 对已存在的目录，先留存原始安全描述符。使用核验过的同一目录身份修改，回读前后权限项、目录身份以及工程和私有兄弟目录的安全描述符。回滚只在当前描述符仍等于本次修改结果时执行。有并发改动时先保留并核对现场。
4. 从 App 的真实工具调用复测。只读策略下，允许工作区读取，拒绝工作区写入及私有兄弟文件读取。若相同调用仍失败，继续核查 Win32 错误、原生 runner 的实际 cwd 和盘符可见性。

这次实测只改变父目录的上述一条权限。相同 App、原生进程、线程、cwd、策略和 3,244 字节工具调用再次执行后，目标沙箱账户读到了工程 README，工作区写入、私有对照文件读取及外部网络访问均被拒绝。工程目录与私有兄弟目录的安全描述符逐字节一致。宿主管理私有工程时，CDS 使用相同的目录元数据约定。[CDS 受管 Windows 安装说明](https://github.com/0xZoharHuang/codex-deepseek-runtime/blob/v0.6.15/docs/windows-managed-maintenance.md)
